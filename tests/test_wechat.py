import sqlite3

from niannian.wechat import (account_scope, normalize_message, open_isolated_db,
                             sender_alias_map, session_key, source_id, split_sender)

ACCOUNT = "wxid_me"


def make_message(**overrides):
    base = {"local_id": 10, "server_id": None, "sort_seq": 100,
            "create_time": 1760000000, "type": "文本", "sender_id": 5,
            "sender_username": "wxid_other",
            "content": "wxid_other:\n晚上开会"}
    base.update(overrides)
    return base


def test_split_sender_group_prefix_stripped():
    origin, body = split_sender("wxid_abc123:\n第一行\n第二行")
    assert origin == "wxid_abc123"
    assert body == "第一行\n第二行"


def test_split_sender_plain_text_and_xml_root():
    assert split_sender("你好") == ("", "你好")
    xml = '<msg><fromusername>wxid_root</fromusername><title>邀请</title></msg>'
    origin, body = split_sender(xml)
    assert origin == "wxid_root"
    assert body == xml  # XML 保留原文，阶段二不做格式化


def test_split_sender_strips_room_prefix_without_naming_a_sender():
    """群自己发的系统行以群 ID 作前缀：要剥掉，但不能把群当成"某个人"。"""
    raw = '45948752706@chatroom:\n<sysmsg type="sysmsgtemplate"><a/></sysmsg>'
    origin, body = split_sender(raw)
    assert origin == ""
    assert body == '<sysmsg type="sysmsgtemplate"><a/></sysmsg>'


def test_normalize_self_detection_and_display_name():
    names = {"wxid_me": "我自己", "wxid_other": "老王", "wxid_pure": "小李"}
    me = normalize_message(ACCOUNT, "wxid_other", "老王", make_message(
        sender_username=ACCOUNT, content="好的收到"), names)
    assert me["is_self"] is True and me["sender_name"] == "我"
    other = normalize_message(ACCOUNT, "room@chatroom", "项目群", make_message(), names)
    assert other["is_self"] is False
    assert other["sender_name"] == "老王"
    assert other["content"] == "晚上开会"
    stranger = normalize_message(ACCOUNT, "room@chatroom", "项目群", make_message(
        sender_username="wxid_unknown",
        content="wxid_unknown:\n大家好"), names)
    assert stranger["sender_name"] == "群成员"


def test_normalize_prefers_resolved_sender_over_body_prefix():
    # 分片解析出的真实发送者优先：正文前缀只是兜底，不能盖过它。
    names = {ACCOUNT: "我自己", "wxid_a": "甲", "wxid_b": "乙"}
    row = make_message(sender_username=ACCOUNT, content="wxid_a:\n我自己发的")
    item = normalize_message(ACCOUNT, "room@chatroom", "项目群", row, names)
    assert item["is_self"] is True and item["sender_name"] == "我"
    assert item["content"] == "我自己发的"


def make_shard(tmp_path, aliases, rows, table="Msg_x"):
    path = tmp_path / "message_0.db"
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("CREATE TABLE Name2Id (user_name TEXT, is_session INTEGER)")
        conn.executemany("INSERT INTO Name2Id (rowid, user_name, is_session) VALUES (?,?,?)",
                         aliases)
        conn.execute(f"CREATE TABLE {table} (sort_seq INTEGER, local_id INTEGER, "
                     "real_sender_id INTEGER)")
        conn.executemany(f"INSERT INTO {table} VALUES (?,?,?)", rows)
    return conn


def test_sender_alias_map_uses_per_shard_id_space(tmp_path):
    # 同一条 sort_seq+local_id 在两个分片里可能指向不同的人：解析必须跟着分片走。
    conn = make_shard(tmp_path, [(5, ACCOUNT, 0), (6, "wxid_me2", 0), (9, "", 0)],
                      [(100, 7, 6), (101, 8, 9), (999, 9, 5)])
    found = sender_alias_map(conn, "Msg_x", {(100, 7), (101, 8), (200, 7)})
    conn.close()
    assert found[(100, 7)] == "wxid_me2"
    assert found[(101, 8)] == ""      # 系统消息：分片里名字为空，不是"某个人"
    assert (200, 7) not in found      # 不在本分片，交给别的分片


def test_sender_alias_map_maps_self_rowid_not_global_index(tmp_path):
    # real_sender_id=5 在本分片是自己；全局 SenderName2Id 的 5 是别人，绝不能混用。
    conn = make_shard(tmp_path, [(5, ACCOUNT, 0)], [(100, 7, 5)])
    found = sender_alias_map(conn, "Msg_x", {(100, 7)})
    conn.close()
    assert found[(100, 7)] == ACCOUNT
    item = normalize_message(ACCOUNT, "wxid_friend", "老王", make_message(
        sort_seq=100, local_id=7, sender_username=ACCOUNT, content="我来看看"),
        {ACCOUNT: "我自己", "wxid_friend": "老王"})
    assert item["is_self"] is True and item["sender_name"] == "我"



def test_normalize_unknown_sender_keeps_type_placeholder():
    row = make_message(content="", type="图片", sender_username="wxid_x")
    item = normalize_message(ACCOUNT, "room@chatroom", "群", row, {})
    assert item["message_type"] == "图片"
    assert item["content"] == "[图片]"


def test_normalize_without_any_sender_is_system_message():
    """微信自己也没给发送者的行（拍一拍/撤回）不套到具体人头上，也不参与归组。"""
    row = make_message(sender_username="", sender_id=9,
                       content="<msg><appmsg><title>\"影\" 拍了拍 \"YX\"</title></appmsg></msg>")
    item = normalize_message(ACCOUNT, "room@chatroom", "项目群", row, {"wxid_a": "甲"})
    assert item["sender_name"] == "系统消息" and item["sender_key"] == ""
    assert item["is_self"] is False
    private = normalize_message(ACCOUNT, "wxid_friend", "老王", row, {})
    assert private["sender_name"] == "系统消息"


def test_normalize_uses_session_display_names():
    """显示名由采集侧按会话算好传入：备注 > 群名片 > 昵称，这里只认最终值。"""
    names = {"wxid_a": "科技委员21陈永源", "wxid_b": "26同"}
    first = normalize_message(ACCOUNT, "room@chatroom", "班级群", make_message(
        sender_username="wxid_a", content="交作业"), names)
    assert first["sender_name"] == "科技委员21陈永源"
    assert first["sender_key"] == "wxid_a"


def test_source_id_stability_and_dedup_key():
    row = make_message()
    a = source_id(ACCOUNT, "room@chatroom", row, str(row["content"]))
    b = source_id(ACCOUNT, "room@chatroom", row, str(row["content"]))
    assert a == b
    c = source_id(ACCOUNT, "room@chatroom", {**row, "sort_seq": 101}, "另一条")
    assert a != c
    # 有服务器 ID 时优先用它去重，正文变化不影响
    with_srv = {**row, "server_id": 987}
    assert source_id(ACCOUNT, "u", with_srv, "x") == source_id(ACCOUNT, "u", with_srv, "y")


def test_session_key_isolation(tmp_path):
    scope = account_scope(ACCOUNT)
    assert session_key(ACCOUNT, "wxid_other") == f"wechat:{scope}:wxid_other"
    assert len(scope) == 16


def test_open_isolated_db_without_wechat_reports_clearly(tmp_path):
    # 本机未登录微信时应给出明确错误，而不是堆栈噪声。
    try:
        open_isolated_db(tmp_path, {"wechat_db_dir": str(tmp_path / "no-such-dir")})
        raise AssertionError("应当失败")
    except RuntimeError as exc:
        assert "微信" in str(exc)
