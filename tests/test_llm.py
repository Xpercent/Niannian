"""Transport and model instruction contracts, with captured message content."""

import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from shiguang.llm import (Instruction, LLMExtractor, NEW_MESSAGE_CHARS, HISTORY_CHARS, TASK_CHARS,
                       clip_text, requires_review)

ROOT = Path(__file__).resolve().parents[1]


class InstructionTests(unittest.TestCase):
    def test_strict_schema_and_timezone(self):
        for payload in ({"action": "CREATE", "title": " "}, {"action": "UPDATE"},
                        {"action": "RESOLVE", "related_task_id": "4"},
                        {"action": "CREATE", "title": "确认交付对象A", "due_at": "2026-10-09T18:00:00"},
                        {"action": "CREATE", "title": "确认交付对象A", "extra": True}):
            with self.assertRaises(ValueError):
                Instruction.model_validate(payload)
        self.assertEqual(Instruction(action="CREATE", title="确认交付对象A", due_at="2026-10-09T18:00:00Z").action, "CREATE")

    def test_mutually_exclusive_references(self):
        with self.assertRaises(ValueError):
            Instruction(action="UPDATE", related_task_id=1, related_task_ref="0:0")


class ModelTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / "data" / "initial-wechat.json"
        if not path.exists():
            self.skipTest("Captured WeChat fixture unavailable; no fabricated messages")
        messages = json.loads(path.read_text(encoding="utf-8"))["messages"]
        self.messages = messages
        self.message = messages[0]
        self.second = next(row for row in messages[1:] if row["session_id"] == self.message["session_id"])
        self.model = LLMExtractor("https://example.invalid/v1", "never-displayed", "provider/model")
        self.tasks = [{"id": 5, "title": "确认交付对象A", "description": "domain fixture", "owner": None,
                       "priority": "medium", "due_at": None, "version": 1}]

    def response(self, payload):
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(payload)}}]}).encode())

    def test_multiple_instructions_and_thinking_always_disabled(self):
        payload = {"instructions": [{"action": "CREATE", "title": "确认交付对象A"}, {"action": "CREATE", "title": "提交独立对象B"}]}
        with patch("shiguang.llm.urlopen", return_value=self.response(payload)) as mocked:
            result = self.model.extract(self.message, [], self.tasks, "", "session")
        self.assertEqual(len(result), 2)
        request = json.loads(mocked.call_args.args[0].data)
        self.assertIs(request["enable_thinking"], False)
        context = json.loads(request["messages"][1]["content"])
        self.assertIsInstance(context["new_message"]["is_self"], bool)
        self.assertEqual(context["new_message"]["content"], self.message["content"])

    def test_clip_text_honours_tiny_and_zero_budgets(self):
        long_text = "通知" * 500
        self.assertEqual(clip_text(long_text, 0), "")
        for budget in (1, 10, 21, 22, 60, 120, 2600):
            self.assertLessEqual(len(clip_text(long_text, budget)), budget, budget)
        self.assertEqual(clip_text("短文本", 100), "短文本")
        kept = clip_text(long_text, 2600)
        self.assertTrue(kept.startswith(long_text[:100]) and kept.endswith(long_text[-100:]))

    def test_configured_budgets_and_sampling_reach_the_request(self):
        tuned = LLMExtractor("https://example.invalid/v1", "unused", "provider/model", temperature=0.6,
                             max_tokens_single=1500, max_tokens_batch=3000, new_message_chars=300,
                             history_chars=200, history_rows=2, task_chars=120, profile_chars=60)
        largest = max(self.messages, key=lambda row: len(row["content"]))
        tasks = [{**self.tasks[0], "id": index + 1} for index in range(100)]
        with patch("shiguang.llm.urlopen", return_value=self.response({"instructions": [{"action": "IGNORE"}]})) as mocked:
            tuned.extract(largest, self.messages, tasks, "画像" * 500, "session")
        request = json.loads(mocked.call_args.args[0].data)
        data = json.loads(request["messages"][1]["content"])
        self.assertEqual((request["temperature"], request["max_tokens"]), (0.6, 1500))
        self.assertIs(request["enable_thinking"], False)
        self.assertLessEqual(len(data["new_message"]["content"]), 300)
        self.assertLessEqual(sum(len(row["content"]) for row in data["history"]), 200)
        self.assertLessEqual(len(data["history"]), 2)
        self.assertLessEqual(sum(len(json.dumps(task, ensure_ascii=False)) for task in data["pending_tasks"]), 120)
        self.assertLessEqual(len(data["user_profile"]), 60)
        with patch("shiguang.llm.urlopen", return_value=self.response({"items": []})) as mocked:
            tuned.extract_batch([self.message, self.second], [], tasks)
        self.assertEqual(json.loads(mocked.call_args.args[0].data)["max_tokens"], 3000)

    def test_history_rows_zero_sends_no_context(self):
        bare = LLMExtractor("https://example.invalid/v1", "unused", "model", history_rows=0)
        with patch("shiguang.llm.urlopen", return_value=self.response({"instructions": [{"action": "IGNORE"}]})) as mocked:
            bare.extract(self.message, self.messages, [], "", "session")
        data = json.loads(json.loads(mocked.call_args.args[0].data)["messages"][1]["content"])
        self.assertEqual(data["history"], [])

    def test_malformed_item_does_not_discard_valid_batch_siblings(self):
        payload = {"items": [{"index": 0, "instructions": [{"action": "CREATE", "title": "确认交付对象A"}]},
                             {"index": 1, "instructions": [{"action": "RESOLVE", "related_task_id": 999}]}]}
        with patch("shiguang.llm.urlopen", return_value=self.response(payload)):
            result = self.model.extract_batch([self.message, self.second], [], self.tasks)
        self.assertEqual(list(result), [0])

    def test_batch_lifecycle_references_require_earlier_creation(self):
        payload = {"items": [{"index": 0, "instructions": [{"action": "CREATE", "title": "确认交付对象A"}]},
                             {"index": 1, "instructions": [{"action": "UPDATE", "related_task_ref": "0:0", "priority": "high"}]}]}
        with patch("shiguang.llm.urlopen", return_value=self.response(payload)):
            result = self.model.extract_batch([self.message, self.second], [], self.tasks)
        self.assertEqual(result[1][0].related_task_ref, "0:0")
        payload["items"][1]["instructions"][0]["related_task_ref"] = "1:0"
        with patch("shiguang.llm.urlopen", return_value=self.response(payload)):
            result = self.model.extract_batch([self.message, self.second], [], self.tasks)
        self.assertNotIn(1, result)

    def test_duplicate_indexes_are_rejected(self):
        payload = {"items": [{"index": 0, "instructions": [{"action": "IGNORE"}]},
                             {"index": 0, "instructions": [{"action": "CREATE", "title": "确认交付对象A"}]},
                             {"index": 22, "instructions": [{"action": "IGNORE"}]}]}
        with patch("shiguang.llm.urlopen", return_value=self.response(payload)):
            self.assertEqual(self.model.extract_batch([self.message, self.second], [], self.tasks), {})

    def test_missing_endpoint_and_full_endpoint(self):
        for url in ("https://example.invalid/v1", "https://example.invalid/v1/chat/completions"):
            model = LLMExtractor(url, "unused", "any-model")
            self.assertEqual(model.endpoint, "https://example.invalid/v1/chat/completions")

    def test_transport_failure_preserves_fallback_signal(self):
        with patch("shiguang.llm.urlopen", side_effect=TimeoutError):
            self.assertIsNone(self.model.extract(self.message, [], self.tasks))
        self.assertEqual((self.model.stats["requests"], self.model.stats["failures"], self.model.last_error), (1, 1, "TimeoutError"))

    def test_context_budget_uses_real_long_messages(self):
        largest = max(self.messages, key=lambda row: len(row["content"]))
        tasks = [{**self.tasks[0], "id": index + 1} for index in range(100)]
        with patch("shiguang.llm.urlopen", return_value=self.response({"instructions": [{"action": "IGNORE"}]})) as mocked:
            self.model.extract(largest, self.messages, tasks)
        body = json.loads(mocked.call_args.args[0].data)
        data = json.loads(body["messages"][1]["content"])
        self.assertLessEqual(len(data["new_message"]["content"]), NEW_MESSAGE_CHARS)
        self.assertLessEqual(sum(len(row["content"]) for row in data["history"]), HISTORY_CHARS)
        self.assertLessEqual(sum(len(json.dumps(task, ensure_ascii=False)) for task in data["pending_tasks"]), TASK_CHARS)
        self.assertLessEqual(body["max_tokens"], 700)

    def test_no_profile_conditional_notice_rejects_model_created_tasks(self):
        candidate = next((row for row in self.messages if requires_review(row, "")), None)
        if candidate is None:
            self.skipTest("No captured role-conditional notice")
        with patch("shiguang.llm.urlopen", return_value=self.response({"instructions": [{"action": "CREATE", "title": "确认交付对象A"}]})):
            result = self.model.extract(candidate, [], self.tasks)
        self.assertEqual([instruction.action for instruction in result], ["IGNORE"])
        self.assertIn("需复核", result[0].context_summary)

    def test_minimal_ignore_tolerates_irrelevant_provider_nulls(self):
        with patch("shiguang.llm.urlopen", return_value=self.response({"instructions": [{"action": "IGNORE", "title": None,
                                                                                         "context_summary": None, "related_task_id": 0}]})):
            result = self.model.extract(self.message, [], self.tasks)
        self.assertEqual([instruction.action for instruction in result], ["IGNORE"])

    def test_empty_instruction_list_means_ignore_not_rule_fallback(self):
        with patch("shiguang.llm.urlopen", return_value=self.response({"items": [{"index": 0, "instructions": []},
                                                                                 {"index": 1, "instructions": []}]})):
            result = self.model.extract_batch([self.message, self.second], [], self.tasks)
        self.assertEqual(list(result), [0, 1])
        self.assertEqual([result[index][0].action for index in result], ["IGNORE", "IGNORE"])

    def test_information_only_titles_cannot_become_tasks(self):
        from shiguang.llm import guard_decisions
        for title in ("i志愿注册流程指南", "返校健康安全提醒", "返校确认操作流程"):
            result = guard_decisions(self.message, [Instruction(action="CREATE", title=title)])
            self.assertEqual([command.action for command in result], ["IGNORE"])

    def test_relevant_current_object_precedes_newest_unrelated_tasks(self):
        real = max(self.messages, key=lambda message: len(message["content"]))
        object_name = next(iter(__import__('re').findall(r"[\u4e00-\u9fff]{4,}", real["content"])))[:8]
        tasks = [{**self.tasks[0], "id": index + 2, "title": "确认 unrelated_object_" + str(index)} for index in range(100)]
        tasks.append({**self.tasks[0], "id": 1, "title": "确认" + object_name})
        data = self.model._data(real, [], tasks, "", "")
        self.assertEqual(data["pending_tasks"][0]["id"], 1)


if __name__ == "__main__":
    unittest.main()
