"""Transport and model instruction contracts, with captured message content."""

import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from shiguang.llm import Instruction, LLMExtractor

ROOT = Path(__file__).resolve().parents[1]


class InstructionTests(unittest.TestCase):
    def test_strict_schema_and_timezone(self):
        for payload in ({"action": "CREATE", "title": " "}, {"action": "UPDATE"},
                        {"action": "RESOLVE", "related_task_id": "4"},
                        {"action": "CREATE", "title": "Task A", "due_at": "2026-10-09T18:00:00"},
                        {"action": "CREATE", "title": "Task A", "extra": True}):
            with self.assertRaises(ValueError):
                Instruction.model_validate(payload)
        self.assertEqual(Instruction(action="CREATE", title="Task A", due_at="2026-10-09T18:00:00Z").action, "CREATE")

    def test_mutually_exclusive_references(self):
        with self.assertRaises(ValueError):
            Instruction(action="UPDATE", related_task_id=1, related_task_ref="0:0")


class ModelTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / "data" / "initial-wechat.json"
        if not path.exists():
            self.skipTest("Captured WeChat fixture unavailable; no fabricated messages")
        messages = json.loads(path.read_text(encoding="utf-8"))["messages"]
        self.message = messages[0]
        self.second = next(row for row in messages[1:] if row["session_id"] == self.message["session_id"])
        self.model = LLMExtractor("https://example.invalid/v1", "never-displayed", "provider/model")
        self.tasks = [{"id": 5, "title": "Task A", "description": "domain fixture", "owner": None,
                       "priority": "medium", "due_at": None, "version": 1}]

    def response(self, payload):
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(payload)}}]}).encode())

    def test_multiple_instructions_and_thinking_always_disabled(self):
        payload = {"instructions": [{"action": "CREATE", "title": "Task A"}, {"action": "CREATE", "title": "Task B"}]}
        with patch("shiguang.llm.urlopen", return_value=self.response(payload)) as mocked:
            result = self.model.extract(self.message, [], self.tasks, "", "session")
        self.assertEqual(len(result), 2)
        request = json.loads(mocked.call_args.args[0].data)
        self.assertIs(request["enable_thinking"], False)
        context = json.loads(request["messages"][1]["content"])
        self.assertIsInstance(context["new_message"]["is_self"], bool)
        self.assertEqual(context["new_message"]["content"], self.message["content"])

    def test_malformed_item_does_not_discard_valid_batch_siblings(self):
        payload = {"items": [{"index": 0, "instructions": [{"action": "CREATE", "title": "Task A"}]},
                             {"index": 1, "instructions": [{"action": "RESOLVE", "related_task_id": 999}]}]}
        with patch("shiguang.llm.urlopen", return_value=self.response(payload)):
            result = self.model.extract_batch([self.message, self.second], [], self.tasks)
        self.assertEqual(list(result), [0])

    def test_batch_lifecycle_references_require_earlier_creation(self):
        payload = {"items": [{"index": 0, "instructions": [{"action": "CREATE", "title": "Task A"}]},
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
                             {"index": 0, "instructions": [{"action": "CREATE", "title": "Task A"}]},
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


if __name__ == "__main__":
    unittest.main()
