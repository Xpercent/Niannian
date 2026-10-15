"""上游读取器：工程内隔离缓存的 IsolatedDB，以及只在分片上跑的补查。

这里的查询全部打在**微信自己的**解密缓存上（不是本工程的库），所以只能由采集线程
经 :class:`niannian.wechat.Collector` 调——一个进程里不许出现第二个读取器，理由见
AGENTS.md 的「一个进程里只能有一个读取器」。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from .contacts import display_name, group_nicknames
from .ids import account_scope
from .media import hash_from_blob

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


def server_seq_map(conn: sqlite3.Connection, table: str, wanted: set) -> dict:
    """``{svrid: sort_seq}``——按 ``server_id`` 反查被引那条消息在本会话的排序号。

    引用消息只带被引那条的 ``svrid``，而跳转要的是 ``sort_seq``（会话内唯一，
    也正是我们的翻页游标）。查的是微信的分片库而不是本工程库，所以原文还在
    导入窗口外时同样查得到，等它被导入后同一个 sort_seq 自然对上。
    """
    if not wanted:
        return {}
    keys = [int(s) if str(s).isdigit() else str(s) for s in wanted]
    quoted = '"%s"' % str(table).replace('"', '""')
    marks = ",".join("?" * len(keys))
    cursor = conn.execute(
        f"SELECT server_id, sort_seq FROM {quoted} WHERE server_id IN ({marks})", keys)
    return {str(row[0]): int(row[1]) for row in cursor}


def open_isolated_db(root: Path, settings: dict):
    """按上游库建立工程内隔离缓存的读取器；可复用本机已有的同账号密钥副本。"""
    candidate = root / "wechatauto-replica"
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
        # 只从"别处已经拿到过钥匙"的两个位置抄一份过来。
        guesses = [
            Path(tempfile.gettempdir()) / "wechatauto_db" / item["account"] / "keys.json",
        ]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            guesses.append(Path(local) / "wechatauto_keys" / f"{item['account']}.json")
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

        def media_hashes(self, user: str, keys: set) -> dict:
            """批量取图片行的本机文件哈希（只走采集线程，读上游库）。"""
            return self._each_shard(user, keys, media_hash_map)

        def quote_seqs(self, user: str, svrids: set) -> dict:
            """引用消息的定位：被引那条在本会话分片里的 ``sort_seq``。"""
            return self._each_shard(user, svrids, server_seq_map)

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
                nick = group.get(wxid)
                if not nick:
                    continue
                # 定序交给 contacts.display_name，别在这里再写一遍"谁赢"：
                # 那条优先级是拿微信自己算好的显示名实测反推出来的。
                names[wxid] = display_name(str(member.get("remark") or ""), nick,
                                           str(member.get("nick_name") or ""), wxid)
            return names

    return IsolatedDB(db_dir=settings.get("wechat_db_dir") or None,
                      account=settings.get("wechat_account") or None,
                      workdir=str(cache_root))
