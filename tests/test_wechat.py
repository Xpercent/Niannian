from niannian.wechat import (account_scope, normalize_message, open_isolated_db,
                             session_key, source_id, split_sender)

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
    assert body == xml  # XML 保留原文，阶段一不做格式化


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


def test_normalize_unknown_sender_keeps_type_placeholder():
    row = make_message(content="", type="图片", sender_username="wxid_x")
    item = normalize_message(ACCOUNT, "room@chatroom", "群", row, {})
    assert item["message_type"] == "图片"
    assert item["content"] == "[图片]"


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
