"""SQLite 仓储：消息/水位原子提交、乐观任务更新、一致性快照。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

SHANGHAI = timezone(timedelta(hours=8))


def canonical_time(value: str | datetime | None = None) -> str:
    at = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00")) if value else datetime.now(timezone.utc)
    if not at.tzinfo:
        at = at.replace(tzinfo=SHANGHAI)
    return at.astimezone(timezone.utc).isoformat(timespec="microseconds")


def utc_now() -> str:
    return canonical_time()


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, account TEXT NOT NULL DEFAULT '', username TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL, avatar TEXT, monitored INTEGER NOT NULL DEFAULT 0,
    unread INTEGER NOT NULL DEFAULT 0, last_message_at TEXT, created_at TEXT NOT NULL,
    UNIQUE(account,username)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id),
    account TEXT NOT NULL DEFAULT '', sender_key TEXT NOT NULL DEFAULT '',
    sender_name TEXT NOT NULL, content TEXT NOT NULL, message_type TEXT NOT NULL DEFAULT 'text',
    is_self INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, inserted_at TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0, metadata_json TEXT NOT NULL DEFAULT '{}',
    source_id TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_message_cursor ON messages(session_id,created_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_message_queue ON messages(processed,created_at,id);
CREATE TABLE IF NOT EXISTS ingestion_keys (
    source_id TEXT PRIMARY KEY, message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id),
    title TEXT NOT NULL, description TEXT NOT NULL, owner TEXT, priority TEXT NOT NULL DEFAULT 'medium',
    due_at TEXT, status TEXT NOT NULL DEFAULT 'pending', source_message_ids TEXT NOT NULL DEFAULT '[]',
    context_summary TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    completed_at TEXT, version INTEGER NOT NULL DEFAULT 1,
    CHECK(status IN ('pending','completed','cancelled')), CHECK(priority IN ('high','medium','low'))
);
CREATE INDEX IF NOT EXISTS idx_task_status_due ON tasks(status,due_at);
CREATE INDEX IF NOT EXISTS idx_task_session ON tasks(session_id,status);
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    action TEXT NOT NULL, source TEXT NOT NULL, message_ids TEXT NOT NULL DEFAULT '[]',
    detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_event ON task_events(task_id,id);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    pattern TEXT NOT NULL, action TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, CHECK(action IN ('ignore','extract_task'))
);
CREATE TABLE IF NOT EXISTS ignored_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
    message_ids TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ignored_session ON ignored_messages(session_id,id);
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wechat_checkpoints (
    account TEXT NOT NULL, session_id TEXT NOT NULL, sort_seq INTEGER NOT NULL,
    updated_at TEXT NOT NULL, PRIMARY KEY(account,session_id)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


class ConflictError(Exception):
    pass


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO schema_version VALUES(1,?)", (utc_now(),))
            if not conn.execute("SELECT 1 FROM app_settings WHERE key='rules_initialized'").fetchone():
                conn.executemany("INSERT INTO rules(name,description,pattern,action,created_at) VALUES(?,?,?,?,?)", [
                    ("明确请求", "本地降级时识别行动请求", r"请|麻烦|帮我|需要|给我|补齐|提交|确认|检查|整理", "extract_task", utc_now()),
                    ("纯问候", "整条消息是问候时忽略，避免误伤正文", r"^(早安|晚安|早上好|你好|哈哈+|谢谢|收到啦)[！!。 .\s]*$", "ignore", utc_now()),
                ])
                conn.execute("INSERT INTO app_settings VALUES('rules_initialized','true')")

    def rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self.connect() as conn:
            return conn.execute(sql, params).lastrowid or 0

    def runtime(self, key: str, default=None):
        item = self.one("SELECT value FROM app_settings WHERE key=?", (key,))
        return json.loads(item["value"]) if item else default

    def set_runtime(self, key: str, value) -> None:
        self.execute("INSERT INTO app_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))

    def sync_catalog(self, account: str, items: list[dict]) -> None:
        """目录同步保留监控选择、未读和已采集的消息时间。"""
        with self.connect() as conn:
            for item in items:
                sid = item["id"]
                username = item.get("username", sid)
                name = item.get("name") or username
                latest = canonical_time(item["last_message_at"]) if item.get("last_message_at") else None
                conn.execute("INSERT INTO sessions(id,account,username,name,avatar,last_message_at,created_at) VALUES(?,?,?,?,?,?,?) "
                             "ON CONFLICT(id) DO UPDATE SET name=excluded.name,avatar=excluded.avatar,"
                             "last_message_at=CASE WHEN excluded.last_message_at IS NOT NULL AND (sessions.last_message_at IS NULL OR sessions.last_message_at<excluded.last_message_at) THEN excluded.last_message_at ELSE sessions.last_message_at END",
                             (sid, account, username, name, item.get("avatar") or name[:1], latest, utc_now()))

    @staticmethod
    def _ingest(conn: sqlite3.Connection, payload: dict) -> dict:
        existing = conn.execute("SELECT id FROM messages WHERE source_id=?", (payload["source_id"],)).fetchone()
        if existing:
            return {"id": existing[0], "duplicate": True}
        timestamp = canonical_time(payload["created_at"])
        metadata = payload.get("metadata", {})
        sender_key = payload.get("sender_key") or metadata.get("sender_username") or payload["sender_name"]
        cur = conn.execute("INSERT INTO messages(session_id,account,sender_key,sender_name,content,message_type,is_self,created_at,inserted_at,metadata_json,source_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (payload["session_id"], payload.get("account", ""), sender_key, payload["sender_name"], payload["content"],
                            payload.get("message_type", "text"), int(payload["is_self"]), timestamp, utc_now(),
                            json.dumps(metadata, ensure_ascii=False), payload["source_id"]))
        conn.execute("INSERT INTO ingestion_keys VALUES(?,?)", (payload["source_id"], cur.lastrowid))
        conn.execute("UPDATE sessions SET unread=unread+?,last_message_at=CASE WHEN last_message_at IS NULL OR last_message_at<? THEN ? ELSE last_message_at END WHERE id=?",
                     (int(not payload["is_self"]), timestamp, timestamp, payload["session_id"]))
        return {"id": cur.lastrowid, "duplicate": False}

    def ingest(self, payload: dict) -> dict:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._ingest(conn, payload)

    def ingest_batch(self, messages: list[dict], checkpoint: dict | None = None) -> dict:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            results = [self._ingest(conn, message) for message in messages]
            if checkpoint:
                conn.execute("INSERT INTO wechat_checkpoints VALUES(?,?,?,?) ON CONFLICT(account,session_id) DO UPDATE SET sort_seq=MAX(sort_seq,excluded.sort_seq),updated_at=excluded.updated_at",
                             (checkpoint["account"], checkpoint["session_id"], checkpoint["sort_seq"], utc_now()))
            inserted = sum(not r["duplicate"] for r in results)
            return {"imported": inserted, "inserted": inserted, "duplicates": sum(r["duplicate"] for r in results), "items": results}

    def sessions(self) -> list[dict]:
        return self.rows("SELECT s.*,(SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) message_count, "
                         "(SELECT COUNT(*) FROM tasks t WHERE t.session_id=s.id AND t.status='pending') task_count "
                         "FROM sessions s ORDER BY s.monitored DESC,s.last_message_at DESC,s.name")

    @staticmethod
    def decode_message(row: dict) -> dict:
        return {**row, "is_self": bool(row["is_self"]), "metadata": json.loads(row.get("metadata_json", "{}"))}

    @staticmethod
    def decode_task(row: dict) -> dict:
        row = dict(row)
        row["source_message_ids"] = json.loads(row["source_message_ids"])
        return row

    def list_tasks(self, status="pending", q="", priority="", session_id="", due="all") -> list[dict]:
        clauses, params = [], []
        for key, value in (("status", status), ("priority", priority), ("session_id", session_id)):
            if value and value != "all":
                clauses.append(f"t.{key}=?")
                params.append(value)
        if q:
            clauses.append("(t.title LIKE ? ESCAPE '\\' OR t.description LIKE ? ESCAPE '\\' OR s.name LIKE ? ESCAPE '\\')")
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.extend(["%" + escaped + "%"] * 3)
        now = datetime.now(SHANGHAI)
        start = canonical_time(now.replace(hour=0, minute=0, second=0, microsecond=0))
        end = canonical_time(now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
        if due == "overdue":
            clauses.append("t.due_at IS NOT NULL AND julianday(t.due_at)<julianday(?)")
            params.append(utc_now())
        elif due == "today":
            clauses.append("julianday(t.due_at)>=julianday(?) AND julianday(t.due_at)<julianday(?)")
            params.extend([start, end])
        elif due == "upcoming":
            clauses.append("julianday(t.due_at)>=julianday(?)")
            params.append(end)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.rows("SELECT t.*,s.name session_name FROM tasks t JOIN sessions s ON s.id=t.session_id" + where +
                         " ORDER BY CASE t.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,t.due_at IS NULL,julianday(t.due_at),t.id DESC", tuple(params))
        return [self.decode_task(r) for r in rows]

    def task(self, task_id: int) -> dict | None:
        row = self.one("SELECT t.*,s.name session_name FROM tasks t JOIN sessions s ON s.id=t.session_id WHERE t.id=?", (task_id,))
        if not row:
            return None
        task = self.decode_task(row)
        ids = task["source_message_ids"]
        task["messages"] = [self.decode_message(r) for r in self.rows("SELECT * FROM messages WHERE session_id=? AND id IN (" + ",".join("?" for _ in ids) + ") ORDER BY created_at,id", (task["session_id"], *ids))] if ids else []
        task["events"] = [{**r, "detail": json.loads(r["detail_json"])} for r in self.rows("SELECT * FROM task_events WHERE task_id=? ORDER BY id DESC", (task_id,))]
        return task

    def update_task(self, task_id: int, fields: dict, version: int) -> dict | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not existing:
                return None
            if existing["version"] != version:
                raise ConflictError("待办已发生变化，请重新读取后保存")
            allowed = {k: v for k, v in fields.items() if k in {"title", "description", "owner", "priority", "due_at", "status"}}
            if "due_at" in allowed and allowed["due_at"]:
                allowed["due_at"] = canonical_time(allowed["due_at"])
            if not allowed:
                return self.decode_task(dict(existing))
            now = utc_now()
            allowed["updated_at"] = now
            if "status" in allowed:
                allowed["completed_at"] = now if allowed["status"] == "completed" else None
            assignments = ",".join(f"{key}=?" for key in allowed)
            conn.execute(f"UPDATE tasks SET {assignments},version=version+1 WHERE id=? AND version=?", (*allowed.values(), task_id, version))
            conn.execute("INSERT INTO task_events(task_id,action,source,detail_json,created_at) VALUES(?,?,?,?,?)",
                         (task_id, "MANUAL", "manual", json.dumps({"before": {k: existing[k] for k in fields if k in existing.keys()}, "after": fields}, ensure_ascii=False), now))
        return self.task(task_id)

    def message_page(self, sid: str, limit: int = 40, before_id: int | None = None) -> dict:
        params: tuple = (sid,)
        condition = "session_id=?"
        if before_id is not None:
            anchor = self.one("SELECT created_at FROM messages WHERE id=? AND session_id=?", (before_id, sid))
            if not anchor:
                raise ValueError("分页游标不属于此会话")
            condition += " AND (created_at<? OR (created_at=? AND id<?))"
            params += (anchor["created_at"], anchor["created_at"], before_id)
        rows = self.rows("SELECT * FROM messages WHERE " + condition + " ORDER BY created_at DESC,id DESC LIMIT ?", (*params, limit + 1))
        items = [self.decode_message(r) for r in rows[:limit]]
        return {"items": items, "has_more": len(rows) > limit, "before_id": items[-1]["id"] if items else None}

    def stats(self) -> dict:
        row = self.one("SELECT COUNT(*) messages,SUM(CASE WHEN processed=0 THEN 1 ELSE 0 END) queued FROM messages")
        counts = self.one("SELECT SUM(status='pending') pending,SUM(status='completed') completed,SUM(status='cancelled') cancelled,SUM(status='pending' AND priority='high') high_priority FROM tasks")
        return {**{k: (v or 0) for k, v in {**row, **counts}.items()}, "queued": self.queued_count(),
                "monitored": self.one("SELECT COUNT(*) n FROM sessions WHERE monitored=1")["n"],
                "due_today": len(self.list_tasks(due="today")), "overdue": len(self.list_tasks(due="overdue"))}

    def queued_count(self) -> int:
        return self.one("SELECT COUNT(*) n FROM messages m JOIN sessions s ON s.id=m.session_id WHERE m.processed=0 AND s.monitored=1")["n"]

    def backup(self, label: str = "manual") -> dict:
        directory = self.path.parent / "backups"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S-%f")
        target = directory / f"shiguang-{label}-{stamp}.db"
        with self.connect() as source:
            dest = sqlite3.connect(target)
            try:
                source.backup(dest)
                if dest.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("备份完整性检查未通过")
            finally:
                dest.close()
        for stale in sorted(directory.glob(f"shiguang-{label}-*.db"), reverse=True)[10:]:
            stale.unlink()
        return {"file": target.name, "bytes": target.stat().st_size, "created_at": utc_now()}

    def export(self) -> dict:
        with self.connect() as conn:
            conn.execute("BEGIN")
            return {"version": 1, "exported_at": utc_now(), **{table: [dict(r) for r in conn.execute("SELECT * FROM " + table)] for table in ("sessions", "messages", "tasks", "task_events", "rules", "ignored_messages")}}
