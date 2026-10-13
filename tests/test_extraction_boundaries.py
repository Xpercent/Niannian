"""真实本机消息只读取样，在临时库验证指令与事务边界；不调用云端。"""

import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from niannian.engine import Engine
from niannian.instructions import Instruction
from niannian.llm import LLMExtractor
from niannian.storage import Database, utc_now


@pytest.fixture
def real_messages():
    path = Path(__file__).resolve().parents[1] / "data" / "workspace.db"
    if not path.exists():
        pytest.skip("缺少真实消息数据库；不构造聊天替代")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        session = conn.execute(
            "SELECT session_id FROM messages WHERE is_self=0 AND length(content) BETWEEN 1 AND 2600 "
            "GROUP BY session_id HAVING COUNT(DISTINCT created_at)>=3 ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()
        if not session:
            pytest.skip("缺少同会话真实消息样本")
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id=? AND is_self=0 "
            "AND length(content) BETWEEN 1 AND 2600 ORDER BY created_at,id", (session[0],)
        ).fetchall()
    selected, times = [], set()
    for row in rows:
        if row["created_at"] not in times:
            selected.append({**dict(row), "metadata": json.loads(row["metadata_json"])})
            times.add(row["created_at"])
        if len(selected) == 3:
            return selected
    pytest.skip("缺少独立时间的真实消息样本")


@pytest.fixture
def workspace(tmp_path, real_messages):
    db = Database(tmp_path / "replay.db")
    db.init()
    first = real_messages[0]
    db.sync_catalog(first["account"], [{"id": first["session_id"], "name": first["session_id"]}])
    db.execute("UPDATE sessions SET monitored=1")
    for row in real_messages:
        db.ingest(row)
    engine = Engine(db, {"ai_enabled": True, "merge_window_seconds": 0,
                         "batch_char_budget": 10000, "settle_window_seconds": 0})
    return db, engine, first["session_id"]


class Model:
    enabled = True
    last_error = None

    def __init__(self, answer):
        self.answer = answer
        self.stats = {"requests": 0, "failures": 0, "seconds": 0}
        self.deferred = []

    def extract_batch(self, *args):
        self.stats["requests"] += 1
        return self.answer()

    def record_final_decisions(self, *args):
        pass

    def record_deferred(self, message, note=None):
        self.deferred.append((message["message_ids"], note))


def run(engine, model):
    with patch("niannian.engine.LLMExtractor", return_value=model), \
            patch.object(engine, "_gate", return_value=None), \
            patch("niannian.engine.local_rules.decisions", side_effect=AssertionError("不能本地兜底")):
        return engine.process(force=True)


def test_segment_conflict_rolls_back_create_and_leaves_sources_pending(workspace):
    db, engine, sid = workspace
    now = utc_now()
    task = db.execute("INSERT INTO tasks(session_id,title,description,created_at,updated_at) VALUES(?,?,?,?,?)",
                      (sid, "独立领域对象A", "领域记录", now, now))

    def answer():
        db.update_task(task, {"priority": "low"}, 1)
        return {0: [Instruction(action="CREATE", title="核对领域对象B"),
                    Instruction(action="UPDATE", related_task_id=task, priority="high")],
                1: [Instruction(action="RESOLVE", related_task_ref="0:0", resolution="cancelled")],
                2: [Instruction(action="IGNORE")]}

    model = Model(answer)
    result = run(engine, model)
    assert (result["created"], result["updated"], result["processed"], result["skipped"]) == (0, 0, 1, 2)
    assert db.one("SELECT COUNT(*) AS n FROM tasks")["n"] == 1
    assert db.one("SELECT priority,version FROM tasks WHERE id=?", (task,)) == {"priority": "low", "version": 2}
    assert db.one("SELECT COUNT(*) AS n FROM task_events WHERE source='llm'")["n"] == 0
    assert [row["processed"] for row in db.rows("SELECT processed FROM messages ORDER BY created_at,id")] == [0, 0, 1]
    assert len(model.deferred) == 2


def test_committed_batch_create_can_be_updated_and_cancelled(workspace):
    db, engine, _ = workspace
    model = Model(lambda: {
        0: [Instruction(action="CREATE", title="核对领域对象B")],
        1: [Instruction(action="UPDATE", related_task_ref="0:0", priority="high")],
        2: [Instruction(action="RESOLVE", related_task_ref="0:0", resolution="cancelled")],
    })
    result = run(engine, model)
    assert (result["created"], result["updated"], result["resolved"], result["processed"]) == (1, 1, 1, 3)
    task = db.one("SELECT status,priority,version,source_message_ids FROM tasks")
    assert (task["status"], task["priority"], task["version"]) == ("cancelled", "high", 3)
    assert len(json.loads(task["source_message_ids"])) == 3
    assert [row["action"] for row in db.rows("SELECT action FROM task_events ORDER BY id")] == ["CREATE", "UPDATE", "RESOLVE"]
    assert not model.deferred


def test_invalid_later_command_rolls_back_prior_update_and_ignore(workspace):
    db, engine, sid = workspace
    now = utc_now()
    task = db.execute("INSERT INTO tasks(session_id,title,description,created_at,updated_at) VALUES(?,?,?,?,?)",
                      (sid, "独立领域对象A", "领域记录", now, now))
    model = Model(lambda: {0: [Instruction(action="IGNORE"),
                              Instruction(action="UPDATE", related_task_id=task, priority="high"),
                              Instruction(action="UPDATE", related_task_id=99999, priority="low")]})
    result = run(engine, model)
    assert (result["updated"], result["ignored"], result["processed"]) == (0, 0, 0)
    assert db.one("SELECT priority,version FROM tasks") == {"priority": "medium", "version": 1}
    assert db.one("SELECT COUNT(*) AS n FROM ignored_messages")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM task_events")["n"] == 0


def test_scheduler_candidates_respect_account_monitoring_and_processed(workspace):
    db, engine, _ = workspace
    assert engine.has_work()
    db.execute("UPDATE sessions SET monitored=0")
    assert not engine.has_work()
    db.execute("UPDATE sessions SET monitored=1,account='other-account'")
    assert not engine.has_work()
    db.execute("UPDATE sessions SET account=(SELECT account FROM messages LIMIT 1)")
    db.execute("UPDATE messages SET processed=1")
    assert not engine.has_work()


def response(payload):
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(payload)}}]}).encode())


def test_batch_isolates_duplicate_invalid_missing_and_extra_answers(real_messages):
    class Trace:
        def record(self, call):
            return 1

        def outcome(self, call_id, outcome, counters):
            self.counters = counters

    trace = Trace()
    model = LLMExtractor("https://example.invalid/v1", "unused", "model", trace=trace)
    payload = {"items": [{"index": 0, "instructions": [{"action": "IGNORE"}]},
                         {"index": 1, "instructions": [{"action": "CREATE", "title": "领域对象"}]},
                         {"index": 1, "instructions": [{"action": "IGNORE"}]},
                         {"index": 8, "instructions": [{"action": "IGNORE"}]},
                         {"index": "2", "instructions": []}, None]}
    with patch("niannian.llm.urlopen", return_value=response(payload)):
        assert list(model.extract_batch(real_messages, [], [])) == [0]
    assert model.last_diagnostics["duplicate_indexes"] == [1]
    assert model.last_diagnostics["extra_indexes"] == [8]
    assert model.last_diagnostics["missing_indexes"] == [2]
    assert model.last_diagnostics["unindexed_entries"] == 2
    assert trace.counters == {"answered": 1, "guard": 0, "rejected": 0, "missing": 1}


def test_model_cannot_target_task_omitted_by_input_budget(real_messages):
    model = LLMExtractor("https://example.invalid/v1", "unused", "model", task_chars=0)
    tasks = [{"id": 5, "title": "独立领域对象A", "priority": "medium", "due_at": None}]
    payload = {"instructions": [{"action": "UPDATE", "related_task_id": 5, "priority": "high"}]}
    with patch("niannian.llm.urlopen", return_value=response(payload)):
        assert model.extract(real_messages[0], [], tasks) is None
    assert model.last_payload_stats["tasks_sent"] == 0
