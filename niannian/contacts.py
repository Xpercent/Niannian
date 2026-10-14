"""发送者显示名：备注 > 群名片 > 昵称。

群名片（成员在这个群里自己设的显示名）**没有独立的表列**——`chatroom_member`
只有 room_id + member_id 两列。它塞在 `chat_room.ext_buffer` 的 protobuf 里，
上游 `get_group_members()` 只读 contact 的昵称/备注，从来没解析过这个 buffer，
所以"备注 → 昵称"中间那一档是缺的：通知群里 32 人设了群名片（如
"科技委员21陈永源"），照上游取到的是全局昵称"五大天王-陈德华"。

优先级不是猜的：拿微信自己算好的
`session.db.SessionTable.last_sender_display_name` 与三方逐一比对，7 例
"群名片/备注/昵称互不相同"的样本里 6 例（无备注）跟随群名片、1 例（有备注）
跟随备注。
"""

from __future__ import annotations


def protobuf_fields(buf: bytes):
    """最小 protobuf 扫描，产出 ``(字段号, 值)``。

    只解这里用得到的两种 wire 类型：2（长度前缀，bytes/str/嵌套消息）与
    0（varint）。遇到别的类型就停下——宁可少给，不猜偏移量把后面的数据
    当成字段读。
    """
    index = 0
    while index < len(buf):
        head = buf[index]
        index += 1
        number, wire = head >> 3, head & 7
        if not number:
            return
        if wire == 2:
            if index >= len(buf):
                return
            length = buf[index]
            index += 1
            if length > len(buf) - index:
                return
            yield number, buf[index:index + length]
            index += length
        elif wire == 0:
            value = shift = 0
            while index < len(buf):
                byte = buf[index]
                index += 1
                value |= (byte & 0x7F) << shift
                if not byte & 0x80:
                    break
                shift += 7
            yield number, value
        else:
            return


def _text(buf: bytes) -> str:
    return buf.decode("utf-8", "ignore").strip() if isinstance(buf, bytes) else ""


def group_nicknames(ext_buffer) -> dict:
    """`chat_room.ext_buffer` → ``{成员wxid: 群名片}``；没设过群名片的成员不出现。

    成员条目是外层字段 1 的嵌套消息，内部 1=成员wxid、2=群名片、
    4=邀请人；空群名片按微信语义等于"没设"，不收录。
    """
    buf = as_bytes(ext_buffer)
    found: dict[str, str] = {}
    for number, entry in protobuf_fields(buf):
        if number != 1 or not isinstance(entry, bytes):
            continue
        member = {n: v for n, v in protobuf_fields(entry)}
        user = _text(member.get(1, b""))
        display = _text(member.get(2, b""))
        if user.startswith("wxid_") and display:
            found[user] = display
    return found


def as_bytes(value) -> bytes:
    """兼容 sqlite 把 BLOB 读成 str 的情况（latin-1 能原样往返字节）。"""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("latin-1", "ignore")
    return b""


def display_name(remark: str, group_nickname: str, nickname: str, wxid: str) -> str:
    """一个成员在该群里的显示名：备注 > 群名片 > 昵称 > wxid。"""
    return remark or group_nickname or nickname or wxid
