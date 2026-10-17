"""提炼轮次：批次怎么切、失败怎么办、任务与标记怎么同事务落库。模型一律用替身。"""

import sqlite3
import threading

import pytest
from fastapi import HTTPException

from niannian import extract, extractor
from niannian.api import create_app
from niannian.extractor import Extractor
from niannian.ids import session_key
from niannian.storage import Database

CFG = {"llm_base_url": "https://api.siliconflow.cn/v1", "llm_model": "Qwen/Qwen3-8B",
       "llm_api_key": "sk-test-value", "llm_thinking": False}

ACCOUNT = "wxid_me"
ONE_TASK = ('{"tasks":[{"title":"完成问卷","description":"全班填写，明天截止",'
            '"deadline":"2026-10-13T0:00","priority":"high","id":[0,2]}]}')


def message(sid, seq, text=None):
    return {"session_id": sid, "source_id": f"wechat:{seq}", "sender_key": "wxid_other",
            "sender_name": "老王", "is_self": False,
            "content": text if text is not None else f"消息{seq}",
            "message_type": "文本", "created_at": "2026-10-11T12:00:00+00:00",
            "sort_seq": seq, "metadata": {}}


def seed(db, sid, name="三班", monitored=True, start_seq=100, count=3):
    db.sync_catalog(ACCOUNT, [{"id": sid, "username": sid, "name": name,
                               "unread": 0, "last_message_at": None}])
    db.set_monitored(sid, monitored)
    db.ingest([message(sid, start_seq + n) for n in range(count)])


def ids_of(db):
    """本轮待发的那些行的自增 id（commit 的标记入参用它）。"""
    return [row["id"] for row in db.pending_extraction()]


class FakeChat:
    """替身服务商：记下每次收到的 user_content，按剧本回话或抛错。"""

    def __init__(self, reply=ONE_TASK, error=None):
        self.calls = []
        self.reply = reply
        self.error = error
        self.after = None
        self.hold = None

    def __call__(self, settings, messages, *, max_tokens=None, timeout=None):
        self.calls.append({"system": messages[0]["content"], "user": messages[1]["content"],
                           "max_tokens": max_tokens, "timeout": timeout})
        if self.hold is not None:
            self.hold.wait(10)            # 让用例能把线程按在这一批上
        if self.after is not None:
            self.after()
        if self.error is not None:
            raise self.error
        return {"text": self.reply, "reasoning": "", "model": "fake"}


@pytest.fixture
def fake(monkeypatch):
    """把替身装进 ``extractor.chat``，并把回复与错误做成可改的剧本。"""
    stub = FakeChat()
    monkeypatch.setattr(extractor, "chat", stub)
    return stub


@pytest.fixture
def db(tmp_path):
    store = Database(tmp_path / "niannian.db")
    store.init()
    return store


def start_round(store, session_id="", settings=CFG):
    """起一轮并等它跑完（走的真是线程，接口用例之外也能直接断言状态）。"""
    ex = Extractor(store, settings)
    ex.start(session_id)
    if ex._worker:                                  # noqa: 只是等后台那一轮结束
        assert ex._worker.join(timeout=20) is None
    return ex


def run(db, session_id="", settings=CFG):
    return start_round(db, session_id, settings).status()


# ------------------------------------------------------------------ 一轮的形状


def test_round_creates_tasks_and_marks_every_message_extracted(db, fake):
    sid = session_key(ACCOUNT, "room_a")
    seed(db, sid, count=3)

    state = run(db)

    assert (state["running"], state["total"], state["done"], state["batches"],
            state["created"], state["failed_batches"], state["error"],
            state["session"]) == (False, 3, 3, 1, 1, 0, None, "三班")
    assert state["started_at"] and state["finished_at"]
    tasks = db.list_tasks(status="all")
    assert [t["title"] for t in tasks] == ["完成问卷"]
    assert tasks[0]["priority"] == "high"
    assert tasks[0]["deadline"] == "2026-10-13T00:00+08:00"
    assert tasks[0]["description"] == "全班填写，明天截止"
    assert tasks[0]["session_id"] == sid and tasks[0]["status"] == "pending"
    # 依据落库的是**消息的 sort_seq**：批内编号只在那一次请求里有意义
    assert tasks[0]["sources"] == [100, 102]
    assert db.pending_extraction() == []


def test_start_answers_with_the_backlog_before_the_first_reply(db, fake):
    """接口立刻回的那份状态就得带上积压条数：total 是 0 看着像"这轮什么都没找到"。"""
    hold = threading.Event()
    fake.hold = hold
    seed(db, session_key(ACCOUNT, "room_a"))

    ex = Extractor(db, CFG)
    snapshot = ex.start("")

    assert (snapshot["accepted"], snapshot["running"], snapshot["total"]) == (True, True, 3)
    hold.set()
    ex._worker.join(timeout=20)                       # noqa


def test_request_carries_the_policy_and_a_bounded_budget(db, fake):
    seed(db, session_key(ACCOUNT, "room_a"))

    run(db)

    call = fake.calls[0]
    assert "只挑出**我本人**需要做的一件事" in call["system"]
    assert call["max_tokens"] == extractor.MAX_TOKENS
    assert call["timeout"] == extractor.TIMEOUT_SECONDS


def test_thinking_on_gets_more_room_and_a_longer_wait(db, fake):
    """两档分开：思考内容能吃光小额度、单次能跑到 30 秒以上（真机踩过）。"""
    first = session_key(ACCOUNT, "room_a")
    second = session_key(ACCOUNT, "room_b")
    seed(db, first)
    seed(db, second, name="另一个群", start_seq=900)

    run(db, session_id=first)
    run(db, session_id=second, settings={**CFG, "llm_thinking": True})

    plain, thinking = fake.calls
    assert (plain["max_tokens"], plain["timeout"]) == (extractor.MAX_TOKENS,
                                                       extractor.TIMEOUT_SECONDS)
    assert (thinking["max_tokens"], thinking["timeout"]) == (
        extractor.MAX_TOKENS_THINKING, extractor.TIMEOUT_THINKING_SECONDS)


def test_second_round_has_nothing_to_ask(db, fake):
    seed(db, session_key(ACCOUNT, "room_a"))

    run(db)
    state = run(db)

    assert state["total"] == 0 and state["batches"] == 0
    assert len(fake.calls) == 1


def test_no_task_is_still_processed(db, fake):
    """判出"这批没有我要做的事"是正常结果：照样标记，不然每轮重问同一批。"""
    fake.reply = '{"tasks":[]}'
    sid = session_key(ACCOUNT, "room_a")
    seed(db, sid)

    state = run(db)

    assert state["created"] == 0 and state["done"] == 3
    assert db.pending_extraction() == []


def test_batch_size_caps_one_request_and_numbering_restarts(db, fake):
    sid = session_key(ACCOUNT, "room_a")
    seed(db, sid, count=extract.BATCH_LIMIT + 5)

    state = run(db)

    assert len(fake.calls) == 2 and state["batches"] == 2
    first, second = fake.calls
    assert first["user"].count('<msg id=') == extract.BATCH_LIMIT
    assert '<msg id="0"' in second["user"]
    assert second["user"].count('<msg id=') == 5


def test_only_monitored_sessions_are_read(db, fake):
    followed = session_key(ACCOUNT, "room_a")
    seed(db, followed)
    seed(db, session_key(ACCOUNT, "room_b"), name="不打扰的群",
         monitored=False, start_seq=900)

    state = run(db)

    assert state["total"] == 3
    assert all("消息900" not in call["user"] for call in fake.calls)


def test_session_filter_keeps_one_session(db, fake):
    first = session_key(ACCOUNT, "room_a")
    second = session_key(ACCOUNT, "room_b")
    seed(db, first)
    seed(db, second, name="另一个群", start_seq=900)

    state = run(db, session_id=second)

    assert state["total"] == 3
    assert "消息900" in fake.calls[0]["user"] and "消息100" not in fake.calls[0]["user"]
    assert len(db.pending_extraction(session_id=first)) == 3


def test_extracted_history_is_context_without_index(db, fake):
    """已提炼过的旧消息进 <history> 当参照：不带编号，因此也当不成任务依据。"""
    sid = session_key(ACCOUNT, "room_a")
    seed(db, sid, count=1, start_seq=50)
    db.commit_extraction([], ids_of(db))
    db.ingest([message(sid, 60)])
    fake.reply = ONE_TASK.replace("[0,2]", "[0]")

    state = run(db)

    user = fake.calls[0]["user"]
    history, messages = user.split("<messages>")
    assert "消息50" in history and "消息60" in messages
    assert ' id="' not in history
    assert state["done"] == 1
    assert db.list_tasks(status="all")[0]["sources"] == [60]


def test_reimported_older_messages_still_get_extracted(db, fake):
    """这是选 extracted_at 而不是会话级游标的原因：回填的更旧消息排在游标下面也要能追上。"""
    sid = session_key(ACCOUNT, "room_a")
    seed(db, sid, count=2, start_seq=200)
    fake.reply = '{"tasks":[]}'
    run(db)

    db.ingest([message(sid, 150, text="回填的旧消息")])
    fake.reply = ONE_TASK.replace("[0,2]", "[0]")
    state = run(db)

    assert state["total"] == 1
    assert "回填的旧消息" in fake.calls[1]["user"]
    assert db.list_tasks(status="all")[0]["sources"] == [150]


# ------------------------------------------------------------------ 失败与取消


def test_failed_batch_leaves_the_messages_for_next_round(db, fake):
    fake.error = RuntimeError("provider exploded")
    sid = session_key(ACCOUNT, "room_a")
    seed(db, sid)

    state = run(db)

    assert state["done"] == 0 and state["failed_batches"] == 1
    # 服务商的错翻成人话再存，并且不带密钥
    assert state["error"] == "模型请求失败：RuntimeError"
    assert "sk-test-value" not in state["error"]
    assert len(db.pending_extraction()) == 3
    assert db.list_tasks(status="all") == []

    fake.error = None
    again = run(db)
    assert again["created"] == 1 and again["error"] is None


def test_unparsable_reply_is_a_failure_not_an_empty_round(db, fake):
    """"读了个半截 JSON"不能当成"这批没有任务"——那会静默把消息标成已处理。"""
    fake.reply = "对不起我不会"
    seed(db, session_key(ACCOUNT, "room_a"))

    state = run(db)

    assert state["failed_batches"] == 1 and state["done"] == 0
    assert "模型没回 JSON" in state["error"]
    assert len(db.pending_extraction()) == 3


def test_cancel_between_batches(db, fake):
    """取消停在批与批之间：已回来的那批照常落库，没发出的那些不动。"""
    sid = session_key(ACCOUNT, "room_a")
    seed(db, sid, count=extract.BATCH_LIMIT + 5)
    ex = Extractor(db, CFG)
    fake.after = ex._stop.set                       # noqa: 只用来指定取消点

    ex.start("")
    ex._worker.join(timeout=20)                     # noqa

    state = ex.status()
    assert len(fake.calls) == 1
    assert state["done"] == extract.BATCH_LIMIT
    assert "已取消" in state["error"]
    assert len(db.pending_extraction()) == 5


# ------------------------------------------------------------------ 存储层


def test_commit_extraction_marks_even_with_no_tasks(db):
    sid = session_key(ACCOUNT, "room_a")
    seed(db, sid, count=1)

    created = db.commit_extraction([], ids_of(db))

    assert created == 0
    assert db.pending_extraction() == []
    assert db.list_tasks(status="all") == []


def test_list_tasks_filters_and_tolerates_a_broken_sources_cell(db):
    sid = session_key(ACCOUNT, "room_a")
    seed(db, sid, count=2)
    rows = db.pending_extraction()
    db.commit_extraction([
        {"session_id": sid, "title": "完成问卷", "description": "", "deadline": None,
         "priority": "high", "sources": [rows[0]["sort_seq"], rows[1]["sort_seq"]]},
        {"session_id": sid, "title": "交表", "description": "学号+姓名", "deadline": None,
         "priority": "medium", "sources": [rows[0]["sort_seq"]]},
    ], [row["id"] for row in rows])
    with db.connect() as conn:
        conn.execute("UPDATE tasks SET sources='[' WHERE title='交表'")

    # 新→旧：后插进来的那条排在最前
    assert [t["title"] for t in db.list_tasks()] == ["交表", "完成问卷"]
    assert [t["title"] for t in db.list_tasks(priority="high")] == ["完成问卷"]
    assert [t["title"] for t in db.list_tasks(query="学号")] == ["交表"]
    assert db.list_tasks(session_id="没这个会话") == []
    assert db.list_tasks(status="completed") == []
    # 坏 JSON 当空表：不能让一行写坏的数据把整个清单查询打死
    assert db.list_tasks()[0]["sources"] == []
    assert db.list_tasks()[1]["sources"] == [100, 101]


def test_old_database_without_the_extraction_column_says_how_to_fix_it(tmp_path):
    path = tmp_path / "niannian.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT, source_id TEXT UNIQUE, sender_key TEXT,
                        sender_name TEXT, is_self INTEGER, content TEXT,
                        message_type TEXT, created_at TEXT, sort_seq INTEGER,
                        metadata TEXT, kind TEXT, media_md5 TEXT, local_file TEXT,
                        tier TEXT)""")

    with pytest.raises(RuntimeError) as got:
        Database(path).init()

    assert "extracted_at" in str(got.value) and "重新采集" in str(got.value)


# ------------------------------------------------------------------ 接口


class QuietCollector:
    """假采集器：不起线程、不碰微信，接口用例只关心提炼那几条路由。"""

    def __init__(self, db, settings, root):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def status(self):
        return {"state": "connected", "account": ACCOUNT}

    def catalog(self):
        return []


def endpoint(app, path):
    return next(route.endpoint for route in app.routes
                if getattr(route, "path", "") == path)


def build_app(tmp_path, monkeypatch, fake, api_key="sk-test-value"):
    monkeypatch.setattr(extractor, "chat", fake)
    (tmp_path / "config.json").write_text(f'{{"llm_api_key": "{api_key}"}}', encoding="utf-8")
    monkeypatch.setattr("niannian.api.Collector", QuietCollector)
    return create_app(tmp_path)


def test_extract_endpoint_refuses_to_ask_the_provider_without_a_key(tmp_path, monkeypatch):
    fake = FakeChat()
    app = build_app(tmp_path, monkeypatch, fake, api_key="")

    with pytest.raises(HTTPException) as got:
        endpoint(app, "/api/extract")(payload={"session_id": ""})

    assert got.value.status_code == 400
    assert "还没有填 API Key" in got.value.detail
    assert fake.calls == []                       # 一次都没出网


def test_extract_endpoint_starts_a_round_and_tasks_are_readable(tmp_path, monkeypatch):
    fake = FakeChat()
    app = build_app(tmp_path, monkeypatch, fake)
    seed(app.state.store, session_key(ACCOUNT, "room_a"))

    accepted = endpoint(app, "/api/extract")(payload={"session_id": ""})
    app.state.extractor._worker.join(timeout=10)  # noqa: 等后台那一轮跑完

    assert accepted["accepted"] is True and accepted["running"] is True
    status = endpoint(app, "/api/extract/status")()
    assert status["running"] is False and status["created"] == 1
    got = endpoint(app, "/api/tasks")(session_id="", status="pending", priority="",
                                      q="", limit=100)
    assert [t["title"] for t in got["items"]] == ["完成问卷"]
    assert got["items"][0]["sources"] == [100, 102]


def test_extract_endpoint_rejects_unknown_session_and_refuses_a_second_round(
        tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch, FakeChat())

    with pytest.raises(HTTPException) as got:
        endpoint(app, "/api/extract")(payload={"session_id": "没这个会话"})
    assert got.value.status_code == 404

    app.state.extractor.state["running"] = True    # 上一轮还没结束
    assert app.state.extractor.start("")["accepted"] is False
