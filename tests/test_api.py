from niannian.api import create_app, type_label
from niannian.wechat import session_key


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


class NoMediaCollector:
    """假采集器：一旦被读 ``media`` 就炸，用来守"消息列表是纯读"这条线。"""

    def __init__(self, db, settings, root):
        self.db, self.settings, self.root = db, settings, root

    def start(self):
        pass

    def stop(self):
        pass

    def update_settings(self, values):
        pass

    def catalog(self):
        return []

    def status(self):
        return {"state": "connected", "account": "", "error": None,
                "last_sync_at": None, "imported": 0, "sessions": 0,
                "mode": "read_only", "running": False}

    @property
    def media(self):
        raise AssertionError("消息列表是纯读：请求线程不许碰图片解密")


def messages_endpoint(app):
    return next(route.endpoint for route in app.routes
                if getattr(route, "path", "") == "/api/sessions/{session_id}/messages")


def test_messages_endpoint_never_touches_media(tmp_path, monkeypatch):
    """回归守卫：缺链接的图片行原样返回，请求线程不去解密。

    以前"读取时顺便补图片链接"会连带触发上游找图片钥匙（整块读微信进程内存，
    实测把界面冻成转圈两分钟），补链接的活已全部下放到采集线程的每轮轮询。
    """
    monkeypatch.setattr("niannian.api.Collector", NoMediaCollector)
    app = create_app(tmp_path)
    store = app.state.store
    sid = session_key("wxid_me", "wxid_other")
    store.sync_catalog("wxid_me", [{"id": sid, "username": "wxid_other", "name": "老王",
                                    "unread": 0, "last_message_at": None}])
    store.ingest([{"session_id": sid, "source_id": "wechat:img1", "sender_key": "wxid_other",
                   "sender_name": "老王", "is_self": False, "content": "[图片]",
                   "message_type": "图片", "created_at": "2026-10-15T00:00:00+00:00",
                   "sort_seq": 500,
                   "metadata": {"attachment": {"kind": "image"},
                                "media_md5": "a" * 32}}])

    page = messages_endpoint(app)(sid, limit=40, before_seq=None, before_id=None)

    item = page["items"][0]
    assert "local_file" not in item["metadata"]      # 没被请求线程动过
    assert item["metadata"]["media_md5"] == "a" * 32
    assert item["type_label"] == "图片"
