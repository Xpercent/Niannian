import sqlite3
import sys
from pathlib import Path

from niannian.wechat import (Collector, account_scope, normalize_message,
                             open_isolated_db, quote_name,
                             server_seq_map, session_key, source_id, split_sender)

ROOT = Path(__file__).resolve().parent.parent

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


def test_upstream_pin_resolves_senders_per_shard():
    """发送者解析交回上游了（1.2.6.1 / issue #34），本仓库不再有兜底补丁。

    所以 pin 不能往回退：退回 1.2.5.1 会拿 message_resource.db 的全局
    ``SenderName2Id`` 解析分片编号，气泡上就是毫不相干的人。这里认接口不认版本号——
    全局索引被删掉、按分片解析的入口在，才算上游还担得起这件事。
    """
    if str(ROOT / "wechatauto-replica") not in sys.path:
        sys.path.insert(0, str(ROOT / "wechatauto-replica"))
    from wechatauto.db import WeChatDB

    assert not hasattr(WeChatDB, "_sender_id_index")
    assert hasattr(WeChatDB, "sender_username")
    assert hasattr(WeChatDB, "_name2id_map")



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


PAT_XML = ('<msg><appmsg><type>62</type><title>"甲" 拍了拍 "乙" 说：“家里管饭吗”</title>'
           "<patinfo><fromusername>wxid_a</fromusername>"
           "<pattedusername>wxid_b</pattedusername></patinfo></appmsg>"
           "<fromusername></fromusername></msg>")
QUOTE_XML = ('<msg><appmsg><title>记得报名</title><type>57</type>'
             "<refermsg><displayname>甲</displayname><content>一长段通知</content>"
             "</refermsg></appmsg><fromusername>wxid_a</fromusername></msg>")


def test_normalize_takes_pat_sender_from_patinfo():
    """拍一拍没有群前缀、根节点 fromusername 也是空的：动手的人只在 patinfo 里，
    署名要认回真人，正文要落那句整话而不是 XML。"""
    row = make_message(sender_username="", content=PAT_XML)
    item = normalize_message(ACCOUNT, "room@chatroom", "项目群", row, {"wxid_a": "甲"})
    assert item["sender_key"] == "wxid_a" and item["sender_name"] == "甲"
    assert item["content"] == '"甲" 拍了拍 "乙" 说：“家里管饭吗”'
    mine = normalize_message(ACCOUNT, "room@chatroom", "项目群",
                             make_message(sender_username=ACCOUNT, content=PAT_XML.replace(
                                 "wxid_a", ACCOUNT)), {ACCOUNT: "我自己"})
    assert mine["is_self"] is True and mine["sender_name"] == "我"


def test_quote_name_resolved_from_author_when_wechat_gives_none():
    """私聊里微信不给 displayname：作者是自己写"我"，否则走显示名表，再落到会话名。"""
    names = {ACCOUNT: "我自己", "wxid_friend": "老王"}
    mine = {"author": ACCOUNT}
    quote_name(mine, ACCOUNT, "老王", names, False)
    assert mine["name"] == "我"
    friend = {"author": "wxid_friend"}
    quote_name(friend, ACCOUNT, "老王", names, False)
    assert friend["name"] == "老王"
    # 会话名兜底只给私聊：群聊里认不出的人不猜，宁可只显示被引内容
    group = {"author": "wxid_unknown"}
    quote_name(group, ACCOUNT, "班级群", names, True)
    assert "name" not in group
    kept = {"author": ACCOUNT, "name": "微信给的名字"}
    quote_name(kept, ACCOUNT, "老王", names, False)
    assert kept["name"] == "微信给的名字"


def test_normalize_quote_body_is_the_reply_only():
    """引用消息：正文只留回复那句话，被引内容以短标签进 metadata，全文不入库。"""
    row = make_message(sender_username="wxid_a", content=QUOTE_XML)
    item = normalize_message(ACCOUNT, "room@chatroom", "项目群", row, {"wxid_a": "甲"})
    assert item["content"] == "记得报名"
    quoted = item["metadata"]["attachment"]["quoted"]
    assert quoted["label"] == "一长段通知" and "<" not in quoted["label"]
    assert item["metadata"]["attachment"]["kind"] == "quote"


TRANSFER_XML = ('<msg><appmsg><title>微信转账</title><type>2000</type><wcpayinfo>'
                "<paysubtype>1</paysubtype><feedesc>￥10.00</feedesc>"
                "<payer_username>wxid_a</payer_username>"
                "<receiver_username>wxid_b</receiver_username>"
                "</wcpayinfo></appmsg></msg>")


def test_normalize_transfer_direction_and_target():
    """同一笔钱微信发两条卡片，双方 wxid 一模一样：方向只能拿发送者去比。"""
    names = {ACCOUNT: "我自己", "wxid_a": "(=°Д°=)", "wxid_b": "影"}
    room = "room@chatroom"
    paid = normalize_message(ACCOUNT, room, "宿舍群", make_message(
        sender_username="wxid_a", content=TRANSFER_XML), names)
    assert paid["content"] == "向影 [转账] ￥10.00"
    received = normalize_message(ACCOUNT, room, "宿舍群", make_message(
        sender_username="wxid_b", content=TRANSFER_XML), names)
    assert received["content"] == "收到(=°Д°=) [转账] ￥10.00"
    assert received["metadata"]["attachment"] == {
        **received["metadata"]["attachment"], "direction": "in", "target": "(=°Д°=)"}
    # 第三方视角的行（发送者既不是付款也不是收款）不加方向，也不贴 wxid
    stranger = normalize_message(ACCOUNT, room, "宿舍群", make_message(
        sender_username="wxid_c", content=TRANSFER_XML), names)
    assert stranger["content"] == "[转账] ￥10.00"
    # 自己付钱时同样成立：对象仍是收款方
    mine = normalize_message(ACCOUNT, room, "宿舍群", make_message(
        sender_username=ACCOUNT,
        content=TRANSFER_XML.replace("wxid_a", ACCOUNT)), names)
    assert mine["is_self"] is True and mine["content"] == "向影 [转账] ￥10.00"


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


def test_server_seq_map_resolves_quoted_locator(tmp_path):
    """引用定位：按 server_id 反查 sort_seq；查不到的键不出现，调用方按 0 处理。"""
    conn = sqlite3.connect(tmp_path / "message_0.db")
    with conn:
        conn.execute("CREATE TABLE Msg_x (server_id INTEGER, sort_seq INTEGER)")
        conn.executemany("INSERT INTO Msg_x VALUES (?,?)",
                         [(1802656594381821731, 100), (0, 101)])
    found = server_seq_map(conn, "Msg_x",
                           {"1802656594381821731", "5147842299794484807"})
    conn.close()
    assert found == {"1802656594381821731": 100}


def test_attach_quotes_fills_locator_and_defaults_to_zero():
    class Stub:
        _wx = type("WX", (), {"quote_seqs": lambda self, user, ids: {"7": 42}})()

    quote = {"metadata": {"attachment": {"kind": "quote", "quoted": {"svrid": "7"}}}}
    missed = {"metadata": {"attachment": {"kind": "quote", "quoted": {"svrid": "9"}}}}
    image = {"metadata": {"attachment": {"kind": "image"}}}
    Collector._attach_quotes(Stub(), "room@chatroom", [quote, missed, image])
    assert quote["metadata"]["attachment"]["quoted"]["sort_seq"] == 42
    assert missed["metadata"]["attachment"]["quoted"]["sort_seq"] == 0
    assert "sort_seq" not in image["metadata"]["attachment"]


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


class FakeImageStore:
    """只测"补链接"这条接线：真解密要读微信本机文件，测试里不碰。"""

    def __init__(self, linked=True):
        self.calls = []
        self._linked = linked

    def attach(self, user, digest, stem):
        self.calls.append((user, digest, stem))
        if not self._linked:
            return None
        return {"local_file": f"scope/{stem}.jpg", "tier": "mid",
                "tier_label": "本机图", "bytes": 4096}


def make_image_db(tmp_path, monitored=True):
    from niannian.storage import Database

    store = Database(tmp_path / "t.db")
    store.init()
    sid = session_key(ACCOUNT, "wxid_other")
    store.sync_catalog(ACCOUNT, [{"id": sid, "username": "wxid_other", "name": "老王",
                                  "unread": 0, "last_message_at": None}])
    if monitored:
        store.set_monitored(sid, True)
    store.ingest([{**normalize_message(ACCOUNT, "wxid_other", "老王",
                                        make_message(content="看图"), {}),
                   "source_id": "wechat:img1", "sort_seq": 500,
                   "metadata": {"attachment": {"kind": "image"},
                                "media_md5": "a" * 32, "local_id": 7}}])
    return store, sid


def collector_for(store, tmp_path, media):
    collector = Collector(store, {"poll_seconds": 1.0}, tmp_path)
    collector._state["account"] = ACCOUNT
    collector._media = media
    return collector


def test_catchup_images_links_and_writes_metadata(tmp_path):
    """图片链接由采集线程每轮补：凭 media_md5 定位，落名带会话与序号，回写 metadata。"""
    store, sid = make_image_db(tmp_path)
    media = FakeImageStore()
    assert collector_for(store, tmp_path, media)._catchup_images() == 1
    assert media.calls == [("wxid_other", "a" * 32, "wxid_other_500")]
    row = store.messages_page(sid, limit=10)["items"][0]
    assert row["metadata"]["local_file"] == "scope/wxid_other_500.jpg"
    assert row["metadata"]["tier_label"] == "本机图"
    # 补好的行下一轮不该再出现在清单里
    assert store.pending_images(limit=10) == []


def test_catchup_images_keeps_row_pending_when_file_not_here_yet(tmp_path):
    """本机一份都没有时不动 metadata：文件可能之后才落盘，下一轮还会再试。"""
    store, sid = make_image_db(tmp_path)
    assert collector_for(store, tmp_path, FakeImageStore(linked=False))._catchup_images() == 0
    assert [row["sort_seq"] for row in store.pending_images(limit=10)] == [500]


def test_catchup_images_does_nothing_before_connected(tmp_path):
    """没连上微信（media=None）就安静跳过，不抛错也不把整轮采集带崩。"""
    store, _ = make_image_db(tmp_path)
    assert collector_for(store, tmp_path, None)._catchup_images() == 0


def test_catchup_images_skips_unfollowed_sessions(tmp_path):
    """没关注的会话不进补图清单：补图范围必须与采集范围一致。"""
    store, _ = make_image_db(tmp_path, monitored=False)
    media = FakeImageStore()
    assert collector_for(store, tmp_path, media)._catchup_images() == 0
    assert media.calls == []


class FakeWX:
    """够用的假上游库：_attach 只要账号、昵称表和会话目录。"""

    wxid = "wxid_me"
    account = "wxid_me_13a7"
    cfg_dword = 4096

    def get_self_info(self):
        return {"username": "wxid_me", "nick_name": "我"}

    def nickname_map(self, refresh=False):
        return {"wxid_other": "老王"}

    def get_sessions(self, limit=100):
        return []


def bare_collector(tmp_path):
    from niannian.storage import Database

    store = Database(tmp_path / "t.db")
    store.init()
    return Collector(store, {"poll_seconds": 1.0}, tmp_path)


def test_attach_failure_waits_for_wechat_and_can_recover(tmp_path, monkeypatch):
    """连不上不再退出线程：置 waiting_wechat，等微信打开后自己接上。"""
    import niannian.wechat as wxmod

    state = {"opened": False}

    def fake_open(root, settings):
        if not state["opened"]:
            raise RuntimeError("未检测到 Weixin.exe，请先登录微信再运行")
        return FakeWX()

    monkeypatch.setattr(wxmod, "open_isolated_db", fake_open)
    monkeypatch.setattr(wxmod, "wechat_running", lambda: False)
    collector = bare_collector(tmp_path)

    assert collector._attach() is False
    status = collector.status()
    assert status["state"] == "waiting_wechat"
    assert "Weixin" in status["error"]
    assert collector._wx is None and collector._media is None

    state["opened"] = True                      # 用户把微信打开了
    assert collector._attach() is True
    status = collector.status()
    assert status["state"] == "connected" and status["account"] == "wxid_me"


def test_attach_failure_with_wechat_running_is_error(tmp_path, monkeypatch):
    """微信开着却读不通是真故障，界面要说"异常"，不能含糊成"等微信启动"。"""
    import niannian.wechat as wxmod

    def fake_open(root, settings):
        raise RuntimeError("数据库无可用密钥: contact/contact.db")

    monkeypatch.setattr(wxmod, "open_isolated_db", fake_open)
    monkeypatch.setattr(wxmod, "wechat_running", lambda: True)
    collector = bare_collector(tmp_path)

    assert collector._attach() is False
    assert collector.status()["state"] == "error"


def test_reattach_respects_cooldown(tmp_path, monkeypatch):
    """没连上时按冷却重试，不是每轮都重开一遍微信库。"""
    import time as _time

    import niannian.wechat as wxmod

    calls = {"n": 0}

    def fake_open(root, settings):
        calls["n"] += 1
        raise RuntimeError("未检测到 Weixin.exe")

    monkeypatch.setattr(wxmod, "open_isolated_db", fake_open)
    monkeypatch.setattr(wxmod, "wechat_running", lambda: False)
    collector = bare_collector(tmp_path)
    collector._attach()
    assert calls["n"] == 1

    collector._try_reattach()                   # 冷却未到：一次都不重试
    assert calls["n"] == 1

    collector._next_attach = _time.monotonic() - 1
    collector._try_reattach()
    assert calls["n"] == 2


def test_commands_fail_fast_when_disconnected(tmp_path):
    """线程活着但没连上：命令立刻报错，不能让网页请求干等 90 秒超时。"""
    import threading

    collector = bare_collector(tmp_path)
    holder = threading.Event()
    collector._thread = threading.Thread(target=holder.wait, daemon=True)
    collector._thread.start()
    try:
        assert collector._wx is None
        assert collector.catalog() == []        # 回落到本地已存目录，不排队
        try:
            collector.reimport("wechat:sc:wxid_other", 10)
            raise AssertionError("应当立刻报错")
        except RuntimeError as exc:
            assert "微信还没连上" in str(exc)
    finally:
        holder.set()


def test_reader_thread_recovers_after_wechat_starts(tmp_path, monkeypatch):
    """端到端守这条：微信后启动，采集线程自己接上，不需要重启服务。"""
    import time as _time

    import niannian.wechat as wxmod

    calls = {"n": 0}

    def fake_open(root, settings):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("未检测到 Weixin.exe，请先登录微信再运行")
        return FakeWX()

    monkeypatch.setattr(wxmod, "open_isolated_db", fake_open)
    monkeypatch.setattr(wxmod, "wechat_running", lambda: False)
    monkeypatch.setattr(wxmod, "ATTACH_RETRY_SECONDS", 0.2)
    monkeypatch.setattr(Collector, "_build_media", lambda self, account: None)

    collector = bare_collector(tmp_path)
    collector.start()
    try:
        deadline = _time.monotonic() + 6.0
        while _time.monotonic() < deadline and collector.status()["state"] != "connected":
            _time.sleep(0.05)
        assert collector.status()["state"] == "connected"
        assert calls["n"] >= 2                  # 是同一条线程重试上来的，不是重启
        assert collector.status()["running"] is True
    finally:
        collector.stop()


class FakeKeyMedia:
    def __init__(self, ready=False):
        self.key_ready = ready
        self.prepared = 0

    def prepare_key(self):
        self.prepared += 1
        self.key_ready = True


class FakeWXNoCfg(FakeWX):
    """连接建立在微信没开的时候：库能读，但 cfg 编号压根没取到。"""

    cfg_dword = None


def test_media_key_reattaches_when_cfg_was_never_taken(tmp_path, monkeypatch):
    """cfg 编号缺失时不该去扫内存（扫了也派生不出钥匙），要的是重连一次。"""
    import niannian.wechat as wxmod

    monkeypatch.setattr(wxmod, "wechat_running", lambda: True)
    collector = bare_collector(tmp_path)
    collector._wx = FakeWXNoCfg()
    media = FakeKeyMedia()
    collector._media = media
    rebuilt = []
    monkeypatch.setattr(collector, "_detach", lambda: rebuilt.append("detach"))
    monkeypatch.setattr(collector, "_attach", lambda: rebuilt.append("attach"))

    collector._ensure_media_key()

    assert rebuilt == ["detach", "attach"]
    assert media.prepared == 0


def test_media_key_waits_for_wechat_without_probing_every_round(tmp_path, monkeypatch):
    """微信没开：什么都不做，但要定好冷却，别每轮都去问一次进程表。"""
    import time

    import niannian.wechat as wxmod

    monkeypatch.setattr(wxmod, "wechat_running", lambda: False)
    collector = bare_collector(tmp_path)
    collector._wx = FakeWX()
    media = FakeKeyMedia()
    collector._media = media

    collector._ensure_media_key()
    assert media.prepared == 0
    assert collector._next_key_at > time.monotonic()


def test_media_key_prepare_is_throttled(tmp_path, monkeypatch):
    """扫内存是几十秒级重活：一次尝试之后要等冷却，不能每轮都扫。"""
    import niannian.wechat as wxmod

    monkeypatch.setattr(wxmod, "wechat_running", lambda: True)
    collector = bare_collector(tmp_path)
    collector._wx = FakeWX()
    media = FakeKeyMedia()
    collector._media = media

    collector._ensure_media_key()
    collector._ensure_media_key()                # 冷却未到
    assert media.prepared == 1

    media.key_ready = False
    collector._next_key_at = 0.0
    collector._ensure_media_key()
    assert media.prepared == 2


def test_media_key_is_untouched_when_already_ready(tmp_path, monkeypatch):
    """钥匙已在手就一个字都不做：不探进程、不扫内存，轮询照旧读文字。"""
    import niannian.wechat as wxmod

    probes = []
    monkeypatch.setattr(wxmod, "wechat_running", lambda: probes.append(1) or True)
    collector = bare_collector(tmp_path)
    collector._wx = FakeWX()
    collector._media = FakeKeyMedia(ready=True)

    collector._ensure_media_key()

    assert probes == []
