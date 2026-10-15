import os
from pathlib import Path

from niannian.media import (hash_from_blob, media_scope, pick_tier, safe_media_path)


def test_hash_from_blob_takes_packed_info_not_xml():
    packed = b'\x08\x05\x10D\x1a"" 3d11d910dfa03787d8b96a2964079fd2X\x00'
    assert hash_from_blob(packed) == "3d11d910dfa03787d8b96a2964079fd2"
    assert hash_from_blob(b"\x08D\x10\x05X\x00") == ""
    assert hash_from_blob(None) == ""


def test_pick_tier_prefers_original_and_drops_empty_shell():
    assert pick_tier({}) is None
    assert pick_tier({"thumb": ("/p/_t.dat", 5000)}) == ("thumb", "/p/_t.dat")
    assert pick_tier({"mid": ("/p/m.dat", 40000), "thumb": ("/p/t.dat", 5000)})[0] == "mid"
    assert pick_tier({"original": ("/p/h.dat", 900), "mid": ("/p/m.dat", 40000)})[0] == "mid"
    assert pick_tier({"original": ("/p/h.dat", 900000), "mid": ("/p/m.dat", 40000)})[0] == "original"


def test_safe_media_path_blocks_traversal_and_absolute(tmp_path):
    base = tmp_path / "media"
    (base / "abc123").mkdir(parents=True)
    (base / "abc123" / "pic.jpg").write_bytes(b"x")
    assert safe_media_path(base, "abc123/pic.jpg") == base / "abc123" / "pic.jpg"
    assert safe_media_path(base, "abc123\\pic.jpg") == base / "abc123" / "pic.jpg"
    for bad in ("", "/etc/passwd", "C:\\Windows\\a.jpg", "\\\\srv\\sh\\a.jpg",
                "abc123/../secret", "../../etc/passwd", "abc123/sub/pic.jpg",
                "abc123/missing.jpg"):
        assert safe_media_path(base, bad) is None, bad


def test_media_scope_is_stable_and_short():
    assert media_scope("wxid_me") == media_scope("wxid_me")
    assert len(media_scope("wxid_me")) == 16
    assert media_scope("wxid_me") != media_scope("wxid_you")


def test_image_store_writes_inside_its_directory(tmp_path, monkeypatch):
    """attach 只用上游的“定位 + 解密 + 落盘”，链接必须是媒体目录内的相对路径。"""
    from niannian.media import ImageStore

    class FakeMedia:
        def __init__(self):
            self._image_key = None

        def _derive_cfg_key(self):
            return ("k" * 32, 0x88)      # cfg 在手：钥匙免费，构造时即就绪

        def _load_persisted_key(self):
            return None

        def _persist_key(self, key):
            pass

        def _image_files(self, user, digest):
            return {"thumb": (f"/fake/{digest}_t.dat", 4096)}

        def decrypt_image(self, path):
            return b"\xff\xd8\xff\xe0" + b"0" * 40

        def _write_decrypted(self, data, stem, save_dir):
            os.makedirs(save_dir, exist_ok=True)
            out = Path(save_dir) / (stem + ".jpg")
            out.write_bytes(data)
            return str(out)

    store = ImageStore(FakeMedia(), tmp_path, "wxid_me")
    assert store.key_ready is True
    linked = store.attach("room@chatroom", "ab" * 16, "room@chatroom_123")
    assert linked["tier"] == "thumb" and linked["tier_label"] == "预览图"
    assert linked["local_file"].startswith(f"{media_scope('wxid_me')}/")
    assert (tmp_path / "data" / "wechat" / "media" / linked["local_file"]).is_file()
    assert store.resolve(linked["local_file"]) is not None
    assert store.attach("room@chatroom", "", "x") is None


class GuardedMedia:
    """上游替身：把"要钥匙"的三个入口都记下来，用来证明解密不会被顺手触发扫描。"""

    def __init__(self, cfg_key=None, persisted=None, scanned=None):
        self._image_key = None
        self.calls = []
        self._cfg_key = cfg_key
        self._persisted = persisted
        self._scanned = scanned

    def _derive_cfg_key(self):
        self.calls.append("derive")
        return self._cfg_key

    def _load_persisted_key(self):
        self.calls.append("persisted")
        return self._persisted

    def _scan_aes_key(self, monitor=False):
        self.calls.append(f"scan:{monitor}")
        return self._scanned

    def _persist_key(self, key):
        self.calls.append(f"persist:{key}")

    def _image_files(self, user, digest):
        return {"mid": (f"/fake/{digest}.dat", 4096)}

    def decrypt_image(self, path):
        self.calls.append("decrypt")
        return b"\xff\xd8\xff\xe0"

    def _write_decrypted(self, data, stem, save_dir):
        os.makedirs(save_dir, exist_ok=True)
        out = Path(save_dir) / (stem + ".jpg")
        out.write_bytes(data)
        return str(out)


def test_attach_refuses_without_key_and_never_reaches_the_scanner(tmp_path):
    """没钥匙就是不解密：上游那条"没钥匙就去扫内存"的路一条都不能走。

    这条闸门是转圈两分钟的根治点——网页请求线程也会调 attach，但绝不允许它
    连带触发进程内存扫描。
    """
    from niannian.media import ImageStore

    media = GuardedMedia()
    store = ImageStore(media, tmp_path, "wxid_me")
    assert store.key_ready is False
    assert store.attach("wxid_a", "ab" * 16, "wxid_a_1") is None
    assert "decrypt" not in media.calls and not any("scan" in c for c in media.calls)


def test_prepare_key_scans_once_without_monitor_and_persists(tmp_path):
    """补钥匙时扫内存只扫一次（monitor=False），扫到就落盘，之后不再扫。"""
    from niannian.media import ImageStore

    media = GuardedMedia(scanned="s" * 32)
    store = ImageStore(media, tmp_path, "wxid_me")
    assert store.prepare_key() is True
    assert "scan:False" in media.calls and "scan:True" not in media.calls
    assert "persist:" + "s" * 32 in media.calls
    assert media._image_key == "s" * 32
    assert store.attach("wxid_a", "ab" * 16, "wxid_a_1")["tier"] == "mid"
    assert store.prepare_key() is True                    # 已就绪，不再扫
    assert media.calls.count("scan:False") == 1


def test_prepare_key_keeps_the_gate_closed_when_nothing_is_found(tmp_path):
    """扫不到就照旧关着：宁可显示"本机暂无"，也不让请求线程去反复扫内存。"""
    from niannian.media import ImageStore

    media = GuardedMedia(scanned=None)
    store = ImageStore(media, tmp_path, "wxid_me")
    assert store.prepare_key() is False
    assert store.key_ready is False
    assert store.attach("wxid_a", "ab" * 16, "wxid_a_1") is None


def test_key_from_cache_avoids_any_scan(tmp_path):
    """上次扫到的钥匙落盘后，这一轮直接复用，一次内存都不扫。"""
    from niannian.media import ImageStore

    media = GuardedMedia(persisted="p" * 32)
    store = ImageStore(media, tmp_path, "wxid_me")
    assert store.key_ready is True
    assert not any("scan" in c for c in media.calls)
    assert media._image_key == "p" * 32


def test_cfg_derived_key_is_persisted_for_next_time(tmp_path):
    """cfg 现派的钥匙要落盘：下次服务在微信没开时启动，也能直接解图，不用再等。

    微信重启后这把钥匙会变，所以取用缓存时上游自己会校验，过期缓存不会将错就错。
    """
    from niannian.media import ImageStore

    media = GuardedMedia(cfg_key=("c" * 32, 0x88))
    store = ImageStore(media, tmp_path, "wxid_me")
    assert store.key_ready is True
    assert "persist:" + "c" * 32 in media.calls
    assert not any("scan" in c for c in media.calls)

    cached = GuardedMedia(persisted="c" * 32)
    assert ImageStore(cached, tmp_path, "wxid_me").key_ready is True
    assert not any(c.startswith("persist:") for c in cached.calls)   # 本就是缓存来的
