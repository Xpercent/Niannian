"""模型调用诊断：完整请求与响应、校验对照、滞留队列，只用真实捕获正文回放。"""

import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from niannian.engine import Engine
from niannian.jobs import ExtractionJobs
from niannian.storage import Database, utc_now

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    path = ROOT / "data" / "initial-wechat.json"
    if not path.exists():
        raise unittest.SkipTest("缺少真实微信捕获 data/initial-wechat.json；不用合成聊天替代")
    return json.loads(path.read_text(encoding="utf-8"))


def body(payload, finish="stop"):
    return io.BytesIO(json.dumps({"choices": [{"finish_reason": finish,
                                               "message": {"content": json.dumps(payload, ensure_ascii=False)}}],
                                  "usage": {"prompt_tokens": 900, "completion_tokens": 30}}).encode())


class Config:
    def __init__(self, values):
        self.values = values

    def read(self):
        return dict(self.values)


class TraceTests(unittest.TestCase):
    """真实捕获 + 模拟传输：验证链路把发出去和收回来的东西原样留档。"""

    def setUp(self):
        self.fixture = fixture()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "trace.db")
        self.db.init()
        self.db.sync_catalog(self.fixture["account"], self.fixture["catalog"])
        self.db.execute("UPDATE sessions SET monitored=1")
        largest = max(self.fixture["messages"], key=lambda row: len(row["content"]))
        self.sid = largest["session_id"]
        self.message = largest
        self.values = {"ai_enabled": True, "ai_base_url": "https://example.invalid/v1", "ai_api_key": "test",
                       "ai_model": "provider/test-model", "ai_timeout_seconds": 5, "merge_window_seconds": 0,
                       "settle_window_seconds": 0, "batch_interval_seconds": 300}
        self.config = Config(self.values)
        self.engine = Engine(self.db, self.config)
        self.jobs = ExtractionJobs(self.db, self.engine, self.config)
        self.addCleanup(self.jobs.stop)

    def ingest(self):
        original = self.message
        return self.db.ingest({**original, "account": self.fixture["account"],
                               "created_at": (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat(),
                               "sender_key": original["metadata"].get("sender_username") or original["sender_name"]})["id"]

    def run_round(self, responses):
        """真实链路跑一轮；单个异常元素按抛出处理。请求前过滤另有规则用例覆盖。"""
        single = responses[0] if len(responses) == 1 else None
        throws = isinstance(single, BaseException) or (isinstance(single, type) and issubclass(single, BaseException))
        options = {"side_effect": responses} if len(responses) > 1 or throws else {"return_value": single}
        with patch.object(self.engine, "_gate", return_value=None), patch("niannian.llm.urlopen", **options):
            self.jobs.start(force=True)
            self.jobs.worker.join(timeout=30)
        self.assertEqual(self.jobs.latest()["status"], "completed")

    def test_round_stores_full_request_and_response(self):
        message_id = self.ingest()
        answer = {"instructions": [{"action": "CREATE", "title": "确认交付对象A"}]}
        self.run_round([body(answer)])
        run_id = self.jobs.latest()["id"]
        rows = self.db.model_call_rows(days=30)
        self.assertEqual(len(rows), 1)
        call = self.db.model_call_detail(rows[0]["id"])
        self.assertEqual(call["kind"], "single")
        self.assertEqual(call["run_id"], run_id)
        self.assertEqual(call["session_id"], self.sid)
        self.assertEqual(call["message_ids"], [message_id])
        self.assertEqual(call["status"], "ok")
        self.assertEqual(call["model"], "provider/test-model")
        self.assertEqual(call["temperature"], 0.0)
        self.assertEqual(call["finish_reason"], "stop")
        self.assertEqual(call["usage_json"]["prompt_tokens"], 900)
        # 请求正文里必须能看到模型实际收到的原文与系统指令，不含密钥与接口地址。
        sent = call["request_json"]
        sent_text = json.dumps(sent, ensure_ascii=False)
        self.assertNotIn("api_key", sent_text)
        self.assertNotIn("Bearer", sent_text)
        self.assertNotIn("example.invalid", sent_text)
        self.assertEqual(json.loads(sent["messages"][1]["content"])["new_message"]["content"], self.message["content"])
        self.assertIn("只提炼微信账号本人", sent["messages"][0]["content"])
        self.assertIs(sent["enable_thinking"], False)
        self.assertIn("确认交付对象A", call["response_text"])
        self.assertEqual(call["parsed_json"], answer)
        self.assertGreaterEqual(call["duration_seconds"], 0)
        self.assertGreater(call["input_chars"], 1000)
        self.assertEqual(call["output_chars"], len(call["response_text"]))
        self.assertEqual(call["segments"], 1)
        self.assertEqual(call["answered_items"], 1)
        stats = call["stats_json"]
        self.assertEqual(stats["history_budget"], 1200)
        self.assertEqual(len(stats["segment_chars"]), 1)
        counts = self.db.model_call_counts([run_id])
        self.assertEqual(counts[run_id]["calls"], 1)
        self.assertEqual(counts[run_id]["single_calls"], 1)

    def test_long_segment_compression_and_extract_each_record_a_call(self):
        self.values["new_message_chars"] = 80
        self.ingest()
        summary = self.message["content"][:60]
        responses = [body({"summary": summary, "complete": True}),
                     body({"instructions": [{"action": "IGNORE"}]})]
        self.run_round(responses)
        rows = self.db.model_call_rows(days=30)
        self.assertEqual([row["kind"] for row in rows][::-1], ["compress", "single"])
        compression = self.db.model_call_detail(rows[1]["id"])
        source_text = json.loads(compression["request_json"]["messages"][1]["content"])
        self.assertEqual(source_text["source_text"], self.message["content"])
        self.assertEqual(compression["outcome_json"]["items"][0]["summary"], summary)
        self.assertEqual(compression["message_ids"], [compression["messages"][0]["id"]])
        extract = self.db.model_call_detail(rows[0]["id"])
        self.assertTrue(extract["stats_json"]["compressed_segments"][0])
        self.assertEqual(extract["segment_summary"], summary)
        self.assertEqual(json.loads(extract["request_json"]["messages"][1]["content"])["new_message"]["content"], summary)

    def test_guard_rejection_is_visible_with_reason_and_landing(self):
        message_id = self.ingest()
        # 输出限制由引擎最终校验；真实原文不被改写，诊断保留拒绝原因。
        self.values["task_title_max_chars"] = 10
        answer = {"instructions": [{"action": "CREATE", "title": "确认交付对象A" * 3}]}
        self.run_round([body(answer)])
        rows = self.db.model_call_rows(days=30, guarded=True)
        self.assertEqual(len(rows), 1)
        detail = self.db.model_call_detail(rows[0]["id"])
        item = detail["outcome_json"]["items"][0]
        self.assertEqual(item["proposed"][0]["action"], "CREATE")
        self.assertEqual(item["final"][0]["action"], "IGNORE")
        self.assertIn("配置上限", item["final"][0]["context_summary"])
        self.assertEqual(detail["guard_changes"], 1)
        self.assertEqual(detail["ignored"][0]["message_ids"], [message_id])
        self.assertIn("配置上限", detail["ignored"][0]["reason"])
        issues = self.db.model_call_issues(30)
        self.assertTrue(any("配置上限" in row["reason"] for row in issues["guard_reasons"]), issues["guard_reasons"])
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM tasks")["n"], 0)

    def test_transport_failure_keeps_request_and_error_for_next_round(self):
        self.ingest()
        self.run_round([TimeoutError])
        rows = self.db.model_call_rows(days=30, status="error")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["error"], "TimeoutError")
        detail = self.db.model_call_detail(rows[0]["id"])
        self.assertIsNone(detail["parsed_json"])
        self.assertIn("只提炼微信账号本人", detail["request_json"]["messages"][0]["content"])
        self.assertEqual(detail["status"], "error")
        self.assertIn("content", detail["request_json"]["messages"][1]["content"])
        self.assertEqual(self.db.one("SELECT processed FROM messages")["processed"], 0)
        backlog = self.db.backlog()
        self.assertEqual(len(backlog["messages"]), 1)
        self.assertEqual(backlog["messages"][0]["calls"], 1)
        self.assertEqual(backlog["messages"][0]["failures"], 1)
        self.assertEqual(self.db.model_call_issues(30)["buckets"][0]["type"], "request_error")

    def test_truncated_output_is_flagged_not_swallowed(self):
        self.ingest()
        self.run_round([body({"instructions": [{"action": "IGNORE"}]}, finish="length")])
        rows = self.db.model_call_rows(days=30, truncated=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "single")
        self.assertEqual(self.db.model_call_issues(30)["buckets"][0]["type"], "output_truncated")

    def test_retention_drops_oldest_diagnostic_rows_only(self):
        self.db.execute("INSERT INTO runs VALUES('r1','completed',?,?)", (json.dumps({"id": "r1"}), utc_now()))
        with patch("niannian.storage.MODEL_CALL_KEEP", 3):
            for index in range(5):
                self.db.log_model_call({"kind": "single", "status": "ok", "run_id": "r1",
                                        "session_id": self.sid, "message_ids": [index],
                                        "request_json": "{\"n\":" + str(index) + "}"})
        rows = self.db.model_call_rows(days=0)
        self.assertEqual(len(rows), 3)
        self.assertEqual([row["message_ids"][0] for row in rows], [4, 3, 2])
        self.assertNotIn("request_json", rows[0])
        self.assertEqual(self.db.model_call_retention()["keep"], 1200)

    def test_empty_round_records_nothing(self):
        self.run_round([AssertionError("没有待提炼消息时不应请求模型")])
        self.assertEqual(self.db.model_call_rows(days=30), [])


class DiagnosticsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.started = utc_now()
        self.app = create_app_for_test(Path(self.temp.name))
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.db = self.app.state.container.db
        self.db.sync_catalog("account-a", [{"id": "sid-1", "username": "sid-1", "name": "测试会话"}])
        self.db.execute("UPDATE sessions SET monitored=1")
        self.message_id = self.db.ingest({"session_id": "sid-1", "account": "account-a", "sender_name": "同学甲",
                                          "content": "记得提交实践登记表", "is_self": False,
                                          "created_at": self.started, "source_id": "diagnostics-1"})["id"]
        # model_calls.run_id 有外键，轮次行必须先存在。
        self.db.execute("INSERT INTO runs VALUES('run-1','completed',?,?)",
                        (json.dumps({"id": "run-1", "status": "completed", "started_at": self.started,
                                     "finished_at": self.started, "trigger": "manual",
                                     "result": {"items": 1, "processed": 1, "created": 1}}), self.started))
        self.call_id = self.db.log_model_call({
            "kind": "batch", "status": "ok", "run_id": "run-1", "session_id": "sid-1",
            "message_ids": [self.message_id], "segments": 1, "started_at": self.started, "duration_seconds": 1.5,
            "model": "provider/test-model", "max_tokens": 1200, "finish_reason": "stop",
            "request_json": json.dumps({"messages": [{"role": "user", "content": "记得提交实践登记表"}]}),
            "response_text": "{\"items\":[]}", "parsed_json": {"items": []}, "stats_json": {"history_sent": 0}})
        self.db.record_model_call_outcome(
            self.call_id,
            {"items": [{"index": 0, "message_ids": [self.message_id],
                        "proposed": [{"action": "CREATE", "title": "提交实践登记表"}],
                        "final": [{"action": "CREATE", "title": "提交实践登记表"}]}],
             "diagnostics": {"input_count": 1, "missing_indexes": []}},
            {"answered": 1, "guard": 0, "rejected": 0, "missing": 0})
        self.task_id = self.db.execute("INSERT INTO tasks(session_id,title,description,source_message_ids,created_at,"
                                       "updated_at) VALUES(?,?,?,?,?,?)",
                                       ("sid-1", "提交实践登记表", "来自诊断测试", json.dumps([self.message_id]),
                                        self.started, utc_now()))
        self.db.execute("INSERT INTO task_events(task_id,action,source,message_ids,detail_json,created_at) "
                        "VALUES(?,?,?,?,?,?)", (self.task_id, "CREATE", "llm", json.dumps([self.message_id]),
                                                "{}", utc_now()))
        # 轮次时间窗要覆盖上面的落库事件，落库后再收口 finished_at。
        self.db.execute("UPDATE runs SET snapshot=json_set(snapshot,'$.finished_at',?),updated_at=? WHERE id='run-1'",
                        (utc_now(), utc_now()))

    def test_runs_endpoint_attaches_call_counts_without_secrets(self):
        payload = self.client.get("/api/diagnostics/runs?days=30").json()
        self.assertEqual(payload["runs"][0]["id"], "run-1")
        self.assertEqual(payload["runs"][0]["calls"]["calls"], 1)
        self.assertEqual(payload["runs"][0]["calls"]["batch_calls"], 1)
        self.assertEqual(payload["retention"]["keep"], 1200)
        self.assertEqual(payload["queued"], 1)
        self.assertEqual(payload["runs"][0]["created"], 1)
        text = json.dumps(payload, ensure_ascii=False)
        for forbidden in ("api_key", "base_url", "Bearer", "siliconflow", "example.invalid"):
            self.assertNotIn(forbidden, text)

    def test_run_detail_returns_calls_and_landing_ledger(self):
        payload = self.client.get("/api/diagnostics/runs/run-1").json()
        self.assertEqual(payload["run"]["id"], "run-1")
        self.assertEqual([call["id"] for call in payload["calls"]], [self.call_id])
        self.assertEqual(payload["calls"][0]["session_name"], "测试会话")
        self.assertEqual([event["action"] for event in payload["ledger"]["events"]], ["CREATE"])

    def test_call_detail_exposes_original_message_and_outcome(self):
        detail = self.client.get(f"/api/diagnostics/calls/{self.call_id}").json()
        self.assertEqual(detail["session_name"], "测试会话")
        self.assertEqual([row["content"] for row in detail["messages"]], ["记得提交实践登记表"])
        self.assertEqual(detail["outcome_json"]["items"][0]["final"][0]["title"], "提交实践登记表")
        self.assertEqual([event["task_id"] for event in detail["events"]],
                         [self.db.one("SELECT id FROM tasks")["id"]])
        self.assertEqual(detail["segments"], 1)

    def test_missing_call_reports_retention_limit(self):
        self.assertEqual(self.client.get("/api/diagnostics/calls/9999").status_code, 404)

    def test_call_search_filters_by_message_kind_and_keyword(self):
        self.assertEqual([row["id"] for row in self.client.get(
            f"/api/diagnostics/calls?message_id={self.message_id}").json()["calls"]], [self.call_id])
        self.assertEqual(self.client.get("/api/diagnostics/calls?q=实践登记表").json()["calls"][0]["id"], self.call_id)
        self.assertEqual(self.client.get("/api/diagnostics/calls?q=不存在的关键词").json()["calls"], [])
        self.assertEqual(self.client.get("/api/diagnostics/calls?kind=compress").json()["calls"], [])
        self.assertEqual(self.client.get("/api/diagnostics/calls?guarded=true").json()["calls"], [])
        self.assertEqual(self.client.get("/api/diagnostics/calls?kind=sideways").status_code, 422)

    def test_backlog_lists_unprocessed_message_with_touch_history(self):
        self.db.execute("UPDATE messages SET processed=0")
        payload = self.client.get("/api/diagnostics/backlog").json()
        self.assertEqual(payload["queued"], 1)
        self.assertEqual(payload["sessions"][0]["messages"], 1)
        self.assertEqual(payload["messages"][0]["calls"], 1)
        self.assertTrue(payload["settle_window_seconds"] >= 0)


def create_app_for_test(root: Path):
    from niannian.api import create_app
    return create_app(root, live_wechat=False)


if __name__ == "__main__":
    unittest.main()
