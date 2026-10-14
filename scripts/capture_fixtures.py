"""把关注会话的消息**原文**（含未解析的 XML）导出成一个可离线回放的 fixture 库。

用途：pytest 是唯一自动化验证，但真机验证要求本机登录着微信。这个脚本把上游
`get_messages` 给出的原始行落进 `data/fixtures/capture.db`，测试就能拿它离线回放
`normalize_message`，不需要微信在线。

跑法（**要先停掉本工程的服务**：同一份解密缓存不该被两个读取方同时开）::

    .venv\\Scripts\\python scripts\\capture_fixtures.py            # 每个会话最多 100 条
    .venv\\Scripts\\python scripts\\capture_fixtures.py --limit 300

只读：不驱动微信界面、不发消息、不触发下载附件。产物在 `data/`（已 gitignore），
里面是真实聊天内容，不要提交。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from niannian.config import load_config
from niannian.storage import Database
from niannian.wechat import open_isolated_db

# 上游 get_messages 给的就是这几列，fixture 按原样存，回放时直接喂给 normalize_message
SCHEMA = """
CREATE TABLE sessions (
  username TEXT PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  names_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE messages (
  session_username TEXT NOT NULL REFERENCES sessions(username) ON DELETE CASCADE,
  local_id INTEGER NOT NULL DEFAULT 0,
  server_id TEXT,
  sort_seq INTEGER NOT NULL DEFAULT 0,
  message_type TEXT NOT NULL DEFAULT '文本',
  sender_id INTEGER NOT NULL DEFAULT 0,
  sender_username TEXT NOT NULL DEFAULT '',
  create_time INTEGER NOT NULL DEFAULT 0,
  content TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_messages_session ON messages(session_username, sort_seq, local_id);
CREATE TABLE meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);
"""


def collect(wx, username: str, limit: int) -> list[dict]:
    """从最新往旧翻页取满 limit 条，返回按时间升序的原始行。"""
    rows: list[dict] = []
    while len(rows) < limit:
        page = wx.get_messages(username, limit=min(200, limit - len(rows)),
                               offset=len(rows))
        if not page:
            break
        rows.extend(page)
    rows.sort(key=lambda m: (int(m.get("sort_seq") or 0), int(m.get("local_id") or 0)))
    return rows


def write_fixture(path: Path, account: str, captured: list[tuple], limit: int) -> dict:
    """落盘 fixture 库；返回每个会话的统计（含还剩多少条正文是 XML）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    stats = {}
    with conn:
        conn.executescript(SCHEMA)
        for session, names, rows in captured:
            conn.execute("INSERT INTO sessions (username, name, names_json) VALUES (?,?,?)",
                         (session["username"], session.get("name", ""),
                          json.dumps(names, ensure_ascii=False)))
            for message in rows:
                content = str(message.get("content") or "")
                conn.execute(
                    "INSERT INTO messages (session_username, local_id, server_id, sort_seq, "
                    "message_type, sender_id, sender_username, create_time, content) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (session["username"], int(message.get("local_id") or 0),
                     message.get("server_id"), int(message.get("sort_seq") or 0),
                     str(message.get("type") or "文本"), int(message.get("sender_id") or 0),
                     str(message.get("sender_username") or ""),
                     int(message.get("create_time") or 0), content))
            xml = sum(1 for m in rows
                      if str(m.get("content") or "").strip().startswith("<"))
            stats[session.get("name") or session["username"]] = {"rows": len(rows), "xml": xml}
        conn.executemany("INSERT INTO meta (key, value) VALUES (?,?)", [
            ("account", account),
            ("captured_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
            ("limit", str(limit)),
        ])
    conn.close()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="导出关注会话的原始消息 fixture")
    parser.add_argument("--limit", type=int, default=100, help="每个会话最多导几条")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "fixtures" / "capture.db")
    args = parser.parse_args()

    settings = load_config(ROOT)
    store = Database(ROOT / "data" / "niannian.db")
    store.init()
    followed = store.list_sessions(monitored_only=True)
    if not followed:
        print("工程库里没有正在关注的会话，先在看板里关注再跑。")
        return 1
    account = followed[0]["account"]

    wx = open_isolated_db(ROOT, settings)
    captured = []
    for session in followed:
        names = wx.display_names(session["username"])
        rows = collect(wx, session["username"], max(1, args.limit))
        captured.append((session, names, rows))

    stats = write_fixture(args.out, account, captured, args.limit)
    total = sum(v["rows"] for v in stats.values())
    xml = sum(v["xml"] for v in stats.values())
    print("写入 %s\n共 %d 条，其中正文仍是 XML 的 %d 条" % (args.out, total, xml))
    for name, value in stats.items():
        print("  %-24s rows=%3d xml=%3d" % (name, value["rows"], value["xml"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
