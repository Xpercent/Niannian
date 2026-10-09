"""只读捕获真实微信测试素材；先停止工作台采集，再运行此脚本。"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shiguang.config import ConfigStore, atomic_text
from shiguang.wechat import acquire_instance_lock, infer_self_ids, normalize, open_wechat, session_id


def main():
    parser = argparse.ArgumentParser(description="捕获真实微信回归素材，不发送消息")
    parser.add_argument("--sessions", type=int, default=30)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    lock = acquire_instance_lock(ROOT / "data" / "wechat" / "cache" / "reader.lock")
    if not lock:
        raise SystemExit("采集器正在运行。请在工作台暂停微信连接后再捕获；不会并发读取。")
    try:
        source = open_wechat(ROOT, ConfigStore(ROOT).read())
        account = source.get_self_info()["username"]
        names = source.nickname_map()
        sessions = source.get_sessions(limit=max(1, min(args.sessions, 1000)))
        catalog, raw, normalized = [], [], []
        for session in sessions:
            username = session["username"]
            name = names.get(username) or username
            catalog.append({"id": session_id(account, username), "username": username, "name": name})
            messages = source.get_messages(username, limit=max(1, min(args.limit, 1000)))
            own = infer_self_ids(account, messages)
            for message in reversed(messages):
                raw.append({"session_id": username, "session_name": name, "raw": message})
                normalized.append(normalize(account, username, {**message, "session_name": name}, own))
        atomic_text(ROOT / "data" / "raw-wechat.json", json.dumps({"account": account, "captured_at": time.time(), "messages": raw}, ensure_ascii=False))
        atomic_text(ROOT / "data" / "initial-wechat.json", json.dumps({"account": account, "catalog": catalog, "messages": normalized}, ensure_ascii=False))
        print(json.dumps({"connected": True, "sessions": len(sessions), "actual_messages": len(raw),
                          "verified_self": sum(m["is_self"] and m["identity_confident"] for m in normalized)}, ensure_ascii=False))
    finally:
        lock.close()


if __name__ == "__main__":
    main()
