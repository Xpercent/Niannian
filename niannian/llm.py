"""OpenAI-compatible extraction transport and independent instruction validation."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any
from urllib.request import Request, urlopen

from .prompts import COMPRESSION_POLICY, extraction_system
from .model_context import render_compression, render_extraction
from .instructions import Instruction, clean_instruction

log = logging.getLogger(__name__)
NEW_MESSAGE_CHARS = 2600
HISTORY_CHARS = 1200
HISTORY_ROWS = 6
TASK_CHARS = 1600
MAX_TOKENS_SINGLE = 700
MAX_TOKENS_BATCH = 1200
TEMPERATURE = 0.0


TRUNCATION_MARKER = "\n[…原文过长，完整来源保存在本地…]\n"


def clip_text(text: str, budget: int) -> str:
    text = str(text)
    if len(text) <= budget:
        return text
    if budget <= 0:
        return ""
    if budget <= len(TRUNCATION_MARKER):
        return text[:budget]
    # Preserve both the request opening and its later instructions/deadline.
    head = min(budget * 2 // 3, budget - len(TRUNCATION_MARKER))
    tail = budget - head - len(TRUNCATION_MARKER)
    return text[:head] + TRUNCATION_MARKER + (text[-tail:] if tail > 0 else "")


def _brief(message: dict[str, Any]) -> dict[str, Any]:
    return {"sender_name": message["sender_name"], "content": message.get("model_content", message["content"]),
            "content_is_summary": "model_content" in message,
            "is_self": bool(message["is_self"]), "created_at": message["created_at"]}


def _item(index, message, raw, proposed, final, *, summary=None, note=None) -> dict[str, Any]:
    """诊断对照条目：模型原样返回、结构校验后、业务校验后三份一起留档。"""
    def dump(commands):
        return None if commands is None else [command.model_dump() for command in commands]
    ids = message.get("message_ids") or ([message["id"]] if message.get("id") else [])
    return {"index": index, "message_ids": list(ids), "raw": raw, "proposed": dump(proposed),
            "final": dump(final), "summary": summary, "note": note,
            "rejected": proposed is None and final is None and summary is None and note is None}


class LLMExtractor:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 25, enabled: bool = True,
                 temperature: float = TEMPERATURE, max_tokens_single: int = MAX_TOKENS_SINGLE,
                 max_tokens_batch: int = MAX_TOKENS_BATCH,
                 new_message_chars: int = NEW_MESSAGE_CHARS, history_chars: int = HISTORY_CHARS,
                 history_rows: int = HISTORY_ROWS, task_chars: int = TASK_CHARS, trace=None,
                 task_title_max_chars: int = 120, max_tasks_per_message: int = 0):
        self.timeout = timeout
        self.enabled = bool(enabled and base_url and api_key)
        self.last_error: str | None = None
        self.stats = {"requests": 0, "failures": 0, "seconds": 0.0}
        self.last_diagnostics = {}
        self.last_outcome = []
        # 诊断记录器；引擎注入，写入失败不回滚提炼事务，见 engine.ModelCallTrace。
        self.trace = trace
        self.last_call_id: int | None = None
        self.last_payload_stats: dict[str, Any] = {}
        # 请求参数由设置页可调；enable_thinking 始终固定为关闭。
        self.temperature, self.max_tokens_single, self.max_tokens_batch = temperature, max_tokens_single, max_tokens_batch
        self.budgets = {"new_message": new_message_chars, "history": history_chars, "rows": history_rows,
                        "tasks": task_chars}
        self.task_title_max_chars = task_title_max_chars
        self.max_tasks_per_message = max_tasks_per_message
        self.configure(base_url, api_key, model)

    def configure(self, base_url: str, api_key: str, model: str) -> None:
        endpoint = base_url.strip().rstrip("/")
        self.endpoint = endpoint if endpoint.endswith("/chat/completions") else f"{endpoint}/chat/completions"
        self.api_key, self.model = api_key, model

    def _post(self, payload: dict[str, Any]):
        request = Request(self.endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                          method="POST")
        return urlopen(request, timeout=self.timeout)

    def probe(self) -> str | None:
        try:
            with self._post({"model": self.model, "temperature": 0, "max_tokens": 12, "enable_thinking": False,
                             "messages": [{"role": "user", "content": "ping"}]}) as response:
                body = json.load(response)
            if not body.get("choices"):
                raise ValueError("Model returned no choices")
            return None
        except Exception as exc:
            return f"{type(exc).__name__}: {str(exc)[:180]}"

    def compress(self, message: dict[str, Any]) -> str | None:
        """Never clip the source before summarizing, or accept a clipped summary."""
        budget = self.budgets["new_message"]
        self.last_payload_stats = {"source_chars": len(message["content"]), "summary_budget": budget}
        result = self._complete(
            {"source_text": message["content"], "max_summary_chars": budget,
             "context_available": False}, False, kind="compress", policy=COMPRESSION_POLICY,
            max_tokens=min(16000, max(1200, budget * 3)), require_finished=True)
        summary = result.get("summary") if result else None
        if (not result or result.get("complete") is not True or not isinstance(summary, str)
                or not summary.strip() or len(summary) > budget or len(summary) >= len(message["content"])):
            if result is not None:
                self.stats["failures"] += 1
            self.last_error = "CompressionRejected"
            self._trace_outcome([_item(0, message, result, None, None, note="压缩输出未通过完整性校验")])
            return None
        self._trace_outcome([_item(0, message, result, None, None, summary=summary)])
        return summary

    def _complete(self, data: dict[str, Any], batch: bool, *, kind: str, policy=None,
                  max_tokens=None, require_finished=False) -> Any:
        self.last_diagnostics = {}
        self.last_outcome = []
        system = policy if policy is not None else extraction_system(
            self.task_title_max_chars, self.max_tasks_per_message,
            len(data.get("new_messages", [])) if batch else None)
        # 输入用 XML 而不是单行 JSON：每条消息的边界与编号由标签给出，正文实体转义后造不出假边界。
        render = render_compression if kind == "compress" else render_extraction
        user = render(data)
        payload = {"model": self.model, "temperature": self.temperature,
                   "max_tokens": max_tokens if max_tokens is not None else (self.max_tokens_batch if batch else self.max_tokens_single),
                   "response_format": {"type": "json_object"},
                   # 诊断记录的就是发出去的字节，思考开关在这里落实而不是请求前偷偷改写。
                   "enable_thinking": False,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        started = time.perf_counter()
        self.stats["requests"] += 1
        record = {"kind": kind, "model": self.model, "temperature": self.temperature,
                  "max_tokens": payload["max_tokens"], "segments": len(data.get("new_messages") or [data.get("new_message") or {}]),
                  "request_json": json.dumps(payload, ensure_ascii=False), "input_chars": len(system) + len(user),
                  "stats_json": {**self.last_payload_stats,
                                 "segment_chars": [len(item.get("content") or "")
                                                   for item in (data.get("new_messages") or [data.get("new_message") or {}])],
                                 "compressed_segments": [bool(item.get("content_is_summary"))
                                                         for item in (data.get("new_messages") or [data.get("new_message") or {}])],
                                 "system_chars": len(system), "payload_chars": len(user)}}
        parsed = None
        try:
            with self._post(payload) as response:
                body = json.load(response)
            choice = body["choices"][0]
            record["finish_reason"] = choice.get("finish_reason")
            record["usage_json"] = body.get("usage")
            raw = choice["message"]["content"]
            if not isinstance(raw, str):
                raise ValueError("Model returned no text content")
            record["response_text"] = raw
            record["output_chars"] = len(raw)
            if require_finished and record["finish_reason"] != "stop":
                raise ValueError("Incomplete compression response")
            parsed = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
            if not isinstance(parsed, dict):
                raise ValueError("Expected a JSON object")
            self.last_error = None
            record["parsed_json"] = parsed
            record["status"] = "ok"
            return parsed
        except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            self.stats["failures"] += 1
            self.last_error = type(exc).__name__
            record["error"] = type(exc).__name__
            record["error_detail"] = str(exc)[:500]
            log.warning("模型请求失败：%s", self.last_error)
            return None
        finally:
            self.stats["seconds"] += time.perf_counter() - started
            record["duration_seconds"] = round(time.perf_counter() - started, 3)
            record["parsed_json"] = parsed
            self.last_call_id = self.trace.record(record) if self.trace else None

    def _trace_outcome(self, items: list[dict[str, Any]], diagnostics: dict[str, Any] | None = None) -> None:
        self.last_outcome = items
        if not self.trace or self.last_call_id is None:
            return
        diagnostics = self.last_diagnostics if diagnostics is None else diagnostics
        answered = sum(1 for item in items if item.get("final") is not None or item.get("summary"))
        guard = sum(1 for item in items if item.get("proposed") != item.get("final") and item.get("final") is not None)
        self.trace.outcome(self.last_call_id, {"items": items, "diagnostics": diagnostics},
                           {"answered": answered, "guard": guard,
                            "rejected": sum(1 for item in items if item.get("rejected")),
                            "missing": len(diagnostics.get("missing_indexes", []))})

    def _validate(self, values: Any, tasks: list[dict[str, Any]], index: int | None = None,
                  decisions: dict[int, list[Instruction]] | None = None, session_name: str = "") -> list[Instruction] | None:
        if values == []:
            return [Instruction(action="IGNORE")]
        if not isinstance(values, list) or not values:
            return None
        instructions = []
        for value in values:
            try:
                instruction = Instruction.model_validate(clean_instruction(value, tasks, session_name))
                if instruction.related_task_ref:
                    previous, command = map(int, instruction.related_task_ref.split(":"))
                    existing = (decisions or {}).get(previous, [])
                    if index is None or previous >= index or command >= len(existing) or existing[command].action != "CREATE":
                        raise ValueError("Invalid batch task reference")
                instructions.append(instruction)
            except (ValueError, TypeError):
                # Keep an item's commands together: a bad command defers that
                # item, while valid siblings in the overall batch remain usable.
                self.last_error = "InstructionRejected"
                return None
        return instructions

    def _data(self, message, context, tasks, profile, session_name, relevance_text=None):
        history, history_size = [], 0
        rows = self.budgets["rows"]
        candidates = context[-rows:] if rows else []
        for row in reversed(candidates):
            brief = _brief(row)
            size = len(brief["content"])
            if history_size + size > self.budgets["history"]:
                continue
            history.append(brief)
            history_size += size
        pending, task_size = [], 0
        current = re.sub(r"所有人|同学|大家|各位|通知", "", relevance_text or message["content"])
        def relevance(task):
            title = re.sub(r"所有人|同学|大家|各位|通知", "", task["title"])
            terms = re.findall(r"[A-Za-z][A-Za-z0-9_+-]{2,}", title)
            for run in re.findall(r"[\u4e00-\u9fff]{4,}", title):
                terms.extend(run[index:index + 4] for index in range(len(run) - 3))
            return sum(term.lower() in current.lower() for term in terms), task["id"]
        for task in sorted(tasks, key=relevance, reverse=True):
            brief = {key: task.get(key) for key in ("id", "title", "priority", "due_at")}
            brief["title"] = clip_text(brief["title"] or "", 120)
            size = len(json.dumps(brief, ensure_ascii=False))
            if task_size + size > self.budgets["tasks"]:
                break
            pending.append(brief)
            task_size += size
        # 诊断用：模型实际收到多少历史/待办，剩余部分是预算放不下还是压根没有。
        self.last_payload_stats = {"session_name": session_name, "is_group": message["session_id"].endswith("@chatroom"),
                                   "history_available": len(context), "history_candidates": len(candidates),
                                   "history_sent": len(history), "history_chars": history_size,
                                   "history_budget": self.budgets["history"],
                                   "tasks_available": len(tasks), "tasks_sent": len(pending),
                                   "tasks_chars": task_size, "tasks_budget": self.budgets["tasks"],
                                   "profile_chars": len(profile or ""), "message_budget": self.budgets["new_message"]}
        return {"reference_time": message["created_at"], "timezone": "Asia/Shanghai",
                "session": {"name": session_name, "is_group": message["session_id"].endswith("@chatroom")},
                "user_profile": profile, "history": history[::-1],
                "pending_tasks": pending}

    def extract(self, message, context, tasks, profile="", session_name="") -> list[Instruction] | None:
        if not self.enabled:
            return None
        data = self._data(message, context, tasks, profile, session_name)
        result = self._complete({**data, "new_message": _brief(message)}, False, kind="single")
        if result is None:
            return None
        # Also accept the previous one-instruction contract for provider compatibility.
        values = result.get("instructions", [result] if "action" in result else None)
        validated = self._validate(values, data["pending_tasks"], session_name=session_name)
        self._trace_outcome([_item(0, message, result, validated, validated)])
        return validated

    def extract_batch(self, items, context, tasks, profile="", session_name="") -> dict[int, list[Instruction]]:
        if not self.enabled or not items:
            return {}
        data = self._data(items[-1], context, tasks, profile, session_name, "\n".join(item["content"] for item in items))
        result = self._complete({**data, "new_messages": [_brief(item) for item in items]}, True, kind="batch")
        if result is None:
            return {}
        entries = result.get("items", result.get("instructions", []))
        if not isinstance(entries, list):
            self.last_diagnostics = {"shape_rejected": True, "input_count": len(items)}
            self._trace_outcome([])
            return {}
        indexed = {}
        raw_entries: dict[int, Any] = {}
        duplicated = set()
        extra_indexes = []
        unindexed = 0
        for entry in entries:
            if not isinstance(entry, dict):
                unindexed += 1
                continue
            index = entry.get("index")
            if type(index) is not int:
                unindexed += 1
                continue
            # 模型偶尔会给没发出去的段落也编一个索引，还会把条数凑满；只丢它自己越界的那几条，
            # 整批真实回答不能因此作废，否则这些段落每一轮都会重新失败、永远留在队列里。
            if not 0 <= index < len(items):
                extra_indexes.append(index)
                continue
            if index in indexed:
                duplicated.add(index)
            indexed[index] = entry.get("instructions", [entry["instruction"]] if "instruction" in entry else None)
            raw_entries[index] = entry
        answers: dict[int, list[Instruction]] = {}
        outcome: list[dict[str, Any]] = []
        rejected = []
        for index, values in sorted(indexed.items()):
            if index in duplicated:
                continue
            instructions = self._validate(values, data["pending_tasks"], index, answers, session_name)
            if instructions is not None:
                answers[index] = instructions
                outcome.append(_item(index, items[index], raw_entries[index], instructions, instructions))
            else:
                rejected.append(index)
                outcome.append(_item(index, items[index], raw_entries[index], None, None))
        for index in sorted(duplicated):
            outcome.append(_item(index, items[index], raw_entries[index], None, None, note="同一索引重复返回"))
        self.last_diagnostics = {"input_count": len(items), "returned_indexes": sorted(indexed),
                                 "accepted_indexes": sorted(answers), "rejected_indexes": rejected,
                                 "duplicate_indexes": sorted(duplicated), "extra_indexes": sorted(extra_indexes),
                                 "unindexed_entries": unindexed,
                                 "missing_indexes": sorted(set(range(len(items))) - set(indexed))}
        self._trace_outcome(outcome)
        return answers

    def record_final_decisions(self, messages, answers) -> None:
        """更新同一次模型留档，最终业务校验只由引擎执行一次。"""
        finals = {tuple(message["message_ids"]): answers[index]
                  for index, message in enumerate(messages) if index in answers}
        for item in self.last_outcome:
            commands = finals.get(tuple(item["message_ids"]))
            if commands is not None:
                item["final"] = [command.model_dump() for command in commands]
        self._trace_outcome(self.last_outcome, self.last_diagnostics)

    def record_deferred(self, message, note="引用的同批创建未落地，本段保持未处理，下轮再请求") -> None:
        for item in self.last_outcome:
            if item["message_ids"] == message["message_ids"]:
                item["note"] = note
        self._trace_outcome(self.last_outcome, self.last_diagnostics)
