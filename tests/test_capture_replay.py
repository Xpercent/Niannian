"""拿真机捕获的 fixture 库离线回放 `normalize_message`。

fixture 由 `scripts/capture_fixtures.py` 生成（关注会话各最多 100 条**原始正文**），
落在 `data/fixtures/capture.db`。没有这个文件就整组 skip——**不造聊天数据刷绿**。

这里断言的都是"任何一次重新捕获都必须成立"的不变式，不是某批数据的具体条数。
"""

import json
import sqlite3
from pathlib import Path

import pytest

from niannian.describe import parse_xml, summarize
from niannian.wechat import normalize_message, split_sender

CAPTURE = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "capture.db"

pytestmark = pytest.mark.skipif(not CAPTURE.is_file(), reason=(
    "缺少真机捕获 fixture，先跑 scripts/capture_fixtures.py（需要本机登录微信）"))


def rows():
    conn = sqlite3.connect(f"file:{CAPTURE.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        account = conn.execute("SELECT value FROM meta WHERE key='account'").fetchone()[0]
        sessions = {r["username"]: (r["name"], json.loads(r["names_json"]))
                    for r in conn.execute("SELECT username, name, names_json FROM sessions")}
        out = []
        for row in conn.execute("SELECT * FROM messages ORDER BY session_username, sort_seq"):
            name, names = sessions[row["session_username"]]
            message = {"local_id": row["local_id"], "server_id": row["server_id"],
                       "sort_seq": row["sort_seq"], "type": row["message_type"],
                       "sender_id": row["sender_id"],
                       "sender_username": row["sender_username"],
                       "create_time": row["create_time"], "content": row["content"]}
            out.append((account, row["session_username"], name, message, names))
        return out
    finally:
        conn.close()


def is_quote(content: str) -> bool:
    """引用/回复消息（appmsg type=57）：本轮按用户要求不格式化，允许留 XML。"""
    root = parse_xml(content)
    app = root.find("appmsg") if root is not None and root.tag == "msg" else None
    return app is not None and (app.findtext("type") or "").strip() == "57"


def test_capture_has_replayable_sessions():
    captured = rows()
    assert captured, "fixture 里没有消息，重新捕获一次再跑"
    assert all(message["content"] for _, _, _, message, _ in captured)


def test_only_quote_messages_stay_raw_xml():
    """除引用消息外，任何真机正文都不该以 XML 形态进气泡。"""
    for account, username, name, message, names in rows():
        body = normalize_message(account, username, name, message, names)["content"]
        if body.lstrip().startswith("<"):
            # 判断前先剥群前缀：产品侧看到的正文是 split_sender 之后的那一段
            assert is_quote(split_sender(message["content"])[1]), \
                f"{message['type']} 的正文漏了没解析：{body[:80]}"


def test_every_row_has_content_and_a_sender():
    for account, username, name, message, names in rows():
        item = normalize_message(account, username, name, message, names)
        assert item["content"].strip(), item["metadata"]
        assert item["sender_name"], item["metadata"]


def test_parsed_kinds_summarize_and_match_storage():
    """解析出来的每一类：摘要与 describe/summarize 一致，且不重复套方括号。"""
    seen = set()
    for account, username, name, message, names in rows():
        item = normalize_message(account, username, name, message, names)
        detail = item["metadata"]["attachment"]
        if not detail:
            continue
        seen.add(detail["kind"])
        assert item["content"] == summarize(detail)
        assert item["content"].count("[") <= 1, item["content"]
        if detail["kind"] == "image":
            assert item["content"] == "[图片]"
        if detail["kind"] == "emoji":
            assert item["content"] == "[动画表情]"
    assert {"image", "emoji"} <= seen, f"这批捕获没覆盖到基础类型：{seen}"


def test_transfer_direction_never_leaks_a_wxid():
    """转账方向只能拿发送者比 payer/receiver；认不出对方就不加前缀，绝不贴 wxid。"""
    checked = 0
    for account, username, name, message, names in rows():
        item = normalize_message(account, username, name, message, names)
        detail = item["metadata"]["attachment"] or {}
        if detail.get("kind") != "transfer":
            continue
        checked += 1
        content = item["content"]
        assert not content.lstrip().startswith("<")
        assert "wxid_" not in content, content
        sender = item["sender_key"]
        payer, receiver = detail["payer_username"], detail["receiver_username"]
        if sender == payer:
            assert content.startswith("向"), content
            assert detail["target"] == names.get(receiver)
        elif sender == receiver:
            assert content.startswith("收到"), content
            assert detail["target"] == names.get(payer)
        else:
            assert content.startswith("[转账]"), content
    if not checked:
        pytest.skip("这批捕获里没有转账消息，重新关注一个有转账的会话再跑")
