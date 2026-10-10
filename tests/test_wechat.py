"""Integration invariants replay only the locally captured real WeChat rows."""

import json
import sqlite3
import threading
from collections import defaultdict
from pathlib import Path

import pytest

from niannian.media import TITLE_REQUEST, attachment_only, parse_attachment, safe_media_path, summarize
from niannian.storage import Database
from niannian.wechat import WechatWorker, _sender, acquire_instance_lock, infer_self_ids, normalize, session_id


@pytest.fixture(scope="module")
def captured():
    path = Path(__file__).resolve().parents[1] / "data" / "raw-wechat.json"
    if not path.exists():
        pytest.skip("A locally captured WeChat fixture is required; synthetic messages are forbidden")
    return json.loads(path.read_text(encoding="utf-8"))


class CapturedSource:
    def __init__(self, captured):
        self.groups = defaultdict(list)
        self.names = {}
        for row in captured["messages"]:
            self.groups[row["session_id"]].append(dict(row["raw"]))
            self.names[row["session_id"]] = row["session_name"]

    def get_sessions(self, limit=1000):
        return [{"username": username, "unread": 0,
                 "last_time": max(m["create_time"] for m in messages)}
                for username, messages in self.groups.items()][:limit]

    def nickname_map(self, refresh=False):
        return self.names

    def get_messages(self, username, limit=20, offset=0):
        messages = sorted(self.groups[username], key=lambda m: m["sort_seq"], reverse=True)
        return messages[offset:offset + limit]


@pytest.fixture
def worker(tmp_path, captured):
    db = Database(tmp_path / "test.sqlite3")
    db.init()
    reader = WechatWorker(db, {"history_limit": 10}, tmp_path)
    reader._wx = CapturedSource(captured)
    reader._state["account"] = captured["account"]
    reader._sync_catalog()
    return reader


def test_real_self_messages_and_group_prefix(captured):
    self_rows = [row for row in captured["messages"] if _sender(row["raw"].get("content", ""))[0] == captured["account"]]
    assert self_rows
    for row in self_rows:
        payload = normalize(captured["account"], row["session_id"], row["raw"])
        assert payload["is_self"] and payload["sender_name"] == "我"
        assert payload["sender_key"] == captured["account"]
        assert payload["metadata"]["identity_confident"]
    group = next(row for row in captured["messages"] if row["session_id"].endswith("@chatroom")
                 and _sender(row["raw"].get("content", ""))[0] and row["raw"]["type"] == "文本")
    origin, content = _sender(group["raw"]["content"])
    result = normalize(captured["account"], group["session_id"], group["raw"])
    assert result["content"] == content and result["sender_key"] == origin


def test_nonstandard_self_id_from_real_source(captured):
    candidates = [row for row in captured["messages"] if _sender(row["raw"].get("content", ""))[0] == captured["account"]
                  and row["raw"].get("sender_id") != 2]
    assert candidates
    for row in candidates:
        assert row["raw"]["sender_id"] in infer_self_ids(captured["account"], [row["raw"]])
        assert normalize(captured["account"], row["session_id"], row["raw"])["is_self"]


def test_numeric_self_has_no_unverified_default(captured):
    assert infer_self_ids(captured["account"], []) == set()
    rows = [row for row in captured["messages"] if row["raw"].get("sender_id") == 2
            and not _sender(row["raw"].get("content", ""))[0]]
    for row in rows:
        payload = normalize(captured["account"], row["session_id"], row["raw"])
        assert not payload["is_self"] and not payload["metadata"]["identity_confident"]


def test_stale_index_cannot_override_actual_group_sender(captured):
    rows = [row for row in captured["messages"] if
            (row["raw"].get("sender_username") == captured["account"] or row["raw"].get("sender_id") == 2)
            and _sender(row["raw"].get("content", ""))[0] not in {"", captured["account"]}]
    assert rows
    for row in rows:
        payload = normalize(captured["account"], row["session_id"], row["raw"])
        assert not payload["is_self"]
        assert payload["metadata"]["identity_conflict"]
        assert payload["metadata"]["identity_confident"]
        if row["raw"].get("sender_id") == 2:
            assert 2 not in infer_self_ids(captured["account"], [row["raw"]])


def test_account_scopes_and_stable_source_identity(captured):
    row = captured["messages"][0]
    another_real_username = next(r["session_id"] for r in captured["messages"] if r["session_id"] != captured["account"])
    first = normalize(captured["account"], row["session_id"], row["raw"])
    second = normalize(another_real_username, row["session_id"], row["raw"])
    assert first["source_id"] != second["source_id"]
    assert first["session_id"] != second["session_id"]
    assert first["source_id"] == normalize(captured["account"], row["session_id"], row["raw"])["source_id"]


def test_catalog_is_opt_in_and_preserves_selection(worker):
    assert not any(row["monitored"] for row in worker.db.rows("SELECT * FROM sessions"))
    row = worker.db.one("SELECT * FROM sessions LIMIT 1")
    worker.db.execute("UPDATE sessions SET monitored=1 WHERE id=?", (row["id"],))
    worker._sync_catalog()
    assert worker.db.one("SELECT monitored FROM sessions WHERE id=?", (row["id"],))["monitored"] == 1


def test_real_history_replay_is_idempotent(worker):
    row = worker.db.one("SELECT * FROM sessions LIMIT 1")
    first = worker._history(row["id"], 100)
    count = worker.db.one("SELECT COUNT(*) AS n FROM messages")["n"]
    second = worker._history(row["id"], 100)
    assert count > 0 and first["imported"] == count
    assert second["duplicates"] == count
    assert worker.db.one("SELECT COUNT(*) AS n FROM messages")["n"] == count
    assert worker._watermark(row["id"]) == first["checkpoint"]


def test_verified_identity_evidence_survives_restart(worker, captured):
    authoritative = next(row for row in captured["messages"] if
                         not row["session_id"].endswith("@chatroom") and
                         _sender(row["raw"].get("content", ""))[0] == captured["account"])
    numeric_id = authoritative["raw"]["sender_id"]
    item = worker.db.one("SELECT * FROM sessions WHERE username=?", (authoritative["session_id"],))
    no_origin = next(raw for raw in worker._wx.groups[item["username"]] if
                     raw.get("sender_id") == numeric_id and not _sender(raw.get("content", ""))[0])
    worker._store(item, [authoritative["raw"]], 0)
    restarted = WechatWorker(worker.db, {}, worker.root)
    restarted._wx = worker._wx
    restarted._state["account"] = captured["account"]
    restarted._store(item, [no_origin], worker._watermark(item["id"]))
    message = worker.db.one("SELECT * FROM messages WHERE source_id=?",
                            (normalize(captured["account"], item["username"], no_origin)["source_id"],))
    metadata = json.loads(message["metadata_json"])
    assert message["is_self"] and metadata["identity_confident"]
    assert metadata["identity_source"] == "verified_sender_id"


def test_public_operations_use_one_source_thread(tmp_path, captured, monkeypatch):
    import niannian.wechat as integration
    db = Database(tmp_path / "queue.sqlite3")
    db.init()
    thread_ids = []

    class ObservedSource(CapturedSource):
        def get_self_info(self):
            thread_ids.append(threading.get_ident())
            return {"username": captured["account"]}

        def get_sessions(self, limit=1000):
            thread_ids.append(threading.get_ident())
            return super().get_sessions(limit)

        def get_messages(self, username, limit=20, offset=0):
            thread_ids.append(threading.get_ident())
            return super().get_messages(username, limit, offset)

    monkeypatch.setattr(integration, "open_wechat", lambda *args: ObservedSource(captured))
    monkeypatch.setattr(integration, "MediaCache", lambda *args: None)
    reader = WechatWorker(db, {"wechat_poll_seconds": 1}, tmp_path)
    try:
        reader.start()
        catalog = reader.catalog()
        assert len(catalog) > 0
        result = reader.import_history(catalog[0]["id"], 10)
        assert result["read"] >= 0
        assert len(set(thread_ids)) == 1 and threading.get_ident() not in thread_ids
    finally:
        assert reader.stop()["state"] == "stopped"


def test_checkpoint_never_commits_when_message_transaction_fails(worker):
    row = worker.db.one("SELECT * FROM sessions LIMIT 1")
    worker.db.execute("CREATE TRIGGER reject_message BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        worker._history(row["id"], 100)
    assert worker._watermark(row["id"]) is None
    assert worker.db.one("SELECT COUNT(*) AS n FROM messages")["n"] == 0
    worker.db.execute("DROP TRIGGER reject_message")
    assert worker._history(row["id"], 100)["imported"] > 0


def test_paused_session_continues_ingestion_and_restart_catches_gap(worker):
    row = max(worker.db.rows("SELECT * FROM sessions"), key=lambda r: len(worker._wx.groups[r["username"]]))
    all_rows = worker._wx.groups[row["username"]]
    ascending = sorted(all_rows, key=lambda m: m["sort_seq"])
    assert len(ascending) > 5
    worker._store(row, ascending[:5], 0)
    assert worker.db.one("SELECT monitored FROM sessions WHERE id=?", (row["id"],))["monitored"] == 0
    restarted = WechatWorker(worker.db, {}, worker.root)
    restarted._wx = worker._wx
    restarted._state["account"] = worker._state["account"]
    restarted._sync_selected()
    assert worker.db.one("SELECT COUNT(*) AS n FROM messages WHERE session_id=?", (row["id"],))["n"] == len(all_rows)
    assert restarted._watermark(row["id"]) == max(m["sort_seq"] for m in all_rows)


def test_real_media_xml_has_readable_summary(captured, tmp_path):
    parsed = [parse_attachment(_sender(row["raw"].get("content", ""))[1]) for row in captured["messages"]]
    attachments = [item for item in parsed if item]
    assert attachments
    assert any(item["kind"] == "image" for item in attachments)
    for item in attachments:
        assert summarize(item).startswith("[")
        assert "aeskey" not in item and "cdnurl" not in item
    assert safe_media_path(tmp_path, "../config.json") is None
    assert safe_media_path(tmp_path, "account/../config.json") is None


def test_actual_attachment_labels_cannot_invent_requests(captured):
    attachments = [normalize(captured["account"], row["session_id"], row["raw"])
                   for row in captured["messages"]]
    kinds = set()
    checked = 0
    for payload in attachments:
        description = payload["metadata"].get("attachment", {})
        if description.get("kind") not in {"miniapp", "link", "file"}:
            continue
        title = description.get("name", "")
        if TITLE_REQUEST.search(title):
            assert not attachment_only(payload["content"])
            continue
        assert attachment_only(payload["content"])
        # Replay repetition of the captured asset's own title; no new chat prose.
        if title:
            assert attachment_only(payload["content"] + "\n" + title)
        kinds.add(description["kind"])
        checked += 1
    assert checked and kinds == {"miniapp", "link", "file"}


def test_actual_action_caption_remains_actionable(captured):
    payloads = [normalize(captured["account"], row["session_id"], row["raw"])
                for row in captured["messages"]]
    caption = next(payload["content"] for payload in payloads if
                   not payload["metadata"].get("attachment") and TITLE_REQUEST.search(payload["content"]))
    attachment = next(payload["content"] for payload in payloads if
                      payload["metadata"].get("attachment", {}).get("kind") == "miniapp")
    assert not attachment_only(attachment + "\n" + caption)


def test_lock_covers_complete_project_cache(tmp_path):
    path = tmp_path / "cache" / "reader.lock"
    lock = acquire_instance_lock(path)
    assert lock is not None
    try:
        assert acquire_instance_lock(path) is None
    finally:
        lock.close()
    second = acquire_instance_lock(path)
    assert second is not None
    second.close()


class DelayedMedia:
    """真实回放里不产文件，指定轮次起才返回档名，模拟微信比采集更晚落盘。"""

    def __init__(self, filename):
        self.filename = filename
        self.attached = False

    def enrich(self, username, payload):
        if payload["metadata"].get("attachment", {}).get("kind") != "image":
            return
        if self.attached:
            payload["metadata"]["local_file"] = self.filename
            payload["metadata"]["media_tier"] = "thumb"


def test_later_media_extraction_backfills_existing_message(worker):
    row = next(item for item in worker.db.rows("SELECT * FROM sessions")
               if any(m["type"] == "图片" for m in worker._wx.groups[item["username"]]))
    messages = [m for m in worker._wx.groups[row["username"]] if m["type"] == "图片"]
    assert messages
    filename = "0123456789abcdef/media_backfill_thumb.jpg"
    worker.db.execute("DELETE FROM ingestion_keys")
    worker.db.execute("DELETE FROM messages")
    worker.db.execute("DELETE FROM wechat_checkpoints")
    media = DelayedMedia(filename)
    worker._media = media
    watermark = max(int(m["sort_seq"]) for m in messages)

    # 第一轮：微信还没写出预览档，采集只能先入库不带文件的消息。
    worker._store(row, messages, 0)
    first = worker.db.rows("SELECT id,metadata_json,processed FROM messages WHERE session_id=? ORDER BY id",
                           (row["id"],))
    assert first and not any("local_file" in r["metadata_json"] for r in first)

    # 第二轮：文件落盘，重复扫描必须把档位补挂到已存在的消息行上。
    media.attached = True
    worker._store(row, messages, watermark)
    second = worker.db.rows("SELECT id,metadata_json,processed FROM messages WHERE session_id=? ORDER BY id",
                            (row["id"],))
    assert [r["id"] for r in second] == [r["id"] for r in first]
    assert [r["processed"] for r in second] == [r["processed"] for r in first]
    attached = [r for r in second if '"local_file"' in r["metadata_json"]]
    assert len(attached) == len(messages)
    assert all(filename in r["metadata_json"] for r in attached)

    # 第三轮：已挂档的行不再被覆盖，也不重复产出消息或动水位。
    media.filename = "override.jpg"
    worker._store(row, messages, watermark)
    final = worker.db.rows("SELECT metadata_json FROM messages WHERE session_id=?", (row["id"],))
    assert len(final) == len(second)
    assert not any("override.jpg" in r["metadata_json"] for r in final)
    assert worker._watermark(row["id"]) == watermark
