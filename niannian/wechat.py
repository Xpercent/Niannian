"""只读微信采集：单线程串行读取本机微信数据库，水位增量 + 关注即自动导入历史。

设计要点（借鉴原项目思路，代码全新）：
- 只有 wechat-reader 线程触碰上游库；API 侧读取目录也经命令队列进入该线程。
- 解密缓存固定在本工程 data/wechat/cache/<账号scope>/，避免与其它工程互相污染。
- 消息与水位在同一事务落库；重复扫描由 source_id 唯一键兜底。
- 阶段一不做媒体挂载与非文本消息格式化：正文按上游给出的文本原样入库。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from .config import clamp_history_limit
from .contacts import group_nicknames
from .media import ImageStore, hash_from_blob, parse_image, summarize
from .storage import Database, now_utc

log = logging.getLogger(__name__)

GROUP_PREFIX = re.compile(r"^(wxid_[^:\s<>]+):\s*\n")
# 群自己发的系统行（进群提示等）用群 ID 当前缀，它不是"某个人"，只剥不认。
ROOM_PREFIX = re.compile(r"^\d+@chatroom:\s*\n")
PACKED_COLUMNS = "sort_seq, local_id, packed_info_data"


def media_hash_map(conn: sqlite3.Connection, table: str, wanted: set) -> dict:
    """{(sort_seq, local_id): 本机附件文件哈希}。

    这个哈希只在 ``packed_info_data`` 里，正文 XML 的 ``md5`` 属性是另一套值，
    拿它去 attach 目录找文件一条也找不到。
    """
    if not wanted:
        return {}
    seqs = sorted({seq for seq, _ in wanted})
    quoted = '"%s"' % str(table).replace('"', '""')
    marks = ",".join("?" * len(seqs))
    cursor = conn.execute(
        f"SELECT {PACKED_COLUMNS} FROM {quoted} WHERE sort_seq IN ({marks})", seqs)
    found = {}
    for sort_seq, local_id, blob in cursor:
        key = (int(sort_seq), int(local_id))
        if key in wanted:
            found[key] = hash_from_blob(blob)
    return found


def sender_alias_map(conn: sqlite3.Connection, table: str,
                     wanted: set) -> dict:
    """{(sort_seq, local_id): 发送者wxid}——用消息分片库自己的 Name2Id 还原。

    微信 4.x 的 real_sender_id 是「本分片库 Name2Id 的 rowid」，id 空间按分片
    各自编号；上游拿 message_resource.db 的全局 SenderName2Id 去查，会把发送者
    解析成毫不相干的人（甚至解析不出），群聊无正文前缀的行和私聊自己发的消息
    因此全错。
    """
    if not wanted:
        return {}
    seqs = sorted({seq for seq, _ in wanted})
    quoted = '"%s"' % str(table).replace('"', '""')
    marks = ",".join("?" * len(seqs))
    aliases = {int(row[0]): str(row[1] or "") for row in
               conn.execute("SELECT rowid, user_name FROM Name2Id")}
    found = {}
    cursor = conn.execute(
        f"SELECT sort_seq, local_id, real_sender_id FROM {quoted} "
        f"WHERE sort_seq IN ({marks})", seqs)
    for sort_seq, local_id, sender_id in cursor:
        key = (int(sort_seq), int(local_id))
        if key in wanted:
            found[key] = aliases.get(int(sender_id or 0), "")
    return found


def account_scope(account: str) -> str:
    """账号目录指纹：用于隔离解密缓存与会话主键，避免路径含非法字符。"""
    return hashlib.sha256(str(account).encode("utf-8")).hexdigest()[:16]


def session_key(account: str, username: str) -> str:
    return f"wechat:{account_scope(account)}:{username}"


def split_sender(content: str) -> tuple[str, str]:
    """剥离正文封装并给出发送者线索：群前缀优先，其次附件 XML 根节点 fromusername。

    群前缀属于封装而非正文，返回的正文一律去掉它；XML 场景保留原文。
    前缀是群自己 ID 的（进群提示等系统行）只剥不认，返回空发送者。
    """
    match = GROUP_PREFIX.match(content)
    if match:
        return match.group(1), content[match.end():]
    match = ROOM_PREFIX.match(content)
    if match:
        return "", content[match.end():]
    if content.lstrip().startswith("<"):
        try:
            root = ElementTree.fromstring(content)
            name = (root.findtext("fromusername") or "").strip()
            if name:
                return name, content
        except ElementTree.ParseError:
            pass
    return "", content


def source_id(account: str, username: str, message: dict, raw: str) -> str:
    """源去重键：有服务器 ID 用它，否则用排序号+本地 ID+时间+原文。"""
    server_id = message.get("server_id")
    parts = ([account, username, "srv", str(server_id)] if server_id else
             [account, username, message.get("sort_seq"), message.get("local_id"),
              message.get("create_time"), raw])
    digest = hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8"))
    return f"wechat:{digest.hexdigest()}"


def normalize_message(account: str, username: str, session_name: str,
                      message: dict, names: dict) -> dict:
    """把上游真实行转换成本地消息记录；不美化、不猜测内容。"""
    raw = str(message.get("content") or "")
    origin, body = split_sender(raw)
    attachment = parse_image(body)
    resolved = str(message.get("sender_username") or "")
    sender = resolved or origin
    is_self = bool(sender) and sender == account
    group = username.endswith("@chatroom")
    if is_self:
        sender_key_value, sender_name = account, "我"
    elif not sender:
        # 微信自己也没给这条消息发送者（拍一拍、撤回、进群提示这类系统行）：
        # 不硬套到某个人头上，也不参与按人归组。
        sender_key_value, sender_name = "", "系统消息"
    elif not group:
        sender_key_value = username
        sender_name = session_name or names.get(username) or "会话成员"
    else:
        sender_key_value = sender
        nick = str(names.get(sender) or "")
        is_placeholder = (not nick) or nick.isdigit() or nick.startswith("wxid_")
        sender_name = "群成员" if is_placeholder else nick
    try:
        created = datetime.fromtimestamp(int(message["create_time"]), timezone.utc)
    except (KeyError, TypeError, ValueError, OSError):
        created = datetime.now(timezone.utc)
    return {
        "session_id": session_key(account, username),
        "source_id": source_id(account, username, message, raw),
        "sender_key": sender_key_value,
        "sender_name": sender_name,
        "is_self": is_self,
        "content": (summarize(attachment) if attachment
                    else body or f"[{message.get('type') or '消息'}]"),
        "message_type": str(message.get("type") or "文本"),
        "created_at": created.isoformat(timespec="seconds"),
        "sort_seq": int(message.get("sort_seq") or 0),
        "metadata": {
            "local_id": message.get("local_id"),
            "server_id": message.get("server_id"),
            "sort_seq": message.get("sort_seq"),
            "type": message.get("type"),
            "sender_id": message.get("sender_id"),
            "origin_sender_username": origin,
            "resolved_sender_username": resolved,
            "attachment": attachment,
        },
    }


def open_isolated_db(root: Path, settings: dict):
    """按上游库建立工程内隔离缓存的读取器；可复用本机已有的同账号密钥副本。"""
    for candidate in (root / "wechatauto-replica",):
        if str(candidate) not in sys.path and candidate.is_dir():
            sys.path.insert(0, str(candidate))
    from wechatauto.db import WeChatDB, list_accounts

    cache_root = Path(root) / "data" / "wechat" / "cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    accounts = list_accounts(settings.get("wechat_db_dir") or None)
    if not accounts:
        raise RuntimeError("未找到本机微信数据目录，请先登录微信。")
    for item in accounts:
        target = cache_root / account_scope(item["account"]) / "keys.json"
        if target.exists():
            continue
        guesses = [
            Path(tempfile.gettempdir()) / "wechatauto_db" / item["account"] / "keys.json",
        ]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            guesses.append(Path(local) / "wechatauto_keys" / f"{item['account']}.json")
        guesses.append(cache_root.parent.parent / "wechat" / "cache"
                       / account_scope(item["account"]) / "keys.json")
        for guess in guesses:
            if guess.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(guess, target)
                break

    class IsolatedDB(WeChatDB):
        """workdir 始终跟随当前账号落在工程缓存内，密钥不许被上游改写位置。"""

        @property
        def workdir(self):
            return str(cache_root / account_scope(self.account))

        @workdir.setter
        def workdir(self, value):
            Path(self.workdir).mkdir(parents=True, exist_ok=True)

        @property
        def keys_file(self):
            return str(Path(self.workdir) / "keys.json")

        @keys_file.setter
        def keys_file(self, value):
            pass

        def _stable_key_dirs(self):
            return [str(cache_root / "keys")]

        def _each_shard(self, user: str, wanted: set, collect) -> dict:
            """在该会话命中的每个消息分片上跑一次 collect(conn, table, wanted)。

            分片级查询一律按 ``(sort_seq, local_id)`` 定位：``local_id`` 各分片
            独立计数，跨分片不唯一。
            """
            found: dict = {}
            if not wanted:
                return found
            conns = self._msg_conns(user)
            try:
                for conn, table in conns:
                    try:
                        found.update(collect(conn, table, wanted))
                    except sqlite3.Error:
                        continue
            finally:
                for conn, _ in conns:
                    conn.close()
            return found

        def _fix_senders(self, user: str, rows: list) -> list:
            """用分片内 Name2Id 覆盖上游 sender_username；解析不到的置空，回落到正文前缀。"""
            wanted = {(int(r.get("sort_seq") or 0), int(r.get("local_id") or 0))
                      for r in rows}
            found = self._each_shard(user, wanted, sender_alias_map)
            for row in rows:
                key = (int(row.get("sort_seq") or 0), int(row.get("local_id") or 0))
                row["sender_username"] = found.get(key, "")
            return rows

        def media_hashes(self, user: str, keys: set) -> dict:
            """批量取图片行的本机文件哈希（只走采集线程，读上游库）。"""
            return self._each_shard(user, keys, media_hash_map)

        def room_nicknames(self, room: str) -> dict:
            """群成员在这个群里设的群名片：``{wxid: 群名片}``。"""
            conn = self._contact_conn()
            if not conn:
                return {}
            try:
                row = conn.execute("SELECT ext_buffer FROM chat_room WHERE username=?",
                                   (room,)).fetchone()
            except sqlite3.Error:
                return {}
            finally:
                conn.close()
            return group_nicknames(row["ext_buffer"]) if row else {}

        def display_names(self, user: str) -> dict:
            """该会话发送者的显示名，补上上游缺的群名片一档（备注 > 群名片 > 昵称）。"""
            names = dict(self.nickname_map())
            if not user.endswith("@chatroom"):
                return names
            group = self.room_nicknames(user)
            if not group:
                return names
            for member in self.get_group_members(user):
                wxid = str(member.get("username") or "")
                # 有备注时备注赢，这里不动；只有没备注的成员才换成群名片。
                if group.get(wxid) and not str(member.get("remark") or ""):
                    names[wxid] = group[wxid]
            return names

        def get_messages(self, user, limit=20, offset=0):
            return self._fix_senders(
                user, super().get_messages(user, limit=limit, offset=offset))

        def get_new_messages(self, user, since_seq=0, limit=200):
            return self._fix_senders(
                user, super().get_new_messages(user, since_seq=since_seq, limit=limit))

    return IsolatedDB(db_dir=settings.get("wechat_db_dir") or None,
                      account=settings.get("wechat_account") or None,
                      workdir=str(cache_root))


class Collector:
    """单采集线程 + 命令队列。状态机：stopped→starting→connected／error。"""

    def __init__(self, db: Database, settings: dict, root: Path):
        self.db = db
        self.settings = dict(settings)
        self.root = Path(root)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._commands: queue.Queue = queue.Queue()
        self._guard = threading.RLock()
        self._state = {
            "state": "stopped", "account": "", "error": None,
            "last_sync_at": None, "imported": 0, "sessions": 0,
            "mode": "read_only",
        }
        self._wx = None
        self._media = None

    # ------------------------------------------------------------ 对外接口
    @property
    def media(self):
        """图片链接解析器；未连接时为 None（此时库里已有链接仍可由 API 使用）。"""
        return self._media
    def status(self) -> dict:
        with self._guard:
            return {**self._state,
                    "running": bool(self._thread and self._thread.is_alive()),
                    "cache": str(self.root / "data" / "wechat" / "cache")}

    def start(self) -> dict:
        with self._guard:
            if self._thread and self._thread.is_alive():
                return self.status()
            self._stop.clear()
            self._state.update(state="starting", error=None)
            self._thread = threading.Thread(target=self._run, name="wechat-reader",
                                            daemon=True)
            self._thread.start()
        return self.status()

    def stop(self) -> dict:
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=10)
            if thread.is_alive():
                self._state_update(state="stopping")
        return self.status()

    def catalog(self) -> list[dict]:
        """会话目录：线程活着就现场同步，否则回退到本地已存目录。"""
        if self._thread and self._thread.is_alive():
            future = self._request("catalog")
            return future.result(timeout=90)
        return self.db.list_sessions(account=self._state["account"])

    # ------------------------------------------------------------ 内部
    def _state_update(self, **values):
        with self._guard:
            self._state.update(values)

    def _request(self, operation: str, *args) -> Future:
        if not (self._thread and self._thread.is_alive()):
            raise RuntimeError("微信采集器未运行。")
        future = Future()
        self._commands.put((operation, args, future))
        return future

    def _run(self):
        try:
            self._wx = open_isolated_db(self.root, self.settings)
            info = self._wx.get_self_info() or {}
            account = info.get("username") or self._wx.wxid
            if not account:
                raise RuntimeError("无法确定当前微信账号。")
            self._state_update(account=account)
            from wechatauto.media import MediaDownloader
            self._media = ImageStore(MediaDownloader(self._wx), self.root, account)
            self._sync_catalog()
            self._state_update(state="connected", error=None)
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            self._state_update(state="error", error=message[:300])
            log.warning("微信连接失败：%s", message)
            self._wx = None
            self._media = None
            self._drain_commands()
            if self._stop.is_set():
                self._state_update(state="stopped")
            return
        try:
            while not self._stop.is_set():
                deadline = time.monotonic() + max(1.0, float(
                    self.settings.get("poll_seconds", 3.0)))
                while not self._stop.is_set() and time.monotonic() < deadline:
                    try:
                        operation, args, future = self._commands.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    self._execute(operation, args, future)
                if self._stop.is_set():
                    break
                try:
                    self._sync_catalog()
                    self._poll_followed()
                    self._state_update(state="connected", error=None,
                                       last_sync_at=now_utc())
                except Exception as exc:
                    self._state_update(state="error", error=str(exc)[:300])
                    log.warning("采集轮次失败：%s", exc)
        finally:
            self._wx = None
            self._media = None
            self._drain_commands()
            self._state_update(state="stopped")

    def _execute(self, operation: str, args: tuple, future: Future):
        try:
            if operation == "catalog":
                future.set_result(self._sync_catalog())
            else:
                future.set_exception(RuntimeError(f"未知命令：{operation}"))
        except Exception as exc:
            future.set_exception(exc)

    def _drain_commands(self):
        while True:
            try:
                _, _, future = self._commands.get_nowait()
                future.set_exception(RuntimeError("微信采集器已停止。"))
            except queue.Empty:
                return

    def _sync_catalog(self) -> list[dict]:
        assert self._wx is not None
        account = self._state["account"]
        names = self._wx.nickname_map(refresh=True)
        items = []
        for row in self._wx.get_sessions(limit=1000):
            username = str(row["username"])
            last_time = int(row.get("last_time") or 0)
            items.append({
                "id": session_key(account, username),
                "username": username,
                "name": names.get(username) or username,
                "unread": int(row.get("unread") or 0),
                "last_message_at": (
                    datetime.fromtimestamp(last_time, timezone.utc)
                    .isoformat(timespec="seconds") if last_time else None),
            })
        self.db.sync_catalog(account, items)
        self._state_update(sessions=len(items))
        return self.db.list_sessions(account=account)

    def _poll_followed(self):
        assert self._wx is not None
        account = self._state["account"]
        for session in self.db.list_sessions(account=account, monitored_only=True):
            if self._stop.is_set():
                return
            watermark = self.db.watermark(account, session["id"])
            if watermark is None:
                self._import_recent(session, clamp_history_limit(
                    self.settings.get("history_limit")))
                continue
            collected: list[dict] = []
            since = watermark
            while not self._stop.is_set():
                page = self._wx.get_new_messages(session["username"],
                                                 since_seq=since, limit=200)
                if not page:
                    break
                collected.extend(page)
                since = max(int(m["sort_seq"]) for m in page)
                if len(page) < 200:
                    break
            if collected and not self._stop.is_set():
                self._store(session, collected, watermark)

    def _import_recent(self, session: dict, limit: int) -> dict:
        """首次关注自动导入：从最新往旧翻页取满 limit 条（阶段一无手动导入）。"""
        assert self._wx is not None
        account = self._state["account"]
        collected: list[dict] = []
        while len(collected) < limit and not self._stop.is_set():
            page = self._wx.get_messages(session["username"],
                                         limit=min(200, limit - len(collected)),
                                         offset=len(collected))
            if not page:
                break
            collected.extend(page)
        if self._stop.is_set():
            return {"inserted": 0, "read": 0}
        watermark = self.db.watermark(account, session["id"]) or 0
        return self._store(session, collected, watermark)

    def _store(self, session: dict, messages: list[dict], watermark: int) -> dict:
        assert self._wx is not None
        account = self._state["account"]
        names = self._wx.display_names(session["username"])
        payloads = []
        for message in sorted(messages,
                              key=lambda m: (int(m.get("sort_seq") or 0),
                                             int(m.get("local_id") or 0))):
            payloads.append(normalize_message(
                account, session["username"], session.get("name", ""), message, names))
        self._attach_media(session["username"], payloads)
        highest = max([int(watermark)] + [p["sort_seq"] for p in payloads]) if payloads \
            else int(watermark)
        checkpoint = {"account": account, "session_id": session["id"],
                      "sort_seq": highest}
        inserted = self.db.ingest(payloads, checkpoint=checkpoint)
        self._state_update(imported=self._state["imported"] + inserted)
        return {"inserted": inserted, "read": len(payloads)}

    def _attach_media(self, username: str, payloads: list[dict]) -> None:
        """给图片行补本机文件链接。只读微信已有文件，绝不驱动界面去下载。

        本机一份都没有时只写 ``media_md5``：文件可能之后才落到盘上，
        API 读取时会凭这个哈希再补一次，不需要再碰上游库。
        """
        if self._media is None:
            return
        images = [p for p in payloads if p["metadata"].get("attachment")]
        if not images:
            return
        keys = {(p["sort_seq"], int(p["metadata"].get("local_id") or 0))
                for p in images}
        digests = self._wx.media_hashes(username, keys)
        for payload in images:
            meta = payload["metadata"]
            digest = digests.get(
                (payload["sort_seq"], int(meta.get("local_id") or 0)), "")
            meta["media_md5"] = digest
            linked = self._media.attach(username, digest,
                                        f"{username}_{payload['sort_seq']}")
            if linked:
                meta.update(linked)
