"""运行配置：内置默认值，可被工程根目录 config.json 覆盖。"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS: dict = {
    "host": "127.0.0.1",       # 仅监听本机，避免对外暴露微信数据
    "port": 8765,
    "wechat_db_dir": "",       # 留空则自动探测微信 4.x 数据目录
    "wechat_account": "",      # 留空则自动选择本机登录账号
    "history_limit": 100,      # 关注会话后自动导入的最近消息条数
    "poll_seconds": 3.0,       # 采集轮询间隔
}

# 设置页只开放这三项：host 改了会把本机微信数据暴露到局域网且容易把自己关在
# 门外，wechat_db_dir / wechat_account 属于"连不上就没有数据"的启动期参数，
# 都留给你手工编辑 config.json。
EDITABLE_KEYS = ("history_limit", "poll_seconds", "port")
# 这些键改了必须重启才生效，界面上要如实标出来，不能假装已应用。
RESTART_KEYS = ("port",)


def clamp_history_limit(value) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return DEFAULTS["history_limit"]
    return max(1, min(limit, 1000))


def validate(values: dict) -> dict:
    """校验设置页提交的三项，返回规范化后的值；不合法抛 ValueError。

    抛出的文案是直接给用户看的，所以写成人话而不是字段名。
    注意与 :func:`load_config` 分工不同：读文件时**宽容**（坏值退回默认，
    保证服务起得来），存文件时**严格**（不能把明显写错的东西落盘）。
    """
    out: dict = {}

    try:
        out["history_limit"] = int(values["history_limit"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("导入条数必须是整数") from None
    if not 1 <= out["history_limit"] <= 1000:
        raise ValueError("导入条数要在 1 到 1000 之间")

    try:
        out["poll_seconds"] = float(values["poll_seconds"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("轮询间隔必须是秒数") from None
    if not 1.0 <= out["poll_seconds"] <= 3600.0:
        raise ValueError("轮询间隔要在 1 到 3600 秒之间")

    try:
        out["port"] = int(values["port"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("端口必须是整数") from None
    if not 1024 <= out["port"] <= 65535:
        raise ValueError("端口要在 1024 到 65535 之间（不要用 1024 以下的特权端口）")

    return out


def save_config(root: Path, values: dict) -> dict:
    """把给定键写进 config.json，**保留文件里已有的其它键**。

    先写临时文件再原子替换：进程在写的中途被杀掉也不会留下半个 JSON。
    """
    path = Path(root) / "config.json"
    current: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current = loaded
        except (OSError, json.JSONDecodeError):
            current = {}        # 坏文件不能挡住保存，覆盖重写即可
    current.update(values)
    temp = path.parent / (path.name + ".tmp")
    temp.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    os.replace(temp, path)
    return current


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
