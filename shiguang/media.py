"""微信非文本消息：把原始 XML 拆成可读摘要 + 结构化附件描述，并按需解出本地文件。

上游 ``WeChatDB.get_messages()`` 对图片/表情/文件/卡片一律原样返回 ``message_content``
里的那坨 XML。本模块只负责**解析**（纯函数、可离线测试）与**分派抽取**（鸭子类型
接收一个 ``MediaDownloader``，不 import 上游，保证没装上游依赖时也能导入本模块）。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


# 附件描述里**不保留** cdnurl / encrypturl / aeskey：那些是带时效的下载凭证，
# 既会过期又把密钥写进本地库，而模型提炼和溯源上下文都不需要它们。
#
# 同理不拿 XML 的 md5 去定位本地文件：图片本地 .dat 的真实名字来自
# ``packed_info_data``，与 XML 里那个 ``md5`` 是两个值（实测同一张图
# XML=49e5bcf4… 本地=97a8d050…）。这里记的是 CDN 资产 id，只做溯源用。
def parse_attachment(content: str) -> dict[str, Any] | None:
    """把消息正文的原始 XML 解析成附件描述；不是附件（纯文本/解析失败）返回 None。"""
    text = (content or "").strip()
    if not text.startswith("<"):
        return None
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None
    if root.tag == "msg":
        for child in root:
            if child.tag in ("img", "emoji", "videomsg", "voicemsg", "appmsg", "location"):
                return _describe(child)
        return None
    return _describe(root)


def _attr(node: ElementTree.Element, name: str) -> str:
    return (node.get(name) or "").strip()


def _int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _child(node: ElementTree.Element, name: str) -> str:
    found = node.find(name)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def _describe(node: ElementTree.Element) -> dict[str, Any] | None:
    tag = node.tag
    if tag == "img":
        # cdnthumbwidth/height 是**缩略图**尺寸，不是原图尺寸，别混进 width/height
        return _attachment("image", md5=_attr(node, "md5"),
                           thumb_width=_int(_attr(node, "cdnthumbwidth")),
                           thumb_height=_int(_attr(node, "cdnthumbheight")),
                           size=_int(_attr(node, "hdlength")) or _int(_attr(node, "length")))
    if tag == "emoji":
        return _attachment("emoji", md5=_attr(node, "md5"),
                           width=_int(_attr(node, "width")),
                           height=_int(_attr(node, "height")),
                           size=_int(_attr(node, "len")))
    if tag == "videomsg":
        return _attachment("video", md5=_attr(node, "md5"),
                           width=_int(_attr(node, "width")),
                           height=_int(_attr(node, "height")),
                           size=_int(_attr(node, "length")),
                           seconds=_int(_attr(node, "playlength")))
    if tag == "voicemsg":
        # voicelength 单位是毫秒
        ms = _int(_attr(node, "voicelength"))
        return _attachment("voice", seconds=ms // 1000 if ms else None)
    if tag == "location":
        label = _attr(node, "label") or _attr(node, "address") or _attr(node, "poiname")
        return _attachment("location", name=label)
    if tag == "appmsg":
        return _describe_appmsg(node)
    return None


def _describe_appmsg(node: ElementTree.Element) -> dict[str, Any] | None:
    code = _child(node, "type")
    title = _child(node, "title")
    if code == "6":
        attach = node.find("appattach")
        size = _int(_child(attach, "totallen")) if attach is not None else None
        ext = (_child(attach, "fileext") if attach is not None else "").lower()
        return _attachment("file", name=title, ext=ext, size=size,
                           md5=_child(node, "md5"))
    if code == "5":
        return _attachment("link", name=title, url=_child(node, "url"))
    source = _child(node, "sourcedisplayname") or _child(node, "fromusername")
    kind = "miniapp" if node.find("weappinfo") is not None else "card"
    return _attachment(kind, name=title, source=source, url=_child(node, "url"),
                       md5=_child(node, "md5"))


def _attachment(kind: str, **fields: Any) -> dict[str, Any]:
    data = {"kind": kind}
    data.update({key: value for key, value in fields.items()
                 if value not in (None, "", 0)})
    return data


def human_size(value: int | None) -> str:
    if not value or value < 0:
        return ""
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return ("%d %s" % (round(size), unit)) if unit == "B" else ("%.1f %s" % (size, unit))
        size /= 1024
    return ""


# 卡片标题经常只是「@所有人」这类提及或纯符号，摘出来对判断毫无信息量，
# 还不如留成空壳摘要，让上层能识别并跳过它。
USELESS_NAME = re.compile(r"^(?:@\S+|[\W_]+)$", re.UNICODE)
# 只剩一个类型标签、没有任何细节的摘要：既不该立成待办，也不值得占模型上下文。
PLACEHOLDER = re.compile(r"^\[(?:图片|动画表情|视频|语音|文件|链接|卡片|小程序|位置|附件)\]$")


def clean_name(value: str | None) -> str:
    text = (value or "").strip()
    return "" if not text or USELESS_NAME.match(text) else text


def is_placeholder(content: str) -> bool:
    return bool(PLACEHOLDER.match((content or "").strip()))


def attachment_only(content: str) -> bool:
    """整条消息只有附件摘要、没有任何文字——这种消息不构成可行动请求。"""
    lines = [line.strip() for line in (content or "").splitlines() if line.strip()]
    return bool(lines) and all(is_placeholder(line) for line in lines)


def summarize(attachment: dict[str, Any]) -> str:
    """界面与模型上下文里显示的一行摘要，替代原始 XML。"""
    kind = attachment.get("kind", "")
    labels = {"image": "图片", "emoji": "动画表情", "video": "视频", "voice": "语音",
              "file": "文件", "link": "链接", "card": "卡片", "miniapp": "小程序",
              "location": "位置"}
    label = labels.get(kind, "附件")
    if kind in ("video", "voice") and attachment.get("seconds"):
        return "[%s %d:%02d]" % (label, attachment["seconds"] // 60, attachment["seconds"] % 60)
    parts = [clean_name(attachment.get("name"))]
    if kind == "file":
        parts.append(human_size(attachment.get("size")))
    elif kind in ("card", "miniapp", "link"):
        parts.append(clean_name(attachment.get("source")))
    parts = [part for part in parts if part]
    return ("[%s] " % label) + " · ".join(parts) if parts else "[%s]" % label


# 只有这四类上游给了本机抽取入口；表情在 4.x 本机不落盘，卡片/链接没有文件本体。
EXTRACT_KINDS = {"image": "download_image", "video": "download_video",
                 "voice": "download_voice", "file": "download_file"}


class MediaIndex:
    """(会话, local_id) -> 已抽出的文件名，持久化在 media.db。

    桥接每次重启都会重放 `--history`，没有这份索引就要对已知消息重新走一遍
    微信附件目录扫描。文件被外部删掉时 `known()` 仍返回旧名字，调用方按存在性
    判断后重新抽取即可，所以它只是「先试这条路」的提示，不是事实来源。
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS extracted("
                         "session_id TEXT NOT NULL, local_id INTEGER NOT NULL, file TEXT NOT NULL,"
                         "PRIMARY KEY(session_id, local_id))")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:
                yield conn
        finally:
            conn.close()

    def known(self, session_id: str, local_id: int) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT file FROM extracted WHERE session_id=? AND local_id=?",
                               (session_id, int(local_id))).fetchone()
        return row[0] if row else None

    def remember(self, session_id: str, local_id: int, filename: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO extracted VALUES(?,?,?) ON CONFLICT(session_id,local_id)"
                         " DO UPDATE SET file=excluded.file", (session_id, int(local_id), filename))

    def forget_session(self, session_id: str) -> list[str]:
        with self._connect() as conn:
            files = [row[0] for row in conn.execute(
                "SELECT file FROM extracted WHERE session_id=?", (session_id,)).fetchall()]
            conn.execute("DELETE FROM extracted WHERE session_id=?", (session_id,))
        return files

    def entries(self) -> list[tuple[str, int, str]]:
        with self._connect() as conn:
            return conn.execute("SELECT session_id, local_id, file FROM extracted ORDER BY rowid").fetchall()


def media_tier(filename: str | None) -> str | None:
    """从上游落盘文件名后缀判断本机拿到的是哪一档。

    上游按 ``<会话>_<local_id>[_h|_thumb].<ext>`` 命名：``_h`` 是原件，``_thumb``
    是预览图，没有后缀的是微信默认下发的那一份（可能是完整图，也可能本身就是预览版）。
    """
    if not filename:
        return None
    stem = Path(str(filename)).stem
    if stem.endswith("_h"):
        return "original"
    if stem.endswith("_thumb"):
        return "thumb"
    return "mid"


def attach_local(metadata: dict[str, Any], filename: str | None) -> dict[str, Any]:
    if filename:
        metadata["local_file"] = filename
        metadata["media_tier"] = media_tier(filename)
    return metadata


def extract(media: Any, user: str, local_id: int, kind: str) -> str | None:
    """解出本地媒体文件，返回文件名（相对媒体目录）；拿不到返回 None。

    ``media`` 是上游 ``MediaDownloader``；只走本机读取，绝不驱动界面。
    """
    method = EXTRACT_KINDS.get(kind)
    if media is None or method is None or local_id is None:
        return None
    try:
        if kind == "image":
            # tier='best'：原图 > 压缩版 > 预览图，档位标在文件名后缀上
            path = media.download_image(user, int(local_id), tier="best")
        else:
            path = getattr(media, method)(user, int(local_id))
    except Exception:  # 不可用的本机附件不会阻断文本与水位提交。
        return None
    if not path:
        return None
    return Path(str(path)).name


def metadata_of(text: str | None) -> dict[str, Any]:
    """单行 metadata 坏了不能让删除会话或媒体回收整个崩掉。"""
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def account_media_dir(media_root: Path, account: str) -> Path:
    """账号自己的媒体目录：``<root>/<账号哈希前 16 位>``。

    本机文件名是 ``<会话>_<local_id>``，换一个微信账号后同名会话与 local_id 都可能重演，
    所以按账号分目录，文件名不再跨账号互相覆盖。哈希算法与桥接的水位目录保持一致。
    """
    return Path(media_root) / hashlib.sha256(str(account).encode()).hexdigest()[:16]


def safe_media_path(media_root: Path, stored: Any) -> Path | None:
    """把 metadata 里记的路径解析到媒体根目录**内部**的安全路径。

    ``local_file`` 是别人能影响到的字符串（桥接摘要、甚至手工录入），
    以前只靠 ``Path(name).name`` 削掉目录，现在允许一层账号子目录，
    所以必须逐段校验并确认结果仍在根目录里。
    """
    text = str(stored or "").strip().replace("\\", "/")
    if not text or text.startswith("/"):
        return None
    segments = [part for part in text.split("/") if part not in {"", "."}]
    if not segments or len(segments) > 2 or any(part == ".." for part in segments):
        return None
    root = Path(media_root).resolve()
    target = (root / Path(*segments)).resolve()
    return target if target.is_relative_to(root) else None


class MediaCache:
    """One account's local-only attachment extractor and persistent file index."""

    def __init__(self, wx: Any, root: Path, account: str):
        self.root = Path(root)
        self.directory = account_media_dir(self.root / "media", account)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index = MediaIndex(self.directory / "index.sqlite3")
        from wechatauto.media import MediaDownloader
        self.downloader = MediaDownloader(wx, save_dir=str(self.directory))

    def enrich(self, username: str, payload: dict[str, Any]) -> None:
        metadata = payload["metadata"]
        attachment = metadata.get("attachment")
        local_id = metadata.get("local_id")
        if not attachment or local_id is None or attachment["kind"] not in EXTRACT_KINDS:
            return
        filename = self.index.known(username, local_id)
        if not filename or not (self.directory / Path(filename).name).is_file():
            filename = extract(self.downloader, username, local_id, attachment["kind"])
            if filename:
                self.index.remember(username, local_id, filename)
        if filename:
            attach_local(metadata, f"{self.directory.name}/{Path(filename).name}")

