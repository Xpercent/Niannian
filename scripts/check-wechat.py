"""通过工作台 API 检查链路，仅输出计数，不输出账号或聊天正文。"""

import argparse
import json
from urllib.request import urlopen

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8765)
args = parser.parse_args()
with urlopen(f"http://127.0.0.1:{args.port}/api/health", timeout=10) as response:
    health = json.load(response)
print(json.dumps({"ok": health["ok"], "version": health["version"], "wechat_state": health["wechat"]["state"],
                  "wechat_error": health["wechat"].get("error"), "catalog_sessions": health["wechat"].get("sessions", 0),
                  "stats": health["stats"], "thinking_enabled": health["thinking_enabled"]}, ensure_ascii=False, indent=2))
