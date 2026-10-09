"""Domain regressions using captured WeChat content, never invented messages."""

import json
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from shiguang.engine import Engine, parse_time
from shiguang.llm import Instruction
from shiguang.storage import Database, utc_now
from shiguang.wechat import _sender, normalize

ROOT = Path(__file__).resolve().parents[1]


def captured():
    path = ROOT / "data" / "initial-wechat.json"
    if not path.exists():
        raise unittest.SkipTest("Local captured WeChat fixture unavailable; no synthetic substitute")
    return json.loads(path.read_text(encoding="utf-8"))


class FakeModel:
    enabled = True
    last_error = None

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.stats = {"requests": 0, "failures": 0, "seconds": 0.0}

    def extract(self, *args):
        self.stats["requests"] += 1
        return self.answers.get(0)

    def extract_batch(self, *args):
        self.stats["requests"] += 1
        return self.answers


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = captured()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "test.db")
        self.db.init()
        self.db.sync_catalog(self.fixture["account"], self.fixture["catalog"])
        self.db.execute("UPDATE sessions SET monitored=1")
        self.values = {"ai_enabled": True, "ai_base_url": "https://example.invalid/v1", "ai_api_key": "test",
                       "ai_model": "test", "merge_window_seconds": 0, "settle_window_seconds": 60}
        self.engine = Engine(self.db, self.values)
        largest = Counter(message["session_id"] for message in self.fixture["messages"]).most_common(1)[0][0]
        self.real = [message for message in self.fixture["messages"] if message["session_id"] == largest][:8]
        self.sid = largest

    def ingest(self, index=0, seconds_ago=600):
        original = self.real[index]
        payload = {**original, "account": self.fixture["account"],
                   "created_at": (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat(),
                   "sender_key": original["metadata"].get("sender_username") or original["sender_name"]}
        return self.db.ingest(payload)["id"]

    def task(self, title="Task A", session=None):
        now = utc_now()
        return self.db.execute("INSERT INTO tasks(session_id,title,description,created_at,updated_at) VALUES(?,?,?,?,?)",
                               (session or self.sid, title, "domain fixture", now, now))

    def run_model(self, answers, **kwargs):
        fake = FakeModel(answers)
        with patch("shiguang.engine.LLMExtractor", return_value=fake), patch.object(self.engine, "_gate", return_value=None):
            return self.engine.process(force=True, **kwargs)

    def test_multiple_tasks_from_one_real_message(self):
        message_id = self.ingest()
        result = self.run_model({0: [Instruction(action="CREATE", title="Task A"), Instruction(action="CREATE", title="Task B")]})
        self.assertEqual(result["created"], 2)
        self.assertEqual(result["processed"], 1)
        tasks = self.db.rows("SELECT * FROM tasks")
        self.assertEqual([json.loads(task["source_message_ids"]) for task in tasks], [[message_id], [message_id]])
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM task_events")["n"], 2)

    def test_in_batch_create_then_update_reference(self):
        self.ingest(0, 700)
        self.ingest(1, 500)
        result = self.run_model({0: [Instruction(action="CREATE", title="Task A")],
                                 1: [Instruction(action="UPDATE", related_task_ref="0:0", priority="high")]})
        self.assertEqual((result["created"], result["updated"]), (1, 1))
        task = self.db.one("SELECT * FROM tasks")
        self.assertEqual((task["priority"], task["version"]), ("high", 2))
        self.assertEqual(len(json.loads(task["source_message_ids"])), 2)

    def test_others_completed_cannot_resolve_but_cancel_can(self):
        task_id = self.task()
        self.ingest()
        first = self.run_model({0: [Instruction(action="RESOLVE", related_task_id=task_id)]})
        self.assertEqual(first["resolved"], 0)
        self.assertEqual(self.db.one("SELECT status FROM tasks WHERE id=?", (task_id,))["status"], "pending")
        self.ingest(1, 400)
        second = self.run_model({0: [Instruction(action="RESOLVE", related_task_id=task_id, resolution="cancelled")]})
        self.assertEqual(second["resolved"], 1)
        self.assertEqual(self.db.one("SELECT status,version FROM tasks WHERE id=?", (task_id,)), {"status": "cancelled", "version": 2})

    def test_transaction_rolls_back_all_instructions_and_processed_flag(self):
        message_id = self.ingest()
        apply = self.engine._apply
        calls = []

        def fail_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("transaction regression")
            return apply(*args, **kwargs)

        with patch.object(self.engine, "_apply", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.run_model({0: [Instruction(action="CREATE", title="Task A"), Instruction(action="CREATE", title="Task B")]})
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM tasks")["n"], 0)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM task_events")["n"], 0)
        self.assertEqual(self.db.one("SELECT processed FROM messages WHERE id=?", (message_id,))["processed"], 0)

    def test_paused_session_keeps_backlog(self):
        message_id = self.ingest()
        self.db.execute("UPDATE sessions SET monitored=0 WHERE id=?", (self.sid,))
        self.assertEqual(self.engine.process(force=True)["processed"], 0)
        self.assertEqual(self.db.one("SELECT processed FROM messages WHERE id=?", (message_id,))["processed"], 0)
        self.db.execute("UPDATE sessions SET monitored=1 WHERE id=?", (self.sid,))
        self.assertEqual(self.run_model({0: [Instruction(action="IGNORE")]})["processed"], 1)

    def test_pause_during_model_request_prevents_commit(self):
        self.ingest()
        fake = FakeModel()

        def answer(*args):
            self.db.execute("UPDATE sessions SET monitored=0 WHERE id=?", (self.sid,))
            return [Instruction(action="CREATE", title="Task A")]

        fake.extract = answer
        with patch("shiguang.engine.LLMExtractor", return_value=fake), patch.object(self.engine, "_gate", return_value=None):
            result = self.engine.process(force=True)
        self.assertEqual((result["skipped"], result["created"], result["processed"]), (1, 0, 0))

    def test_manual_change_while_model_runs_is_not_overwritten(self):
        task_id = self.task()
        self.ingest()
        fake = FakeModel()

        def answer(*args):
            self.db.update_task(task_id, {"priority": "low"}, 1)
            return [Instruction(action="UPDATE", related_task_id=task_id, priority="high")]

        fake.extract = answer
        with patch("shiguang.engine.LLMExtractor", return_value=fake), patch.object(self.engine, "_gate", return_value=None):
            result = self.engine.process(force=True)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(self.db.one("SELECT priority,version FROM tasks WHERE id=?", (task_id,)), {"priority": "low", "version": 2})

    def test_cross_session_reference_is_rejected(self):
        other = next(row["id"] for row in self.fixture["catalog"] if row["id"] != self.sid)
        task_id = self.task(session=other)
        self.ingest()
        result = self.run_model({0: [Instruction(action="RESOLVE", related_task_id=task_id, resolution="cancelled")]})
        self.assertEqual(result["resolved"], 0)
        self.assertEqual(self.db.one("SELECT status FROM tasks WHERE id=?", (task_id,))["status"], "pending")

    def test_cancel_after_one_item_leaves_the_remainder(self):
        self.ingest(0, 700)
        self.ingest(1, 500)
        finished = []
        result = self.run_model({0: [Instruction(action="CREATE", title="Task A")], 1: [Instruction(action="CREATE", title="Task B")]},
                                progress=lambda done, total, session: finished.append(done),
                                should_stop=lambda: 1 in finished)
        self.assertEqual((result["processed"], result["cancelled"], result["created"]), (1, 1, 1))

    def test_active_burst_waits_until_quiet(self):
        self.ingest(0, 80)
        self.ingest(1, 30)
        self.assertEqual(self.engine.pending_messages(), [])
        self.assertEqual(len(self.engine.pending_messages(force=True)), 2)

    def test_configuration_is_read_once_per_round(self):
        self.ingest()

        class Store:
            calls = 0

            def read(inner):
                inner.calls += 1
                return self.values.copy()

        store = Store()
        self.engine.config = store
        self.run_model({0: [Instruction(action="IGNORE")]})
        self.assertEqual(store.calls, 1)

    def test_real_text_has_no_keyword_hard_rejection(self):
        rules = self.db.rows("SELECT * FROM rules WHERE enabled=1")
        candidates = [row for row in self.fixture["messages"] if not row["content"].lstrip().startswith("[")]
        self.assertTrue(candidates)
        for row in candidates[:10]:
            gated = self.engine._gate(row, rules)
            if gated is not None:
                self.assertTrue(any(command.context_summary == "命中完整忽略规则" for command in gated))

    def test_time_and_sender_identity_are_stable(self):
        self.assertEqual(parse_time("2026-10-09T00:00:00Z"), parse_time("2026-10-09T08:00:00+08:00"))
        self.assertIsNotNone(parse_time("2026-10-09T08:00:00").tzinfo)
        first_id = self.ingest(0, 700)
        second_id = self.ingest(1, 690)
        rows = self.db.rows("SELECT * FROM messages WHERE id IN (?,?) ORDER BY id", (first_id, second_id))
        # Identity change only exercises grouping; content still comes from capture.
        rows[0]["sender_key"], rows[1]["sender_key"] = "identity-a", "identity-b"
        rows[1]["sender_name"] = rows[0]["sender_name"]
        self.assertEqual(len(self.engine.aggregate(rows, 60)), 2)

    def test_self_message_can_resolve_but_cannot_create_or_update(self):
        payload = self.captured_own_message()
        task_id = self.task()
        self.db.ingest(payload)
        result = self.run_model({0: [Instruction(action="CREATE", title="Task B"),
                                     Instruction(action="UPDATE", related_task_id=task_id, priority="high"),
                                     Instruction(action="RESOLVE", related_task_id=task_id)]})
        self.assertEqual((result["created"], result["updated"], result["resolved"]), (0, 0, 1))
        self.assertEqual(self.db.one("SELECT status,version FROM tasks WHERE id=?", (task_id,)), {"status": "completed", "version": 2})

    def captured_own_message(self):
        path = ROOT / "data" / "raw-wechat.json"
        if not path.exists():
            self.skipTest("Raw capture with authoritative embedded origin unavailable")
        capture = json.loads(path.read_text(encoding="utf-8"))
        own = next((row for row in capture["messages"] if _sender(row["raw"]["content"])[0] == capture["account"]), None)
        if own is None:
            self.skipTest("No own message with verified embedded origin")
        payload = normalize(capture["account"], own["session_id"], own["raw"], set())
        self.assertTrue(payload["is_self"])
        self.assertIs(payload["metadata"]["identity_confident"], True)
        # This archived fixture predates account-scoped local session IDs. Map
        # the normalized source to the same captured session already in this DB.
        self.sid = own["session_id"]
        payload["session_id"] = self.sid
        self.db.execute("UPDATE sessions SET monitored=1 WHERE id=?", (self.sid,))
        return payload

    def test_unverified_self_identity_cannot_complete(self):
        payload = self.captured_own_message()
        payload["metadata"].pop("identity_confident")
        task_id = self.task()
        self.db.ingest(payload)
        result = self.run_model({0: [Instruction(action="RESOLVE", related_task_id=task_id)]})
        self.assertEqual(result["resolved"], 0)
        self.assertEqual(self.db.one("SELECT status,version FROM tasks WHERE id=?", (task_id,)), {"status": "pending", "version": 1})

    def test_bounded_selection_gives_each_real_session_a_turn(self):
        for original in self.fixture["messages"]:
            self.db.ingest({**original, "account": self.fixture["account"],
                            "sender_key": original["metadata"].get("sender_username") or original["sender_name"]})
        with patch("shiguang.engine.ROUND_LIMIT", 12), patch("shiguang.engine.PER_SESSION_LIMIT", 8):
            rows = self.engine.pending_messages(force=True)
        selected = Counter(row["session_id"] for row in rows)
        actual = set(row["session_id"] for row in self.fixture["messages"])
        self.assertEqual(set(selected), actual)
        self.assertEqual(len(rows), 12)
        self.assertLessEqual(max(selected.values()) - min(selected.values()), 1)

    def test_history_excludes_late_ingested_future_messages(self):
        self.ingest(0, 30)  # Ingest the more recent captured message first.
        self.ingest(1, 600)
        row = self.db.rows("SELECT * FROM messages ORDER BY id DESC LIMIT 1")[0]
        item = self.engine.aggregate([row], 0)[0]
        self.assertEqual(self.engine._history(item), [])


if __name__ == "__main__":
    unittest.main()
