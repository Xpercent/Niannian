"""Read-only WeChat integration: one worker, isolated cache, atomic checkpoints."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .media import MediaCache, parse_attachment, summarize

log = logging.getLogger(__name__)


def account_scope(account: str) -> str:
    return hashlib.sha256(str(account).encode()).hexdigest()[:16]


def session_id(account: str, username: str) -> str:
    return f"wechat:{account_scope(account)}:{username}"


def _sender(content: str) -> tuple[str, str]:
    """Group prefix and root-level XML origin are authoritative sender hints."""
    match = re.match(r"^(wxid_[^:\s<>]+):\s*\n", content)
    if match:
        return match.group(1), content[match.end():]
    if content.lstrip().startswith("<"):
        try:
            root = ElementTree.fromstring(content)
            name = root.findtext("fromusername")
            if name:
                return name.strip(), content
        except ElementTree.ParseError:
            pass
    return "", content


def infer_self_ids(account: str, messages: list[dict]) -> set[int]:
    """Resolve per-session numeric IDs using actual embedded origins, not stale indexes."""
    ids: set[int] = set()
    origins: dict[int, set[str]] = {}
    for message in messages:
        origin, _ = _sender(str(message.get("content") or ""))
        if origin:
            try:
                origins.setdefault(int(message["sender_id"]), set()).add(origin)
            except (ValueError, TypeError, KeyError):
                pass
    for numeric_id, names in origins.items():
        if names == {account}:
            ids.add(numeric_id)
        else:
            ids.discard(numeric_id)
    return ids


def normalize(account: str, username: str, message: dict,
              self_sender_ids: set[int] | None = None) -> dict:
    """Convert an actual source row without exposing attachment XML as content."""
    raw = str(message.get("content") or "")
    origin, content = _sender(raw)
    sender = origin or str(message.get("sender_username") or "")
    try:
        sender_id_value = int(message.get("sender_id"))
    except (ValueError, TypeError):
        sender_id_value = None
    self_ids = self_sender_ids if self_sender_ids is not None else set()
    is_self = origin == account if origin else sender_id_value in self_ids
    confident = bool(origin or sender_id_value in self_ids)
    source_sender = str(message.get("sender_username") or "")
    conflict = bool(origin and ((source_sender and source_sender != origin) or
                               (sender_id_value == 2 and origin != account)))
    if is_self and source_sender and source_sender != account:
        conflict = True
    group = username.endswith("@chatroom")
    if is_self:
        sender = account
        name = "我"
    elif not group:
        sender = username
        name = message.get("session_name") or message.get("sender_name") or "会话成员"
    else:
        name = message.get("sender_name") or "群成员"
        if str(name).isdigit() or str(name).startswith("wxid_"):
            name = "群成员"
    attachment = parse_attachment(content)
    body = summarize(attachment) if attachment else content or f"[{message.get('type', '附件')}]"
    metadata = {key: message.get(key) for key in
                ("local_id", "server_id", "sort_seq", "type", "md5", "sender_id")}
    metadata.update({"sender_username": sender, "username": username})
    metadata.update({"source_sender_username": source_sender,
                     "origin_sender_username": origin, "identity_conflict": conflict,
                     "identity_confident": confident,
                     "identity_source": "embedded_origin" if origin else
                     "verified_sender_id" if sender_id_value in self_ids else "unverified_index"})
    if attachment:
        metadata["attachment"] = attachment
    server_id = message.get("server_id")
    identity = [account, username, "server", str(server_id)] if server_id else [
        account, username, message.get("sort_seq"), message.get("local_id"),
        message.get("create_time"), raw,
    ]
    source = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    return {"session_id": session_id(account, username), "account": account,
            "sender_key": sender or f"unknown:{sender_id_value}", "sender_name": str(name),
            "is_self": bool(is_self), "identity_confident": confident, "content": body[:19000],
            "message_type": str(message.get("type") or "文本"),
            "created_at": datetime.fromtimestamp(int(message["create_time"]), timezone.utc).isoformat(),
            "source_id": f"wechat:{source}", "metadata": metadata}


def acquire_instance_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def open_wechat(root: Path, settings: dict):
    """Keep even upstream account-recovery caches inside the project's lock scope."""
    upstream = Path(settings.get("wechat_upstream") or root / "wechatauto-replica")
    if str(upstream.resolve()) not in sys.path:
        sys.path.insert(0, str(upstream.resolve()))
    from wechatauto.db import WeChatDB, list_accounts

    cache_root = root / "data" / "wechat" / "cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    accounts = list_accounts(settings.get("wechat_db_dir"))
    if not accounts:
        raise RuntimeError("未找到本机微信数据目录，请先登录微信。")
    for item in accounts:
        target = cache_root / account_scope(item["account"]) / "keys.json"
        if not target.exists():
            candidates = [Path(tempfile.gettempdir()) / "wechatauto_db" / item["account"] / "keys.json"]
            local = os.environ.get("LOCALAPPDATA")
            if local:
                candidates.append(Path(local) / "wechatauto_keys" / f"{item['account']}.json")
            for candidate in candidates:
                if candidate.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(candidate, target)
                    break

    class IsolatedWeChatDB(WeChatDB):
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
            pass  # The base class can recover accounts, but cannot relocate our cache.

        def _stable_key_dirs(self):
            return [str(cache_root / "keys")]

    return IsolatedWeChatDB(db_dir=settings.get("wechat_db_dir"),
                           account=settings.get("wechat_account") or accounts[0]["account"],
                           workdir=str(cache_root))


class WechatWorker:
    """Only this thread touches the source DB; all commands enter through its queue."""

    def __init__(self, db, config, root: Path):
        self.db, self.config, self.root = db, config, Path(root)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._commands: queue.Queue = queue.Queue()
        self._guard = threading.RLock()
        self._state = {"state": "stopped", "account": "", "error": None,
                       "last_sync_at": None, "imported": 0, "sessions": 0,
                       "media_available": False, "mode": "read_only"}
        self._wx = None
        self._media = None
        self._lock_handle = None
        self._identities: dict[str, dict[str, list[str]]] = {}
        with self.db.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS wechat_checkpoints("
                         "account TEXT NOT NULL,session_id TEXT NOT NULL,sort_seq INTEGER NOT NULL,"
                         "updated_at TEXT NOT NULL DEFAULT '',"
                         "PRIMARY KEY(account,session_id))")

    def _settings(self) -> dict:
        if isinstance(self.config, dict):
            return self.config
        if hasattr(self.config, "read"):
            return self.config.read()
        if hasattr(self.config, "snapshot"):
            return self.config.snapshot()
        if hasattr(self.config, "load"):
            return self.config.load()
        return {}

    def _update(self, **values):
        with self._guard:
            self._state.update(values)

    def status(self) -> dict:
        with self._guard:
            return {**self._state, "running": bool(self._thread and self._thread.is_alive()),
                    "cache": str(self.root / "data" / "wechat" / "cache")}

    def start(self) -> dict:
        with self._guard:
            if self._thread and self._thread.is_alive():
                return self.status()
            self._stop.clear()
            self._update(state="starting", error=None)
            self._thread = threading.Thread(target=self._run, name="wechat-reader", daemon=True)
            self._thread.start()
        return self.status()

    def stop(self) -> dict:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=8)
        if self._thread and self._thread.is_alive():
            self._update(state="stopping")
        return self.status()

    def _request(self, operation: str, *args):
        if not self._thread or not self._thread.is_alive():
            raise RuntimeError("微信采集尚未连接，请先启动采集。")
        future = Future()
        self._commands.put((operation, args, future))
        return future.result(timeout=90)

    def catalog(self) -> list:
        if self._thread and self._thread.is_alive():
            return self._request("catalog")
        return self.db.rows("SELECT * FROM sessions WHERE account<>'' ORDER BY last_message_at DESC")

    def import_history(self, session_id: str, limit: int = 100) -> dict:
        return self._request("history", session_id, max(1, min(int(limit), 10000)))

    def _run(self):
        try:
            self._lock_handle = acquire_instance_lock(self.root / "data" / "wechat" / "cache" / "reader.lock")
            if self._lock_handle is None:
                raise RuntimeError("当前工程的微信缓存已被另一采集器占用。")
            self._wx = open_wechat(self.root, self._settings())
            account = self._wx.get_self_info().get("username") or self._wx.wxid
            self._update(account=account)
            try:
                self._media = MediaCache(self._wx, self.root / "data" / "wechat", account)
                self._update(media_available=True)
            except Exception as exc:
                log.warning("Local media unavailable: %s", type(exc).__name__)
            self._sync_catalog()
            self._update(state="connected", error=None)
            while not self._stop.is_set():
                try:
                    self._sync_catalog()
                    self._sync_selected()
                    self._update(state="connected", error=None,
                                 last_sync_at=datetime.now(timezone.utc).isoformat())
                except Exception as exc:
                    self._update(state="error", error=str(exc)[:300])
                    log.warning("WeChat read cycle failed: %s", type(exc).__name__)
                deadline = time.monotonic() + max(1, float(self._settings().get("wechat_poll_seconds", 3)))
                while not self._stop.is_set() and time.monotonic() < deadline:
                    try:
                        operation, args, future = self._commands.get(timeout=.2)
                    except queue.Empty:
                        continue
                    try:
                        result = self._sync_catalog() if operation == "catalog" else self._history(*args)
                        future.set_result(result)
                    except Exception as exc:
                        future.set_exception(exc)
        except Exception as exc:
            self._update(state="error", error=str(exc)[:300])
            log.warning("WeChat connection failed: %s", type(exc).__name__)
        finally:
            self._wx = self._media = None
            if self._lock_handle:
                self._lock_handle.close()
                self._lock_handle = None
            while True:
                try:
                    _, _, future = self._commands.get_nowait()
                    future.set_exception(RuntimeError("微信采集器已停止。"))
                except queue.Empty:
                    break
            if self._stop.is_set():
                self._update(state="stopped")

    def _sync_catalog(self) -> list:
        account = self._state["account"]
        items = []
        names = self._wx.nickname_map(refresh=True)
        for item in self._wx.get_sessions(limit=1000):
            username = item["username"]
            timestamp = item.get("last_time") or 0
            items.append({"id": session_id(account, username), "username": username,
                          "name": names.get(username) or username, "avatar": "",
                          "unread": int(item.get("unread") or 0),
                          "last_message_at": datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
                          if timestamp else None})
        self.db.sync_catalog(account, items)
        self._update(sessions=len(items))
        return self.db.rows("SELECT * FROM sessions WHERE account=? ORDER BY last_message_at DESC", (account,))

    def _watermark(self, sid: str) -> int | None:
        row = self.db.one("SELECT sort_seq FROM wechat_checkpoints WHERE account=? AND session_id=?",
                          (self._state["account"], sid))
        return int(row["sort_seq"]) if row else None

    def _sync_selected(self):
        sessions = self.db.rows(
            "SELECT s.* FROM sessions s WHERE s.account=? AND (s.monitored=1 OR EXISTS "
            "(SELECT 1 FROM wechat_checkpoints c WHERE c.account=s.account AND c.session_id=s.id))",
            (self._state["account"],))
        for item in sessions:
            if self._stop.is_set():
                break
            watermark = self._watermark(item["id"])
            if watermark is None:
                self._history(item["id"], int(self._settings().get("history_limit", 100)))
                continue
            messages = []
            offset, size = 0, 200
            while not self._stop.is_set():
                page = self._wx.get_messages(item["username"], limit=size, offset=offset)
                messages.extend(m for m in page if int(m["sort_seq"]) >= watermark)
                if len(page) < size or any(int(m["sort_seq"]) < watermark for m in page):
                    break
                offset += len(page)
            if not self._stop.is_set() and messages:
                self._store(item, messages, watermark)

    def _history(self, sid: str, limit: int) -> dict:
        item = self.db.one("SELECT * FROM sessions WHERE id=? AND account=?", (sid, self._state["account"]))
        if not item:
            raise ValueError("会话不属于当前微信账号。")
        messages = []
        while len(messages) < limit and not self._stop.is_set():
            page = self._wx.get_messages(item["username"], limit=min(200, limit-len(messages)), offset=len(messages))
            if not page:
                break
            messages.extend(page)
        if self._stop.is_set():
            raise RuntimeError("采集器停止，未提交历史导入。")
        return self._store(item, messages, self._watermark(sid) or 0)

    def _store(self, item: dict, messages: list[dict], watermark: int) -> dict:
        account = self._state["account"]
        identity_key = f"wechat_identity:{item['id']}"
        observations = self._identities.setdefault(item["id"], self.db.runtime(identity_key, {}))
        for raw in messages:
            origin, _ = _sender(str(raw.get("content") or ""))
            numeric_id = raw.get("sender_id")
            if origin and numeric_id is not None:
                values = observations.setdefault(str(numeric_id), [])
                if origin not in values:
                    values.append(origin)
        self_ids = {int(key) for key, origins in observations.items() if origins == [account]}
        names = self._wx.nickname_map()
        payloads = []
        for raw in sorted(messages, key=lambda value: (int(value["sort_seq"]), int(value["local_id"]))):
            origin, _ = _sender(str(raw.get("content") or ""))
            sender = origin or raw.get("sender_username")
            payload = normalize(account, item["username"],
                                {**raw, "session_name": item["name"], "sender_name": names.get(sender)}, self_ids)
            if self._media:
                try:
                    self._media.enrich(item["username"], payload)
                except Exception:
                    log.warning("Media index temporarily unavailable")
            payloads.append(payload)
        checkpoint = {"account": account, "session_id": item["id"],
                      "sort_seq": max([watermark] + [int(m["sort_seq"]) for m in messages])}
        result = self.db.ingest_batch(payloads, checkpoint=checkpoint)
        self.db.set_runtime(identity_key, observations)
        self._update(imported=self._state["imported"] + int(result.get("inserted", 0)))
        return {**result, "read": len(messages), "checkpoint": checkpoint["sort_seq"]}
