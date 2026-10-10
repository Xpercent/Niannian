"""仓储层：微信落盘晚于采集时，重复扫描按 source_id 补挂本机附件。"""

from pathlib import Path
import tempfile

from shiguang.storage import Database


def _db(tmp_path: Path | str) -> Database:
    db = Database(Path(tmp_path) / "app.db")
    db.init()
    db.sync_catalog("a", [{"id": "s1", "username": "u1", "name": "测试会话"}])
    return db


def test_attach_media_backfills_only_pending_messages():
    with tempfile.TemporaryDirectory() as temp:
        db = _db(temp)
        db.ingest({"session_id": "s1", "account": "a", "sender_key": "k",
                   "sender_name": "n", "content": "[图片]", "message_type": "图片",
                   "is_self": False, "created_at": "2026-10-09T14:31:13+00:00",
                   "source_id": "src-1", "metadata": {"attachment": {"kind": "image"}}})

        assert db.attach_media([{"source_id": "src-1", "filename": "acc/1_thumb.jpg",
                                 "tier": "thumb"}]) == 1
        # 已有档位的行不被后续扫描覆盖，即使新文件名来自更高档位。
        assert db.attach_media([{"source_id": "src-1", "filename": "acc/1_h.png",
                                 "tier": "original"}]) == 0
        assert db.attach_media([{"source_id": "src-2", "filename": "acc/2.jpg"}]) == 0
        assert db.attach_media([{"source_id": "src-1"}, {"filename": "x.jpg"}, {}]) == 0

        metadata = db.decode_message(db.one("SELECT * FROM messages"))["metadata"]
        assert metadata["local_file"] == "acc/1_thumb.jpg"
        assert metadata["media_tier"] == "thumb"


def test_attach_media_keeps_content_and_processed_untouched():
    with tempfile.TemporaryDirectory() as temp:
        db = _db(temp)
        db.ingest({"session_id": "s1", "account": "a", "sender_key": "k",
                   "sender_name": "n", "content": "[图片]", "message_type": "图片",
                   "is_self": False, "created_at": "2026-10-09T14:31:13+00:00",
                   "source_id": "src-1", "metadata": {"attachment": {"kind": "image"}}})
        db.execute("UPDATE messages SET processed=1")
        before = db.one("SELECT id,content,processed,created_at,inserted_at FROM messages")

        db.attach_media([{"source_id": "src-1", "filename": "acc/1_thumb.jpg"}])

        after = db.one("SELECT id,content,processed,created_at,inserted_at FROM messages")
        assert after == before
