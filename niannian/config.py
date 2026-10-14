"""运行配置：内置默认值，可被工程根目录 config.json 覆盖。"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULTS: dict = {
    "host": "127.0.0.1",       # 仅监听本机，避免对外暴露微信数据
    "port": 8765,
    "wechat_db_dir": "",       # 留空则自动探测微信 4.x 数据目录
    "wechat_account": "",      # 留空则自动选择本机登录账号
    "history_limit": 100,      # 关注会话后自动导入的最近消息条数
    "poll_seconds": 3.0,       # 采集轮询间隔
}


def clamp_history_limit(value) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return DEFAULTS["history_limit"]
    return max(1, min(limit, 1000))


def load_config(root: Path) -> dict:
    """读取 config.json（不存在时用默认值），只做宽容合并，不校验未知键。"""
    settings = dict(DEFAULTS)
    path = Path(root) / "config.json"
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"config.json 读取失败：{exc}") from exc
        if isinstance(loaded, dict):
            settings.update(loaded)
    settings["history_limit"] = clamp_history_limit(settings.get("history_limit"))
    try:
        settings["poll_seconds"] = max(1.0, float(settings.get("poll_seconds", 3.0)))
    except (TypeError, ValueError):
        settings["poll_seconds"] = DEFAULTS["poll_seconds"]
    return settings
