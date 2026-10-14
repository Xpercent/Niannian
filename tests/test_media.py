import os
from pathlib import Path

from niannian.media import (hash_from_blob, media_scope, parse_image, pick_tier,
                            safe_media_path, summarize)

IMAGE_XML = (
    '<?xml version="1.0"?>\n<msg>\n\t<img aeskey="d427de8ff397a962a0d17fa70e5defb6" '
    'encryver="1" cdnthumblength="9882" cdnthumbheight="210" cdnthumbwidth="118" '
    'length="76183" md5="6793b97ea4cb8f58ba2f65086e3b761b"><live><md5 /></live>'
    "</img>\n</msg>"
)


def test_parse_image_identifies_image_and_ignores_xml_md5():
    """只认 <img>；XML 的 md5 不是磁盘文件名，故意不解析出来免得被误用。"""
    assert parse_image(IMAGE_XML) == {"kind": "image"}


def test_parse_image_rejects_text_and_other_attachments():
    assert parse_image("晚上开会") is None
    assert parse_image("<msg><voicemsg voicelength='123'/></msg>") is None
    assert parse_image("<msg><img broken") is None


def test_summarize_replaces_raw_xml():
    assert summarize(parse_image(IMAGE_XML)) == "[图片]"
    assert summarize(None) == ""


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
    linked = store.attach("room@chatroom", "ab" * 16, "room@chatroom_123")
    assert linked["tier"] == "thumb" and linked["tier_label"] == "预览图"
    assert linked["local_file"].startswith(f"{media_scope('wxid_me')}/")
    assert (tmp_path / "data" / "wechat" / "media" / linked["local_file"]).is_file()
    assert store.resolve(linked["local_file"]) is not None
    assert store.attach("room@chatroom", "", "x") is None
