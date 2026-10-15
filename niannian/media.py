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

import os
import re
from pathlib import Path

from .ids import account_scope

# 与上游一致的优先级：原图 > 下发那份 > 预览图。
TIERS = ("original", "mid", "thumb")
TIER_SUFFIX = {"original": "_h", "mid": "", "thumb": "_thumb"}
TIER_LABEL = {"original": "原图", "mid": "本机图", "thumb": "预览图"}
# packed_info_data 是 protobuf，文件哈希以可见 ASCII 十六进制串躺在里面。
HASH_IN_BLOB = re.compile(rb"([0-9a-fA-F]{32})")


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


def tier_rank(tier) -> int:
    """档位高低的排序值：越大越高，认不出的当最低（不会覆盖已认出的）。"""
    return len(TIERS) - TIERS.index(tier) if tier in TIERS else 0


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
    """把微信本机的 .dat 解密成本工程媒体目录里的图片，返回可入库的相对链接。

    **钥匙闸门**：图片 AES 钥匙没在手之前，:meth:`attach` 一律不解密。上游一旦发现
    没钥匙就会去整块扫微信进程内存（几十秒级，且 Python 的 GIL 会让整个服务冻住），
    那种事只允许采集线程显式叫 :meth:`prepare_key` 去做，不能由一次页面读取触发。
    """

    def __init__(self, downloader, root: Path, account: str):
        self._media = downloader
        self.account = account
        # 账号指纹当目录名：不同账号的同号会话不会互相覆盖
        self.directory = media_root(root) / account_scope(account)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._key_ready = self._adopt(self._free_key())

    @property
    def key_ready(self) -> bool:
        """图片钥匙是否已在手——没在手就绝不碰解密。"""
        return self._key_ready

    def _free_key(self) -> tuple:
        """不用扫内存就能拿到的钥匙，返回 ``(钥匙, 是不是 cfg 现派的)``。

        优先级：cfg 编号派生 → 上次拿到后落盘的缓存。派生出来的那份要顺手落盘，
        否则下次服务在微信没开时启动，又得等微信开起来重要一遍。
        """
        try:
            derived = self._media._derive_cfg_key()
            if derived:
                return str(derived[0]), "cfg"
            return str(self._media._load_persisted_key() or ""), ""
        except Exception:
            return "", ""

    def _adopt(self, got: tuple) -> bool:
        """把钥匙放进上游的"显式注入"槽位，之后解密就不会再去要钥匙。"""
        aes_key, from_cfg = got
        if not aes_key:
            return False
        self._media._image_key = aes_key
        if from_cfg:
            try:
                self._media._persist_key(aes_key)    # 微信重启后密钥会变，取用时自校验
            except Exception:
                pass
        return True

    def prepare_key(self) -> bool:
        """补齐图片钥匙：免费的两条路都走不通时**只扫一次**进程内存并落盘。

        刻意不用上游的 monitor 模式（每 2 秒重扫、最长 120 秒）——扫不到就交回
        False，由调用方按冷却决定何时再试。只在采集线程调用。
        """
        if self._key_ready or self._adopt(self._free_key()):
            self._key_ready = True
            return True
        try:
            found = self._media._scan_aes_key(monitor=False)
        except Exception:
            return False
        if not self._adopt((str(found or ""), "")):
            return False
        try:
            self._media._persist_key(found)        # 下次免扫
        except Exception:
            pass
        self._key_ready = True
        return True

    def attach(self, user: str, digest: str, stem: str) -> dict | None:
        """按文件哈希取本机最高一档解密；本机一份都没有返回 None。"""
        if not self._key_ready:
            return None
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

    def discard(self, stored: str) -> None:
        """删掉升级后被替下来的那一份旧图，媒体目录里不留没人引用的孤儿。

        只认库里那种相对链接（同样过 :func:`safe_media_path` 收口），删失败不响：
        库里的新链接已经生效，旧文件留着也只是占地方。
        """
        path = self.resolve(stored)
        if not path:
            return
        try:
            path.unlink()
        except OSError:
            pass
