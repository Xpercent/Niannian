"""FastAPI 应用：REST 接口 + 前端构建产物托管。阶段一只提供看板所需接口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import load_config
from .storage import Database
from .wechat import Collector


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def create_app(root: Path | None = None) -> FastAPI:
    root = Path(root) if root else project_root()
    settings = load_config(root)
    store = Database(root / "data" / "niannian.db")
    store.init()
    collector = Collector(store, settings, root)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        collector.start()
        try:
            yield
        finally:
            collector.stop()

    app = FastAPI(title="Niannian 看板", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.collector = collector

    @app.get("/api/status")
    def api_status():
        status = collector.status()
        status.update(store.counts())
        return status

    @app.get("/api/sessions")
    def api_sessions():
        account = collector.status()["account"]
        return store.list_sessions(account=account, monitored_only=True)

    @app.get("/api/sessions/{session_id}/messages")
    def api_messages(session_id: str, limit: int = Query(40, ge=1, le=200),
                     before_id: int | None = Query(None, ge=1)):
        if not store.session(session_id):
            raise HTTPException(404, "会话不存在")
        return store.messages_page(session_id, limit=limit, before_id=before_id)

    @app.get("/api/wechat/catalog")
    def api_catalog():
        try:
            return collector.catalog()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    def _set_follow(session_id: str, monitored: bool):
        session = store.session(session_id)
        if not session:
            raise HTTPException(404, "会话不存在")
        if not store.set_monitored(session_id, monitored):
            raise HTTPException(404, "会话不存在")
        return {"id": session_id, "monitored": monitored}

    @app.post("/api/sessions/{session_id}/follow")
    def api_follow(session_id: str):
        return _set_follow(session_id, True)

    @app.post("/api/sessions/{session_id}/unfollow")
    def api_unfollow(session_id: str):
        return _set_follow(session_id, False)

    dist = root / "web" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        page = dist / "index.html"
        if page.is_file():
            return FileResponse(page)
        return JSONResponse({"detail": "前端尚未构建：请在 web/ 下执行 npm install && npm run build"},
                            status_code=503)

    return app
