"""Domain regressions using captured WeChat content, never invented messages."""

import json
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from niannian import local_rules
from niannian.engine import Engine
from niannian.instructions import Instruction, guard_decisions
from niannian.storage import Database, parse_time, utc_now
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

    def record_final_decisions(self, messages, answers):
        pass

    def record_deferred(self, message, note=None):
        pass

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
        with patch("niannian.engine.LLMExtractor", return_value=fake), patch.object(self.engine, "_gate", return_value=None):
            return self.engine.process(force=True, **kwargs)

    def test_legacy_limits_do_not_defer_backlog(self):
        for index in range(6):
            self.ingest(index, 900 - index * 30)
        rows = self.engine.pending_messages(force=True, values={"per_session_limit": 3, "round_limit": 4})
        self.assertEqual(len(rows), 6)

    def test_new_arrival_defers_remaining_session_without_processing_it(self):
        first = self.ingest(0, 700)
        second = self.ingest(1, 500)
        self.values["batch_char_budget"] = max(len(row["content"]) for row in self.real[:2])
        fake = FakeModel({0: [Instruction(action="IGNORE")]})
        def progress(done, total, session):
            if done == 1:
                self.ingest(2, 0)
        with patch("niannian.engine.LLMExtractor", return_value=fake), patch.object(self.engine, "_gate", return_value=None):
            result = self.engine.process(progress=progress)
        self.assertEqual(fake.stats["requests"], 1)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(self.db.one("SELECT processed FROM messages WHERE id=?", (first,))["processed"], 1)
        self.assertEqual(self.db.one("SELECT processed FROM messages WHERE id=?", (second,))["processed"], 0)

    def test_long_content_uses_persisted_summary_and_preserves_original(self):
        original = max(self.real, key=lambda row: len(row["content"]))
        self.real = [original]
        message_id = self.ingest(0)
        self.values["new_message_chars"] = 200
        before = self.db.one("SELECT * FROM messages WHERE id=?", (message_id,))
        fake = FakeModel({0: [Instruction(action="IGNORE")]})
        summary = original["content"][:150]  # Simulated model output only.
        with patch.object(fake, "compress", return_value=summary, create=True) as compress, \
                patch("niannian.engine.LLMExtractor", return_value=fake), \
                patch.object(self.engine, "_gate", return_value=None), \
                patch.object(fake, "extract", wraps=fake.extract) as extract:
            result = self.engine.process(force=True)
            self.assertEqual(result["processed"], 1)
            sent = extract.call_args.args[0]
            self.assertEqual(sent["model_content"], summary)
            self.assertEqual(sent["content"], original["content"])
            after = self.db.one("SELECT * FROM messages WHERE id=?", (message_id,))
            self.assertEqual(after["content"], before["content"])
            item = self.engine.aggregate([before], 0)[0]
            cached = self.engine._compressed_item(item, fake, self.values)
            self.assertEqual(cached["model_content"], summary)
            self.assertEqual(compress.call_count, 1)
            metadata = json.loads(after["metadata_json"])
            self.assertEqual(metadata["text_compression"]["message_ids"], [message_id])
            for key, value in json.loads(before["metadata_json"]).items():
                self.assertEqual(metadata[key], value)
            self.values["ai_model"] = "changed-model"
            self.engine._compressed_item(item, fake, self.values)
            self.assertEqual(compress.call_count, 2)

    def test_failed_compression_does_not_process_or_extract(self):
        self.real = [max(self.real, key=lambda row: len(row["content"]))]
        message_id = self.ingest(0)
        self.values["new_message_chars"] = 200
        fake = FakeModel()
        with patch.object(fake, "compress", return_value=None, create=True), \
                patch("niannian.engine.LLMExtractor", return_value=fake), \
                patch.object(self.engine, "_gate", return_value=None):
            result = self.engine.process(force=True)
        self.assertEqual(result["processed"], 0)
        self.assertEqual(fake.stats["requests"], 0)
        self.assertEqual(self.db.one("SELECT processed FROM messages WHERE id=?", (message_id,))["processed"], 0)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM task_events")["n"], 0)

    def test_cloud_failure_keeps_source_pending_until_next_success(self):
        message_id = self.ingest()
        fake = FakeModel()
        with patch("niannian.engine.LLMExtractor", return_value=fake), \
                patch.object(self.engine, "_gate", return_value=None), \
                patch("niannian.engine.local_rules.decisions", side_effect=AssertionError("No local fallback")):
            failed = self.engine.process(force=True)
            self.assertEqual(failed["processed"], 0)
            self.assertEqual(failed["skipped"], 1)
            self.assertEqual(self.db.one("SELECT processed FROM messages WHERE id=?", (message_id,))["processed"], 0)
            self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM ignored_messages")["n"], 0)
            fake.answers = {0: [Instruction(action="IGNORE")]}
            retried = self.engine.process(force=True)
        self.assertEqual(retried["processed"], 1)
        self.assertEqual(fake.stats["requests"], 2)

    def test_partial_cloud_response_only_processes_valid_sibling(self):
        first = self.ingest(0, 700)
        second = self.ingest(1, 500)
        fake = FakeModel({1: [Instruction(action="IGNORE")]})
        with patch("niannian.engine.LLMExtractor", return_value=fake), \
                patch.object(self.engine, "_gate", return_value=None), \
                patch("niannian.engine.local_rules.decisions", side_effect=AssertionError("No local fallback")):
            result = self.engine.process(force=True)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(self.db.one("SELECT processed FROM messages WHERE id=?", (first,))["processed"], 0)
        self.assertEqual(self.db.one("SELECT processed FROM messages WHERE id=?", (second,))["processed"], 1)

    def test_model_request_parameters_come_from_settings(self):
        self.ingest(0)
        seen = {}

        def spy(*args, **kwargs):
            seen.update(kwargs)
            return FakeModel({0: [Instruction(action="IGNORE")]})

        with patch("niannian.engine.LLMExtractor", side_effect=spy), \
                patch.object(self.engine, "_gate", return_value=None):
            self.engine.process(force=True)
        self.assertEqual(seen["max_tokens_single"], 700)
        self.assertEqual(seen["temperature"], 0.0)
        self.assertEqual(seen["history_rows"], 6)
        self.assertNotIn("ai_api_key", seen)

        self.values.update({"ai_temperature": 0.5, "ai_max_tokens_batch": 4000, "new_message_chars": 900,
                            "history_rows": 2, "batch_char_budget": 5000})
        seen.clear()
        with patch("niannian.engine.LLMExtractor", side_effect=spy), \
                patch.object(self.engine, "_gate", return_value=None):
            self.engine.process(force=True)
        self.assertEqual((seen["temperature"], seen["max_tokens_batch"], seen["new_message_chars"],
                          seen["history_rows"]), (0.5, 4000, 900, 2))

    def test_only_character_budget_splits_requests(self):
        for index in range(6):
            self.ingest(index, 900 - index * 90)
        self.values["batch_limit"] = 1  # A legacy key must not cap requests.
        self.values["batch_char_budget"] = 60000
        shapes = []
        with patch("niannian.engine.LLMExtractor", side_effect=lambda *args, **kwargs: Recorder(shapes)), \
                patch.object(self.engine, "_gate", return_value=None):
            wide = self.engine.process(force=True)
        self.assertEqual((wide["batches"], wide["batched_items"]), (1, 6))
        self.assertEqual(shapes, ["batch"])
        self.db.execute("UPDATE messages SET processed=0")
        self.values["batch_char_budget"] = 1
        shapes.clear()
        with patch("niannian.engine.LLMExtractor", side_effect=lambda *args, **kwargs: Recorder(shapes)), \
                patch.object(self.engine, "_gate", return_value=None):
            narrow = self.engine.process(force=True)
        self.assertEqual(narrow["processed"], 6)
        self.assertEqual(shapes, ["single"] * 6)

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

    def test_partial_batch_retry_does_not_refresh_other_model_versions(self):
        task_id = self.task()
        self.ingest(0, 700)
        self.ingest(1, 500)
        fake = FakeModel()

        def answer(*args):
            self.db.update_task(task_id, {"priority": "low"}, 1)
            return {1: [Instruction(action="UPDATE", related_task_id=task_id, priority="high")]}

        fake.extract_batch = answer
        with patch("niannian.engine.LLMExtractor", return_value=fake), patch.object(self.engine, "_gate", return_value=None), \
                patch("niannian.engine.local_rules.decisions", side_effect=AssertionError("Cloud failures must not fall back")):
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
        self.assertFalse(self.engine.has_work())

    def test_has_work_tracks_unprocessed_messages(self):
        self.assertFalse(self.engine.has_work())
        message_id = self.ingest()
        self.assertTrue(self.engine.has_work())
        self.db.execute("UPDATE messages SET processed=1 WHERE id=?", (message_id,))
        self.assertFalse(self.engine.has_work())

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
                      local_rules.CONDITIONAL.search(row["content"])]
        if not candidates:
            self.skipTest("No captured conditional group notice")
        for original in candidates:
            item = {**original, "message_ids": [1]}
            decisions = local_rules.decisions(item, [], [])
            self.assertTrue(all(command.action == "IGNORE" for command in decisions))

    def test_generic_group_words_cannot_associate_unrelated_tasks(self):
        candidates = [row for row in self.fixture["messages"] if "所有人" in row["content"]]
        if not candidates:
            self.skipTest("No captured all-member group notice")
        pending = [{"id": 1, "title": "@所有人 unrelated_object"}]
        for row in candidates:
            self.assertIsNone(local_rules.related(row["content"], pending))

    def test_matching_requires_distinctive_object_and_unique_target(self):
        # Pure task and object contracts contain no invented chat messages.
        self.assertIsNone(local_rules.related("所有人", [{"id": 1, "title": "通知所有人"}]))
        self.assertIsNone(local_rules.related("项目", [{"id": 1, "title": "确认项目"}]))
        pending = [{"id": 1, "title": "提交独特交付对象"}, {"id": 2, "title": "检查另一交付对象"}]
        self.assertEqual(local_rules.related("独特交付对象", pending)["id"], 1)
        self.assertIsNone(local_rules.related("交付对象", pending))

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
                    real = normalize(raw_fixture["account"], found["session_id"], found["raw"])
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
        candidates = [row for row in self.fixture["messages"] if not local_rules.REQUEST.search(row["content"]) and
                      local_rules.ACTION_OBJECT.search(row["content"]) and not local_rules.CONDITIONAL.search(row["content"]) and
                      8 <= len(row["content"]) <= 120 and not row["is_self"]]
        if not candidates:
            self.skipTest("No captured explicit special action outside builtin request pattern")
        import re
        real = candidates[0]
        custom = {"action": "extract_task", "pattern": re.escape(real["content"][:4])}
        decisions = local_rules.decisions(real, [], [custom])
        self.assertTrue(any(command.action == "CREATE" for command in decisions))

    def test_real_group_notices_reach_model_without_role_keyword_gate(self):
        candidates = [row for row in self.fixture["messages"]
                      if row["session_id"].endswith("@chatroom") and not row["is_self"]
                      and any(word in row["content"] for word in ("班长", "报名", "如果"))
                      and not row["content"].lstrip().startswith("[")]
        if not candidates:
            self.skipTest("No captured group notice with role or participation conditions")
        for row in candidates:
            self.assertIsNone(self.engine._gate(row, []))

    def test_engine_checks_once_and_keeps_legal_sibling(self):
        self.ingest()
        self.values["task_title_max_chars"] = 40
        commands = [Instruction(action="CREATE", title="确认" + "对象" * 24),
                    Instruction(action="CREATE", title="办理银行卡")]
        fake = FakeModel({0: commands})
        with patch("niannian.engine.LLMExtractor", return_value=fake), \
                patch.object(self.engine, "_gate", return_value=None), \
                patch("niannian.engine.guard_decisions", wraps=guard_decisions) as checked:
            result = self.engine.process(force=True)
        self.assertEqual((result["created"], result["ignored"]), (1, 1))
        self.assertEqual(checked.call_count, 1)
        self.assertEqual(self.db.one("SELECT title FROM tasks")["title"], "办理银行卡")

    def test_task_count_config_preserves_source_on_rejected_batch_reference(self):
        first = self.ingest(0, 700)
        second = self.ingest(1, 500)
        self.values["max_tasks_per_message"] = 1
        result = self.run_model({0: [Instruction(action="CREATE", title="办理银行卡"),
                                    Instruction(action="CREATE", title="查看公示")],
                                 1: [Instruction(action="UPDATE", related_task_ref="0:1", priority="high")]})
        self.assertEqual((result["created"], result["skipped"]), (1, 1))
        self.assertEqual(self.db.one("SELECT processed FROM messages WHERE id=?", (first,))["processed"], 1)
        self.assertEqual(self.db.one("SELECT processed FROM messages WHERE id=?", (second,))["processed"], 0)

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
        payload = normalize(capture["account"], own["session_id"], own["raw"])
        self.assertTrue(payload["is_self"])
        self.assertNotIn("identity_confident", payload["metadata"])
        # This archived fixture predates account-scoped local session IDs. Map
        # the normalized source to the same captured session already in this DB.
        self.sid = own["session_id"]
        payload["session_id"] = self.sid
        self.db.execute("UPDATE sessions SET monitored=1 WHERE id=?", (self.sid,))
        return payload

    def test_self_completion_uses_is_self_without_confirmation_marker(self):
        payload = self.captured_own_message()
        task_id = self.task()
        self.db.ingest(payload)
        result = self.run_model({0: [Instruction(action="RESOLVE", related_task_id=task_id)]})
        self.assertEqual(result["resolved"], 1)
        self.assertEqual(self.db.one("SELECT status,version FROM tasks WHERE id=?", (task_id,)), {"status": "completed", "version": 2})

    def test_all_real_sessions_are_drained_in_sequence(self):
        for original in self.fixture["messages"]:
            self.db.ingest({**original, "account": self.fixture["account"],
                            "sender_key": original["metadata"].get("sender_username") or original["sender_name"]})
        rows = self.engine.pending_messages(force=True)
        expected = self.db.one("SELECT COUNT(*) AS n FROM messages WHERE processed=0")["n"]
        self.assertEqual(len(rows), expected)
        fake = Recorder([])
        sessions = []
        with patch("niannian.engine.LLMExtractor", return_value=fake), patch.object(self.engine, "_gate", return_value=None):
            result = self.engine.process(force=True, progress=lambda done, total, session: sessions.append(session) if session else None)
        self.assertEqual(result["processed"], expected)
        self.assertEqual(self.db.queued_count(), 0)
        blocks = [name for index, name in enumerate(sessions) if index == 0 or sessions[index - 1] != name]
        self.assertEqual(len(blocks), len(set(sessions)))

    def test_history_excludes_late_ingested_future_messages(self):
        self.ingest(0, 30)  # Ingest the more recent captured message first.
        self.ingest(1, 600)
        row = self.db.rows("SELECT * FROM messages ORDER BY id DESC LIMIT 1")[0]
        item = self.engine.aggregate([row], 0)[0]
        self.assertEqual(self.engine._history(item), [])


if __name__ == "__main__":
    unittest.main()
