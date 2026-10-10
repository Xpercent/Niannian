"""轮次串行化、取消、恢复；不用聊天测试数据。"""

import tempfile
import unittest
from pathlib import Path
from threading import Event

from niannian.jobs import ExtractionJobs
from niannian.storage import Database


class Config:
    def read(self):
        return {"ai_timeout_seconds": 5, "batch_interval_seconds": 300}


class BlockingEngine:
    def __init__(self):
        self.started, self.release = Event(), Event()
        self.calls = 0

    def process(self, force, session_id, progress, should_stop):
        self.calls += 1
        self.started.set()
        self.release.wait(3)
        progress(0, 0, None)
        return {"cancelled": int(should_stop()), "processed": 0}


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "jobs.db")
        self.db.init()
        self.engine = BlockingEngine()
        self.jobs = ExtractionJobs(self.db, self.engine, Config())

    def tearDown(self):
        self.engine.release.set()
        self.jobs.stop()
        self.temp.cleanup()

    def test_single_run_and_cancel_persist(self):
        first = self.jobs.start()
        self.assertTrue(self.engine.started.wait(2))
        duplicate = self.jobs.start()
        self.assertEqual(duplicate["id"], first["id"])
        self.jobs.cancel(first["id"])
        self.assertTrue(self.jobs.get(first["id"])["cancel_requested"])
        self.engine.release.set()
        self.jobs.worker.join(timeout=3)
        self.assertEqual(self.engine.calls, 1)
        self.assertEqual(self.jobs.latest()["status"], "cancelled")

    def test_restart_marks_interrupted_run_retriable(self):
        self.jobs._persist({"id": "interrupted", "status": "running", "total": 10, "done": 3})
        self.jobs.recover()
        self.assertEqual(self.jobs.get("interrupted")["status"], "failed")
        self.assertIn("未处理消息保留", self.jobs.get("interrupted")["error"])

    def test_worker_error_is_visible_in_persistent_result(self):
        def failed(**kwargs):
            raise RuntimeError("controlled engine failure")
        self.engine.process = failed
        run = self.jobs.start()
        self.jobs.worker.join(timeout=3)
        self.assertEqual(self.jobs.get(run["id"])["status"], "failed")
        self.assertIn("controlled engine failure", self.jobs.get(run["id"])["error"])
