"""Domain regressions using captured WeChat content, never invented messages."""

import json
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from niannian.engine import Engine, parse_time
from niannian.llm import Instruction
from niannian.storage import Database, utc_now
from niannian.wechat import _sender, normalize

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


class Recorder(FakeModel):
    """记录引擎把段落切成几次请求，用来验证批次参数。"""

    def __init__(self, shapes):
        super().__init__()
        self.shapes = shapes

    def extract(self, message, *args):
        self.stats["requests"] += 1
        self.shapes.append("single")
        return [Instruction(action="IGNORE")]

    def extract_batch(self, items, *args):
        self.stats["requests"] += 1
        self.shapes.append("batch")
        return {index: [Instruction(action="IGNORE")] for index in range(len(items))}


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

    def task(self, title="确认交付对象A", session=None):
        now = utc_now()
        return self.db.execute("INSERT INTO tasks(session_id,title,description,created_at,updated_at) VALUES(?,?,?,?,?)",
                               (session or self.sid, title, "domain fixture", now, now))

    def run_model(self, answers, **kwargs):
        fake = FakeModel(answers)
        # These domain fixtures test state transitions independently of model
        # language quality. Separate capture tests exercise the semantic guard.
        with patch("niannian.engine.LLMExtractor", return_value=fake), patch.object(self.engine, "_gate", return_value=None), \
                patch("niannian.engine.guard_decisions", side_effect=lambda message, commands, profile: commands):
            return self.engine.process(force=True, **kwargs)

    def test_round_and_session_limits_come_from_settings(self):
        for index in range(6):
            self.ingest(index, 900 - index * 30)
        capped = self.engine.pending_messages(force=True, values={"per_session_limit": 3, "round_limit": 100})
        self.assertTrue(capped)
        self.assertLessEqual(max(Counter(row["session_id"] for row in capped).values()), 3)
        tight = self.engine.pending_messages(force=True, values={"per_session_limit": 80, "round_limit": 4})
        self.assertEqual(len(tight), 4)
        default = self.engine.pending_messages(force=True, values={})
        self.assertGreater(len(default), len(capped))

    def test_model_request_parameters_come_from_settings(self):
        self.ingest(0)
        seen = {}

        def spy(*args, **kwargs):
            seen.update(kwargs)
            return FakeModel({0: [Instruction(action="IGNORE")]})

        with patch("niannian.engine.LLMExtractor", side_effect=spy), \
                patch.object(self.engine, "_gate", return_value=None), \
                patch("niannian.engine.guard_decisions", side_effect=lambda message, commands, profile: commands):
            self.engine.process(force=True)
        self.assertEqual(seen["max_tokens_single"], 700)
        self.assertEqual(seen["temperature"], 0.0)
        self.assertEqual(seen["history_rows"], 6)
        self.assertNotIn("ai_api_key", seen)

        self.values.update({"ai_temperature": 0.5, "ai_max_tokens_batch": 4000, "new_message_chars": 900,
                            "history_rows": 2, "batch_limit": 1, "batch_char_budget": 5000})
        seen.clear()
        with patch("niannian.engine.LLMExtractor", side_effect=spy), \
                patch.object(self.engine, "_gate", return_value=None), \
                patch("niannian.engine.guard_decisions", side_effect=lambda message, commands, profile: commands):
            self.engine.process(force=True)
        self.assertEqual((seen["temperature"], seen["max_tokens_batch"], seen["new_message_chars"],
                          seen["history_rows"]), (0.5, 4000, 900, 2))

    def test_batch_limit_splits_requests(self):
        self.ingest(0, 900)
        self.ingest(1, 800)
        self.ingest(2, 700)
        shapes = []
        self.values["batch_limit"] = 1
        with patch("niannian.engine.LLMExtractor", side_effect=lambda *args, **kwargs: Recorder(shapes)), \
                patch.object(self.engine, "_gate", return_value=None), \
                patch("niannian.engine.guard_decisions", side_effect=lambda message, commands, profile: commands):
            wide = self.engine.process(force=True)
        self.db.execute("UPDATE messages SET processed=0")
        self.values["batch_limit"] = 4
        with patch("niannian.engine.LLMExtractor", side_effect=lambda *args, **kwargs: Recorder(shapes)), \
                patch.object(self.engine, "_gate", return_value=None), \
                patch("niannian.engine.guard_decisions", side_effect=lambda message, commands, profile: commands):
            narrow = self.engine.process(force=True)
        # batch_limit=1 时每段单独请求；放宽后一次带走多段，请求次数随之减少。
        self.assertEqual((wide["batches"], wide["items"]), (0, 3))
        self.assertEqual(shapes.count("single"), 3)
        self.assertEqual((narrow["batches"], narrow["batched_items"]), (1, 3))
        self.assertEqual(shapes.count("batch"), 1)

    def test_multiple_tasks_from_one_real_message(self):
        message_id = self.ingest()
        result = self.run_model({0: [Instruction(action="CREATE", title="确认交付对象A"), Instruction(action="CREATE", title="提交独立对象B")]})
        self.assertEqual(result["created"], 2)
        self.assertEqual(result["processed"], 1)
        tasks = self.db.rows("SELECT * FROM tasks")
        self.assertEqual([json.loads(task["source_message_ids"]) for task in tasks], [[message_id], [message_id]])
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM task_events")["n"], 2)

    def test_in_batch_create_then_update_reference(self):
        self.ingest(0, 700)
        self.ingest(1, 500)
        result = self.run_model({0: [Instruction(action="CREATE", title="确认交付对象A")],
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
                self.run_model({0: [Instruction(action="CREATE", title="确认交付对象A"), Instruction(action="CREATE", title="提交独立对象B")]})
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
            return [Instruction(action="CREATE", title="确认交付对象A")]

        fake.extract = answer
        with patch("niannian.engine.LLMExtractor", return_value=fake), patch.object(self.engine, "_gate", return_value=None):
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
        with patch("niannian.engine.LLMExtractor", return_value=fake), patch.object(self.engine, "_gate", return_value=None):
            result = self.engine.process(force=True)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(self.db.one("SELECT priority,version FROM tasks WHERE id=?", (task_id,)), {"priority": "low", "version": 2})

    def test_partial_batch_fallback_does_not_refresh_other_model_versions(self):
        task_id = self.task()
        self.ingest(0, 700)
        self.ingest(1, 500)
        fake = FakeModel()

        def answer(*args):
            self.db.update_task(task_id, {"priority": "low"}, 1)
            return {1: [Instruction(action="UPDATE", related_task_id=task_id, priority="high")]}

        fake.extract_batch = answer
        with patch("niannian.engine.LLMExtractor", return_value=fake), patch.object(self.engine, "_gate", return_value=None), \
                patch.object(self.engine, "_fallback", return_value=[Instruction(action="IGNORE")]):
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
        result = self.run_model({0: [Instruction(action="CREATE", title="确认交付对象A")], 1: [Instruction(action="CREATE", title="提交独立对象B")]},
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
                self.assertTrue(any(command.context_summary == "命中完整忽略规则" or
                                    command.context_summary.startswith("需复核：") for command in gated))

    def test_real_conditional_group_notices_do_not_fragment_into_tasks(self):
        candidates = [row for row in self.fixture["messages"] if row["session_id"].endswith("@chatroom") and
                      self.engine.CONDITIONAL.search(row["content"])]
        if not candidates:
            self.skipTest("No captured conditional group notice")
        for original in candidates:
            item = {**original, "message_ids": [1]}
            decisions = self.engine._fallback(item, [], [], profile="")
            self.assertTrue(all(command.action == "IGNORE" for command in decisions))
            self.assertTrue(any("需复核" in command.context_summary for command in decisions))

    def test_generic_group_words_cannot_associate_unrelated_tasks(self):
        candidates = [row for row in self.fixture["messages"] if "所有人" in row["content"]]
        if not candidates:
            self.skipTest("No captured all-member group notice")
        pending = [{"id": 1, "title": "@所有人 unrelated_object"}]
        for row in candidates:
            self.assertIsNone(self.engine.related(row["content"], pending))

    def test_matching_requires_distinctive_object_and_unique_target(self):
        # Pure task and object contracts contain no invented chat messages.
        self.assertIsNone(self.engine.related("所有人", [{"id": 1, "title": "通知所有人"}]))
        self.assertIsNone(self.engine.related("项目", [{"id": 1, "title": "确认项目"}]))
        pending = [{"id": 1, "title": "提交独特交付对象"}, {"id": 2, "title": "检查另一交付对象"}]
        self.assertEqual(self.engine.related("独特交付对象", pending)["id"], 1)
        self.assertIsNone(self.engine.related("交付对象", pending))

    def test_semantic_reminder_merges_source_and_audit(self):
        real = next((row for row in self.fixture["messages"] if "i志愿" in row["content"] and
                     "注册" in row["content"]), None)
        if real is None:
            raw_path = ROOT / "data" / "raw-wechat.json"
            if raw_path.exists():
                raw_fixture = json.loads(raw_path.read_text(encoding="utf-8"))
                found = next((row for row in raw_fixture["messages"] if "i志愿" in row["raw"]["content"] and
                              "注册" in row["raw"]["content"]), None)
                if found:
                    real = normalize(raw_fixture["account"], found["session_id"], found["raw"], set())
                    real["session_id"] = found["session_id"]
            if real is None:
                self.skipTest("No captured registration reminder")
        self.sid = real["session_id"]
        task_id = self.task("i志愿注册流程")
        payload = {**real, "account": self.fixture["account"]}
        message_id = self.db.ingest(payload)["id"]
        item = self.engine.aggregate(self.db.rows("SELECT * FROM messages WHERE id=?", (message_id,)), 0)[0]
        with self.db.connect() as conn:
            outcome, landed = self.engine._apply(conn, item, Instruction(action="CREATE", title="完成i志愿注册并提交截图"), "llm")
        self.assertEqual((outcome, landed), ("merged", task_id))
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM tasks")["n"], 1)
        task = self.db.one("SELECT source_message_ids,version FROM tasks WHERE id=?", (task_id,))
        self.assertIn(message_id, json.loads(task["source_message_ids"]))
        self.assertEqual(task["version"], 2)
        self.assertEqual(self.db.one("SELECT action FROM task_events WHERE task_id=?", (task_id,))["action"], "MERGE")

    def test_semantic_merge_keeps_different_objects_and_actions_separate(self):
        candidates = [{"id": 1, "title": "填写课程确认表"}, {"id": 2, "title": "提交课程确认表"}]
        match = self.engine._duplicate("完成填写课程确认表", candidates)
        self.assertEqual(match[0]["id"], 1)
        self.assertIsNone(self.engine._duplicate("填写另一资料表", candidates))
        self.assertIsNone(self.engine._duplicate("填写另一课程确认表", candidates))
        self.assertIsNone(self.engine._duplicate("提交通知所有人", [{"id": 1, "title": "提交所有人通知"}]))

    def test_user_extract_rule_enables_real_special_action_candidate(self):
        from niannian.llm import requires_review
        candidates = [row for row in self.fixture["messages"] if not self.engine.REQUEST.search(row["content"]) and
                      self.engine.ACTION_OBJECT.search(row["content"]) and not self.engine.CONDITIONAL.search(row["content"]) and
                      not requires_review(row, "") and 8 <= len(row["content"]) <= 120 and not row["is_self"]]
        if not candidates:
            self.skipTest("No captured explicit special action outside builtin request pattern")
        import re
        real = candidates[0]
        custom = {"action": "extract_task", "pattern": re.escape(real["content"][:4])}
        decisions = self.engine._fallback(real, [], [custom])
        self.assertTrue(any(command.action == "CREATE" for command in decisions))

    def test_specific_unknown_roles_are_reviewed_in_real_capture(self):
        from niannian.llm import ROLE_ASSIGNMENT, UNIVERSAL_REQUEST
        candidates = [row for row in self.fixture["messages"] if row["session_id"].endswith("@chatroom") and
                      ROLE_ASSIGNMENT.search(row["content"]) and not UNIVERSAL_REQUEST.search(row["content"])]
        if not candidates:
            self.skipTest("No captured role-directed group instruction")
        for row in candidates:
            gate = self.engine._gate(row, [])
            self.assertIsNotNone(gate)
            self.assertTrue(all(command.action == "IGNORE" for command in gate))

    def test_final_engine_guard_rejects_information_title(self):
        self.ingest()
        fake = FakeModel({0: [Instruction(action="CREATE", title="返校确认操作流程")]})
        with patch("niannian.engine.LLMExtractor", return_value=fake), patch.object(self.engine, "_gate", return_value=None):
            result = self.engine.process(force=True)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["ignored"], 1)

    def test_mixed_all_member_notice_does_not_assign_special_role_to_self(self):
        from niannian.llm import guard_decisions, ROLE_ASSIGNMENT, UNIVERSAL_REQUEST
        candidates = [row for row in self.fixture["messages"] if ROLE_ASSIGNMENT.search(row["content"]) and
                      UNIVERSAL_REQUEST.search(row["content"])]
        if not candidates and (ROOT / "data" / "raw-wechat.json").exists():
            raw_fixture = json.loads((ROOT / "data" / "raw-wechat.json").read_text(encoding="utf-8"))
            for original in raw_fixture["messages"]:
                item = normalize(raw_fixture["account"], original["session_id"], original["raw"], set())
                if ROLE_ASSIGNMENT.search(item["content"]) and UNIVERSAL_REQUEST.search(item["content"]):
                    candidates.append(item)
        if not candidates:
            self.skipTest("No captured mixed role and universal instruction")
        row = candidates[0]
        role_excerpt = ROLE_ASSIGNMENT.search(row["content"])[0]
        decisions = guard_decisions(row, [Instruction(action="CREATE", title=role_excerpt[:40], description=role_excerpt)])
        self.assertTrue(all(command.action == "IGNORE" for command in decisions))

    def test_universal_signing_request_survives_negative_exception(self):
        from niannian.llm import guard_decisions, UNIVERSAL_REQUEST, CONDITIONAL_PROHIBITION
        raw_path = ROOT / "data" / "raw-wechat.json"
        if not raw_path.exists():
            self.skipTest("Raw capture unavailable")
        raw_fixture = json.loads(raw_path.read_text(encoding="utf-8"))
        candidates = []
        for original in raw_fixture["messages"]:
            item = normalize(raw_fixture["account"], original["session_id"], original["raw"], set())
            if "双签" in item["content"] and UNIVERSAL_REQUEST.search(item["content"]) and CONDITIONAL_PROHIBITION.search(item["content"]):
                candidates.append(item)
        if not candidates:
            self.skipTest("No real universal signing request with negative exception")
        result = guard_decisions(candidates[0], [Instruction(action="CREATE", title="完成双签表")])
        self.assertEqual([command.action for command in result], ["CREATE"])

    def test_real_optional_household_transfer_requires_review(self):
        from niannian.llm import guard_decisions, requires_review
        raw_path = ROOT / "data" / "raw-wechat.json"
        if not raw_path.exists():
            self.skipTest("Raw capture unavailable")
        capture = json.loads(raw_path.read_text(encoding="utf-8"))
        row = next((entry for entry in capture["messages"] if "户口" in entry["raw"]["content"] and
                    "如果还有" in entry["raw"]["content"]), None)
        if row is None:
            self.skipTest("No real optional household transfer invitation")
        item = normalize(capture["account"], row["session_id"], row["raw"], set())
        self.assertTrue(requires_review(item, ""))
        commands = guard_decisions(item, [Instruction(action="CREATE", title="确认户口迁移")])
        self.assertEqual([command.action for command in commands], ["IGNORE"])

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
        result = self.run_model({0: [Instruction(action="CREATE", title="提交独立对象B"),
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
        with patch("niannian.engine.ROUND_LIMIT", 12), patch("niannian.engine.PER_SESSION_LIMIT", 8):
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
