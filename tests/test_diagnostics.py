"""提炼诊断：每批请求发出去的正文与模型原样回复逐字留档，接口只读不写。"""

import json
import threading
import time

import pytest
from fastapi import HTTPException

from niannian import extractor
from niannian.api import create_app
from niannian.extractor import Extractor
from niannian.ids import session_key
from niannian.storage import EXTRACT_RUNS_KEPT, Database

ACCOUNT = "wxid_me"
CFG = {"llm_base_url": "https://api.siliconflow.cn/v1", "llm_model": "Qwen/Qwen3-8B",
       "llm_api_key": "sk-test-value", "llm_thinking": False}
ONE_TASK = '{"tasks":[{"title":"完成问卷","description":"全班填写","' \
           'deadline":"2026-10-13T0:00","priority":"high","id":[0]}]}'


def message(sid, seq):
    return {"session_id": sid, "source_id": f"wechat:{seq}", "sender_key": "wxid_other",
            "sender_name": "老王", "is_self": False, "content": f"消息{seq}",
            "message_type": "文本", "created_at": "2026-10-11T12:00:00+00:00",
            "sort_seq": seq, "metadata": {}}


def seed(db, sid, name="三班", count=3):
    db.sync_catalog(ACCOUNT, [{"id": sid, "username": sid, "name": name,
                               "unread": 0, "last_message_at": None}])
    db.set_monitored(sid, True)
    db.ingest([message(sid, 100 + n) for n in range(count)])


class FakeChat:
    """替身服务商：记下发出去的那两条 message，连思考内容一起回。"""

    def __init__(self, reply=ONE_TASK, error=None):
        self.sent = []
        self.reply = reply
        self.error = error
        self.hold = None

    def __call__(self, settings, messages, **kwargs):
        self.sent.append([dict(item) for item in messages])
        if self.hold is not None:
            self.hold.wait(10)
        if self.error is not None:
            raise self.error
        return {"text": self.reply, "reasoning": "先想了一遍全班", "model": "fake-model"}


@pytest.fixture
def fake(monkeypatch):
    stub = FakeChat()
    monkeypatch.setattr(extractor, "chat", stub)
    return stub


@pytest.fixture
def db(tmp_path):
    store = Database(tmp_path / "niannian.db")
    store.init()
    return store


def run(db, session_id="", settings=CFG):
    ex = Extractor(db, settings)
    ex.start(session_id)
    if ex._worker:                                   # noqa: 只是等后台那一轮结束
        assert ex._worker.join(timeout=20) is None
    return ex.status()


# ------------------------------------------------------------------ 留档的形状


def test_a_round_stores_the_request_and_the_reply_verbatim(db, fake):
    seed(db, session_key(ACCOUNT, "room_a"))

    run(db)

    runs = db.extract_runs()
    assert len(runs) == 1
    row = runs[0]
    assert (row["total"], row["done"], row["created"], row["failed_batches"],
            row["calls"], row["failures"], row["error"]) == (3, 3, 1, 0, 1, 0, None)
    assert row["started_at"] and row["finished_at"]

    detail = db.extract_run(row["id"])
    assert detail["run"]["id"] == row["id"]
    call = detail["calls"][0]
    assert (call["session_name"], call["messages"], call["model"],
            call["status"]) == ("三班", 3, "fake-model", "ok")
    assert call["duration_ms"] >= 0


def test_the_run_list_carries_char_counts_not_the_bodies(db, fake):
    """一轮十几批、每批几 KB：列表只给概要，正文按 call id 单条取。"""
    seed(db, session_key(ACCOUNT, "room_a"))
    run(db)

    call = db.extract_run(db.extract_runs()[0]["id"])["calls"][0]
    assert "request" not in call and "response" not in call
    assert call["request_chars"] == len(json.dumps(fake.sent[0], ensure_ascii=False))
    assert call["response_chars"] == len(ONE_TASK)

    full = db.model_call(call["id"])
    assert [item["role"] for item in full["request"]] == ["system", "user"]
    assert "只挑出**我本人**需要做的一件事" in full["request"][0]["content"]
    assert '<msg id="0" sender="老王"' in full["request"][1]["content"]
    assert full["response"] == ONE_TASK
    assert full["reasoning"] == "先想了一遍全班"
    assert full["model"] == "fake-model"
    assert full["status"] == "ok" and full["error"] == ""


def test_a_failed_request_is_recorded_with_the_translated_error(db, fake):
    """失败了也要有这一行：诊断页要看的就是"那次到底发了什么、服务商说了什么"。"""
    fake.error = RuntimeError("provider exploded")
    seed(db, session_key(ACCOUNT, "room_a"))

    run(db)

    row = db.extract_runs()[0]
    assert (row["done"], row["created"], row["failed_batches"], row["calls"],
            row["failures"]) == (0, 0, 1, 1, 1)
    full = db.model_call(db.extract_run(row["id"])["calls"][0]["id"])
    assert full["status"] == "error" and full["response"] == ""
    assert full["error"] == "模型请求失败：RuntimeError"
    assert "sk-test-value" not in str(full)


def test_progress_is_written_before_the_round_finishes(db, fake):
    """跑一半就能看：进度在一批一批之间就写回轮次那一行，不等整轮结束。"""
    hold = threading.Event()
    fake.hold = hold
    seed(db, session_key(ACCOUNT, "room_a"))

    ex = Extractor(db, CFG)
    ex.start("")
    for _ in range(2000):                            # 等第一批真的进到请求里（最多 10 秒）
        if fake.sent:
            break
        time.sleep(0.005)

    row = db.extract_runs()[0]
    assert row["total"] == 3 and row["finished_at"] is None
    assert db.extract_run(row["id"])["calls"] == []   # 还没回来的那批不该有记录

    hold.set()
    assert ex._worker.join(timeout=20) is None         # noqa
    assert db.extract_runs()[0]["finished_at"]


def test_only_the_newest_rounds_are_kept(db, fake):
    """留档会长：只留最近 ``EXTRACT_RUNS_KEPT`` 轮，旧轮的正文跟着轮次一起删。"""
    seed(db, session_key(ACCOUNT, "room_a"))
    for _ in range(EXTRACT_RUNS_KEPT + 5):
        run(db)

    runs = db.extract_runs(limit=100)
    assert len(runs) == EXTRACT_RUNS_KEPT
    assert runs == sorted(runs, key=lambda row: -row["id"])
    with db.connect() as conn:
        # 级联删干净：不该留下没有轮次的孤儿正文
        assert conn.execute("SELECT COUNT(*) FROM model_calls c "
                            "LEFT JOIN extract_runs r ON r.id=c.run_id "
                            "WHERE r.id IS NULL").fetchone()[0] == 0


# ------------------------------------------------------------------ 接口


class QuietCollector:
    """假采集器：不起线程、不碰微信，接口用例只关心诊断那几条路由。"""

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


@pytest.fixture
def app(tmp_path, monkeypatch, fake):
    (tmp_path / "config.json").write_text('{"llm_api_key": "sk-test-value"}',
                                          encoding="utf-8")
    monkeypatch.setattr("niannian.api.Collector", QuietCollector)
    built = create_app(tmp_path)
    seed(built.state.store, session_key(ACCOUNT, "room_a"))
    endpoint(built, "/api/extract")(payload={"session_id": ""})
    built.state.extractor._worker.join(timeout=10)    # noqa: 等那一轮跑完
    return built


def test_diagnostics_endpoints_read_a_round_back(app):
    runs = endpoint(app, "/api/diagnostics/runs")(limit=30)
    assert [row["calls"] for row in runs["items"]] == [1]

    run_id = runs["items"][0]["id"]
    detail = endpoint(app, "/api/diagnostics/runs/{run_id}")(run_id=run_id)
    call_id = detail["calls"][0]["id"]
    call = endpoint(app, "/api/diagnostics/calls/{call_id}")(call_id=call_id)

    assert call["response"] == ONE_TASK
    assert '<msg id="0" sender="老王"' in call["request"][1]["content"]
    # 密钥与服务商地址不入库也不下发
    assert "sk-test-value" not in str([runs, detail, call])
    assert "siliconflow" not in str([runs, detail, call])


def test_diagnostics_endpoints_say_when_there_is_nothing(app):
    with pytest.raises(HTTPException) as got:
        endpoint(app, "/api/diagnostics/runs/{run_id}")(run_id=9999)
    assert got.value.status_code == 404
    assert "轮次" in got.value.detail

    with pytest.raises(HTTPException) as got:
        endpoint(app, "/api/diagnostics/calls/{call_id}")(call_id=9999)
    assert got.value.status_code == 404
    assert "调用" in got.value.detail
