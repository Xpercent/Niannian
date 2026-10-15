"""只读微信采集线程：单线程串行读本机微信库，水位增量 + 关注即自动导入历史。

本文件只剩 :class:`Collector`（状态机 + 命令队列 + 每轮的活）。上下游各归各位：
建读取器与分片补查在 :mod:`niannian.wechat_db`，上游行转成本地记录在
:mod:`niannian.normalize`，标识与目录命名在 :mod:`niannian.ids`，图片落盘在
:mod:`niannian.media`。

设计要点（借鉴原项目思路，代码全新）：
- 只有 wechat-reader 线程触碰上游库；API 侧读取目录也经命令队列进入该线程。
- 解密缓存固定在本工程 data/wechat/cache/<账号scope>/，避免与其它工程互相污染。
- 消息与水位在同一事务落库；重复扫描由 source_id 唯一键兜底。
- 非纯文本正文交给 :mod:`niannian.describe` 解析成结构化描述 + 一行摘要，
  认不出的仍按上游给出的文本原样入库。
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
import time
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path

from .config import clamp_history_limit
from .ids import session_user
from .media import ImageStore, tier_rank
from .normalize import normalize_message
from .storage import Database, now_utc
from .wechat_db import open_isolated_db

log = logging.getLogger(__name__)

# 每轮补图片的行数上限：一条图片行要走"扫目录 + 解密 + 落盘"，几万条历史不能一口气做完。
IMAGE_CATCHUP_LIMIT = 30
# 重扫预览图的行数上限与冷却：一次全树目录遍历是实打实的开销，而用户没点开过的那些
# 永远升不上档，不能每轮都拿同一批最前的行去翻。
TIER_UPGRADE_LIMIT = 10
TIER_SCAN_WINDOW = 200
TIER_RETRY_SECONDS = 600.0
# 连不上微信时多久再试一次。不是错误就躺平的：用户随时可能才把微信打开。
ATTACH_RETRY_SECONDS = 10.0
# 弄图片钥匙的冷却：一次内存扫描是几十秒级的重活，不能每轮都做。
MEDIA_KEY_RETRY_SECONDS = 120.0
# 而"微信开了没有"只是一次进程表查询，问得勤一点，微信一开就能马上动手。
MEDIA_KEY_PROBE_SECONDS = 5.0


def wechat_running() -> bool:
    """微信进程在不在：只查进程表，不驱动界面、不发消息。

    ``tasklist`` 在中文 Windows 上输出 GBK，不显式指定编码时 UTF-8 模式下解码失败
    会让 stdout 变成 None（上游实测踩过，这里同样处理）。
    """
    try:
        done = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="gbk", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return False
    for line in (done.stdout or "").strip().splitlines():
        parts = line.strip('"').split('","')
        if len(parts) >= 2 and parts[1].isdigit():
            return True
    return False


class Collector:
    """单采集线程 + 命令队列。状态机：stopped→starting→connected／waiting_wechat／error。

    连不上微信**不会让线程退出**：置 ``waiting_wechat`` 后由轮询按冷却重试，
    用户把微信打开就自动接上，不需要重启服务。
    """

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
        self._next_attach = 0.0
        self._next_key_at = 0.0
        self._next_key_probe_at = 0.0
        # 预览图重扫的内存冷却表 {消息行 id: 下次允许重扫的时间}：重启就清空，无害。
        self._tier_next_at: dict = {}
        self._failures = 0

    # ------------------------------------------------------------ 对外接口
    @property
    def media(self):
        """图片链接解析器；未连接时为 None（此时库里已有链接仍可由 API 使用）。"""
        return self._media

    def update_settings(self, values: dict) -> dict:
        """把可以热更的键推进采集器自己的副本。

        Collector 构造时拿的是 settings 的**副本**，所以只改 app.state.settings
        不会传进来；轮询间隔每轮重读、导入条数每次导入重读，因此这两项能立即生效。
        ``port`` 之类的启动期参数不在这里处理。
        """
        hot = {key: values[key] for key in ("history_limit", "poll_seconds")
               if key in values}
        with self._guard:
            self.settings.update(hot)
        return dict(hot)

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
        """会话目录：连着微信就现场同步，否则回退到本地已存目录。

        判据是"有没有连上"而不是"线程活着"：线程会在微信没开时照常活着等它启动。
        """
        if self._thread and self._thread.is_alive() and self._wx is not None:
            future = self._request("catalog")
            return future.result(timeout=90)
        return self.db.list_sessions(account=self._state["account"])

    def reimport(self, session_id: str, limit: int) -> dict:
        """按指定条数重新导入某个会话的历史（界面"重新导入"按钮走这里）。

        必须在采集线程上跑：只有它碰上游库。深历史可能读很久，超时给到 300 秒。
        """
        future = self._request("reimport", session_id, int(limit))
        return future.result(timeout=300)

    # ------------------------------------------------------------ 内部
    def _state_update(self, **values):
        with self._guard:
            self._state.update(values)

    def _request(self, operation: str, *args) -> Future:
        if not (self._thread and self._thread.is_alive()):
            raise RuntimeError("微信采集器未运行。")
        if self._wx is None:
            # 线程活着但没连上时，命令永远不会被执行：立刻报错，不能让请求干等超时
            raise RuntimeError("微信还没连上：请打开微信，采集器会自动接上。")
        future = Future()
        self._commands.put((operation, args, future))
        return future

    def _run(self):
        try:
            self._attach()
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
                if self._wx is None:
                    self._try_reattach()
                    continue
                try:
                    self._sync_catalog()
                    self._poll_followed()
                    self._catchup_images()
                    self._upgrade_images()
                    self._ensure_media_key()
                    self._failures = 0
                    self._state_update(state="connected", error=None,
                                       last_sync_at=now_utc())
                except Exception as exc:
                    # 一轮失败、下一轮就自愈的不惊动界面：微信 checkpoint 时上游重建
                    # 解密缓存会短暂撞车（Windows 上表现为 rename 被拒），下一轮自然好。
                    # 连失败两轮才是真异常。同步时间停着不动本身就是信号。
                    self._failures += 1
                    if self._failures >= 2:
                        self._state_update(state="error", error=str(exc)[:300])
                    log.warning("采集轮次失败（连续第 %d 次）：%s", self._failures, exc)
        finally:
            self._detach()
            self._drain_commands()
            self._state_update(state="stopped")

    def _build_media(self, account: str) -> None:
        """把上游的图片解密器包成本工程的 ImageStore（钥匙闸门在它里面）。"""
        from wechatauto.media import MediaDownloader
        self._media = ImageStore(MediaDownloader(self._wx), self.root, account)

    def _detach(self) -> None:
        self._wx = None
        self._media = None

    def _try_reattach(self) -> None:
        """没连上时按冷却重试：用户随时可能才把微信打开。"""
        if time.monotonic() < self._next_attach:
            return
        self._attach()

    def _attach(self) -> bool:
        """连一次微信库。失败不抛出、不退出线程，只改状态并给出下次重试时间。"""
        try:
            self._wx = open_isolated_db(self.root, self.settings)
            info = self._wx.get_self_info() or {}
            account = info.get("username") or self._wx.wxid
            if not account:
                raise RuntimeError("无法确定当前微信账号。")
            self._state_update(account=account)
            self._build_media(account)
            self._sync_catalog()
            self._state_update(state="connected", error=None, last_sync_at=now_utc())
            return True
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            self._detach()
            self._next_attach = time.monotonic() + ATTACH_RETRY_SECONDS
            # "微信没开"和"微信开着但读不通"是两件事，界面上要说不同的话
            state = "waiting_wechat" if not wechat_running() else "error"
            self._state_update(state=state, error=message[:300])
            log.warning("微信连接失败（%s）：%s", state, message)
            self._drain_commands()
            return False

    def _execute(self, operation: str, args: tuple, future: Future):
        try:
            if operation == "catalog":
                future.set_result(self._sync_catalog())
            elif operation == "reimport":
                future.set_result(self._reimport(*args))
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
                self._import_recent(session, self._history_limit(session))
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

    def _catchup_images(self) -> int:
        """把"采集那一刻本机还没文件"的图片行补上链接，每轮限量。

        微信要翻到那条聊天记录才会把预览图落到盘上，所以这类行会一直缺
        ``local_file``。补链接的活**只在这里做**：网页请求线程一旦去解密图片，就可能
        连带触发"翻微信内存找钥匙"，那会把整个服务冻住（实测界面转圈两分钟）。

        哪些行该补由 ``pending_images`` 的投影列说了算（kind/media_md5/local_file 都是
        列，走部分索引）；这里只负责"这一行的 metadata 能不能写回"。
        """
        media = self._media
        if media is None:
            return 0
        done = 0
        for row in self.db.pending_images(IMAGE_CATCHUP_LIMIT):
            if self._stop.is_set():
                break
            meta = row.get("metadata")
            if not meta:
                continue        # 坏 JSON：不写回，免得把 metadata 覆盖成半截
            user = session_user(row["session_id"])
            linked = media.attach(user, row["media_md5"], f"{user}_{row['sort_seq']}")
            if not linked:
                continue
            meta.update(linked)
            self.db.update_metadata(row["id"], meta)
            done += 1
        return done

    def _upgrade_images(self) -> int:
        """把"本机还有更高一档没拿到"的图片行重扫一遍，有就换掉链接。

        和 :meth:`_catchup_images` 是同一条思路的另一半：那一个补"从没有到有"，
        这一个补"从预览图到本机图、再到原图"。用户得在微信里点开那张图（原图还要
        多点一次"查看原图"）大那份才会落盘，我们只读不驱动界面，所以升级只能靠定期
        重扫磁盘追上去——依然只在采集线程做，理由与补链接一样：请求线程不能碰解密。

        每轮真扫的行数封顶，扫过没升上去的进内存冷却表：用户从没点开过的图片永远
        升不上档，不挡的话每轮都会拿队列最前面那批去翻整棵附件树，后面的永远排不到。
        """
        media = self._media
        if media is None:
            return 0
        now = time.monotonic()
        done = tried = 0
        for row in self.db.stale_tier_images(TIER_SCAN_WINDOW):
            if self._stop.is_set() or tried >= TIER_UPGRADE_LIMIT:
                break
            meta = row.get("metadata")
            if not meta:
                continue            # 坏 JSON：同 _catchup_images，不写回
            if now < self._tier_next_at.get(row["id"], 0.0):
                continue            # 冷却中的不占本轮名额，留给下一轮
            tried += 1
            user = session_user(row["session_id"])
            current, old = row["tier"], row["local_file"]
            linked = media.attach(user, row["media_md5"], f"{user}_{row['sort_seq']}")
            if not linked or tier_rank(linked.get("tier")) <= tier_rank(current):
                self._tier_next_at[row["id"]] = now + TIER_RETRY_SECONDS
                continue
            # 先改写库再删旧文件：中途崩了最多留一个没人引用的文件，不会两头都没有
            meta.update(linked)
            self.db.update_metadata(row["id"], meta)
            media.discard(old)
            self._tier_next_at.pop(row["id"], None)
            done += 1
        return done

    def _ensure_media_key(self) -> None:
        """图片钥匙没在手就补，**便宜地勤看、重活限流**。

        两条时间分开：``wechat_running()`` 只是一次进程表查询，几毫秒，所以按
        ``MEDIA_KEY_PROBE_SECONDS`` 勤问一句，微信一开就能立刻动手；而取 cfg 编号、
        扫内存找钥匙是几十秒级的重活，按 ``MEDIA_KEY_RETRY_SECONDS`` 限流。
        原来把两件事共用一个冷却，结果"微信还没开"那一次白问就把 120 秒占掉了，
        用户打开微信后还要再等两分钟才见到图（实测就是这个坑）。

        cfg 编号只从**当前这个**读取器上要：``extract_master_key()`` 是纯读微信进程，
        拿到就填回同一个对象。**不要**在这里重开一个 IsolatedDB——两个读取器各自一套
        ``_build_locks``，会同时重建同一份解密缓存，Windows 上 rename 撞上没关的只读
        句柄就是 ``[WinError 5] 拒绝访问``（实测踩过）。
        """
        media = self._media
        if media is None or media.key_ready:
            return
        now = time.monotonic()
        if now < self._next_key_probe_at:
            return
        self._next_key_probe_at = now + MEDIA_KEY_PROBE_SECONDS
        if not wechat_running():
            return
        if now < self._next_key_at:
            return
        self._next_key_at = now + MEDIA_KEY_RETRY_SECONDS
        if getattr(self._wx, "cfg_dword", None) is None:
            got = self._wx.extract_master_key()
            if got:
                self._wx.cfg_dword = got[1]
        media.prepare_key()

    def _history_limit(self, session: dict) -> int:
        """这个会话该导多少条：会话自己设过的优先，没设过才跟随全局默认。"""
        return clamp_history_limit(session.get("history_limit")
                                   or self.settings.get("history_limit"))

    def _import_recent(self, session: dict, limit: int) -> dict:
        """从最新往旧翻页取满 limit 条；首次关注和手动重新导入共用这一条路径。"""
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

    def _reimport(self, session_id: str, limit: int) -> dict:
        """按 limit 重新导入该会话历史：记下条数 → 清水位 → 立刻导。

        不删任何已有消息：水位清空后 _import_recent 会把最新 limit 条重读一遍，
        重复行由 source_id 唯一键跳过，只有更旧的那段真正入库。
        水位取 max，所以重新导入不会把增量轮询的游标拉回去。
        """
        assert self._wx is not None
        session = self.db.session(session_id)
        if not session:
            raise ValueError("会话不存在")
        effective = clamp_history_limit(limit or self.settings.get("history_limit"))
        self.db.set_history_limit(session_id, effective)
        self.db.clear_watermark(self._state["account"], session_id)
        session["history_limit"] = effective
        result = self._import_recent(session, effective)
        return {"limit": effective, "monitored": bool(session["monitored"]), **result}

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
        self._attach_quotes(session["username"], payloads)
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
        images = [p for p in payloads
                  if (p["metadata"].get("attachment") or {}).get("kind") == "image"]
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

    def _attach_quotes(self, username: str, payloads: list[dict]) -> None:
        """给引用消息补上被引那条的 ``sort_seq``，前端据此决定能不能跳。

        查不到就留 0：跨会话引用（实测本机 0 条）与本机已不存在的原文都走这条路，
        前端显示成不可点的灰色引用行，不会跳错位置。
        """
        quotes = [p for p in payloads
                  if (p["metadata"].get("attachment") or {}).get("kind") == "quote"]
        if not quotes:
            return
        wanted = {str(p["metadata"]["attachment"]["quoted"].get("svrid") or "")
                  for p in quotes}
        wanted.discard("")
        found = self._wx.quote_seqs(username, wanted) if wanted else {}
        for payload in quotes:
            quoted = payload["metadata"]["attachment"]["quoted"]
            quoted["sort_seq"] = int(found.get(str(quoted.get("svrid") or ""), 0))
