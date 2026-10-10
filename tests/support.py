"""测试回放只使用本机实际微信捕获，不生成聊天正文。"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def capture():
    path = ROOT / "data" / "raw-wechat.json"
    if not path.exists():
        raise unittest.SkipTest("缺少真实微信捕获 data/raw-wechat.json；没有合成聊天替代")
    return json.loads(path.read_text(encoding="utf-8"))


def ingest_capture(db, limit=20):
    from niannian.wechat import normalize, session_id
    data = capture()
    account = data["account"]
    items = data["messages"][:limit]
    catalog = {item["session_id"]: {"id": session_id(account, item["session_id"]), "username": item["session_id"], "name": item.get("session_name") or item["session_id"]} for item in items}
    db.sync_catalog(account, list(catalog.values()))
    payloads = [normalize(account, item["session_id"], item["raw"]) for item in items]
    result = db.ingest_batch(payloads)
    return payloads, result


def task_record(db, sid, title="仓储状态验证"):
    from niannian.storage import utc_now
    return db.execute("INSERT INTO tasks(session_id,title,description,created_at,updated_at) VALUES(?,?,?,?,?)", (sid, title, "直接验证任务记录，不构造聊天", utc_now(), utc_now()))
