"""真实摄入回放与任务事务不变量。"""

import copy
import json
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from shiguang.storage import ConflictError, Database, canonical_time
from support import ingest_capture, task_record


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "app.db")
        self.db.init()

    def tearDown(self):
        self.temp.cleanup()

    def test_real_replay_deduplicates_without_unread_growth(self):
        payloads, initial = ingest_capture(self.db)
        self.assertGreater(initial["inserted"], 0)
        before = self.db.sessions()
        replay = self.db.ingest_batch(payloads)
        self.assertEqual(replay["inserted"], 0)
        self.assertEqual(replay["duplicates"], len(payloads))
        self.assertEqual(before, self.db.sessions())

    def test_checkpoint_and_messages_rollback_together(self):
        payloads, _ = ingest_capture(self.db)
        self.db.execute("DELETE FROM ingestion_keys")
        self.db.execute("DELETE FROM messages")
        corrupt = copy.deepcopy(payloads[-1])
        corrupt["session_id"] = "invalid-foreign-session"
        sid = payloads[0]["session_id"]
        checkpoint = {"account": payloads[0]["account"], "session_id": sid, "sort_seq": 100}
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.ingest_batch([payloads[0], corrupt], checkpoint)
        self.assertEqual(self.db.stats()["messages"], 0)
        self.assertIsNone(self.db.one("SELECT * FROM wechat_checkpoints"))
        result = self.db.ingest_batch(payloads, checkpoint)
        self.assertEqual(result["inserted"], len(payloads))
        self.db.ingest_batch([], {**checkpoint, "sort_seq": 1})
        self.assertEqual(self.db.one("SELECT * FROM wechat_checkpoints")["sort_seq"], 100)

    def test_older_history_does_not_move_last_message_back(self):
        payloads, _ = ingest_capture(self.db, limit=100)
        for session in self.db.sessions():
            row = self.db.one("SELECT MAX(created_at) at FROM messages WHERE session_id=?", (session["id"],))
            self.assertEqual(session["last_message_at"], row["at"])

    def test_cursor_replay_has_no_omissions_or_duplicates(self):
        ingest_capture(self.db, limit=100)
        for session in self.db.sessions():
            collected, cursor = [], None
            while True:
                page = self.db.message_page(session["id"], 3, cursor)
                collected.extend(row["id"] for row in page["items"])
                if not page["has_more"]:
                    break
                cursor = page["before_id"]
            self.assertEqual(len(collected), session["message_count"])
            self.assertEqual(len(set(collected)), len(collected))
            with self.assertRaises(ValueError):
                self.db.message_page(session["id"], 3, -1)

    def test_optimistic_update_audit_and_restore(self):
        self.db.sync_catalog("account", [{"id": "account:session", "username": "session", "name": "事务验证"}])
        task_id = task_record(self.db, "account:session")
        updated = self.db.update_task(task_id, {"status": "completed"}, 1)
        self.assertEqual(updated["version"], 2)
        self.assertIsNotNone(updated["completed_at"])
        with self.assertRaises(ConflictError):
            self.db.update_task(task_id, {"status": "cancelled"}, 1)
        restored = self.db.update_task(task_id, {"status": "pending", "due_at": None}, 2)
        self.assertIsNone(restored["completed_at"])
        self.assertEqual(len(restored["events"]), 2)

    def test_wal_backup_is_readable_while_writer_connection_open(self):
        ingest_capture(self.db)
        with self.db.connect():
            result = self.db.backup()
        path = self.db.path.parent / "backups" / result["file"]
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], self.db.stats()["messages"])
        before = self.db.stats()
        exported = self.db.export()
        self.assertEqual(len(exported["messages"]), before["messages"])
        self.assertEqual(before, self.db.stats())

    def test_canonical_utc_comparison_and_index(self):
        self.assertEqual(canonical_time("2026-10-09T08:00:00+08:00"), canonical_time("2026-10-09T00:00:00Z"))
        plan = self.db.rows("EXPLAIN QUERY PLAN SELECT * FROM messages WHERE session_id=? ORDER BY created_at DESC,id DESC LIMIT 40", ("x",))
        self.assertTrue(any("idx_message_cursor" in r["detail"] for r in plan))
