"""标识：账号指纹与会话主键。

单列一个小模块，是因为这三件事被三个地方各自要：解密缓存目录（wechat_db）、
媒体目录（media）、会话主键与读侧反查（wechat/api）。原来 ``wechat.account_scope``
与 ``media.media_scope`` 是逐字相同的两份实现，同一个账号算出同一个指纹这件事
不该靠巧合。
"""

from __future__ import annotations

import hashlib


def account_scope(account: str) -> str:
    """账号目录指纹：隔离解密缓存、媒体目录与会话主键，避免路径含非法字符。"""
    return hashlib.sha256(str(account).encode("utf-8")).hexdigest()[:16]


def session_key(account: str, username: str) -> str:
    """会话主键 ``wechat:<账号指纹>:<微信原始ID>``。

    账号取指纹不取原文：换账号登录不会把两个账号的同名会话混成一堆，也不会把
    wxid 直接暴露到路径与接口参数里。
    """
    return f"wechat:{account_scope(account)}:{username}"


def session_user(session_id: str) -> str:
    """会话主键里的第三段——回传给上游当查询用的微信原始 ID。"""
    parts = str(session_id).split(":", 2)
    return parts[2] if len(parts) == 3 else ""
