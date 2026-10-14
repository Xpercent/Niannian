"""图片附件：定位本机 .dat、解密落盘，库里只存相对链接。

三条关键事实（都是实测出来的，决定了这里的写法）：

1. 磁盘文件名那个 32 位哈希**只在消息行的 ``packed_info_data`` 里**；正文 XML 的
   ``md5`` 属性是另一套值，拿它去 ``msg/attach`` 目录里找文件一条也找不到。
2. 一条图片在本机最多三档：``_h.dat`` 原图（点过"查看原图"才有）、``.dat``
   微信默认下发的那一份、``_t.dat`` 预览图。**没在微信里点开过的图片只有
   ``_t.dat``**，而它能单字节 XOR 解出来——所以预览图必须当成可用素材，不能因为
   "不是原图"就判成"本机没有"。
3. 定位与解密都只读微信自己的文件，绝不驱动界面、不触发下载。
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

# 与上游一致的优先级：原图 > 下发那份 > 预览图。
TIERS = ("original", "mid", "thumb")
TIER_SUFFIX = {"original": "_h", "mid": "", "thumb": "_thumb"}
TIER_LABEL = {"original": "原图", "mid": "本机图", "thumb": "预览图"}
# packed_info_data 是 protobuf，文件哈希以可见 ASCII 十六进制串躺在里面。
HASH_IN_BLOB = re.compile(rb"([0-9a-fA-F]{32})")


def media_scope(account: str) -> str:
    """账号媒体目录名，防止不同账号的同号会话同名文件互相覆盖。"""
    return hashlib.sha256(str(account).encode("utf-8")).hexdigest()[:16]


def hash_from_blob(blob) -> str:
    """从 ``packed_info_data`` 取本机文件名哈希；取不到返回空串。"""
    if isinstance(blob, (bytes, bytearray)) and blob:
        found = HASH_IN_BLOB.search(bytes(blob))
        if found:
            return found.group(1).decode().lower()
    return ""


def pick_tier(files: dict) -> tuple | None:
    """``{tier: (path, bytes)}`` → ``(tier, path)``；一档都没有返回 None。

    ``_h.dat`` 存在但小于 1KB 的是下载中断的空壳，不能当原图用。
    """
    for tier in TIERS:
        hit = files.get(tier)
        if hit and (tier != "original" or hit[1] >= 1024):
            return tier, hit[0]
    return None


def media_root(root: Path) -> Path:
    return Path(root) / "data" / "wechat" / "media"


def safe_media_path(base: Path, stored: str) -> Path | None:
    """把库里存的相对链接解析成媒体根目录内的真实路径，越界一律拒绝。

    链接形如 ``<账号指纹>/<文件名>``，最多两段：不接受绝对路径、盘符、UNC、
    ``..``，也不接受超过两段的深度，避免把库里一个被写坏的字段变成任意文件读取。
    """
    text = str(stored or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or re.match(r"^[a-z]:", text[:2], re.I):
        return None
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts or len(parts) > 2 or any(p == ".." for p in parts):
        return None
    root = Path(base).resolve()
    target = (root / Path(*parts)).resolve()
    return target if target.is_file() and target.is_relative_to(root) else None


class ImageStore:
    """把微信本机的 .dat 解密成本工程媒体目录里的图片，返回可入库的相对链接。"""

    def __init__(self, downloader, root: Path, account: str):
        self._media = downloader
        self.account = account
        self.directory = media_root(root) / media_scope(account)
        self.directory.mkdir(parents=True, exist_ok=True)

    def attach(self, user: str, digest: str, stem: str) -> dict | None:
        """按文件哈希取本机最高一档解密；本机一份都没有返回 None。"""
        if not digest:
            return None
        files = self._media._image_files(user, digest)
        picked = pick_tier(files)
        if not picked:
            return None
        tier, path = picked
        try:
            data = self._media.decrypt_image(path)
        except Exception:
            return None
        if not data:
            return None
        out = self._media._write_decrypted(data, f"{stem}{TIER_SUFFIX[tier]}",
                                           str(self.directory))
        return {"local_file": f"{self.directory.name}/{Path(out).name}",
                "tier": tier, "tier_label": TIER_LABEL[tier],
                "bytes": os.path.getsize(out)}

    def resolve(self, stored: str) -> Path | None:
        """把库里的相对链接解析成媒体目录内的真实路径。"""
        return safe_media_path(self.directory.parent, stored)
