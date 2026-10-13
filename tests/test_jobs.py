"""轮次串行化、取消、恢复；不用聊天测试数据。"""

import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from niannian.jobs import ExtractionJobs
from niannian.storage import Database


class Config:
    def read(self):
        return {"ai_timeout_seconds": 5, "batch_interval_seconds": 300}


class BlockingEngine:
    def __init__(self):
        self.started, self.release = Event(), Event()
        self.calls = 0

    def has_work(self, values=None):
        return True

    def process(self, force, session_id, run_id, progress, should_stop):
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

    def test_scheduler_waits_full_interval_after_completion(self):
        self.engine.release.set()
        with patch("niannian.jobs.time.monotonic", return_value=1000):
            self.jobs.start(force=False)
            self.jobs.worker.join(timeout=3)
        self.assertEqual(self.jobs.last_finished, 1000)
        with patch.object(self.jobs.stop_event, "wait", side_effect=[False, False, True]), \
                patch("niannian.jobs.time.monotonic", side_effect=[1299, 1300]), \
                patch.object(self.jobs, "_start_locked") as start:
            self.jobs._schedule()
        start.assert_called_once_with(False)

    def test_scheduler_skips_round_without_pending_messages(self):
        self.jobs.last_finished = 1000
        self.engine.has_work = lambda values=None: False
        with patch.object(self.jobs.stop_event, "wait", side_effect=[False, True]), \
                patch("niannian.jobs.time.monotonic", side_effect=[1300, 1500]), \
                patch.object(self.jobs, "_start_locked") as start:
            self.jobs._schedule()
        start.assert_not_called()
        self.assertEqual(self.jobs.last_finished, 1500)
        self.assertIsNone(self.jobs.latest())

    def test_scheduler_preserves_interval_when_manual_round_finishes_during_work_check(self):
        self.jobs.last_finished = 1000
        self.engine.release.set()
        def check(values=None):
            self.jobs.start()
            self.jobs.worker.join(timeout=3)
            return True
        self.engine.has_work = check
        with patch("niannian.jobs.time.monotonic", return_value=1300):
            self.jobs._schedule_once()
        self.assertEqual(self.engine.calls, 1)
        self.assertEqual(self.jobs.latest()["trigger"], "manual")
        self.assertEqual(self.jobs.last_finished, 1300)

    def test_empty_check_cannot_overwrite_manual_completion_anchor(self):
        self.jobs.last_finished = 1000
        self.engine.release.set()
        def check(values=None):
            self.jobs.start()
            self.jobs.worker.join(timeout=3)
            return False
        self.engine.has_work = check
        with patch("niannian.jobs.time.monotonic", side_effect=[1300, 1400]):
            self.jobs._schedule_once()
        self.assertEqual(self.jobs.last_finished, 1400)
        self.assertEqual(self.engine.calls, 1)

    def test_manual_round_is_recorded_even_without_work(self):
        self.engine.has_work = lambda values=None: False
        self.engine.process = lambda **kwargs: {"items": 0, "processed": 0}
        run = self.jobs.start(force=True)
        self.jobs.worker.join(timeout=3)
        stored = self.jobs.get(run["id"])
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["trigger"], "manual")
        self.assertEqual(stored["result"]["items"], 0)

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
