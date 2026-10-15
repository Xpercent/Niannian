"""上游私有接口契约：bump submodule pin 之前先认接口，不认版本号。

我们复用着上游一批下划线开头的成员（AGENTS.md 的「上游库的关键陷阱」与「图片附件
的关键事实」里逐条记着），而上游没有义务保住它们。本仓库其余用例都打在替身上
（`test_media.py` 的 FakeMedia/GuardedMedia、`test_wechat.py` 的假 wx），所以
**上游一旦改名，一百多条测试照样全绿、生产立刻瞎**。这一条补的就是这个洞。

每个名字都注明本仓库的调用点：升级后这里红了，照着行号改就行，不用重新考古。
`test_wechat.py::test_upstream_pin_resolves_senders_per_shard` 守的是发送者那一件
事（1.2.6.1/issue #34），与这里互补，不重复。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# 我们在 niannian/ 里实际调用到的上游 WeChatDB 成员 -> 调用点。
# 标注一律写符号名不写行号：第⑤步把 wechat.py 拆开之后这些引用还得有效。
READER_SURFACE = {
    "wxid": "Collector._attach 取当前账号",
    "get_self_info": "Collector._attach",
    "extract_master_key": "Collector._ensure_media_key（只填回同一个读取器，不再开一个）",
    "get_sessions": "Collector._sync_catalog",
    "get_messages": "Collector._import_recent（limit/offset 翻页）",
    "get_new_messages": "Collector._poll_followed（since_seq 增量）",
    "nickname_map": "IsolatedDB.display_names 的第一档",
    "get_group_members": "IsolatedDB.display_names 补群名片那一档",
    "_msg_conns": "IsolatedDB._each_shard（按会话命中的分片逐个跑查询）",
    "_contact_conn": "IsolatedDB.room_nicknames",
}

# 图片这条链路全靠上游私有方法：定位、解密、落盘、四步弄钥匙。
MEDIA_SURFACE = {
    "_image_files": "ImageStore.attach 定位——一次目录扫描出三档副本",
    "decrypt_image": "ImageStore.attach 解密 .dat → 图片字节",
    "_write_decrypted": "ImageStore.attach 落盘——按文件头判格式（wxgf 转 jpg）",
    "_derive_cfg_key": "ImageStore._free_key 免费拿钥匙第一条路（cfg 现派生）",
    "_load_persisted_key": "ImageStore._free_key 第二条路（上次落盘的缓存）",
    "_scan_aes_key": "ImageStore.prepare_key 唯一允许采集线程调的重活，必须 monitor=False",
    "_persist_key": "ImageStore._adopt/prepare_key——cfg 派生那份也要落盘，否则下次还得扫",
}

# 这些是**实例属性**而不是方法：IsolatedDB 靠覆写它们把缓存钉在工程内。
OVERRIDE_POINTS = ("workdir", "keys_file")


@pytest.fixture(scope="module")
def upstream():
    if str(ROOT / "wechatauto-replica") not in sys.path:
        sys.path.insert(0, str(ROOT / "wechatauto-replica"))
    from wechatauto.db import WeChatDB
    from wechatauto.media import MediaDownloader
    return WeChatDB, MediaDownloader


def _missing(owner, names):
    """一次跑完再报，别升级时一个一个试。"""
    return {name: where for name, where in names.items() if not hasattr(owner, name)}


def test_upstream_reader_surface_intact(upstream):
    we_chat_db, _ = upstream
    lost = _missing(we_chat_db, READER_SURFACE)
    assert not lost, "上游读取器接口变了，这些调用点要跟着改：" + "；".join(
        f"{name}（{where}）" for name, where in sorted(lost.items()))


def test_upstream_media_surface_intact(upstream):
    _, media_downloader = upstream
    lost = _missing(media_downloader, MEDIA_SURFACE)
    assert not lost, "上游图片接口变了，media.py 的取图与钥匙全断：" + "；".join(
        f"{name}（{where}）" for name, where in sorted(lost.items()))
    # 注入槽位是个实例属性，不是方法——闸门（key_ready）就挂在这上面
    assert "_image_key" in inspect.getsource(media_downloader.__init__), (
        "上游 MediaDownloader.__init__ 不再初始化 _image_key："
        "ImageStore._adopt 的注入槽位没了，钥匙闸门要重写")


def test_upstream_override_points_still_writable_attributes(upstream):
    """workdir / keys_file 必须仍是可覆写的实例属性。

    IsolatedDB 覆写它们是**带 setter 的**（见 `niannian/wechat.py` 里 open_isolated_db
    那个子类）：如果上游把其中某个改成方法或只读，我们的缓存隔离会静默失效，解密
    缓存跑到临时目录去，与「一个进程里只能有一个读取器」那条约定一起崩。
    """
    we_chat_db, _ = upstream
    init_source = inspect.getsource(we_chat_db.__init__)
    for name in OVERRIDE_POINTS:
        assert f"self.{name} =" in init_source, (
            f"上游 WeChatDB.__init__ 不再直接赋值 self.{name}，"
            "IsolatedDB 的 property 覆写方式要重新确认")
    assert hasattr(we_chat_db, "_stable_key_dirs"), "上游没有 _stable_key_dirs，密钥缓存位置约定变了"


def test_upstream_call_shapes_match_positional_arguments(upstream):
    """我们全是按位置传的：参数个数/顺序变了要在这里先撞，别等真机。"""
    we_chat_db, media_downloader = upstream

    def names(func):
        return list(inspect.signature(func).parameters)

    assert names(media_downloader._image_files) == ["self", "user", "md5"], (
        f"实际是 {names(media_downloader._image_files)}")
    assert names(media_downloader.decrypt_image)[:2] == ["self", "dat_path"]
    assert names(media_downloader._write_decrypted)[:4] == [
        "self", "data", "stem", "save_dir"]
    assert names(media_downloader._scan_aes_key)[:2] == ["self", "monitor"], (
        "monitor 参数不在第二位：prepare_key 会漏出上游的循环扫描，那是转圈两分钟的根因")
    assert names(we_chat_db.get_messages)[:2] == ["self", "user"]
    assert "since_seq" in names(we_chat_db.get_new_messages), (
        "增量读取不再按 since_seq，水位增量要重新对接")
    # 构造时我们只传 db_dir/account/workdir 三个关键字
    ctor = names(we_chat_db.__init__)
    assert {"db_dir", "account", "workdir"} <= set(ctor), f"实际是 {ctor}"
