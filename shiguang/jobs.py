"""协调自动与手动提炼：单一轮次、可取消、进度持久化。"""

from __future__ import annotations

import json
import logging
import time
import uuid
from threading import Event, Lock, Thread

from .storage import utc_now

log = logging.getLogger(__name__)


class ExtractionJobs:
    def __init__(self, db, engine, config):
        self.db, self.engine, self.config = db, engine, config
        self.lock = Lock()
        self.cancel_event = Event()
        self.stop_event = Event()
        self.current: dict | None = None
        self.worker: Thread | None = None
        self.scheduler: Thread | None = None

    def recover(self) -> None:
        for row in self.db.rows("SELECT id,snapshot FROM runs WHERE status='running'"):
            old = json.loads(row["snapshot"])
            old.update(status="failed", error="上次进程退出，未处理消息保留；可重新提炼", finished_at=utc_now())
            self._persist(old)

    def _persist(self, run: dict) -> None:
        self.db.execute("INSERT INTO runs VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,snapshot=excluded.snapshot,updated_at=excluded.updated_at",
                        (run["id"], run["status"], json.dumps(run, ensure_ascii=False), utc_now()))

    def start(self, force=True, session_id=None) -> dict:
        with self.lock:
            if self.current and self.current["status"] == "running":
                return dict(self.current)
            if self.stop_event.is_set():
                raise RuntimeError("服务正在退出")
            self.cancel_event.clear()
            self.current = {"id": uuid.uuid4().hex, "status": "running", "started_at": utc_now(),
                            "finished_at": None, "total": 0, "done": 0, "session": None,
                            "result": None, "error": None, "cancel_requested": False,
                            "trigger": "manual" if force else "scheduled"}
            self._persist(self.current)
            self.worker = Thread(target=self._execute, args=(force, session_id), name="shiguang-extraction", daemon=True)
            self.worker.start()
            return dict(self.current)

    def _progress(self, *args, **kwargs):
        if len(args) == 1 and isinstance(args[0], dict):
            fields = args[0]
        else:
            fields = {"done": args[0] if args else kwargs.get("done", 0),
                      "total": args[1] if len(args) > 1 else kwargs.get("total", 0),
                      "session": args[2] if len(args) > 2 else kwargs.get("session")}
        with self.lock:
            self.current.update({k: v for k, v in fields.items() if k in {"done", "total", "session"}})
            self._persist(self.current)

    def _execute(self, force, session_id):
        try:
            result = self.engine.process(force=force, session_id=session_id, progress=self._progress,
                                         should_stop=lambda: self.cancel_event.is_set() or self.stop_event.is_set())
            with self.lock:
                self.current.update(status="cancelled" if self.cancel_event.is_set() or self.stop_event.is_set() else "completed",
                                    result=result, finished_at=utc_now(), session=None)
                self._persist(self.current)
        except Exception as exc:
            log.exception("提炼轮次失败，未处理消息保留")
            with self.lock:
                self.current.update(status="failed", error=f"{type(exc).__name__}: {str(exc)[:300]}", finished_at=utc_now())
                self._persist(self.current)

    def get(self, run_id: str) -> dict | None:
        row = self.db.one("SELECT snapshot FROM runs WHERE id=?", (run_id,))
        return json.loads(row["snapshot"]) if row else None

    def latest(self) -> dict | None:
        row = self.db.one("SELECT snapshot FROM runs ORDER BY updated_at DESC LIMIT 1")
        return json.loads(row["snapshot"]) if row else None

    def cancel(self, run_id: str) -> dict | None:
        with self.lock:
            if self.current and self.current["id"] == run_id and self.current["status"] == "running":
                self.cancel_event.set()
                self.current["cancel_requested"] = True
                self._persist(self.current)
        return self.get(run_id)

    def start_scheduler(self) -> None:
        self.recover()
        self.scheduler = Thread(target=self._schedule, name="shiguang-scheduler", daemon=True)
        self.scheduler.start()

    def _schedule(self):
        last_run = time.monotonic()
        while not self.stop_event.wait(1):
            interval = self.config.read()["batch_interval_seconds"]
            if time.monotonic() - last_run >= interval:
                last_run = time.monotonic()
                try:
                    self.start(force=False)
                except Exception:
                    log.exception("调度失败")

    def stop(self) -> None:
        self.stop_event.set()
        self.cancel_event.set()
        if self.scheduler:
            self.scheduler.join(timeout=3)
        if self.worker:
            # 当前网络请求有明确 timeout，等待后保留其原子提交结果。
            self.worker.join(timeout=self.config.read()["ai_timeout_seconds"] + 5)
