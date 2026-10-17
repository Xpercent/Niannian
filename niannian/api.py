"""FastAPI 应用：REST 接口 + 前端构建产物托管。含看板与提炼的触发、状态、任务查询接口。"""

from __future__ import annotations

import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import (EDITABLE_KEYS, KEY_LABELS, RESTART_KEYS, load_config,
                     mask_api_key, merge_api_key, save_config, validate)
from .describe import KIND_LABEL
from .extract import PRIORITIES, parse_deadline
from .extractor import Extractor
from .llm import test_connection
from .media import media_root, safe_media_path
from .storage import Database
from .wechat import Collector

# 界面上那三个状态按钮：值必须与 tasks 表的 CHECK 一致，只有这一份清单
TASK_STATUSES = ("pending", "completed", "cancelled")


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


def editable_values(settings: dict) -> dict:
    """设置页开放的键；密钥那一格**永远回空串**（界面上留空的意思是不改）。

    读与存两个接口都要回同一份形状，这里出一次，避免两处各写各的。
    """
    values = {key: settings.get(key) for key in EDITABLE_KEYS}
    values["llm_api_key"] = ""
    return values


def api_key_fields(settings: dict) -> dict:
    """密钥只显示指纹：``sk-…0001`` 与"到底存没存"。"""
    key = str(settings.get("llm_api_key") or "")
    return {"api_key_masked": mask_api_key(key), "api_key_set": bool(key.strip())}


def create_app(root: Path | None = None) -> FastAPI:
    root = Path(root) if root else project_root()
    settings = load_config(root)
    store = Database(root / "data" / "niannian.db")
    store.init()
    collector = Collector(store, settings, root)
    extractor = Extractor(store, settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        collector.start()
        try:
            yield
        finally:
            extractor.stop()
            collector.stop()

    app = FastAPI(title="Niannian 看板", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.collector = collector
    app.state.extractor = extractor

    @app.get("/api/status")
    def api_status():
        return collector.status()

    @app.get("/api/sessions")
    def api_sessions():
        account = collector.status()["account"]
        if not account:
            # 服务先启动、微信后开：账号还不知道，此时不过滤会把别的账号的会话
            # 当成当前账号的列出来（list_sessions 空 account = 不加条件）。
            return []
        return store.list_sessions(account=account, monitored_only=True)

    @app.get("/api/settings")
    def api_get_settings():
        """设置页读当前生效值；``restart_required`` 告诉界面哪些要重启。

        API Key **不回明文**：``values`` 里那个键一律给空串，界面要显示"已经存了
        哪一把"就读 ``api_key_masked``（只有头尾几位）。
        """
        path = root / "config.json"
        return {
            "values": editable_values(settings),
            "restart_required": list(RESTART_KEYS),
            # 中文名由后端给一份：界面再抄一张表就会和这里漂开
            "labels": KEY_LABELS,
            "config_file": path.name,
            "config_exists": path.is_file(),
            **api_key_fields(settings),
        }

    @app.put("/api/settings")
    def api_put_settings(payload: dict = Body(...)):
        """保存设置：写 config.json，能热更的立刻推给采集器，其余标需重启。"""
        try:
            values = validate({key: payload.get(key) for key in EDITABLE_KEYS}
                              | {"llm_api_key": merge_api_key(payload, settings)})
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        changed = {key for key, value in values.items() if settings.get(key) != value}
        if "port" in changed and port_busy(str(settings.get("host")), values["port"]):
            raise HTTPException(400, f"端口 {values['port']} 已被其它程序占用，换一个")
        save_config(root, values)
        collector.update_settings(values)
        settings.update(values)
        return {
            "values": editable_values(settings),
            "applied": sorted(changed - set(RESTART_KEYS)),
            "restart": sorted(changed & set(RESTART_KEYS)),
            "unchanged": not changed,
            **api_key_fields(settings),
        }

    @app.post("/api/settings/test-connection")
    def api_test_connection(payload: dict = Body(default=None)):
        """拿界面上**当前填的**这份参数试一次服务商，不用先保存。

        没提交的键回落到库里已有的值；API Key 留空意思是"沿用已存的那把"，因为
        界面读到的永远是掩码，留空才是常态。参数明显写错时**先不出网**，直接把
        校验那句中文话回回来——测连失败的 ``ok=false`` 就是这条接口的正常结果，
        不走 400/500，界面才知道该把哪一行字变红。
        """
        submitted = payload or {}
        draft = {key: submitted.get(key, settings.get(key)) for key in EDITABLE_KEYS}
        draft["llm_api_key"] = merge_api_key(submitted, settings)
        try:
            draft = validate(draft)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "elapsed_ms": 0}
        return test_connection(draft)

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

    @app.post("/api/extract")
    def api_extract_start(payload: dict = Body(default=None)):
        """起一轮提炼：立刻回状态，不等模型。``session_id`` 空表示所有关注会话。

        模型那边一次只能问一批（一批最长等 180 秒），所以这活放后台线程做，界面按
        ``GET /api/extract/status`` 轮进度。已经在跑就回 ``accepted=false``，不排队第二轮。
        """
        session_id = str((payload or {}).get("session_id") or "").strip()
        if session_id and not store.session(session_id):
            raise HTTPException(404, "会话不存在")
        try:
            return extractor.start(session_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/extract/status")
    def api_extract_status():
        return extractor.status()

    @app.get("/api/tasks")
    def api_tasks(session_id: str = Query(""), status: str = Query("pending"),
                  priority: str = Query(""), q: str = Query(""),
                  due: str = Query("all"),
                  limit: int = Query(100, ge=1, le=200)):
        """任务清单（纯读）。``status`` 给 ``all`` 不过滤状态。

        ``sources`` 是依据消息的 sort_seq 列表，点它跳原文照消息列表那套游标走，
        不需要为任务再加接口。
        """
        return {"items": store.list_tasks(session_id=session_id, status=status,
                                          priority=priority, query=q, due=due,
                                          limit=limit)}

    @app.get("/api/tasks/stats")
    def api_task_stats():
        """待办页那几张指标卡与导航角标上的数字（先于 ``/{task_id}`` 声明，
        不然 "stats" 会被当成任务 id 去转 int）。"""
        return store.task_stats()

    @app.get("/api/tasks/{task_id}")
    def api_task_detail(task_id: int):
        """一条任务连同**依据消息的原文**：抽屉里要看是哪几句话让模型建的这条。"""
        task = store.task(task_id)
        if not task:
            raise HTTPException(404, "待办不存在")
        for item in task["messages"]:
            item["type_label"] = type_label(item)
        _attach_quote_targets(task["session_id"], task["messages"])
        return task

    @app.patch("/api/tasks/{task_id}")
    def api_task_patch(task_id: int, payload: dict = Body(...)):
        """人在界面上改这几个字段（标题/说明/优先级/期限/状态），别的不动。

        期限走 `extract.parse_deadline`：界面交来的是 `datetime-local` 那种没时区的
        "2026-10-18T12:00"，与模型回的那份用**同一个收形状的函数**，库里只有一种形状。
        """
        updates: dict = {}
        if "title" in payload:
            title = str(payload.get("title") or "").strip()
            if not title:
                raise HTTPException(400, "标题不能空着")
            updates["title"] = title
        if "description" in payload:
            updates["description"] = str(payload.get("description") or "").strip()
        if "priority" in payload:
            priority = str(payload.get("priority") or "").strip().lower()
            if priority not in PRIORITIES:
                raise HTTPException(400, "优先级只能是高、普通、低三档之一")
            updates["priority"] = priority
        if "status" in payload:
            status = str(payload.get("status") or "").strip()
            if status not in TASK_STATUSES:
                raise HTTPException(400, "状态只能是进行中、已完成或已忽略")
            updates["status"] = status
        if "deadline" in payload:
            raw = payload.get("deadline")
            if raw is None or str(raw).strip() == "":
                updates["deadline"] = None
            else:
                # parse_deadline 认不出来给 None，而"没给期限"与"给了但读不懂"是两回事：
                # 后者是人在界面上填的，要说清楚，不能悄悄把期限删掉。
                shape = parse_deadline(raw)
                if shape is None:
                    raise HTTPException(400, f"截止时间认不出来：{raw}")
                updates["deadline"] = shape
        if not updates:
            raise HTTPException(400, "没有要修改的内容")
        if not store.update_task(task_id, updates):
            raise HTTPException(404, "待办不存在")
        task = store.task(task_id)
        for item in task["messages"]:
            item["type_label"] = type_label(item)
        _attach_quote_targets(task["session_id"], task["messages"])
        return task

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
        # set_monitored 靠 rowcount 认会话存不存在，不用再先查一遍
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
