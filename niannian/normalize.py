"""上游消息行 → 本地消息记录：署名、摘要、去重键。

这一段是纯函数（不碰微信库、不碰网络、不碰文件），采集线程每导入一批就调一次。
拆出来是因为阶段二的提炼要复用同一套判定：什么算"我发的"、系统行怎么署名、
转账方向怎么认，只应该有一处实现。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from xml.etree import ElementTree

from .describe import describe, summarize
from .ids import session_key

GROUP_PREFIX = re.compile(r"^(wxid_[^:\s<>]+):\s*\n")
# 群自己发的系统行（进群提示等）用群 ID 当前缀，它不是"某个人"，只剥不认。
ROOM_PREFIX = re.compile(r"^\d+@chatroom:\s*\n")


def split_sender(content: str) -> tuple[str, str]:
    """剥离正文封装并给出发送者线索：群前缀优先，其次附件 XML 根节点 fromusername。

    群前缀属于封装而非正文，返回的正文一律去掉它；XML 场景保留原文。
    前缀是群自己 ID 的（进群提示等系统行）只剥不认，返回空发送者。
    """
    match = GROUP_PREFIX.match(content)
    if match:
        return match.group(1), content[match.end():]
    match = ROOM_PREFIX.match(content)
    if match:
        return "", content[match.end():]
    if content.lstrip().startswith("<"):
        try:
            root = ElementTree.fromstring(content)
            name = (root.findtext("fromusername") or "").strip()
            if name:
                return name, content
        except ElementTree.ParseError:
            pass
    return "", content


def transfer_target(desc: dict, sender: str, names: dict) -> None:
    """给转账描述补上"跟谁转"：付款/收款双方里跟发送者相反的那一方。

    微信在群里会为同一笔钱发两条卡片（``paysubtype`` 1 发起、3 已到账），两条的
    ``payer_username``/``receiver_username`` 完全相同，只有发送者换了人，所以方向
    只能拿发送者去比。认不出对方显示名时什么都不加——宁可只显示金额，也不把
    ``wxid_`` 贴进气泡。
    """
    payer = desc.get("payer_username") or ""
    receiver = desc.get("receiver_username") or ""
    if sender and sender == payer:
        desc["direction"], other = "out", receiver
    elif sender and sender == receiver:
        desc["direction"], other = "in", payer
    else:
        return
    nick = str(names.get(other) or "") if other else ""
    if nick and not nick.isdigit() and not nick.startswith("wxid_"):
        desc["target"] = nick


def source_id(account: str, username: str, message: dict, raw: str) -> str:
    """源去重键：有服务器 ID 用它，否则用排序号+本地 ID+时间+原文。"""
    server_id = message.get("server_id")
    parts = ([account, username, "srv", str(server_id)] if server_id else
             [account, username, message.get("sort_seq"), message.get("local_id"),
              message.get("create_time"), raw])
    digest = hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8"))
    return f"wechat:{digest.hexdigest()}"


def quote_name(quoted: dict, account: str, session_name: str, names: dict,
               group: bool) -> None:
    """给引用行补"谁说的"：私聊里微信压根不给 ``displayname``。

    拿被引作者 wxid 走同一张显示名表（备注 > 群名片 > 昵称）。私聊会话里作者不是
    自己就是对方，所以可以落到会话名；**群聊里认不出就不猜**，宁可只显示被引内容。
    """
    if quoted.get("name"):
        return
    author = quoted.get("author") or ""
    if not author:
        return
    if author == account:
        quoted["name"] = "我"
        return
    nick = str(names.get(author) or "")
    if nick and not nick.isdigit() and not nick.startswith("wxid_"):
        quoted["name"] = nick
    elif not group and session_name:
        quoted["name"] = session_name


def normalize_message(account: str, username: str, session_name: str,
                      message: dict, names: dict) -> dict:
    """把上游真实行转换成本地消息记录；不美化、不猜测内容。"""
    raw = str(message.get("content") or "")
    origin, body = split_sender(raw)
    detail = describe(body)
    if not origin and detail and detail.get("kind") == "pat":
        # 拍一拍既没有群前缀，根节点 fromusername 也是空的，动手的人只在 patinfo 里；
        # 不补这一笔它会一直被署名成"系统消息"。
        origin = detail.get("from") or ""
    resolved = str(message.get("sender_username") or "")
    sender = resolved or origin
    is_self = bool(sender) and sender == account
    group = username.endswith("@chatroom")
    if detail and detail.get("kind") == "transfer":
        transfer_target(detail, sender, names)
    if detail and detail.get("kind") == "quote":
        quote_name(detail["quoted"], account, session_name, names, group)
    if is_self:
        sender_key_value, sender_name = account, "我"
    elif not sender:
        # 微信自己也没给这条消息发送者（拍一拍、撤回、进群提示这类系统行）：
        # 不硬套到某个人头上，也不参与按人归组。
        sender_key_value, sender_name = "", "系统消息"
    elif not group:
        sender_key_value = username
        sender_name = session_name or names.get(username) or "会话成员"
    else:
        sender_key_value = sender
        nick = str(names.get(sender) or "")
        is_placeholder = (not nick) or nick.isdigit() or nick.startswith("wxid_")
        sender_name = "群成员" if is_placeholder else nick
    try:
        created = datetime.fromtimestamp(int(message["create_time"]), timezone.utc)
    except (KeyError, TypeError, ValueError, OSError):
        created = datetime.now(timezone.utc)
    return {
        "session_id": session_key(account, username),
        "source_id": source_id(account, username, message, raw),
        "sender_key": sender_key_value,
        "sender_name": sender_name,
        "is_self": is_self,
        "content": (summarize(detail) if detail
                    else body or f"[{message.get('type') or '消息'}]"),
        "message_type": str(message.get("type") or "文本"),
        "created_at": created.isoformat(timespec="seconds"),
        "sort_seq": int(message.get("sort_seq") or 0),
        "metadata": {
            "local_id": message.get("local_id"),
            "server_id": message.get("server_id"),
            "sort_seq": message.get("sort_seq"),
            "type": message.get("type"),
            "sender_id": message.get("sender_id"),
            "origin_sender_username": origin,
            "resolved_sender_username": resolved,
            "attachment": detail,
        },
    }
