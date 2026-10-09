"""应用工厂与薄 HTTP 接口；无导入时初始化或后台线程。"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import __version__
from .config import ConfigStore, ProfileStore, Settings
from .jobs import ExtractionJobs
from .storage import ConflictError, Database, utc_now


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskPatch(Input):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    owner: str | None = Field(default=None, max_length=100)
    priority: Literal["high", "medium", "low"] | None = None
    due_at: str | None = None
    status: Literal["pending", "completed", "cancelled"] | None = None

    @field_validator("title")
    @classmethod
    def valid_title(cls, value):
        if value is not None and not value.strip():
            raise ValueError("标题不能为空")
        return value.strip() if value else value

    @field_validator("due_at")
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            at = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if at.tzinfo is None:
                raise ValueError("截止时间须包含时区")
        return value


class SessionPatch(Input):
    monitored: bool


class HistoryInput(Input):
    limit: int = Field(default=100, ge=1, le=1000)


class ProcessInput(Input):
    session_id: str | None = None


class ProfileInput(Input):
    markdown: str = Field(max_length=30000)


class RuleInput(Input):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    pattern: str = Field(min_length=1, max_length=400)
    action: Literal["ignore", "extract_task"]
    enabled: bool = True

    @field_validator("pattern")
    @classmethod
    def regex(cls, value):
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"正则表达式无效：{exc}") from exc
        return value


class RulePatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    pattern: str | None = Field(default=None, min_length=1, max_length=400)
    action: Literal["ignore", "extract_task"] | None = None
    enabled: bool | None = None

    @field_validator("pattern")
    @classmethod
    def regex(cls, value):
        return RuleInput.regex(value) if value is not None else None


class Application:
    def __init__(self, root: Path, *, live_wechat=True):
        from .engine import Engine
        from .wechat import WechatWorker
        self.root = root.resolve()
        self.config = ConfigStore(self.root)
        values = self.config.read()
        self.db = Database(self.config.resolve(values["db_path"]))
        self.db.init()
        self.profile = ProfileStore(self.config.resolve(values["profile_path"]))
        self.engine = Engine(self.db, self.config, self.profile)
        self.jobs = ExtractionJobs(self.db, self.engine, self.config)
        self.wechat = WechatWorker(self.db, self.config, self.root)
        self.live_wechat = live_wechat

    def start(self):
        self.jobs.start_scheduler()
        if self.live_wechat and self.config.read()["wechat_enabled"]:
            self.wechat.start()

    def stop(self):
        self.wechat.stop()
        self.jobs.stop()


def create_app(root: Path | str | None = None, *, live_wechat=True) -> FastAPI:
    workspace = Path(root) if root else Path(__file__).resolve().parent.parent

    @asynccontextmanager
    async def lifespan(app):
        container = Application(workspace, live_wechat=live_wechat)
        app.state.container = container
        container.start()
        try:
            yield
        finally:
            await asyncio.to_thread(container.stop)

    app = FastAPI(title="拾光 · 真实微信与智能待办", version=__version__, lifespan=lifespan)

    def container() -> Application:
        return app.state.container

    def session_or_404(sid):
        row = container().db.one("SELECT * FROM sessions WHERE id=?", (sid,))
        if not row:
            raise HTTPException(404, "未找到此会话")
        return row

    @app.get("/api/health")
    def health():
        ctx = container()
        return {"ok": True, "version": __version__, "wechat": ctx.wechat.status(),
                "stats": ctx.db.stats(), "ai_enabled": ctx.config.read()["ai_enabled"],
                "thinking_enabled": False, "config_warning": ctx.config.warning}

    @app.get("/api/overview")
    def overview():
        ctx = container()
        return {"stats": ctx.db.stats(), "sessions": ctx.db.sessions(), "wechat": ctx.wechat.status(), "run": ctx.jobs.latest()}

    @app.get("/api/tasks")
    def tasks(status: Literal["pending", "completed", "cancelled", "all"] = "pending", q: str = Query(default="", max_length=500),
              priority: Literal["high", "medium", "low", ""] = "", session_id: str = "", due: Literal["all", "today", "overdue", "upcoming"] = "all"):
        return container().db.list_tasks(status, q, priority, session_id, due)

    @app.get("/api/tasks/{task_id}")
    def task(task_id: int):
        result = container().db.task(task_id)
        if not result:
            raise HTTPException(404, "待办不存在")
        return result

    @app.patch("/api/tasks/{task_id}")
    def patch_task(task_id: int, payload: TaskPatch):
        fields = payload.model_dump(exclude_unset=True, exclude={"version"})
        if any(v is None for k, v in fields.items() if k != "due_at"):
            raise HTTPException(422, "只有截止时间可设为空")
        try:
            result = container().db.update_task(task_id, fields, payload.version)
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not result:
            raise HTTPException(404, "待办不存在")
        return result

    @app.get("/api/sessions")
    def sessions():
        return container().db.sessions()

    @app.patch("/api/sessions/{session_id}")
    def patch_session(session_id: str, payload: SessionPatch):
        session_or_404(session_id)
        container().db.execute("UPDATE sessions SET monitored=? WHERE id=?", (int(payload.monitored), session_id))
        return container().db.one("SELECT * FROM sessions WHERE id=?", (session_id,))

    @app.post("/api/sessions/{session_id}/read")
    def read_session(session_id: str):
        session_or_404(session_id)
        container().db.execute("UPDATE sessions SET unread=0 WHERE id=?", (session_id,))
        return {"ok": True, "unread": 0}

    @app.get("/api/sessions/{session_id}/messages")
    def messages(session_id: str, limit: int = Query(default=40, ge=1, le=200), before_id: int | None = None):
        session_or_404(session_id)
        try:
            return container().db.message_page(session_id, limit, before_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/sessions/{session_id}/ignored")
    def ignored(session_id: str, limit: int = Query(default=30, ge=1, le=100)):
        session_or_404(session_id)
        db = container().db
        rows = db.rows("SELECT * FROM ignored_messages WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit))
        for row in rows:
            ids = json.loads(row["message_ids"])
            row["messages"] = [db.decode_message(r) for r in db.rows("SELECT * FROM messages WHERE session_id=? AND id IN (" + ",".join("?" for _ in ids) + ") ORDER BY created_at,id", (session_id, *ids))] if ids else []
        return rows

    @app.post("/api/sessions/{session_id}/history")
    def history(session_id: str, payload: HistoryInput):
        session_or_404(session_id)
        try:
            return container().wechat.import_history(session_id, payload.limit)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/wechat/catalog")
    def catalog():
        return container().wechat.catalog()

    @app.get("/api/wechat/status")
    def wechat_status():
        return container().wechat.status()

    @app.post("/api/wechat/start")
    def wechat_start():
        ctx = container()
        if not ctx.live_wechat:
            raise HTTPException(409, "当前验证实例关闭了实际微信读取")
        ctx.config.update({"wechat_enabled": True})
        return ctx.wechat.start()

    @app.post("/api/wechat/stop")
    def wechat_stop():
        ctx = container()
        ctx.config.update({"wechat_enabled": False})
        return ctx.wechat.stop()

    @app.get("/api/process/latest")
    def latest_run():
        return container().jobs.latest()

    @app.post("/api/process")
    def process(payload: ProcessInput = ProcessInput()):
        if payload.session_id:
            session_or_404(payload.session_id)
        return container().jobs.start(force=True, session_id=payload.session_id)

    @app.get("/api/process/{run_id}")
    def run_status(run_id: str):
        result = container().jobs.get(run_id)
        if not result:
            raise HTTPException(404, "提炼记录不存在")
        return result

    @app.post("/api/process/{run_id}/cancel")
    def cancel_run(run_id: str):
        result = container().jobs.cancel(run_id)
        if not result:
            raise HTTPException(404, "提炼记录不存在")
        return result

    @app.get("/api/settings")
    def settings():
        return container().config.public()

    @app.patch("/api/settings")
    def patch_settings(payload: dict):
        if not payload:
            raise HTTPException(400, "没有要保存的设置")
        unknown = set(payload) - Settings.model_fields.keys()
        if unknown:
            raise HTTPException(422, "未知设置：" + ",".join(sorted(unknown)))
        if {"db_path", "profile_path"} & payload.keys():
            raise HTTPException(400, "存储路径请修改 config.json 后重新启动")
        ctx = container()
        try:
            result = ctx.config.update(payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if "wechat_enabled" in payload and ctx.live_wechat:
            ctx.wechat.start() if payload["wechat_enabled"] else ctx.wechat.stop()
        return result

    @app.post("/api/settings/probe")
    def probe():
        from .llm import LLMExtractor
        values = container().config.read()
        if not values["ai_api_key"] or not values["ai_base_url"]:
            return {"ok": False, "error": "请先保存接口地址和密钥", "model": values["ai_model"]}
        llm = LLMExtractor(values["ai_base_url"], values["ai_api_key"], values["ai_model"], timeout=values["ai_timeout_seconds"])
        error = llm.probe()
        return {"ok": not bool(error), "error": error, "model": values["ai_model"], "thinking_enabled": False}

    @app.get("/api/profile")
    def profile():
        return {"markdown": container().profile.read()}

    @app.put("/api/profile")
    def save_profile(payload: ProfileInput):
        container().profile.save(payload.markdown)
        return {"saved": True, "characters": len(payload.markdown)}

    @app.get("/api/rules")
    def rules():
        return container().db.rows("SELECT * FROM rules ORDER BY id")

    @app.post("/api/rules", status_code=201)
    def create_rule(payload: RuleInput):
        values = payload.model_dump()
        rule_id = container().db.execute("INSERT INTO rules(name,description,pattern,action,enabled,created_at) VALUES(?,?,?,?,?,?)",
                                         (values["name"], values["description"], values["pattern"], values["action"], int(values["enabled"]), utc_now()))
        return container().db.one("SELECT * FROM rules WHERE id=?", (rule_id,))

    @app.patch("/api/rules/{rule_id}")
    def patch_rule(rule_id: int, payload: RulePatch):
        db = container().db
        if not db.one("SELECT id FROM rules WHERE id=?", (rule_id,)):
            raise HTTPException(404, "规则不存在")
        fields = payload.model_dump(exclude_unset=True)
        if any(v is None for v in fields.values()):
            raise HTTPException(422, "规则字段不能为空值")
        if fields:
            db.execute("UPDATE rules SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?", (*fields.values(), rule_id))
        return db.one("SELECT * FROM rules WHERE id=?", (rule_id,))

    @app.delete("/api/rules/{rule_id}")
    def delete_rule(rule_id: int):
        db = container().db
        if not db.one("SELECT id FROM rules WHERE id=?", (rule_id,)):
            raise HTTPException(404, "规则不存在")
        db.execute("DELETE FROM rules WHERE id=?", (rule_id,))
        return {"deleted": True}

    @app.post("/api/backup")
    def backup():
        return container().db.backup()

    @app.get("/api/export")
    def export(format: Literal["json", "md"] = "json"):
        data = container().db.export()
        if format == "json":
            body = json.dumps(data, ensure_ascii=False, indent=2)
            media_type = "application/json"
        else:
            names = {s["id"]: s["name"] for s in data["sessions"]}
            lines = ["# 拾光待办与消息", "", "导出时间：" + data["exported_at"], ""]
            for task in data["tasks"]:
                lines.extend(["## " + task["title"].replace("\n", " "), "", f"会话：{names.get(task['session_id'], task['session_id'])} · 状态：{task['status']} · 优先级：{task['priority']}", "", task["description"], "", task["context_summary"], ""])
            lines.append("## 原始消息")
            for message in data["messages"]:
                lines.extend(["", f"{message['created_at']} · {names.get(message['session_id'], '')} · {message['sender_name']}", "", "> " + message["content"].replace("\n", "\n> ")])
            body = "\n".join(lines)
            media_type = "text/markdown"
        return Response(body, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="shiguang-{data["exported_at"][:10]}.{format}"'})

    @app.get("/media/{filename:path}")
    def media(filename: str):
        base = (workspace / "data" / "wechat" / "media").resolve()
        path = (base / filename).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise HTTPException(404, "本机附件不存在")
        return FileResponse(path, headers={"X-Content-Type-Options": "nosniff"})

    web = Path(__file__).resolve().parent / "web"
    web.mkdir(exist_ok=True)
    app.mount("/assets", StaticFiles(directory=web), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(web / "index.html", headers={"Cache-Control": "no-cache"})

    return app
