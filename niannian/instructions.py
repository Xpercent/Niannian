"""提炼指令契约、有限的格式归一化与落库前权限检查；不发请求、不写数据库。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .storage import canonical_time, parse_time


class Instruction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["CREATE", "UPDATE", "RESOLVE", "IGNORE"]
    title: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    owner: str | None = Field(default=None, max_length=100)
    priority: Literal["high", "medium", "low"] | None = None
    due_at: str | None = None
    context_summary: str = Field(default="", max_length=4000)
    related_task_id: int | None = Field(default=None, gt=0)
    related_task_ref: str | None = Field(default=None, pattern=r"^\d+:\d+$")
    resolution: Literal["completed", "cancelled"] = "completed"

    @model_validator(mode="before")
    @classmethod
    def relevant_resolution(cls, value):
        if isinstance(value, dict) and value.get("action") != "RESOLVE":
            return {key: item for key, item in value.items() if key != "resolution"}
        return value

    @model_validator(mode="after")
    def check_action(self):
        if self.action == "CREATE" and not (self.title and self.title.strip()):
            raise ValueError("CREATE requires a nonempty title")
        if self.action in {"UPDATE", "RESOLVE"}:
            if bool(self.related_task_id) == bool(self.related_task_ref):
                raise ValueError("Specify exactly one task reference")
        elif self.related_task_id or self.related_task_ref:
            raise ValueError("Only UPDATE and RESOLVE reference a task")
        if self.title is not None and not self.title.strip():
            raise ValueError("A supplied title cannot be blank")
        if self.due_at and datetime.fromisoformat(self.due_at.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("due_at requires a timezone")
        return self


PRIORITY_ALIASES = {
    "1": "high", "2": "medium", "3": "low",
    "高": "high", "中": "medium", "低": "low",
    "urgent": "high", "high": "high", "medium": "medium", "low": "low",
}
TEXT_FIELDS = {"title", "description", "owner", "context_summary"}
UPDATE_FIELDS = {"title", "description", "owner", "priority", "due_at"}
TARGET_ALIASES = ("id", "task_id", "ref")


def _target_id(value: Any, session_name: str = "") -> int:
    """只认完整数字，或明确给出的当前会话名加编号；绝不取任意字符串尾数。"""
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str):
        text = value.strip()
        if session_name and text.startswith(session_name + ":"):
            text = text[len(session_name) + 1:]
        if re.fullmatch(r"[0-9]+", text) and int(text) > 0:
            return int(text)
    raise ValueError("Invalid task id")


def clean_instruction(value: Any, tasks: list[dict[str, Any]] | None = None,
                      session_name: str = "") -> Any:
    """只修复明确的格式差异；无效业务字段、冲突引用及超长文本交给校验拒绝。

    未知键属于输出装饰，可以去掉；不能通过删期限、截标题、猜目标或补完成状态
    把不完整的动作变成成功。UPDATE 的显式 null 期限/负责人表示清空，必须保留。
    """
    if not isinstance(value, dict):
        return value
    action = value.get("action")
    if action == "IGNORE":
        reason = value.get("context_summary")
        return {"action": "IGNORE", **({"context_summary": reason} if isinstance(reason, str) else {})}
    command = {}
    for key, item in value.items():
        if key not in Instruction.model_fields:
            continue
        if item is None and not (action == "UPDATE" and key in {"due_at", "owner"}):
            if key == "resolution" and action == "RESOLVE":
                raise ValueError("RESOLVE requires an explicit resolution")
            continue
        command[key] = item.strip() if key in TEXT_FIELDS and isinstance(item, str) else item
    if "priority" in command:
        priority = command["priority"]
        key = str(priority).strip().lower()
        if type(priority) not in {str, int} or key not in PRIORITY_ALIASES:
            raise ValueError("Invalid priority")
        command["priority"] = PRIORITY_ALIASES[key]
    if action in {"UPDATE", "RESOLVE"}:
        known = {task["id"] for task in tasks or []}
        explicit_id = command.get("related_task_id")
        explicit_ref = command.get("related_task_ref")
        if explicit_id is not None:
            command["related_task_id"] = _target_id(explicit_id)
        if explicit_ref is not None:
            if not isinstance(explicit_ref, str) or not re.fullmatch(r"[0-9]+:[0-9]+", explicit_ref.strip()):
                raise ValueError("Invalid batch task reference")
            command["related_task_ref"] = ":".join(str(int(part)) for part in explicit_ref.split(":"))
        aliases = [_target_id(value[key], session_name) for key in TARGET_ALIASES
                   if key in value and value[key] is not None]
        if aliases:
            targets = set(aliases)
            if explicit_id is not None:
                targets.add(command["related_task_id"])
            if explicit_ref is not None or len(targets) != 1:
                raise ValueError("Conflicting task references")
            command["related_task_id"] = aliases[0]
        if command.get("related_task_id") is not None and command["related_task_id"] not in known:
            raise ValueError("Unknown task reference")
        if action == "RESOLVE" and command.get("resolution") not in {"completed", "cancelled"}:
            raise ValueError("RESOLVE requires an explicit resolution")
        if action == "UPDATE" and not UPDATE_FIELDS.intersection(command):
            raise ValueError("UPDATE requires a changed field")
    else:
        # CREATE 上多余的目标编号不代表任务变更；不应因此丢失明确的新建提案。
        command.pop("related_task_id", None)
        command.pop("related_task_ref", None)
    if command.get("due_at") is not None:
        if not isinstance(command["due_at"], str) or not command["due_at"].strip():
            raise ValueError("Invalid deadline")
        command["due_at"] = canonical_time(parse_time(command["due_at"].strip()))
    return command


def guard_decisions(message, instructions, settings=None):
    """只在引擎落库前检查本人操作权限与可配置的输出限制。"""
    if instructions is None:
        return None
    settings = settings or {}
    title_limit = settings.get("task_title_max_chars", 120)
    task_limit = settings.get("max_tasks_per_message", 0)
    guarded = []
    creates = 0
    for command in instructions:
        reason = ""
        if message["is_self"] and command.action in {"CREATE", "UPDATE"}:
            reason = "本人消息不创建或修改待办"
        elif command.action == "RESOLVE" and command.resolution == "completed" and not message["is_self"]:
            reason = "他人声称完成不能核销本人待办"
        elif command.action in {"CREATE", "UPDATE"} and command.title and len(command.title.strip()) > title_limit:
            reason = f"标题超过配置上限 {title_limit} 字"
        elif command.action == "CREATE":
            creates += 1
            if task_limit and creates > task_limit:
                reason = f"新建任务超过每段配置上限 {task_limit} 项"
        guarded.append(Instruction(action="IGNORE", context_summary=reason) if reason else command)
    return guarded

