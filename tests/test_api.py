from niannian.api import type_label


def test_type_label_prefers_parsed_kind_over_upstream_label():
    """上游把 8 种东西都叫"文件/链接/卡片"，徽标要跟着解析出的 kind 走。"""
    row = {"message_type": "文件/链接/卡片", "metadata": {"attachment": {"kind": "file"}}}
    assert type_label(row) == "文件"
    pat = {"message_type": "文件/链接/卡片", "metadata": {"attachment": {"kind": "pat"}}}
    assert type_label(pat) == "拍一拍"


def test_type_label_falls_back_to_upstream_label():
    # 纯文本、以及本轮没解析的引用消息没有 kind：沿用上游标签，不编一个出来
    assert type_label({"message_type": "文本", "metadata": {"attachment": None}}) == "文本"
    assert type_label({"message_type": "文件/链接/卡片", "metadata": {}}) == "文件/链接/卡片"
    assert type_label({}) == "消息"
