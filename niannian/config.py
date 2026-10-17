"""运行配置：内置默认值，可被工程根目录 config.json 覆盖。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

DEFAULTS: dict = {
    "host": "127.0.0.1",       # 仅监听本机，避免对外暴露微信数据
    "port": 8765,
    "wechat_db_dir": "",       # 留空则自动探测微信 4.x 数据目录
    "wechat_account": "",      # 留空则自动选择本机登录账号
    "history_limit": 100,      # 关注会话后自动导入的最近消息条数
    "poll_seconds": 3.0,       # 采集轮询间隔
    # 阶段二的模型接入（OpenAI 兼容服务商）。这里只放"怎么跟服务商说话"的参数，
    # 提炼的提示词与批次不在这一层。
    "llm_base_url": "https://api.siliconflow.cn/v1",
    "llm_model": "Qwen/Qwen3-8B",
    "llm_api_key": "",
    "llm_thinking": False,         # 思考模式：服务商侧对 Qwen3 **默认是开的**，见 llm.py
    "llm_include_images": False,   # 图片喂不喂模型，默认关（用户 2026-10-16 定）
}

# 设置页开放这些键：host 改了会把本机微信数据暴露到局域网且容易把自己关在
# 门外，wechat_db_dir / wechat_account 属于"连不上就没有数据"的启动期参数，
# 都留给你手工编辑 config.json。
EDITABLE_KEYS = ("history_limit", "poll_seconds", "port",
                 "llm_base_url", "llm_model", "llm_api_key",
                 "llm_thinking", "llm_include_images")
# 这些键改了必须重启才生效，界面上要如实标出来，不能假装已应用。
RESTART_KEYS = ("port",)
BOOL_KEYS = ("llm_thinking", "llm_include_images")

# 界面显示与校验报错都说人话，所以每个开放键有一个中文名（设置页读 GET 回来的
# 这份，不要再在前端抄第二张表）。`llm_api_key` 那一格由 api 抹空后回显。
KEY_LABELS = {
    "history_limit": "导入条数",
    "poll_seconds": "轮询间隔",
    "port": "监听端口",
    "llm_base_url": "服务商地址",
    "llm_model": "模型名",
    "llm_api_key": "API Key",
    "llm_thinking": "思考模式",
    "llm_include_images": "图片喂给模型",
}

TRUE_WORDS = ("true", "1", "on", "yes")
FALSE_WORDS = ("false", "0", "off", "no")


def to_bool(value, default: bool | None = None) -> bool:
    """把开关值认成布尔。

    ``default`` 给了就用它兜底（读文件时宽容），没给就抛 :class:`ValueError`
    （写文件时严格）——认不出来的东西不能悄悄变成"关"，那样用户以为开了思考
    而实际没开。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in TRUE_WORDS:
            return True
        if text in FALSE_WORDS:
            return False
    elif isinstance(value, int) and value in (0, 1):
        return bool(value)
    if default is not None:
        return default
    raise ValueError("不是开或关")


def mask_api_key(value) -> str:
    """能在网页上显示的那份密钥指纹：只留头 3 位与尾 4 位，真密钥不出网关。"""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 8:
        return "•" * len(text)
    return f"{text[:3]}…{text[-4:]}"


def merge_api_key(payload: dict, current: dict) -> str:
    """设置页提交的密钥：留空表示**不改**，勾了"清除"才真的清空。

    读接口回的是掩码，所以界面上的输入框平时是空的；不能把"空"当成"用户想删掉它"。
    """
    if payload.get("clear_llm_api_key"):
        return ""
    submitted = str(payload.get("llm_api_key") or "").strip()
    return submitted or str(current.get("llm_api_key") or "")


def clamp_history_limit(value) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return DEFAULTS["history_limit"]
    return max(1, min(limit, 1000))


def validate(values: dict) -> dict:
    """校验设置页提交的键，返回规范化后的值；不合法抛 ValueError。

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

    url = str(values.get("llm_base_url") or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("服务商地址要写成完整 URL，以 http:// 或 https:// 开头")
    if any(c.isspace() for c in url):
        raise ValueError("服务商地址里不能有空格")
    out["llm_base_url"] = url

    model = str(values.get("llm_model") or "").strip()
    if not model:
        raise ValueError("模型名不能空着，例如 Qwen/Qwen3-8B")
    if len(model) > 120:
        raise ValueError("模型名太长，检查是不是粘贴进多余的内容了")
    out["llm_model"] = model

    key = str(values.get("llm_api_key") or "").strip()
    if key and any(c.isspace() for c in key):
        raise ValueError("API Key 里不能有空格，检查是不是复制漏了字符")
    if len(key) > 300:
        raise ValueError("API Key 太长，检查是不是粘贴错了")
    out["llm_api_key"] = key

    for field in BOOL_KEYS:
        try:
            out[field] = to_bool(values[field])
        except (KeyError, ValueError):
            raise ValueError(f"{KEY_LABELS[field]}只能是开或关") from None

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
    for field in ("llm_base_url", "llm_model", "llm_api_key"):
        if not isinstance(settings.get(field), str):
            settings[field] = DEFAULTS[field]
    for field in BOOL_KEYS:
        settings[field] = to_bool(settings.get(field), default=DEFAULTS[field])
    return settings
