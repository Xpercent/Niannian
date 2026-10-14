from niannian.contacts import display_name, group_nicknames, protobuf_fields


def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def field(number: int, payload) -> bytes:
    """编码一个 protobuf 字段：bytes 走长度前缀（wire 2），int 走 varint（wire 0）。"""
    if isinstance(payload, bytes):
        return bytes([(number << 3) | 2, len(payload)]) + payload
    return bytes([(number << 3) | 0]) + varint(payload)


def member(wxid: str, nickname: str = "", inviter: str = "") -> bytes:
    """照 chat_room.ext_buffer 里的真实形状造一条成员记录。"""
    body = field(1, wxid.encode())
    if nickname:
        body += field(2, nickname.encode())
    body += field(3, 1)
    if inviter:
        body += field(4, inviter.encode())
    return field(1, body)


def test_protobuf_fields_reads_nested_message_and_varint():
    buf = member("wxid_a", "科技委员21陈永源")
    top = dict(protobuf_fields(buf))
    assert set(top) == {1} and isinstance(top[1], bytes)
    inner = dict(protobuf_fields(top[1]))
    assert inner[1] == b"wxid_a"
    assert inner[2] == "科技委员21陈永源".encode()
    assert inner[3] == 1                      # varint 档


def test_group_nicknames_maps_member_display_names_only():
    buf = (member("wxid_a", "科技委员21陈永源")
           + member("wxid_b")                                    # 没设群名片
           + member("wxid_c", "刘恩铄", inviter="wxid_a"))
    assert group_nicknames(buf) == {"wxid_a": "科技委员21陈永源", "wxid_c": "刘恩铄"}


def test_group_nicknames_survives_str_blob_and_empty_input():
    buf = member("wxid_a", "文体委员吴彦朋")
    assert group_nicknames(buf) == group_nicknames(buf.decode("latin-1"))
    assert group_nicknames(b"") == {}
    assert group_nicknames(None) == {}


def test_group_nicknames_ignores_non_member_and_truncated_buffer():
    buf = member("12345@chatroom", "不是成员") + member("wxid_ok", "好")
    assert group_nicknames(buf) == {"wxid_ok": "好"}
    assert group_nicknames(member("wxid_a", "张三")[:5]) == {}   # 长度对不上就停，不猜偏移


def test_display_name_priority_is_remark_then_group_then_nickname():
    assert display_name("助班学长", "厦门黄宸溢（26软2助班）", "Narisse", "wxid_x") == "助班学长"
    assert display_name("", "科技委员21陈永源", "五大天王-陈德华", "wxid_x") == "科技委员21陈永源"
    assert display_name("", "", "影", "wxid_x") == "影"
    assert display_name("", "", "", "wxid_x") == "wxid_x"
