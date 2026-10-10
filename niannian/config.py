"""有类型约束的文件配置；一轮业务处理只使用一份配置快照。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    db_path: str = "data/workspace.db"
    profile_path: str = "data/PROFILE.md"
    ai_enabled: bool = True
    ai_base_url: str = "https://api.siliconflow.cn/v1"
    ai_api_key: str = ""
    ai_model: str = "Qwen/Qwen3-8B"
    ai_timeout_seconds: int = Field(default=40, ge=5, le=180)
    merge_window_seconds: int = Field(default=60, ge=0, le=600)
    settle_window_seconds: int = Field(default=60, ge=0, le=600)
    batch_interval_seconds: int = Field(default=300, ge=15, le=86400)
    wechat_enabled: bool = True
    wechat_poll_seconds: int = Field(default=2, ge=1, le=60)
    history_limit: int = Field(default=100, ge=10, le=1000)
    # 模型请求参数；enable_thinking 不在这里，它始终固定为关闭。
    ai_temperature: float = Field(default=0.0, ge=0, le=2)
    ai_max_tokens_single: int = Field(default=700, ge=64, le=8000)
    ai_max_tokens_batch: int = Field(default=1200, ge=64, le=16000)
    # 每轮的吞吐边界：段数、字数与单轮/单会话取多少条。
    batch_limit: int = Field(default=4, ge=1, le=20)
    batch_char_budget: int = Field(default=4200, ge=400, le=60000)
    round_limit: int = Field(default=240, ge=1, le=2000)
    per_session_limit: int = Field(default=80, ge=1, le=500)
    # 送进模型的上下文预算。
    new_message_chars: int = Field(default=2600, ge=200, le=12000)
    history_chars: int = Field(default=1200, ge=0, le=12000)
    task_chars: int = Field(default=1600, ge=0, le=12000)
    profile_chars: int = Field(default=1200, ge=0, le=12000)
    history_rows: int = Field(default=6, ge=0, le=20)
    # 每个会话保留多少条“无需行动”判定记录。
    ignored_keep_per_session: int = Field(default=100, ge=10, le=2000)

    @field_validator("ai_base_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return value
        if "://" not in value:
            value = "https://" + value
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
            raise ValueError("模型接口须为有效的 HTTP(S) 地址")
        return value

    @field_validator("db_path", "profile_path", "ai_model")
    @classmethod
    def nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("此项不能为空")
        return value

    @classmethod
    def defaults(cls) -> dict:
        """界面用来显示“默认值”和一键恢复；存储路径与密钥不在其中。"""
        values = Settings().model_dump()
        return {key: value for key, value in values.items()
                if key not in {"db_path", "profile_path", "ai_api_key"}}


def atomic_text(path: Path, text: str) -> None:
    """同目录临时文件 + fsync + replace；失败不会覆盖原文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix="." + path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, path)
    finally:
        Path(filename).unlink(missing_ok=True)


class ConfigStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = self.root / "config.json"
        self.lock = RLock()
        self.warning: str | None = None
        values = {}
        if self.path.exists():
            try:
                values = json.loads(self.path.read_text(encoding="utf-8-sig"))
                # 接受原工程的小型配置；不再提供演示播种开关。
                if not isinstance(values, dict):
                    raise ValueError("配置必须是 JSON 对象")
                values = {k: v for k, v in values.items() if k in Settings.model_fields}
            except (ValueError, OSError) as exc:
                self.warning = f"配置读取失败：{type(exc).__name__}；当前使用默认设置，原文件未覆盖"
                values = {}
        try:
            self.settings = Settings.model_validate(values)
        except ValueError as exc:
            self.warning = f"配置校验失败：{str(exc)[:200]}；当前使用默认设置，原文件未覆盖"
            self.settings = Settings()
        if not self.path.exists():
            atomic_text(self.path, json.dumps(self.read(), ensure_ascii=False, indent=2) + "\n")

    def read(self) -> dict:
        with self.lock:
            return self.settings.model_dump()

    def update(self, fields: dict) -> dict:
        with self.lock:
            updated = Settings.model_validate({**self.read(), **fields})
            atomic_text(self.path, json.dumps(updated.model_dump(), ensure_ascii=False, indent=2) + "\n")
            self.settings = updated
            self.warning = None
            return self.public()

    def resolve(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    def public(self) -> dict:
        values = self.read()
        key = values.pop("ai_api_key")
        return {**values, "ai_key_configured": bool(key), "thinking_enabled": False,
                "defaults": Settings.defaults(), "warning": self.warning}


class ProfileStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = RLock()

    def read(self) -> str:
        with self.lock:
            return self.path.read_text(encoding="utf-8-sig") if self.path.exists() else ""

    def save(self, markdown: str) -> None:
        with self.lock:
            atomic_text(self.path, markdown)
