"""模型输入 XML 渲染；与提示词、网络请求及输出契约分别维护。"""

from __future__ import annotations

import re
from typing import Any

from .storage import SHANGHAI, parse_time

# XML 1.0 无法表示这些字符；输入层显示为可见转义，本地原文保持不变。
INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def xml_text(value: Any) -> str:
    """有效 XML 字符解析后保真；非法控制字符可见转义，尖括号不能伪造标签。"""
    text = INVALID_XML.sub(lambda match: f"\\u{ord(match.group()):04x}", str(value))
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\r", "&#13;")


def xml_attr(value: Any) -> str:
    return xml_text(value).replace('"', "&quot;").replace("\n", "&#10;").replace("\t", "&#9;")


def _attrs(attributes: dict | None) -> str:
    return "".join(f' {key}="{xml_attr(value)}"'
                   for key, value in (attributes or {}).items() if value is not None)


def _node(tag: str, attributes: dict | None, body: list[str]) -> str:
    """容器标签：子元素各占一行，空容器写成自闭合。"""
    children = [item for item in body if item]
    if not children:
        return f"<{tag}{_attrs(attributes)} />"
    return f"<{tag}{_attrs(attributes)}>\n" + "\n".join(children) + f"\n</{tag}>"


def _text(tag: str, value: Any, attributes: dict | None = None) -> str:
    """叶子标签：正文内联，开闭标签之间就是给定文本本身。"""
    rendered = "" if value is None else xml_text(value)
    return f"<{tag}{_attrs(attributes)}>{rendered}</{tag}>" if rendered else f"<{tag}{_attrs(attributes)} />"


def _display_time(value: Any) -> str:
    """同一时刻渲染成 Asia/Shanghai 带时区 ISO，模型不必自己做 UTC 到本地的换算。"""
    if not value:
        return ""
    try:
        return parse_time(value).astimezone(SHANGHAI).isoformat()
    except (TypeError, ValueError):
        return str(value)


def _message(message: dict, index: int | None = None) -> str:
    """一条消息：编号/发送者/是否本人/时间作属性，正文单独放在 content 标签内。"""
    attributes = {}
    if index is not None:
        attributes["index"] = index
    attributes["sender"] = message["sender_name"]
    attributes["self"] = "true" if message["is_self"] else "false"
    if message.get("content_is_summary"):
        attributes["summary"] = "true"
    attributes["time"] = _display_time(message["created_at"])
    return _node("message", attributes, [_text("content", message["content"])])


def _profile(value: Any) -> str:
    """空白画像显式标未知；不往用户画像里补身份或意愿。"""
    text = value or ""
    if text.strip():
        return _text("user_profile", text)
    return _text("user_profile", "未提供：不能据此认定本人担任任何职务，也不能认定本人有参与意愿",
                 {"unknown": "true"})


def render_extraction(data: dict[str, Any]) -> str:
    """把一次提炼上下文渲染成 XML；合法文本保持内容，时间统一显示为上海时区。"""
    session = data.get("session") or {}
    current = data.get("new_messages") or [data.get("new_message")]
    return _node("extraction", None, [
        _node("context", None, [
            _text("reference_time", _display_time(data.get("reference_time"))),
            _text("timezone", data.get("timezone")),
            _node("session", {"name": session.get("name"), "group": "true" if session.get("is_group") else "false"}, []),
        ]),
        _profile(data.get("user_profile")),
        _node("pending_tasks", None, [
            _text("task", task["title"], {"id": task["id"], "priority": task.get("priority"),
                                          "due_at": _display_time(task["due_at"]) if task.get("due_at") else None})
            for task in data.get("pending_tasks", [])
        ]),
        _node("history", None, [_message(row) for row in data.get("history", [])]),
        _node("current_messages", None, [_message(item, index) for index, item in enumerate(current)]),
    ])


def render_compression(data: dict[str, Any]) -> str:
    """压缩请求：<source> 内是完整原文，字数上限与是否带上下文写在属性上。"""
    return _node("compression", {"max_summary_chars": data.get("max_summary_chars"),
                                 "context_available": "true" if data.get("context_available") else "false"},
                 [_text("source", data.get("source_text") or "")])
