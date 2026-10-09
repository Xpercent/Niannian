"""真实捕获回放的 HTTP 契约与持久化。"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from shiguang.api import create_app
from support import ingest_capture, task_record


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(self.root, live_wechat=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.db = self.app.state.container.db

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def make_task(self):
        self.db.sync_catalog("account", [{"id": "account:session", "username": "session", "name": "接口验证"}])
        return task_record(self.db, "account:session")

    def test_settings_no_key_echo_validation_and_restart(self):
        result = self.client.patch("/api/settings", json={"batch_interval_seconds": 120, "ai_api_key": "configuration-test-token"})
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("configuration-test-token", result.text)
        self.assertTrue(result.json()["ai_key_configured"])
        self.assertEqual(self.client.patch("/api/settings", json={"db_path": "elsewhere.db"}).status_code, 400)
        self.assertEqual(self.client.patch("/api/settings", json={"settle_window_seconds": -1}).status_code, 422)
        self.assertEqual(self.client.patch("/api/settings", json={"unrecognized": 1}).status_code, 422)
        self.assertEqual(self.client.patch("/api/settings", json={}).status_code, 400)
        with TestClient(create_app(self.root, live_wechat=False)) as fresh:
            self.assertEqual(fresh.get("/api/settings").json()["batch_interval_seconds"], 120)

    def test_task_edit_conflict_refresh_and_clear_due(self):
        task_id = self.make_task()
        url = f"/api/tasks/{task_id}"
        result = self.client.patch(url, json={"version": 1, "title": "版本更新验证", "due_at": "2026-10-10T18:00:00+08:00"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.patch(url, json={"version": 1, "status": "completed"}).status_code, 409)
        self.assertEqual(self.client.patch(url, json={"version": 2, "due_at": "2026-10-10"}).status_code, 422)
        self.assertEqual(self.client.patch(url, json={"version": 2, "title": "   "}).status_code, 422)
        self.assertEqual(self.client.patch(url, json={"version": 2, "status": "completed", "due_at": None}).status_code, 200)
        fresh = self.client.get(url).json()
        self.assertEqual(fresh["status"], "completed")
        self.assertIsNone(fresh["due_at"])
        self.assertEqual(len(fresh["events"]), 2)
        self.assertEqual(self.client.patch(url, json={"version": 3, "status": None}).status_code, 422)

    def test_rules_crud_invalid_pattern_keeps_prior(self):
        before = self.client.get("/api/rules").json()
        self.assertEqual(self.client.post("/api/rules", json={"name": "语法校验", "pattern": "[", "action": "ignore"}).status_code, 422)
        self.assertEqual(self.client.get("/api/rules").json(), before)
        result = self.client.post("/api/rules", json={"name": "语法校验", "pattern": "^$", "action": "ignore"})
        self.assertEqual(result.status_code, 201)
        rule_id = result.json()["id"]
        self.assertEqual(self.client.patch(f"/api/rules/{rule_id}", json={"enabled": False}).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/rules/{rule_id}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/rules/{rule_id}").status_code, 404)

    def test_actual_messages_pagination_read_and_export(self):
        ingest_capture(self.db, limit=100)
        sessions = self.client.get("/api/sessions").json()
        for session in sessions:
            sid = session["id"]
            result = self.client.get(f"/api/sessions/{sid}/messages", params={"limit": 2})
            self.assertEqual(result.status_code, 200)
            self.assertIn("metadata", result.json()["items"][0])
            self.assertEqual(self.client.post(f"/api/sessions/{sid}/read").json()["unread"], 0)
            self.assertEqual(self.client.patch(f"/api/sessions/{sid}", json={"monitored": True}).status_code, 200)
        exported = self.client.get("/api/export").json()
        self.assertEqual(len(exported["messages"]), self.db.stats()["messages"])
        self.assertEqual(self.client.get("/api/export?format=md").status_code, 200)
        self.assertEqual(self.client.get("/api/export?format=bad").status_code, 422)
        self.assertEqual(self.client.post("/api/backup").status_code, 200)

    def test_no_manual_or_demo_ingestion_and_boundary(self):
        self.assertEqual(self.client.get("/api/health").json()["stats"]["messages"], 0)
        self.assertEqual(self.client.post("/api/messages", json={}).status_code, 404)
        self.assertEqual(self.client.get("/api/sessions/not-found/messages").status_code, 404)
        self.assertEqual(self.client.get("/media/..%2F..%2Fconfig.json").status_code, 404)
        self.assertEqual(self.client.get("/assets/..%2F..%2Fconfig.json").status_code, 404)

    def test_profile_readback(self):
        self.assertEqual(self.client.put("/api/profile", json={"markdown": "# 用户画像\n"}).status_code, 200)
        self.assertEqual(self.client.get("/api/profile").json()["markdown"], "# 用户画像\n")
        self.assertEqual(self.client.put("/api/profile", json={"markdown": "x" * 30001}).status_code, 422)
