"""本机单进程启动入口。"""

import argparse
import logging
from pathlib import Path

import uvicorn

from .api import create_app


def main():
    parser = argparse.ArgumentParser(description="念念 · 微信消息与智能待办工作台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--no-wechat", action="store_true", help="关闭实际微信读取，仅查看已有数据")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(args.root, live_wechat=not args.no_wechat), host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
