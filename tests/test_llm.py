"""Transport and model instruction contracts, with captured message content."""

import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from niannian.llm import LLMExtractor, NEW_MESSAGE_CHARS, HISTORY_CHARS, TASK_CHARS, clip_text
from niannian.instructions import Instruction, clean_instruction, guard_decisions
from niannian.model_context import render_compression, render_extraction

ROOT = Path(__file__).resolve().parents[1]


def user_content(mocked):
    """取出真正发给模型的 user content 并按 XML 解析。"""
    request = json.loads(mocked.call_args.args[0].data)
    return ElementTree.fromstring(request["messages"][1]["content"])


def rendered_task_size(node):
    """按引擎选入待办时的同一口径估长度，验证的是配置真的起作用。"""
    brief = {"id": int(node.get("id")), "title": node.text, "priority": node.get("priority"),
             "due_at": node.get("due_at")}
    return len(json.dumps(brief, ensure_ascii=False))


class XmlContextTests(unittest.TestCase):
    """输入渲染：正文可逆转义、编号与本人标记落在属性上、时间按 Asia/Shanghai 给出。"""

    def setUp(self):
        self.data = {
            "reference_time": "2026-10-09T00:00:00+00:00", "timezone": "Asia/Shanghai",
            "session": {"name": '班会"群"<x>', "is_group": True}, "user_profile": "",
            "pending_tasks": [{"id": 7, "title": "提交 A & B 表", "priority": "high", "due_at": None}],
            "history": [{"sender_name": "甲", "content": "先前那句", "is_self": False,
                         "created_at": "2026-10-08T20:00:00+00:00"}],
            "new_messages": [
                {"sender_name": "甲", "content": "先看</content>与&amp;符号", "is_self": False,
                 "created_at": "2026-10-09T01:00:00+00:00"},
                {"sender_name": "我", "content": "已完成 & 收到", "is_self": True,
                 "created_at": "2026-10-09T02:00:00+00:00", "content_is_summary": True},
            ],
        }

    def test_bodies_survive_the_round_trip_unchanged(self):
        root = ElementTree.fromstring(render_extraction(self.data))
        messages = root.findall("./current_messages/message")
        self.assertEqual([node.findtext("content") for node in messages],
                         [item["content"] for item in self.data["new_messages"]])
        self.assertEqual([node.findtext("content") for node in root.findall("./history/message")],
                         ["先前那句"])

    def test_index_self_and_summary_are_attributes(self):
        root = ElementTree.fromstring(render_extraction(self.data))
        messages = root.findall("./current_messages/message")
        self.assertEqual([node.get("index") for node in messages], ["0", "1"])
        self.assertEqual([node.get("self") for node in messages], ["false", "true"])
        self.assertIsNone(messages[0].get("summary"))
        self.assertEqual(messages[1].get("summary"), "true")
        self.assertNotIn("index", root.find("./history/message").attrib)

    def test_times_render_as_shanghai_instants(self):
        root = ElementTree.fromstring(render_extraction(self.data))
        self.assertEqual(root.findtext("./context/reference_time"), "2026-10-09T08:00:00+08:00")
        self.assertEqual(root.findtext("./context/timezone"), "Asia/Shanghai")
        self.assertEqual(root.findall("./current_messages/message")[0].get("time"), "2026-10-09T09:00:00+08:00")

    def test_missing_profile_is_marked_unknown_and_empty_sections_close(self):
        data = {**self.data, "user_profile": "", "history": [], "new_message": self.data["new_messages"][0]}
        data.pop("new_messages")
        rendered = render_extraction(data)
        root = ElementTree.fromstring(rendered)
        self.assertEqual(root.find("./user_profile").get("unknown"), "true")
        self.assertIn("不能据此认定本人担任任何职务", root.findtext("user_profile"))
        self.assertIn("<history />", rendered)
        self.assertEqual([node.get("index") for node in root.findall("./current_messages/message")], ["0"])

    def test_task_and_profile_text_stay_readable(self):
        root = ElementTree.fromstring(render_extraction({**self.data, "user_profile": "我是班长"}))
        task = root.find("./pending_tasks/task")
        self.assertEqual((task.get("id"), task.get("priority"), root.findtext("./user_profile")),
                         ("7", "high", "我是班长"))
        self.assertEqual(task.text, "提交 A & B 表")
        self.assertNotIn("due_at", task.attrib)

    def test_compression_source_is_the_full_original(self):
        source = "第一段\n<compression>\n& 第二段 </source>\n最后一句"
        root = ElementTree.fromstring(render_compression({"source_text": source, "max_summary_chars": 2600,
                                                          "context_available": False}))
        self.assertEqual(root.findtext("source"), source)
        self.assertEqual((root.get("max_summary_chars"), root.get("context_available")), ("2600", "false"))

    def test_extractor_sends_xml_and_keeps_json_output_contract(self):
        model = LLMExtractor("https://example.invalid/v1", "unused", "provider/model")
        message = {"sender_name": "甲", "content": "把表格交上来", "is_self": False,
                   "created_at": "2026-10-09T01:00:00+00:00", "session_id": "45948752706@chatroom",
                   "message_ids": [1]}
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": '{"instructions":[{"action":"IGNORE"}]}'}}]}).encode())
        with patch("niannian.llm.urlopen", return_value=response) as mocked:
            model.extract(message, [], [], "", "班会群")
        body = json.loads(mocked.call_args.args[0].data)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(user_content(mocked).findtext("./current_messages/message/content"), "把表格交上来")

    def test_control_characters_crlf_and_attribute_whitespace(self):
        message = {**self.data["new_messages"][0], "content": "前\r\n后\x00\x1f", "sender_name": '甲\t\n\r"&<'}
        self.data["new_messages"] = [message]
        root = ElementTree.fromstring(render_extraction(self.data))
        node = root.find("./current_messages/message")
        self.assertEqual(node.findtext("content"), "前\r\n后\\u0000\\u001f")
        self.assertEqual(node.get("sender"), message["sender_name"])
        self.assertEqual(message["content"], "前\r\n后\x00\x1f")

    def test_profile_whitespace_and_task_deadline_are_preserved(self):
        self.data["user_profile"] = "  画像\r\n下一行  "
        self.data["pending_tasks"][0]["due_at"] = "2026-10-09T20:00:00Z"
        root = ElementTree.fromstring(render_extraction(self.data))
        self.assertEqual(root.findtext("user_profile"), self.data["user_profile"])
        self.assertEqual(root.find("./pending_tasks/task").get("due_at"), "2026-10-10T04:00:00+08:00")


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


class InstructionToleranceTests(unittest.TestCase):
    """真实回复里的形状噪声只纠正字段；已经答对的行动不该因为一个多余键整段消失。"""

    def setUp(self):
        self.model = LLMExtractor("https://example.invalid/v1", "unused", "provider/model", enabled=False)
        self.tasks = [{"id": 5, "title": "确认交付对象A"}]

    def test_numeric_priority_foreign_keys_and_stray_id_still_create(self):
        created = Instruction.model_validate(clean_instruction(
            {"action": "CREATE", "title": "确认交付对象A", "priority": 1, "index": 0, "id": 0,
             "resolution": None, "context_summary": None, "related_task_ref": "消息索引:4"}))
        self.assertEqual((created.action, created.priority), ("CREATE", "high"))
        self.assertIsNone(created.related_task_ref)

    def test_known_priority_aliases_are_normalised_and_unknown_values_rejected(self):
        for raw, expected in (("HIGH", "high"), ("3", "low"), ("中", "medium")):
            self.assertEqual(clean_instruction({"action": "CREATE", "title": "确认交付对象A", "priority": raw})["priority"], expected)
        for raw in ("很高", 9, True, [], {}):
            self.assertIsNone(self.model._validate([{"action": "CREATE", "title": "确认交付对象A", "priority": raw}], []))

    def test_target_alias_requires_complete_current_session_id(self):
        session = "当前群"
        for raw in ({"id": "5"}, {"task_id": 5}, {"ref": session + ":5"}, {"related_task_id": "5"}):
            result = self.model._validate([{"action": "UPDATE", "priority": "high", **raw}], self.tasks, session_name=session)
            self.assertEqual(result[0].related_task_id, 5)
        for raw in ({"ref": "0:5"}, {"ref": "-5"}, {"ref": "其他群:5"}, {"id": True},
                    {"id": "9"}, {"id": "5", "task_id": 6}, {"related_task_id": 5, "ref": "6"},
                    {"related_task_ref": "消息索引:3", "title": "确认交付对象A"}):
            self.assertIsNone(self.model._validate([{"action": "UPDATE", "priority": "high", **raw}], self.tasks, session_name=session), raw)

    def test_update_title_is_never_used_to_guess_a_target(self):
        self.assertIsNone(self.model._validate([{"action": "UPDATE", "title": "确认交付对象A", "priority": 2}], self.tasks))
        self.assertIsNone(self.model._validate([{"action": "UPDATE", "related_task_id": 5}], self.tasks))

    def test_batch_reference_normalisation_preserves_explicit_position(self):
        previous = {0: [Instruction(action="CREATE", title="确认交付对象A")]}
        result = self.model._validate([{"action": "UPDATE", "related_task_ref": " 00:00 ", "priority": "high"}], [], 1, previous)
        self.assertEqual(result[0].related_task_ref, "0:0")
        for target in ("消息索引:0", "1:0", "0:1", "-1:0"):
            self.assertIsNone(self.model._validate([{"action": "UPDATE", "related_task_ref": target, "priority": "high"}], [], 1, previous))

    def test_deadline_normalisation_and_explicit_clearing(self):
        dated = Instruction.model_validate(clean_instruction(
            {"action": "CREATE", "title": "确认交付对象A", "due_at": "2026-10-09T18:00:00"}))
        self.assertEqual(dated.due_at, "2026-10-09T10:00:00.000000+00:00")
        for deadline in ("下周三", "", False):
            self.assertIsNone(self.model._validate([{"action": "CREATE", "title": "确认交付对象A", "due_at": deadline}], []))
        cleared = self.model._validate([{"action": "UPDATE", "related_task_id": 5, "due_at": None, "owner": None}], self.tasks)[0]
        fields = cleared.model_dump(exclude_unset=True)
        self.assertIn("due_at", fields)
        self.assertIn("owner", fields)
        self.assertIsNone(fields["due_at"])

    def test_resolution_is_never_defaulted_from_incomplete_cloud_output(self):
        for extra in ({}, {"resolution": None}, {"resolution": ""}, {"resolution": {}}, {"resolution": "finished"}):
            self.assertIsNone(self.model._validate([{"action": "RESOLVE", "related_task_id": 5, **extra}], self.tasks))
        for resolution in ("completed", "cancelled"):
            self.assertEqual(self.model._validate([{"action": "RESOLVE", "related_task_id": 5, "resolution": resolution}], self.tasks)[0].resolution, resolution)

    def test_invalid_sibling_defers_the_segment_without_silent_truncation(self):
        valid = {"action": "CREATE", "title": "确认交付对象A"}
        for invalid in ({"action": "UPDATE", "related_task_id": 5},
                        {"action": "CREATE", "title": "对象" * 61},
                        {"action": "CREATE", "title": "独立对象B", "description": "条件" * 2001}):
            self.assertIsNone(self.model._validate([valid, invalid], self.tasks))

    def test_ignore_keeps_only_the_reason(self):
        ignored = Instruction.model_validate(clean_instruction(
            {"action": "IGNORE", "title": "无需处理", "priority": 1, "context_summary": "条件邀请无法确认适用"}))
        self.assertEqual((ignored.action, ignored.title, ignored.context_summary),
                         ("IGNORE", None, "条件邀请无法确认适用"))

    def test_decorative_fields_do_not_discard_valid_instructions(self):
        self.assertEqual(len(self.model._validate([{"action": "CREATE", "title": "确认交付对象A", "priority": 1, "index": 0},
                                                   {"action": "CREATE", "title": "提交独立对象B"}], self.tasks)), 2)


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
        with patch("niannian.llm.urlopen", return_value=self.response(payload)) as mocked:
            result = self.model.extract(self.message, [], self.tasks, "", "session")
        self.assertEqual(len(result), 2)
        request = json.loads(mocked.call_args.args[0].data)
        self.assertIs(request["enable_thinking"], False)
        message = user_content(mocked).find("./current_messages/message")
        self.assertIn(message.get("self"), {"true", "false"})
        self.assertEqual(message.findtext("content"), self.message["content"])

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
                             history_chars=200, history_rows=2, task_chars=120)
        largest = max(self.messages, key=lambda row: len(row["content"]))
        tasks = [{**self.tasks[0], "id": index + 1} for index in range(100)]
        with patch("niannian.llm.urlopen", return_value=self.response({"instructions": [{"action": "IGNORE"}]})) as mocked:
            tuned.extract(largest, self.messages, tasks, "画像" * 500, "session")
        request = json.loads(mocked.call_args.args[0].data)
        root = user_content(mocked)
        self.assertEqual((request["temperature"], request["max_tokens"]), (0.6, 1500))
        self.assertIs(request["enable_thinking"], False)
        self.assertEqual(root.findtext("./current_messages/message/content"), largest["content"])
        history = root.findall("./history/message")
        for row in history:
            self.assertIn(row.findtext("content"), [message["content"] for message in self.messages])
        self.assertLessEqual(sum(len(row.findtext("content")) for row in history), 200)
        self.assertLessEqual(len(history), 2)
        self.assertLessEqual(sum(rendered_task_size(task) for task in root.findall("./pending_tasks/task")), 120)
        self.assertEqual(root.findtext("./user_profile"), "画像" * 500)
        with patch("niannian.llm.urlopen", return_value=self.response({"items": []})) as mocked:
            tuned.extract_batch([self.message, self.second], [], tasks)
        self.assertEqual(json.loads(mocked.call_args.args[0].data)["max_tokens"], 3000)

    def test_batch_accepts_more_than_twenty_source_segments(self):
        items = self.messages[:21]
        self.assertEqual(len(items), 21)
        payload = {"items": [{"index": index, "instructions": [{"action": "IGNORE"}]} for index in range(21)]}
        with patch("niannian.llm.urlopen", return_value=self.response(payload)):
            answers = self.model.extract_batch(items, [], [])
        self.assertEqual(set(answers), set(range(21)))

    def test_extra_indexes_cost_only_themselves_not_the_whole_batch(self):
        # 模型给没发出去的段落也编索引时，此前整批作废：这些段落每轮重新失败，永远留在队列。
        payload = {"items": [{"index": 0, "instructions": [{"action": "CREATE", "title": "确认交付对象A"}]},
                             {"index": 1, "instructions": [{"action": "IGNORE"}]},
                             {"index": 7, "instructions": [{"action": "CREATE", "title": "越界对象"}]}]}
        with patch("niannian.llm.urlopen", return_value=self.response(payload)):
            answers = self.model.extract_batch([self.message, self.second], [], [])
        self.assertEqual(set(answers), {0, 1})
        self.assertEqual(self.model.last_diagnostics["extra_indexes"], [7])
        self.assertEqual(self.model.last_diagnostics["missing_indexes"], [])

    def test_compression_sends_full_source_and_rejects_incomplete_output(self):
        largest = max(self.messages, key=lambda row: len(row["content"]))
        self.model.budgets["new_message"] = 200
        summary = largest["content"][:150]  # Controlled model output, not a new chat.
        def response(payload, finish="stop"):
            return io.BytesIO(json.dumps({"choices": [{"finish_reason": finish,
                "message": {"content": json.dumps(payload)}}]}).encode())
        with patch("niannian.llm.urlopen", return_value=response({"summary": summary, "complete": True})) as mocked:
            self.assertEqual(self.model.compress(largest), summary)
        request = json.loads(mocked.call_args.args[0].data)
        self.assertFalse(request["enable_thinking"])
        root = user_content(mocked)
        self.assertEqual(root.findtext("source"), largest["content"])
        self.assertEqual(root.get("context_available"), "false")
        self.assertIn("上下文缺失", request["messages"][0]["content"])
        self.assertIn("例外", request["messages"][0]["content"])
        for payload, finish in (({"summary": summary, "complete": True}, "length"),
                                ({"summary": summary, "complete": False}, "stop"),
                                ({"summary": largest["content"], "complete": True}, "stop")):
            with patch("niannian.llm.urlopen", return_value=response(payload, finish)):
                self.assertIsNone(self.model.compress(largest))

    def test_history_rows_zero_sends_no_context(self):
        bare = LLMExtractor("https://example.invalid/v1", "unused", "model", history_rows=0)
        with patch("niannian.llm.urlopen", return_value=self.response({"instructions": [{"action": "IGNORE"}]})) as mocked:
            bare.extract(self.message, self.messages, [], "", "session")
        self.assertEqual(user_content(mocked).findall("./history/message"), [])

    def test_malformed_item_does_not_discard_valid_batch_siblings(self):
        payload = {"items": [{"index": 0, "instructions": [{"action": "CREATE", "title": "确认交付对象A"}]},
                             {"index": 1, "instructions": [{"action": "RESOLVE", "related_task_id": 999}]}]}
        with patch("niannian.llm.urlopen", return_value=self.response(payload)):
            result = self.model.extract_batch([self.message, self.second], [], self.tasks)
        self.assertEqual(list(result), [0])

    def test_batch_lifecycle_references_require_earlier_creation(self):
        payload = {"items": [{"index": 0, "instructions": [{"action": "CREATE", "title": "确认交付对象A"}]},
                             {"index": 1, "instructions": [{"action": "UPDATE", "related_task_ref": "0:0", "priority": "high"}]}]}
        with patch("niannian.llm.urlopen", return_value=self.response(payload)):
            result = self.model.extract_batch([self.message, self.second], [], self.tasks)
        self.assertEqual(result[1][0].related_task_ref, "0:0")
        payload["items"][1]["instructions"][0]["related_task_ref"] = "1:0"
        with patch("niannian.llm.urlopen", return_value=self.response(payload)):
            result = self.model.extract_batch([self.message, self.second], [], self.tasks)
        self.assertNotIn(1, result)

    def test_duplicate_indexes_are_rejected(self):
        payload = {"items": [{"index": 0, "instructions": [{"action": "IGNORE"}]},
                             {"index": 0, "instructions": [{"action": "CREATE", "title": "确认交付对象A"}]},
                             {"index": 22, "instructions": [{"action": "IGNORE"}]}]}
        with patch("niannian.llm.urlopen", return_value=self.response(payload)):
            self.assertEqual(self.model.extract_batch([self.message, self.second], [], self.tasks), {})

    def test_missing_endpoint_and_full_endpoint(self):
        for url in ("https://example.invalid/v1", "https://example.invalid/v1/chat/completions"):
            model = LLMExtractor(url, "unused", "any-model")
            self.assertEqual(model.endpoint, "https://example.invalid/v1/chat/completions")

    def test_transport_failure_preserves_fallback_signal(self):
        with patch("niannian.llm.urlopen", side_effect=TimeoutError):
            self.assertIsNone(self.model.extract(self.message, [], self.tasks))
        self.assertEqual((self.model.stats["requests"], self.model.stats["failures"], self.model.last_error), (1, 1, "TimeoutError"))

    def test_context_budget_uses_real_long_messages(self):
        largest = max(self.messages, key=lambda row: len(row["content"]))
        tasks = [{**self.tasks[0], "id": index + 1} for index in range(100)]
        with patch("niannian.llm.urlopen", return_value=self.response({"instructions": [{"action": "IGNORE"}]})) as mocked:
            self.model.extract(largest, self.messages, tasks)
        body = json.loads(mocked.call_args.args[0].data)
        root = user_content(mocked)
        self.assertEqual(root.findtext("./current_messages/message/content"), largest["content"])
        self.assertLessEqual(sum(len(row.findtext("content")) for row in root.findall("./history/message")),
                             HISTORY_CHARS)
        self.assertLessEqual(sum(rendered_task_size(task) for task in root.findall("./pending_tasks/task")), TASK_CHARS)
        self.assertLessEqual(body["max_tokens"], 700)

    def test_model_layer_does_not_apply_semantic_guard(self):
        answer = {"instructions": [{"action": "CREATE", "title": "办理银行卡"}]}
        with patch("niannian.llm.urlopen", return_value=self.response(answer)):
            result = self.model.extract(self.message, [], self.tasks)
        self.assertEqual([command.action for command in result], ["CREATE"])
        self.assertEqual(result[0].title, "办理银行卡")

    def test_minimal_ignore_tolerates_irrelevant_provider_nulls(self):
        with patch("niannian.llm.urlopen", return_value=self.response({"instructions": [{"action": "IGNORE", "title": None,
                                                                                         "context_summary": None, "related_task_id": 0}]})):
            result = self.model.extract(self.message, [], self.tasks)
        self.assertEqual([instruction.action for instruction in result], ["IGNORE"])

    def test_empty_instruction_list_means_ignore_not_rule_fallback(self):
        with patch("niannian.llm.urlopen", return_value=self.response({"items": [{"index": 0, "instructions": []},
                                                                                 {"index": 1, "instructions": []}]})):
            result = self.model.extract_batch([self.message, self.second], [], self.tasks)
        self.assertEqual(list(result), [0, 1])
        self.assertEqual([result[index][0].action for index in result], ["IGNORE", "IGNORE"])

    def test_configured_output_limits_do_not_discard_valid_siblings(self):
        message = next(row for row in self.messages if not row["is_self"])
        commands = [Instruction(action="CREATE", title="办理银行卡"),
                    Instruction(action="CREATE", title="确认" + "对象" * 24),
                    Instruction(action="CREATE", title="查看公示")]
        result = guard_decisions(message, commands, {"task_title_max_chars": 40})
        self.assertEqual([command.action for command in result], ["CREATE", "IGNORE", "CREATE"])
        self.assertIn("40", result[1].context_summary)
        result = guard_decisions(message, commands, {"max_tasks_per_message": 1})
        self.assertEqual([command.action for command in result], ["CREATE", "IGNORE", "IGNORE"])

    def test_configured_title_and_task_limits_reach_model_prompt(self):
        model = LLMExtractor("https://example.invalid/v1", "unused", "model",
                             task_title_max_chars=60, max_tasks_per_message=5)
        with patch("niannian.llm.urlopen", return_value=self.response({"instructions": [{"action": "IGNORE"}]})) as mocked:
            model.extract(self.message, [], [])
        request = json.loads(mocked.call_args.args[0].data)
        prompt = request["messages"][0]["content"]
        self.assertIn("最多60字", prompt)
        self.assertIn("最多新建5项", prompt)
        self.assertIsNotNone(user_content(mocked).find("./current_messages/message"))
        self.assertNotIn("identity_confident", request["messages"][1]["content"])

    def test_default_does_not_limit_instructions_to_twelve(self):
        answer = {"instructions": [{"action": "CREATE", "title": "办理银行卡"} for _ in range(13)]}
        with patch("niannian.llm.urlopen", return_value=self.response(answer)):
            result = self.model.extract(self.message, [], [])
        self.assertEqual(len(result), 13)

    def test_single_request_does_not_inherit_previous_batch_diagnostics(self):
        answer = {"items": [{"index": 0, "instructions": [{"action": "IGNORE"}]}]}
        with patch("niannian.llm.urlopen", return_value=self.response(answer)):
            self.model.extract_batch([self.message, self.second], [], [])
        self.assertEqual(self.model.last_diagnostics["missing_indexes"], [1])
        with patch("niannian.llm.urlopen", return_value=self.response({"instructions": [{"action": "IGNORE"}]})):
            self.model.extract(self.message, [], [])
        self.assertEqual(self.model.last_diagnostics, {})

    def test_relevant_current_object_precedes_newest_unrelated_tasks(self):
        real = max(self.messages, key=lambda message: len(message["content"]))
        object_name = next(iter(__import__('re').findall(r"[\u4e00-\u9fff]{4,}", real["content"])))[:8]
        tasks = [{**self.tasks[0], "id": index + 2, "title": "确认 unrelated_object_" + str(index)} for index in range(100)]
        tasks.append({**self.tasks[0], "id": 1, "title": "确认" + object_name})
        data = self.model._data(real, [], tasks, "", "")
        self.assertEqual(data["pending_tasks"][0]["id"], 1)


if __name__ == "__main__":
    unittest.main()
