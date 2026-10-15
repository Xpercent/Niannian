"""FastAPI 应用：REST 接口 + 前端构建产物托管。阶段一只提供看板所需接口。"""

from __future__ import annotations

import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import (EDITABLE_KEYS, RESTART_KEYS, load_config, save_config,
                     validate)
from .describe import KIND_LABEL
from .media import media_root, safe_media_path
from .storage import Database
from .wechat import Collector


def type_label(item: dict) -> str:
    """气泡上的类型徽标：读取时按解析出的 kind 现算。

    上游标签把文件、转账、拍一拍等 8 种东西一律叫「文件/链接/卡片」，
    解析结果里的 kind 才是真类型。放在读取时算而不是入库时存，
    是为了让存量行不用重采就能改过来。
    """
    meta = item.get("metadata") or {}
    kind = str((meta.get("attachment") or {}).get("kind") or "")
    return KIND_LABEL.get(kind) or str(item.get("message_type") or "消息")


def quoted_of(item: dict) -> dict:
    """取引用行的被引定位；不是引用消息时返回空 dict。"""
    attachment = (item.get("metadata") or {}).get("attachment") or {}
    return attachment.get("quoted") or {}


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def port_busy(host: str, port: int) -> bool:
    """试绑定一下判断端口是否已被占用。

    **故意不设** ``SO_REUSEADDR``：Windows 下那个选项允许重复绑定正在监听的端口，
    探测会假成功，把用户导到一个重启后起不来的端口上。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, int(port)))
        except OSError:
            return True
    return False


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

    @app.get("/api/settings")
    def api_get_settings():
        """设置页读当前生效值；``restart_required`` 告诉界面哪些要重启。"""
        path = root / "config.json"
        return {
            "values": {key: settings.get(key) for key in EDITABLE_KEYS},
            "restart_required": list(RESTART_KEYS),
            "config_file": path.name,
            "config_exists": path.is_file(),
        }

    @app.put("/api/settings")
    def api_put_settings(payload: dict = Body(...)):
        """保存设置：写 config.json，能热更的立刻推给采集器，其余标需重启。"""
        try:
            values = validate({key: payload.get(key) for key in EDITABLE_KEYS})
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        changed = {key for key, value in values.items() if settings.get(key) != value}
        if "port" in changed and port_busy(str(settings.get("host")), values["port"]):
            raise HTTPException(400, f"端口 {values['port']} 已被其它程序占用，换一个")
        save_config(root, values)
        collector.update_settings(values)
        settings.update(values)
        return {
            "values": {key: settings.get(key) for key in EDITABLE_KEYS},
            "applied": sorted(changed - set(RESTART_KEYS)),
            "restart": sorted(changed & set(RESTART_KEYS)),
            "unchanged": not changed,
        }

    @app.get("/api/sessions/{session_id}/messages")
    def api_messages(session_id: str, limit: int = Query(40, ge=1, le=200),
                     before_seq: int | None = Query(None, ge=0),
                     before_id: int | None = Query(None, ge=1)):
        if not store.session(session_id):
            raise HTTPException(404, "会话不存在")
        page = store.messages_page(session_id, limit=limit,
                                   before_seq=before_seq, before_id=before_id)
        for item in page["items"]:
            item["type_label"] = type_label(item)
        _attach_quote_targets(session_id, page["items"])
        return page

    @app.get("/api/sessions/{session_id}/search")
    def api_search(session_id: str, q: str = Query(""),
                   limit: int = Query(50, ge=1, le=200)):
        """在该会话**已入库**的消息正文里找关键词（纯读，毫秒级）。

        只搜得到导进来的那部分：默认每会话最近 100 条，更早的要先用"重新导入"拉进来。
        """
        if not store.session(session_id):
            raise HTTPException(404, "会话不存在")
        items = store.search_messages(session_id, q, limit=limit)
        for item in items:
            item["type_label"] = type_label(item)
        return {"items": items, "query": str(q or "").strip()}

    def _attach_quote_targets(session_id: str, items: list[dict]) -> None:
        """给引用行挂 ``quote_target``：被引那条在本库里存在才给，否则为 None。

        跳转用的是 ``quoted.sort_seq``（会话内唯一，也是翻页游标），所以前端拿到
        target 就滚过去、拿不到就把引用行显示成不可点。
        """
        seqs = {int(quoted_of(item).get("sort_seq") or 0) for item in items}
        found = store.quoted_targets(session_id, seqs)
        for item in items:
            quoted = quoted_of(item)
            if not quoted:
                continue
            seq = int(quoted.get("sort_seq") or 0)
            target = found.get(seq)
            item["quote_target"] = ({"message_id": target, "sort_seq": seq}
                                    if target else None)

    # 消息列表是**纯读**：只把自己库里的行取出来。缺链接的图片由采集线程每轮补齐
    # （wechat.py:_catchup_images），这里一旦去解密图片就可能连带触发"翻微信内存
    # 找钥匙"，那会把整个服务冻住——所以这条线路上不碰 media，也不碰上游库。
    @app.get("/media/{name:path}")
    def api_media(name: str):
        path = safe_media_path(media_root(root), name)
        if not path:
            raise HTTPException(404, "附件不存在")
        return FileResponse(path)

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

    @app.post("/api/sessions/{session_id}/reimport")
    def api_reimport(session_id: str, limit: int = Query(0, ge=0, le=1000)):
        """按 limit 重新导入该会话历史（limit=0 用全局默认）；只影响这一个会话。"""
        try:
            return collector.reimport(session_id, limit)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

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
