"""处理指标只汇总真实轮次记录、任务事件与过滤记录，不估算缺样本的比率。"""

import tempfile
import unittest
import json
from pathlib import Path

from fastapi.testclient import TestClient

from niannian.api import create_app
from niannian.engine import Engine
from niannian.jobs import ExtractionJobs
from niannian.storage import Database
from support import ingest_capture


class Config:
    def __init__(self, values):
        self.values = values

    def read(self):
        return dict(self.values)


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "metrics.db")
        self.db.init()
        payloads, ingested = ingest_capture(self.db, limit=400)
        self.assertGreater(ingested["inserted"], 0)
        self.db.execute("UPDATE sessions SET monitored=1")
        # 关闭云端：整轮走本地判定，仍然是真实捕获数据的真实处理过程。
        values = {"ai_enabled": False, "ai_base_url": "", "ai_api_key": "", "ai_model": "本地判定",
                  "ai_timeout_seconds": 5, "merge_window_seconds": 60, "settle_window_seconds": 0,
                  "batch_interval_seconds": 300}
        self.engine = Engine(self.db, Config(values))
        self.jobs = ExtractionJobs(self.db, self.engine, Config(values))
        self.jobs.start(force=True)
        self.jobs.worker.join(timeout=60)
        self.assertEqual(self.jobs.latest()["status"], "completed")

    def tearDown(self):
        self.jobs.stop()

    def test_real_round_is_aggregated(self):
        result = self.engine.last_result
        metrics = self.db.processing_metrics(30)
        summary = metrics["summary"]
        self.assertEqual(summary["rounds"], 1)
        self.assertEqual(summary["active_rounds"], 1)
        self.assertEqual(summary["rounds_completed"], 1)
        self.assertEqual(summary["items"], result["items"])
        self.assertEqual(summary["processed"], result["processed"])
        self.assertEqual(summary["created"], result["created"])
        self.assertEqual(summary["rule_decided"], result["items"])
        self.assertEqual(summary["llm_decided"], 0)
        self.assertEqual(summary["model_requests"], 0)
        remaining = self.db.one("SELECT COUNT(*) n FROM messages m JOIN sessions s ON s.id=m.session_id "
                                "WHERE m.processed=0 AND s.monitored=1")["n"]
        self.assertEqual(metrics["queued"], remaining)
        self.assertEqual(summary["processed"] + remaining, self.db.one("SELECT COUNT(*) n FROM messages")["n"])
        self.assertEqual(len(metrics["rounds"]), 1)
        self.assertIsNotNone(metrics["rounds"][0]["duration_seconds"])
        self.assertGreaterEqual(metrics["rounds"][0]["duration_seconds"], 0)

    def test_timing_summary_covers_only_workload_rounds(self):
        summary = self.db.processing_metrics(30)["summary"]
        self.assertIsNotNone(summary["average_seconds"])
        self.assertEqual(summary["average_seconds"], summary["median_seconds"])
        self.assertEqual(summary["slowest_seconds"], summary["fastest_seconds"])
        self.assertEqual(summary["model_seconds"], 0.0)
        self.assertIsNone(summary["model_failure_rate"])
        self.assertEqual(summary["local_fallback_rate"], 1.0)
        self.assertGreaterEqual(summary["seconds_per_item"], 0)

    def test_decisions_and_filters_come_from_the_same_round(self):
        metrics = self.db.processing_metrics(30)
        summary = metrics["summary"]
        landed = summary["created"] + summary["updated"] + summary["resolved"] + summary["merged"]
        self.assertGreaterEqual(landed + summary["ignored"], summary["items"])
        self.assertLessEqual(summary["pre_filtered"], summary["items"])
        reasons = metrics["ignored_reasons"]
        # 每条“无需行动”都留下真实原因；界面只列出现最多的 10 类。
        self.assertLessEqual(len(reasons), 10)
        self.assertLessEqual(sum(row["count"] for row in reasons), summary["ignored"])
        if summary["ignored"]:
            self.assertTrue(reasons)
        self.assertTrue(all(row["reason"] for row in reasons))

    def test_window_excludes_older_rounds(self):
        self.assertEqual(self.db.processing_metrics(0)["summary"]["rounds"], 1)
        self.assertEqual(self.db.processing_metrics(365)["summary"]["rounds"], 1)
        empty = self.db.processing_metrics(1)
        self.assertEqual(empty["summary"]["rounds"], 1)
        self.db.execute("UPDATE runs SET snapshot=json_set(snapshot,'$.finished_at','2000-01-01T00:00:00+00:00')")
        self.assertEqual(self.db.processing_metrics(30)["summary"]["rounds"], 0)
        self.assertEqual(self.db.processing_metrics(0)["summary"]["rounds"], 1)


class EmptyMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "empty.db")
        self.db.init()

    def test_no_rounds_returns_empty_not_zero_division(self):
        summary = self.db.processing_metrics(30)["summary"]
        self.assertEqual(summary["rounds"], 0)
        for key in ("average_seconds", "median_seconds", "p95_seconds", "model_share", "seconds_per_item",
                    "model_failure_rate", "local_fallback_rate", "pre_filter_rate", "items_per_batch",
                    "batch_share", "slowest_seconds", "fastest_seconds", "last_round_at",
                    "last_active_round_at"):
            self.assertIsNone(summary[key], key)
        self.assertEqual(self.db.processing_metrics(30)["daily"], [])


class MetricsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(Path(self.temp.name), live_wechat=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_empty_database_reports_no_rounds(self):
        body = self.client.get("/api/metrics?days=7").json()
        self.assertEqual(body["metrics"]["summary"]["rounds"], 0)
        self.assertIsNone(body["metrics"]["summary"]["average_seconds"])
        self.assertEqual(body["metrics"]["rounds"], [])
        self.assertEqual(body["stats"]["queued"], 0)

    def test_model_block_excludes_credentials(self):
        body = self.client.get("/api/metrics").json()
        self.assertEqual(set(body["model"]), {"name", "enabled", "timeout_seconds", "temperature",
                                             "max_tokens_batch", "max_tokens_single", "batch_interval_seconds",
                                             "settle_window_seconds", "merge_window_seconds", "batch_char_budget",
                                             "new_message_chars", "history_chars", "history_rows", "task_chars"})
        self.assertNotIn("api_key", json.dumps(body, ensure_ascii=False))
        self.assertNotIn("base_url", json.dumps(body, ensure_ascii=False))

    def test_window_parameter_is_validated(self):
        self.assertEqual(self.client.get("/api/metrics?days=999").status_code, 422)
        self.assertEqual(self.client.get("/api/metrics?days=-1").status_code, 422)
        self.assertEqual(self.client.get("/api/metrics?days=0").status_code, 200)


if __name__ == "__main__":
    unittest.main()
