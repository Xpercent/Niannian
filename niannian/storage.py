"""SQLite 存储层：会话目录、消息、采集水位。全部查询走显式参数绑定。"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  account TEXT NOT NULL,
  username TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  monitored INTEGER NOT NULL DEFAULT 0,
  unread INTEGER NOT NULL DEFAULT 0,
  last_message_at TEXT,
  updated_at TEXT NOT NULL DEFAULT '',
  history_limit INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL UNIQUE,
  sender_key TEXT NOT NULL DEFAULT '',
  sender_name TEXT NOT NULL DEFAULT '',
  is_self INTEGER NOT NULL DEFAULT 0,
  content TEXT NOT NULL DEFAULT '',
  message_type TEXT NOT NULL DEFAULT '文本',
  created_at TEXT NOT NULL,
  sort_seq INTEGER NOT NULL DEFAULT 0,
  metadata TEXT NOT NULL DEFAULT '{}',
  kind TEXT NOT NULL DEFAULT '',
  media_md5 TEXT NOT NULL DEFAULT '',
  local_file TEXT NOT NULL DEFAULT '',
  tier TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, sort_seq, id);
-- 采集线程每轮取图片活的两条工作清单。这两条**每轮都跑**（默认 3 秒），而 messages
-- 会长到几万行，所以谓词必须能走索引、排序必须就是索引序。原来用 instr(metadata,
-- '"kind": "image"') 匹配文本：走不了索引（每轮全表扫 + 临时 B 树排序），还绑死了
-- json.dumps 的分隔符。下面两个部分索引对应 storage._image_worklist 的两个分支。
CREATE INDEX IF NOT EXISTS idx_messages_image_pending ON messages(sort_seq DESC, id DESC)
  WHERE kind='image' AND media_md5<>'' AND local_file='';
CREATE INDEX IF NOT EXISTS idx_messages_image_upgrade ON messages(sort_seq DESC, id DESC)
  WHERE kind='image' AND local_file<>'' AND tier<>'original';
CREATE TABLE IF NOT EXISTS checkpoints (
  account TEXT NOT NULL,
  session_id TEXT NOT NULL,
  sort_seq INTEGER NOT NULL,
  updated_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (account, session_id)
);
"""

# 老库缺这四列时给一句话说明，而不是让每轮采集撞在 "no such column: kind" 上。
PROJECTION_COLUMNS = ("kind", "media_md5", "local_file", "tier")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


LIKE_SPECIAL = re.compile(r"([%_\\])")


def like_pattern(text: str) -> str:
    """把关键词包成 LIKE 的 ``%子串%``，并转义 ``%``、``_`` 和反斜杠本身。

    不转义的话搜「100%」会把 ``%`` 当通配符，捞出一堆不相干的行；``_`` 同理是单字通配。
    """
    return "%" + LIKE_SPECIAL.sub(r"\\\1", text) + "%"


def load_metadata(text) -> dict | None:
    """metadata 是文本列：坏 JSON 返回 None，由调用方决定当空对象还是跳过这一行。

    非 dict 的合法 JSON（``[]``、``"x"``）同样算坏：调用方一律按 dict 用。
    """
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def as_message(row) -> dict:
    """消息行 → 接口返回的记录：``is_self`` 转 bool，metadata 解析成 dict。"""
    item = dict(row)
    item["is_self"] = bool(item["is_self"])
    item["metadata"] = load_metadata(item.get("metadata")) or {}
    return item


def media_projection(metadata: dict) -> tuple:
    """metadata → ``(kind, media_md5, local_file, tier)`` 四列的值。

    这四列是 metadata 的**投影**，不是第二份事实：只有 :meth:`Database.ingest` 与
    :meth:`Database.update_metadata` 两个写入口会算它，读侧（采集线程的工作清单、
    每轮的判定）一律按列走。metadata 本身仍是完整结构，前端与引用跳转照旧读它。
    """
    meta = metadata or {}
    attachment = meta.get("attachment") or {}
    return (str(attachment.get("kind") or ""), str(meta.get("media_md5") or ""),
            str(meta.get("local_file") or ""), str(meta.get("tier") or ""))


class Database:
    """一个文件库；连接按次创建（WAL），写事务由进程内锁保护。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._write_lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def init(self) -> None:
        with self._write_lock, self.connect() as conn:
            # 先认结构再建索引：投影索引的 WHERE 里用到那四列，老库上 executescript
            # 会直接撞 "no such column: kind"，用户就看不到下面这句话了。
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='messages'").fetchone():
                have = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
                missing = [name for name in PROJECTION_COLUMNS if name not in have]
                if missing:
                    # CREATE TABLE IF NOT EXISTS 不会给已有的表补列，所以这是老库。
                    raise RuntimeError(
                        f"数据库结构是旧版，缺 {'、'.join(missing)} 列："
                        f"删掉 {self.path} 重新采集即可（已入库的消息本来就要重采）。")
            conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ 会话
    def sync_catalog(self, account: str, items: list[dict]) -> None:
        """写入目录快照：只更新展示字段，关注状态（monitored）永不被动改变。"""
        stamp = now_utc()
        with self._write_lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for item in items:
                conn.execute(
                    """INSERT INTO sessions(id, account, username, name, unread,
                                            last_message_at, updated_at)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET
                         name=excluded.name,
                         unread=excluded.unread,
                         last_message_at=excluded.last_message_at,
                         updated_at=excluded.updated_at""",
                    (item["id"], account, item["username"], item.get("name") or "",
                     int(item.get("unread") or 0), item.get("last_message_at"), stamp),
                )

    def session(self, session_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["monitored"] = bool(item["monitored"])
        return item

    def set_history_limit(self, session_id: str, limit: int) -> bool:
        """记下这个会话自己要导入的条数（0 表示跟随全局默认）。

        刻意与全局 ``history_limit`` 分开：调某一个会话的条数不该影响其它会话。
        """
        with self._write_lock, self.connect() as conn:
            cur = conn.execute("UPDATE sessions SET history_limit=? WHERE id=?",
                               (int(limit), session_id))
        return cur.rowcount > 0

    def set_monitored(self, session_id: str, monitored: bool) -> bool:
        """关注/取关。返回 False 表示会话不存在。"""
        with self._write_lock, self.connect() as conn:
            cur = conn.execute(
                "UPDATE sessions SET monitored=?, updated_at=? WHERE id=?",
                (1 if monitored else 0, now_utc(), session_id))
        return cur.rowcount > 0

    def list_sessions(self, account: str = "", monitored_only: bool = False) -> list[dict]:
        sql = """SELECT s.*,
                        (SELECT COUNT(*) FROM messages m
                          WHERE m.session_id = s.id) AS message_count
                 FROM sessions s"""
        args: list = []
        where = []
        if account:
            where.append("s.account=?")
            args.append(account)
        if monitored_only:
            where.append("s.monitored=1")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY s.last_message_at DESC, s.id"
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["monitored"] = bool(item["monitored"])
            out.append(item)
        return out

    # ------------------------------------------------------------------ 水位
    def watermark(self, account: str, session_id: str) -> int | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT sort_seq FROM checkpoints WHERE account=? AND session_id=?",
                (account, session_id)).fetchone()
        return int(row["sort_seq"]) if row else None

    def clear_watermark(self, account: str, session_id: str) -> None:
        """只清采集水位，**不删任何已入库消息**：下一轮会当作没导过重新导入，
        重复行由 messages.source_id 唯一键自动跳过。
        """
        with self._write_lock, self.connect() as conn:
            conn.execute("DELETE FROM checkpoints WHERE account=? AND session_id=?",
                         (account, session_id))

    # ------------------------------------------------------------------ 消息
    def ingest(self, payloads: list[dict], checkpoint: dict | None = None) -> int:
        """同事务插入消息并推进水位；水位只前进不后退。返回新插入条数。"""
        if not payloads and not checkpoint:
            return 0
        stamp = now_utc()
        with self._write_lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            inserted = 0
            for p in payloads:
                metadata = p.get("metadata") or {}
                kind, digest, local_file, tier = media_projection(metadata)
                cur = conn.execute(
                    """INSERT OR IGNORE INTO messages(
                         session_id, source_id, sender_key, sender_name, is_self,
                         content, message_type, created_at, sort_seq, metadata,
                         kind, media_md5, local_file, tier)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (p["session_id"], p["source_id"], p.get("sender_key", ""),
                     p.get("sender_name", ""), 1 if p.get("is_self") else 0,
                     p.get("content", ""), p.get("message_type", "文本"),
                     p["created_at"], int(p.get("sort_seq") or 0),
                     json.dumps(metadata, ensure_ascii=False),
                     kind, digest, local_file, tier))
                inserted += cur.rowcount
            if checkpoint:
                conn.execute(
                    """INSERT INTO checkpoints(account, session_id, sort_seq, updated_at)
                       VALUES(?,?,?,?)
                       ON CONFLICT(account, session_id) DO UPDATE SET
                         sort_seq=MAX(sort_seq, excluded.sort_seq),
                         updated_at=excluded.updated_at""",
                    (checkpoint["account"], checkpoint["session_id"],
                     int(checkpoint["sort_seq"]), stamp))
        return inserted

    def messages_page(self, session_id: str, limit: int = 40,
                      before_seq: int | None = None,
                      before_id: int | None = None) -> dict:
        """按消息时间倒序翻页；items 保持“新→旧”，前端自行反转为阅读顺序。

        排序键必须是 ``sort_seq`` 而不是自增 ``id``：id 只代表入库先后。会话一旦
        重新导入过历史，更旧的消息会带着**更大**的 id 插进来，按 id 排顺序就翻了。
        同一 sort_seq 并列时用 id 做稳定 tiebreak——跨分片 local_id 本身会重复，
        没有更可靠的列能破并列，接受同秒内按入库先后。
        """
        limit = max(1, min(int(limit), 500))
        sql = """SELECT id, session_id, source_id, sender_key, sender_name, is_self,
                        content, message_type, created_at, sort_seq, metadata
                 FROM messages WHERE session_id=?"""
        args: list = [session_id]
        if before_seq is not None:
            sql += " AND (sort_seq<? OR (sort_seq=? AND id<?))"
            args += [int(before_seq), int(before_seq), int(before_id or 0)]
        sql += " ORDER BY sort_seq DESC, id DESC LIMIT ?"
        args.append(limit + 1)
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [as_message(row) for row in rows]
        tail = rows[-1] if rows else None
        return {
            "items": items,
            "before_seq": tail["sort_seq"] if tail else None,
            "before_id": tail["id"] if tail else None,
            "has_more": has_more,
        }

    def quoted_targets(self, session_id: str, seqs: set) -> dict:
        """被引消息在本库里的行号 ``{sort_seq: id}``；没导入的不会出现，前端据此判可点。"""
        wanted = sorted({int(s) for s in seqs if int(s)})
        if not wanted:
            return {}
        marks = ",".join("?" * len(wanted))
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT sort_seq, id FROM messages WHERE session_id=? "
                f"AND sort_seq IN ({marks})", [session_id, *wanted]).fetchall()
        return {int(row["sort_seq"]): int(row["id"]) for row in rows}

    def search_messages(self, session_id: str, query: str, limit: int = 50) -> list[dict]:
        """在该会话**已入库**的消息正文里找子串，新→旧。

        只做子串匹配：本机消息量不大，上 FTS 要先决定分词（中文按字还是按词），
        等真有需要再说。``content`` 存的已经是解析后的中文摘要，所以搜"图片"
        "转账"这类标签天然也能命中，不需要额外匹配 metadata。
        """
        text = str(query or "").strip()
        if not text:
            return []
        limit = max(1, min(int(limit), 200))
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT id, session_id, sender_key, sender_name, is_self, content,
                          message_type, created_at, sort_seq, metadata
                   FROM messages WHERE session_id=? AND content LIKE ? ESCAPE '\\'
                   ORDER BY sort_seq DESC, id DESC LIMIT ?""",
                (session_id, like_pattern(text), limit)).fetchall()
        return [as_message(row) for row in rows]

    def pending_images(self, limit: int = 30) -> list[dict]:
        """关注会话里还缺本机链接的图片行（采集那一刻文件还没落盘的那些），新→旧。"""
        return self._image_worklist(limit, linked=False)

    def stale_tier_images(self, limit: int = 200) -> list[dict]:
        """关注会话里**还没拿到原图**的图片行（手里是预览图或本机下发那一份），新→旧。

        这是采集线程每轮"重扫升级"的候选清单：微信要用户在界面里点开图片、甚至点
        "查看原图"，更高那一档才会落到 ``msg/attach``，而我们只读不驱动界面，所以
        升级只能靠定期重扫磁盘追上去。中间那档 ``.dat`` 本机判不出它是不是完整图
        （上游实测结论），所以停在那一档不算到底，``_h.dat`` 才是终点。

        ``limit`` 给的是**候选窗口**而不是本轮处理数：冷却在采集侧，窗口取太窄会被
        刚扫过的新行占满，排在后面的永远排不到。
        """
        return self._image_worklist(limit, linked=True)

    def _image_worklist(self, limit: int, linked: bool) -> list[dict]:
        """两条图片工作清单共用的查询：只差"手里有没有链接"这一件事。

        判定全按投影列走，不再匹配 metadata 的文本：这样两条都能落在 SCHEMA 里那两个
        部分索引上（排序就是索引序，没有临时 B 树），也不受 ``json.dumps`` 分隔符形状
        的影响。metadata 仍然取回来，因为补完链接要整块写回去；坏 JSON 的行交回来是
        ``None``，由采集侧跳过——**不能**因为一行写坏就把每轮都要跑的查询打死。
        """
        have = ("m.local_file<>'' AND m.tier<>'original'" if linked
                else "m.local_file=''")
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT m.id, m.session_id, m.sort_seq, m.metadata,
                          m.kind, m.media_md5, m.local_file, m.tier
                   FROM messages m JOIN sessions s ON s.id = m.session_id
                   WHERE s.monitored = 1
                     AND m.kind = 'image' AND m.media_md5 <> '' AND {have}
                   ORDER BY m.sort_seq DESC, m.id DESC LIMIT ?""",
                (max(1, int(limit)),)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["metadata"] = load_metadata(item["metadata"])
            out.append(item)
        return out

    def update_metadata(self, message_id: int, metadata: dict) -> None:
        """整块改写一条消息的 metadata（补媒体链接用），并同步刷新投影列。

        内容与水位都不动。投影列只在这里和 :meth:`ingest` 两处写，读侧才不会读到
        跟 metadata 不一致的半截状态。
        """
        kind, digest, local_file, tier = media_projection(metadata)
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """UPDATE messages SET metadata=?, kind=?, media_md5=?,
                          local_file=?, tier=? WHERE id=?""",
                (json.dumps(metadata, ensure_ascii=False), kind, digest, local_file,
                 tier, int(message_id)))
