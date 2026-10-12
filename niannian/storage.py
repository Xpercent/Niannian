"""SQLite 仓储：消息/水位原子提交、乐观任务更新、一致性快照。"""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

SHANGHAI = timezone(timedelta(hours=8))

# 轮次快照按时间倒序取最近这些；调度间隔下足够覆盖数周，再多也不至于拖慢页面。
RUN_SCAN_LIMIT = 1000
# 一次模型调用的完整请求与响应都入库，只保留最近这些条；上限防止本机库无限增长。
MODEL_CALL_KEEP = 1200
# 异常聚合里逐条解析校验对照的记录数上限。
GUARD_SCAN_LIMIT = 300
# Engine.process 的返回值就是指标来源，字段缺失按 0 计（旧快照没有 merged 等键）。
ROUND_RESULT_FIELDS = ("items", "processed", "created", "updated", "resolved", "merged", "ignored",
                       "skipped", "cancelled", "pre_filtered", "llm_decided", "rule_decided",
                       "batches", "batched_items", "model_requests", "model_failures")
ROUND_DAILY_FIELDS = ("items", "processed", "created", "ignored", "model_requests", "model_failures")


def parse_time(value: str) -> datetime:
    """解析 ISO 时间；无时区按 Asia/Shanghai 理解，与界面输入和截止推断一致。"""
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=SHANGHAI)


def _moment(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_time(value)
    except (TypeError, ValueError):
        return None


def _seconds_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None or end < start:
        return None
    return round((end - start).total_seconds(), 2)


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

# 结构变化按版本顺序执行；SCHEMA 只描述 v1 基线，新增表走这里的迁移。
MIGRATIONS = (
    (2, """
CREATE TABLE IF NOT EXISTS model_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '', message_ids TEXT NOT NULL DEFAULT '[]',
    segments INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
    error TEXT, error_detail TEXT, started_at TEXT NOT NULL, duration_seconds REAL NOT NULL DEFAULT 0,
    model TEXT NOT NULL DEFAULT '', temperature REAL, max_tokens INTEGER, finish_reason TEXT,
    request_json TEXT NOT NULL DEFAULT '', response_text TEXT, parsed_json TEXT,
    usage_json TEXT, stats_json TEXT NOT NULL DEFAULT '{}', outcome_json TEXT,
    input_chars INTEGER NOT NULL DEFAULT 0, output_chars INTEGER NOT NULL DEFAULT 0,
    answered_items INTEGER NOT NULL DEFAULT 0, missing_items INTEGER NOT NULL DEFAULT 0,
    rejected_items INTEGER NOT NULL DEFAULT 0, guard_changes INTEGER NOT NULL DEFAULT 0,
    CHECK(kind IN ('compress','single','batch')), CHECK(status IN ('ok','error'))
);
CREATE INDEX IF NOT EXISTS idx_model_call_run ON model_calls(run_id,id);
CREATE INDEX IF NOT EXISTS idx_model_call_started ON model_calls(started_at,id);
CREATE INDEX IF NOT EXISTS idx_model_call_session ON model_calls(session_id,id);
"""),
)

MODEL_CALL_COLUMNS = ("run_id", "kind", "session_id", "message_ids", "segments", "status", "error",
                      "error_detail", "started_at", "duration_seconds", "model", "temperature",
                      "max_tokens", "finish_reason", "request_json", "response_text", "parsed_json",
                      "usage_json", "stats_json", "input_chars", "output_chars")


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
            current = conn.execute("SELECT MAX(version) AS version FROM schema_version").fetchone()["version"] or 1
            for version, script in MIGRATIONS:
                if version <= current:
                    continue
                conn.executescript(script)
                conn.execute("INSERT INTO schema_version VALUES(?,?)", (version, utc_now()))
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

    def attach_media(self, items: list[dict]) -> int:
        """按 source_id 给已入库消息补记本机附件文件名；只动 metadata_json。

        不改水位、内容、processed；已有 local_file 的消息一律跳过，绝不覆盖
        第一次成功提取的档位。微信落盘晚于采集时，重复扫描靠这里补挂。
        """
        targets = [item for item in items if item.get("source_id") and item.get("filename")]
        if not targets:
            return 0
        by_source = {item["source_id"]: item for item in targets}
        placeholders = ",".join("?" * len(by_source))
        updated = 0
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id,source_id,metadata_json FROM messages "
                f"WHERE source_id IN ({placeholders}) "
                "AND json_extract(metadata_json,'$.local_file') IS NULL",
                tuple(by_source)).fetchall()
            for row in rows:
                item = by_source[row["source_id"]]
                metadata = json.loads(row["metadata_json"] or "{}")
                metadata["local_file"] = item["filename"]
                if item.get("tier"):
                    metadata["media_tier"] = item["tier"]
                conn.execute("UPDATE messages SET metadata_json=? WHERE id=?",
                             (json.dumps(metadata, ensure_ascii=False), row["id"]))
                updated += 1
        return updated

    def compressed_text(self, message_id: int, fingerprint: str) -> str | None:
        row = self.one("SELECT metadata_json FROM messages WHERE id=?", (message_id,))
        cached = json.loads(row["metadata_json"]).get("text_compression", {}) if row else {}
        return cached.get("summary") if cached.get("fingerprint") == fingerprint else None

    def save_compressed_text(self, item: dict, fingerprint: str, summary: str) -> bool:
        """Cache on the last source row, merging metadata without changing originals."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = []
            for message_id in item["message_ids"]:
                row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
                if not row or row["session_id"] != item["session_id"] or row["account"] != item["account"]:
                    return False
                rows.append(row)
            if not rows or "\n".join(row["content"] for row in rows) != item["content"]:
                return False
            metadata = json.loads(rows[-1]["metadata_json"])
            metadata["text_compression"] = {"fingerprint": fingerprint, "summary": summary,
                                            "message_ids": item["message_ids"], "created_at": utc_now()}
            conn.execute("UPDATE messages SET metadata_json=? WHERE id=?",
                         (json.dumps(metadata, ensure_ascii=False), rows[-1]["id"]))
        return True

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

    def processing_metrics(self, days: int = 30) -> dict:
        """汇总真实轮次快照、任务事件与过滤记录里的模型处理指标。

        只统计已经落库的记录；缺样本的比率返回 None，由界面显示为“—”，
        不拿估算值冒充实测。
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
        rows = self.rows("SELECT id,status,snapshot,updated_at FROM runs ORDER BY updated_at DESC LIMIT ?",
                         (RUN_SCAN_LIMIT,))
        rounds = []
        for row in rows:
            try:
                snapshot = json.loads(row["snapshot"])
            except (TypeError, ValueError):
                continue
            started = _moment(snapshot.get("started_at")) or _moment(row["updated_at"])
            finished = _moment(snapshot.get("finished_at"))
            ended = finished or started
            if ended is None or (cutoff and ended < cutoff):
                continue
            result = snapshot.get("result") or {}
            round_ = {"id": snapshot.get("id") or row["id"],
                      "status": snapshot.get("status") or row["status"],
                      "trigger": snapshot.get("trigger"),
                      "at": ended.isoformat(timespec="microseconds"),
                      "duration_seconds": _seconds_between(started, finished),
                      "model_seconds": round(float(result.get("model_seconds") or 0), 2),
                      "last_model_error": result.get("last_model_error"),
                      "error": snapshot.get("error"),
                      "queued_after": result.get("queued"),
                      "progress_done": int(snapshot.get("done") or 0),
                      "progress_total": int(snapshot.get("total") or 0)}
            for field in ROUND_RESULT_FIELDS:
                round_[field] = int(result.get(field) or 0)
            round_["active"] = round_["items"] > 0
            rounds.append(round_)
        rounds.sort(key=lambda item: item["at"])
        since = cutoff.isoformat(timespec="microseconds") if cutoff else ""
        where = " WHERE created_at>=?" if since else ""
        params = (since,) if since else ()
        return {"summary": self._round_summary(rounds), "rounds": rounds, "daily": self._round_daily(rounds),
                "events": self.rows("SELECT action,source,COUNT(*) count,MAX(created_at) last_at FROM task_events"
                                    + where + " GROUP BY action,source ORDER BY count DESC", params),
                "ignored_reasons": self.rows("SELECT substr(reason,1,140) reason,source,COUNT(*) count,"
                                             "MAX(created_at) last_at FROM ignored_messages" + where +
                                             " GROUP BY reason,source ORDER BY count DESC LIMIT 10", params),
                "queued": self.queued_count(), "window_days": days,
                "scanned": len(rows), "scanned_limit": RUN_SCAN_LIMIT}

    @staticmethod
    def _round_summary(rounds: list[dict]) -> dict:
        totals = {field: sum(row[field] for row in rounds) for field in ROUND_RESULT_FIELDS}
        timed = [row for row in rounds if row["active"] and row["duration_seconds"] is not None]
        durations = [row["duration_seconds"] for row in timed]
        wall_seconds = round(sum(durations), 2)
        model_seconds = round(sum(row["model_seconds"] for row in rounds), 2)
        timed_items = sum(row["items"] for row in timed)
        decided = totals["llm_decided"] + totals["rule_decided"]
        return {
            "rounds": len(rounds), "active_rounds": sum(1 for row in rounds if row["active"]),
            "rounds_completed": sum(1 for row in rounds if row["status"] == "completed"),
            "rounds_failed": sum(1 for row in rounds if row["status"] == "failed"),
            "rounds_cancelled": sum(1 for row in rounds if row["status"] == "cancelled"),
            "rounds_running": sum(1 for row in rounds if row["status"] == "running"),
            "rounds_with_model_error": sum(1 for row in rounds if row["last_model_error"]),
            "rounds_with_failure": sum(1 for row in rounds if row["error"]),
            **totals, "wall_seconds": wall_seconds, "model_seconds": model_seconds,
            "model_share": round(model_seconds / wall_seconds, 3) if wall_seconds else None,
            "average_seconds": round(wall_seconds / len(durations), 2) if durations else None,
            "median_seconds": Database._percentile(durations, 0.5),
            "p95_seconds": Database._percentile(durations, 0.95),
            "slowest_seconds": max(durations) if durations else None,
            "fastest_seconds": min(durations) if durations else None,
            "seconds_per_item": round(wall_seconds / timed_items, 3) if timed_items else None,
            "model_failure_rate": round(totals["model_failures"] / totals["model_requests"], 3)
            if totals["model_requests"] else None,
            "local_fallback_rate": round(totals["rule_decided"] / decided, 3) if decided else None,
            "pre_filter_rate": round(totals["pre_filtered"] / totals["items"], 3) if totals["items"] else None,
            "batch_share": round(totals["batched_items"] / totals["items"], 3) if totals["items"] else None,
            "items_per_batch": round(totals["batched_items"] / totals["batches"], 2) if totals["batches"] else None,
            "last_round_at": rounds[-1]["at"] if rounds else None,
            "last_active_round_at": next((row["at"] for row in reversed(rounds) if row["active"]), None)}

    @staticmethod
    def _round_daily(rounds: list[dict]) -> list[dict]:
        buckets: dict[str, dict] = {}
        for row in rounds:
            day = _moment(row["at"]).astimezone(SHANGHAI).strftime("%Y-%m-%d")
            bucket = buckets.setdefault(day, {"date": day, "rounds": 0, "active_rounds": 0, "wall_seconds": 0.0,
                                              "model_seconds": 0.0,
                                              **{field: 0 for field in ROUND_DAILY_FIELDS}})
            bucket["rounds"] += 1
            bucket["active_rounds"] += int(row["active"])
            for field in ROUND_DAILY_FIELDS:
                bucket[field] += row[field]
            if row["active"]:
                bucket["wall_seconds"] = round(bucket["wall_seconds"] + (row["duration_seconds"] or 0), 2)
                bucket["model_seconds"] = round(bucket["model_seconds"] + row["model_seconds"], 2)
        return [buckets[key] for key in sorted(buckets)]

    @staticmethod
    def _percentile(values: list[float], fraction: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
        return ordered[index]

    def log_model_call(self, record: dict) -> int:
        """写一次模型调用的完整请求与响应；超出保留量后删最旧的诊断行。"""
        values = {"run_id": None, "kind": "single", "session_id": "", "message_ids": [], "segments": 0,
                  "status": "error", "error": None, "error_detail": None, "started_at": utc_now(),
                  "duration_seconds": 0.0, "model": "", "temperature": None, "max_tokens": None,
                  "finish_reason": None, "request_json": "", "response_text": None, "parsed_json": None,
                  "usage_json": None, "stats_json": {}, "outcome_json": None, "input_chars": 0,
                  "output_chars": 0, **record}
        values["message_ids"] = json.dumps(values["message_ids"], ensure_ascii=False)
        for key in ("request_json", "parsed_json", "usage_json", "stats_json"):
            value = values.get(key)
            if value is not None and not isinstance(value, str):
                values[key] = json.dumps(value, ensure_ascii=False)
        for key in ("segments", "input_chars", "output_chars"):
            values[key] = int(values[key] or 0)
        values["duration_seconds"] = float(values["duration_seconds"] or 0)
        with self.connect() as conn:
            cur = conn.execute("INSERT INTO model_calls(" + ",".join(MODEL_CALL_COLUMNS) + ") VALUES(" +
                               ",".join("?" for _ in MODEL_CALL_COLUMNS) + ")",
                               tuple(values.get(key) for key in MODEL_CALL_COLUMNS))
            conn.execute("DELETE FROM model_calls WHERE id NOT IN "
                         "(SELECT id FROM model_calls ORDER BY id DESC LIMIT ?)", (MODEL_CALL_KEEP,))
            return cur.lastrowid

    def record_model_call_outcome(self, call_id: int, outcome: dict, counters: dict) -> None:
        self.execute("UPDATE model_calls SET outcome_json=?, answered_items=?, missing_items=?,"
                     "rejected_items=?, guard_changes=? WHERE id=?",
                     (json.dumps(outcome, ensure_ascii=False), int(counters.get("answered", 0)),
                      int(counters.get("missing", 0)), int(counters.get("rejected", 0)),
                      int(counters.get("guard", 0)), call_id))

    def model_call_rows(self, run_id="", session_id="", kind="", status="", q="", message_id=None,
                        days=30, limit=80, offset=0, truncated=False, guarded=False) -> list[dict]:
        clauses, params = [], []
        cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
        if cutoff:
            clauses.append("c.started_at>=?")
            params.append(cutoff.isoformat(timespec="microseconds"))
        for key, value in (("run_id", run_id), ("session_id", session_id), ("kind", kind), ("status", status)):
            if value:
                clauses.append(f"c.{key}=?")
                params.append(value)
        if message_id is not None:
            clauses.append("EXISTS(SELECT 1 FROM json_each(c.message_ids) j WHERE j.value=?)")
            params.append(message_id)
        if truncated:
            clauses.append("c.finish_reason='length'")
        if guarded:
            clauses.append("c.guard_changes>0")
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("(c.request_json LIKE ? ESCAPE '\\' OR c.response_text LIKE ? ESCAPE '\\' "
                           "OR c.outcome_json LIKE ? ESCAPE '\\')")
            params.extend(["%" + escaped + "%"] * 3)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.rows("SELECT c.id,c.run_id,c.kind,c.session_id,c.message_ids,c.segments,c.status,c.error,"
                         "c.error_detail,c.started_at,c.duration_seconds,c.model,c.max_tokens,c.finish_reason,"
                         "c.input_chars,c.output_chars,c.answered_items,c.missing_items,c.rejected_items,"
                         "c.guard_changes,c.stats_json,s.name session_name FROM model_calls c "
                         "LEFT JOIN sessions s ON s.id=c.session_id" + where + " ORDER BY c.id DESC LIMIT ? OFFSET ?",
                         (*params, limit, offset))
        for row in rows:
            row["message_ids"] = json.loads(row["message_ids"])
            row["stats_json"] = json.loads(row["stats_json"] or "{}")
            row["session_name"] = row["session_name"] or row["session_id"]
        return rows

    def model_call_detail(self, call_id: int) -> dict | None:
        row = self.one("SELECT * FROM model_calls WHERE id=?", (call_id,))
        if not row:
            return None
        for key in ("request_json", "parsed_json", "usage_json", "stats_json", "outcome_json"):
            row[key] = json.loads(row[key]) if row[key] else None
        row["message_ids"] = json.loads(row["message_ids"])
        session = self.one("SELECT id,name,monitored FROM sessions WHERE id=?", (row["session_id"],))
        row["session_name"] = session["name"] if session else row["session_id"]
        rows = self._message_slice(row["message_ids"])
        row["messages"] = rows
        # 摘要缓存挂在段落末条，代表整段压缩结果而不是那一条消息。
        row["segment_summary"] = rows[-1]["summary"] if rows else None
        marks = ",".join("?" for _ in row["message_ids"])
        related = ("EXISTS(SELECT 1 FROM json_each({table}.message_ids) j WHERE j.value IN (" + marks + "))"
                   if row["message_ids"] else "0")
        row["events"] = self.rows("SELECT e.*,t.title,t.status task_status FROM task_events e "
                                  "LEFT JOIN tasks t ON t.id=e.task_id WHERE e.source!='manual' AND " +
                                  related.format(table="e"), tuple(row["message_ids"]))
        row["ignored"] = self.rows("SELECT * FROM ignored_messages WHERE " +
                                   related.format(table="ignored_messages"), tuple(row["message_ids"]))
        for item in row["events"] + row["ignored"]:
            item["message_ids"] = json.loads(item["message_ids"])
            if "detail_json" in item:
                item["detail"] = json.loads(item["detail_json"] or "{}")
                item.pop("detail_json")
        return row

    def _message_slice(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        rows = self.rows("SELECT id,session_id,sender_name,content,created_at,processed,is_self,"
                         "json_extract(metadata_json,'$.text_compression.summary') summary "
                         f"FROM messages WHERE id IN ({marks}) ORDER BY created_at,id", tuple(ids))
        for row in rows:
            row["is_self"] = bool(row["is_self"])
            row["processed"] = bool(row["processed"])
        return rows

    def model_call_counts(self, run_ids: list[str]) -> dict[str, dict]:
        if not run_ids:
            return {}
        marks = ",".join("?" for _ in run_ids)
        rows = self.rows("SELECT run_id,COUNT(*) calls,SUM(status='error') failures,"
                         "SUM(kind='compress') compress_calls,SUM(kind='batch') batch_calls,"
                         "SUM(kind='single') single_calls,SUM(finish_reason='length') truncated,"
                         "SUM(guard_changes>0) guarded_calls,SUM(answered_items) answered,"
                         "SUM(missing_items) missing,SUM(rejected_items) rejected,"
                         "ROUND(SUM(duration_seconds),2) seconds,MIN(started_at) first_at,MAX(started_at) last_at "
                         f"FROM model_calls WHERE run_id IN ({marks}) GROUP BY run_id", tuple(run_ids))
        return {row["run_id"]: row for row in rows}

    def model_call_issues(self, days: int = 30) -> dict:
        """按失败类型、索引缺口与校验拦截归类；只统计已落库的真实记录。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
        filters, params = ([], [])
        if cutoff:
            filters.append("started_at>=?")
            params.append(cutoff.isoformat(timespec="microseconds"))
        where = (" WHERE " + " AND ".join(filters)) if filters else ""
        buckets: dict[tuple[str, str], dict] = {}

        def add(kind: str, value: str, row: dict):
            bucket = buckets.setdefault((kind, value), {"type": kind, "value": value, "count": 0,
                                                        "last_at": None, "last_call_id": None})
            bucket["count"] += 1
            if (row["started_at"] or "") >= (bucket["last_at"] or ""):
                bucket["last_at"], bucket["last_call_id"] = row["started_at"], row["id"]

        for row in self.rows("SELECT id,kind,status,error,finish_reason,segments,started_at,"
                             "missing_items,rejected_items,guard_changes FROM model_calls" + where, params):
            if row["status"] == "error":
                add("request_error", row["error"] or "Unknown", row)
            if row["finish_reason"] == "length":
                add("output_truncated", row["kind"], row)
            if row["missing_items"]:
                add("index_missing", row["kind"], row)
            if row["rejected_items"]:
                add("instruction_rejected", row["kind"], row)
        reasons: dict[str, dict] = {}
        guard_where = " WHERE " + " AND ".join([*filters, "guard_changes>0"])
        guarded = self.rows("SELECT id,session_id,started_at,outcome_json FROM model_calls" + guard_where +
                            " ORDER BY id DESC LIMIT ?", (*params, GUARD_SCAN_LIMIT))
        for row in guarded:
            outcome = json.loads(row["outcome_json"] or "{}")
            for item in outcome.get("items", []):
                proposed, final = item.get("proposed") or [], item.get("final") or []
                if proposed == final or not any(command.get("action") != "IGNORE" for command in proposed):
                    continue
                # 只统计“模型想建单/改单，被业务校验改成无需行动”的改写原因。
                for command in final:
                    if command.get("action") != "IGNORE":
                        continue
                    reason = (command.get("context_summary") or "未标注原因").strip()[:200]
                    bucket = reasons.setdefault(reason, {"reason": reason, "count": 0,
                                                          "last_at": None, "last_call_id": row["id"]})
                    bucket["count"] += 1
                    if (row["started_at"] or "") >= (bucket["last_at"] or ""):
                        bucket["last_at"], bucket["last_call_id"] = row["started_at"], row["id"]
        return {"buckets": sorted(buckets.values(), key=lambda item: (-item["count"], item["type"])),
                "guard_reasons": sorted(reasons.values(), key=lambda item: -item["count"]),
                "guard_scanned": len(guarded)}

    def model_call_retention(self) -> dict:
        row = self.one("SELECT COUNT(*) stored,MIN(started_at) oldest_at FROM model_calls")
        return {"keep": MODEL_CALL_KEEP, "stored": row["stored"] or 0, "oldest_at": row["oldest_at"]}

    def backlog(self, limit: int = 80) -> dict:
        """未处理的真实消息与被模型触及的情况，用来定位一直留在队列里的段落。"""
        sessions = self.rows("SELECT m.session_id,s.name session_name,s.last_message_at,COUNT(*) messages,"
                             "MIN(m.created_at) oldest,MAX(m.created_at) newest "
                             "FROM messages m JOIN sessions s ON s.id=m.session_id "
                             "WHERE m.processed=0 AND s.monitored=1 GROUP BY m.session_id ORDER BY oldest")
        touched = {row["message_id"]: row for row in self.rows(
            "SELECT j.value message_id,COUNT(*) calls,SUM(c.status='error') failures,"
            "MAX(c.started_at) last_at FROM model_calls c, json_each(c.message_ids) j "
            "WHERE c.kind IN ('single','batch') GROUP BY j.value")}
        rows = self.rows("SELECT m.id,m.session_id,s.name session_name,m.sender_name,m.created_at,"
                         "substr(m.content,1,200) content,m.processed "
                         "FROM messages m JOIN sessions s ON s.id=m.session_id "
                         "WHERE m.processed=0 AND s.monitored=1 ORDER BY m.created_at,m.id LIMIT ?", (limit,))
        for row in rows:
            info = touched.get(row["id"]) or {}
            row["calls"] = info.get("calls") or 0
            row["failures"] = info.get("failures") or 0
            row["last_call_at"] = info.get("last_at")
        return {"sessions": sessions, "messages": rows, "touched": len(touched)}

    def run_records(self, since: str, until: str | None = None) -> dict:
        """一轮里真正落地的变更与判定为无需行动的段落，按轮次时间窗取。"""
        clauses, params = ["created_at>=?"], [since]
        if until:
            clauses.append("created_at<=?")
            params.append(until)
        where = " WHERE " + " AND ".join(clauses)
        events = self.rows("SELECT task_id,action,source,message_ids,created_at,detail_json FROM task_events" +
                           where + " ORDER BY id", tuple(params))
        ignored = self.rows("SELECT id,session_id,message_ids,source,reason,created_at FROM ignored_messages" +
                            where + " ORDER BY id", tuple(params))
        for item in events + ignored:
            item["message_ids"] = json.loads(item["message_ids"])
        for item in events:
            item["detail"] = json.loads(item["detail_json"] or "{}")
            item.pop("detail_json")
        return {"events": events, "ignored": ignored}

    def backup(self, label: str = "manual") -> dict:
        directory = self.path.parent / "backups"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S-%f")
        target = directory / f"backup-{label}-{stamp}.db"
        with self.connect() as source:
            dest = sqlite3.connect(target)
            try:
                source.backup(dest)
                if dest.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("备份完整性检查未通过")
            finally:
                dest.close()
        for stale in sorted(directory.glob(f"backup-{label}-*.db"), reverse=True)[10:]:
            stale.unlink()
        return {"file": target.name, "bytes": target.stat().st_size, "created_at": utc_now()}

    def export(self) -> dict:
        with self.connect() as conn:
            conn.execute("BEGIN")
            return {"version": 1, "exported_at": utc_now(), **{table: [dict(r) for r in conn.execute("SELECT * FROM " + table)] for table in ("sessions", "messages", "tasks", "task_events", "rules", "ignored_messages")}}
