"""入口：python -m niannian —— 按 config.json 启动看板服务。"""

import uvicorn

from .api import create_app
from .config import load_config
from .api import project_root


def main() -> None:
    root = project_root()
    settings = load_config(root)
    uvicorn.run(create_app(root), host=str(settings["host"]),
                port=int(settings["port"]), log_level="info")


if __name__ == "__main__":
    main()
