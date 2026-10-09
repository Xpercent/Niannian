"""Fair message processing, conservative local extraction and atomic task changes."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from .llm import Instruction, LLMExtractor
from .media import attachment_only
from .storage import canonical_time, utc_now

SHANGHAI = timezone(timedelta(hours=8))
BATCH_LIMIT = 8
ROUND_LIMIT = 240
PER_SESSION_LIMIT = 80
IGNORED_KEEP_PER_SESSION = 100


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=SHANGHAI)


def identity_confident(message: dict[str, Any]) -> bool:
    if "identity_confident" in message:
        return message["identity_confident"] is True
    metadata = message.get("metadata")
    if metadata is None:
        try:
            metadata = json.loads(message.get("metadata_json") or "{}")
        except (TypeError, ValueError):
            metadata = {}
    return isinstance(metadata, dict) and metadata.get("identity_confident") is True


class Engine:
    def __init__(self, db, config, profile=None):
        self.db, self.config, self.profile = db, config, profile
        self.lock = Lock()
        self.last_result: dict[str, Any] | None = None
        self.llm: LLMExtractor | None = None

    def _settings(self):
        return dict(self.config.read() if hasattr(self.config, "read") else self.config)

    def pending_messages(self, force=False, session_id=None, values=None):
        """A bounded round gives each active session a turn before consuming a backlog."""
        values = values or self._settings()
        where = " AND s.id=?" if session_id else ""
        params = (session_id,) if session_id else ()
        sessions = self.db.rows(
            "SELECT s.id, MIN(m.created_at) AS oldest FROM sessions s "
            "JOIN messages m ON m.session_id=s.id WHERE s.monitored=1 AND m.processed=0" + where +
            " GROUP BY s.id ORDER BY oldest,s.id", params)
        queues = []
        now = datetime.now(timezone.utc)
        window = values.get("settle_window_seconds", 45)
        for session in sessions:
            rows = self.db.rows("SELECT * FROM messages WHERE session_id=? AND processed=0 "
                                "ORDER BY created_at,id LIMIT ?", (session["id"], PER_SESSION_LIMIT + 1))
            released = []
            if force:
                released = rows[:PER_SESSION_LIMIT]
            else:
                cutoff = 0
                for index, row in enumerate(rows[:PER_SESSION_LIMIT]):
                    created = parse_time(row["created_at"])
                    if (now - created).total_seconds() < window:
                        break
                    # Only cut before a recent tail if there was a real quiet gap.
                    # Old oversized bursts are bounded at PER_SESSION_LIMIT so they
                    # cannot permanently block their own session.
                    following = rows[index + 1] if index + 1 < len(rows) else None
                    if following is None or (parse_time(following["created_at"]) - created).total_seconds() >= window:
                        cutoff = index + 1
                        break
                    if index + 1 == PER_SESSION_LIMIT and (now - parse_time(following["created_at"])).total_seconds() >= window:
                        cutoff = PER_SESSION_LIMIT
                released = rows[:cutoff]
            if released:
                queues.append(deque(released))
        selected = []
        while queues and len(selected) < ROUND_LIMIT:
            remaining = []
            for queue in queues:
                if len(selected) >= ROUND_LIMIT:
                    break
                selected.append(queue.popleft())
                if queue:
                    remaining.append(queue)
            queues = remaining
        return sorted(selected, key=lambda row: (parse_time(row["created_at"]), row["id"]))

    def aggregate(self, messages, merge_window_seconds=None):
        window = (self._settings().get("merge_window_seconds", 60) if merge_window_seconds is None
                  else merge_window_seconds)
        scopes: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in messages:
            key = (row.get("account", ""), row["session_id"])
            groups = scopes[key]
            sender = row.get("sender_key") or ("self" if row["is_self"] else row["sender_name"])
            if groups:
                previous = groups[-1]
                gap = (parse_time(row["created_at"]) - parse_time(previous["created_at"])).total_seconds()
                if sender == previous["sender_key"] and bool(row["is_self"]) == bool(previous["is_self"]) and 0 <= gap <= window:
                    previous["content"] += "\n" + row["content"]
                    previous["message_ids"].append(row["id"])
                    previous["created_at"] = row["created_at"]
                    previous["identity_confident"] &= identity_confident(row)
                    continue
            groups.append({**row, "sender_key": sender, "message_ids": [row["id"]],
                           "first_created_at": row["created_at"], "identity_confident": identity_confident(row)})
        return sorted((group for groups in scopes.values() for group in groups),
                      key=lambda item: (parse_time(item["created_at"]), item["id"]))

    @staticmethod
    def due_at(content, timestamp):
        now = parse_time(timestamp).astimezone(SHANGHAI)
        if "明天" in content:
            date = now + timedelta(days=1)
        elif "今天" in content or "下班前" in content:
            date = now
        else:
            match = re.search(r"(?:周|星期)([一二三四五六日天])", content)
            if not match:
                return None
            weekday = "一二三四五六日".find("日" if match[1] == "天" else match[1])
            date = now + timedelta(days=(weekday - now.weekday()) % 7)
        clock = re.search(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)", content)
        hour, minute = (int(clock[1]), int(clock[2])) if clock else (18, 0)
        return date.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()

    @staticmethod
    def related(content, pending):
        ranked = []
        text = re.sub(r"\s", "", content).lower()
        for task in pending:
            title = re.sub(r"^(?:请|帮我|麻烦|需要|确认|更新|补齐|完成|提交|整理|提供|发送|检查)+", "", task["title"])
            title = re.sub(r"\s", "", title).lower()
            tokens = re.findall(r"[a-z]{3,}|[\u4e00-\u9fff]{2,}", title)
            score = max([len(token) for token in tokens if token in text] + [0])
            if title and title in text:
                score = max(score, len(title))
            if score >= 2:
                ranked.append((score, task))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        if ranked and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
            return ranked[0][1]
        return None

    CANCEL = re.compile(r"(?:已)?取消|不用(?:处理|做|提交)|无需(?:处理|做|提交)|撤销")
    COMPLETE = re.compile(r"已完成|完成了|已发送|已发给|已经发|发给你了|处理好了|搞定了")
    CHANGE = re.compile(r"改成|改为|延期|推迟|提前|变更")
    REQUEST = re.compile(r"请|帮我|麻烦|需要|给我|补齐|确认|提交|整理|记得|务必|别忘|看一下|看下|发一下|发下|能否|能不能|可以.{0,20}[吗么]|截止|之前|作业|报名|填报")
    ACKNOWLEDGEMENT = re.compile(r"(?:收到|收到啦|好的|好|嗯|明白|谢谢|了解)[！!。 .\s]*")

    def _gate(self, item, rules):
        content = item["content"].strip()
        if not content or attachment_only(content):
            return [Instruction(action="IGNORE", context_summary="附件没有文字行动要求")]
        for rule in rules:
            try:
                if rule["action"] == "ignore" and re.fullmatch(rule["pattern"], content, re.I):
                    return [Instruction(action="IGNORE", context_summary="命中完整忽略规则")]
            except re.error:
                continue
        if item["is_self"] and self.ACKNOWLEDGEMENT.fullmatch(content):
            return [Instruction(action="IGNORE", context_summary="本人承诺不是完成凭证")]
        return None

    def _fallback(self, item, pending, rules):
        content = item["content"]
        related = self.related(content, pending)
        if self.CANCEL.search(content):
            if related:
                return [Instruction(action="RESOLVE", related_task_id=related["id"], resolution="cancelled", context_summary=content)]
            return [Instruction(action="IGNORE", context_summary="取消缺少唯一可确认的当前待办")]
        if self.COMPLETE.search(content):
            if item["is_self"] and identity_confident(item) and related:
                return [Instruction(action="RESOLVE", related_task_id=related["id"], context_summary=content)]
            return [Instruction(action="IGNORE", context_summary="完成只认本人且须关联现有待办")]
        if item["is_self"]:
            return [Instruction(action="IGNORE", context_summary="本人消息不创建或修改待办")]
        if self.CHANGE.search(content) and related:
            fields = {"due_at": self.due_at(content, item["created_at"])}
            if not fields["due_at"]:
                fields = {}
            return [Instruction(action="UPDATE", related_task_id=related["id"], description=content,
                                context_summary=content, **fields)]
        clauses = re.split(r"[。；;\n]+", content)
        instructions = []
        for clause in clauses:
            clause = clause.strip()
            matched = bool(self.REQUEST.search(clause))
            for rule in rules:
                try:
                    matched |= rule["action"] == "extract_task" and bool(re.search(rule["pattern"], clause, re.I))
                except re.error:
                    continue
            if matched and clause:
                instructions.append(Instruction(action="CREATE", title=clause[:120], description=clause[:4000],
                                                owner="我", priority="high" if re.search(r"紧急|尽快|今天|下班前", clause) else "medium",
                                                due_at=self.due_at(clause, item["created_at"]), context_summary=clause[:4000]))
        return instructions[:12] or [Instruction(action="IGNORE", context_summary="本地回退未识别明确行动，可在来源中复核")]

    def _history(self, item):
        return self.db.rows("SELECT * FROM messages WHERE session_id=? AND account=? AND id<? AND created_at<=? "
                            "ORDER BY created_at DESC,id DESC LIMIT 15",
                            (item["session_id"], item.get("account", ""), min(item["message_ids"]),
                             canonical_time(item.get("first_created_at", item["created_at"]))))[::-1]

    def _ignored(self, conn, item, source, reason):
        conn.execute("INSERT INTO ignored_messages(session_id,message_ids,source,reason,created_at) VALUES(?,?,?,?,?)",
                     (item["session_id"], json.dumps(item["message_ids"]), source, reason[:4000], utc_now()))
        conn.execute("DELETE FROM ignored_messages WHERE session_id=? AND id NOT IN "
                     "(SELECT id FROM ignored_messages WHERE session_id=? ORDER BY id DESC LIMIT ?)",
                     (item["session_id"], item["session_id"], IGNORED_KEEP_PER_SESSION))

    def _apply(self, conn, item, instruction, source, expected_version=None):
        action = instruction.action
        if action == "IGNORE":
            return None, None
        ids, now = item["message_ids"], utc_now()
        if action == "CREATE":
            title = instruction.title.strip()
            existing = conn.execute("SELECT id FROM tasks WHERE session_id=? AND status='pending' AND title=?",
                                    (item["session_id"], title)).fetchone()
            if existing:
                return None, None
            cur = conn.execute("INSERT INTO tasks(session_id,title,description,owner,priority,due_at,status,"
                               "source_message_ids,context_summary,created_at,updated_at) VALUES(?,?,?,?,?,?,'pending',?,?,?,?)",
                               (item["session_id"], title, instruction.description or title, instruction.owner or "我",
                                instruction.priority or "medium", instruction.due_at, json.dumps(ids),
                                instruction.context_summary, now, now))
            task_id, outcome = cur.lastrowid, "created"
        else:
            existing = conn.execute("SELECT * FROM tasks WHERE id=? AND session_id=? AND status='pending'",
                                    (instruction.related_task_id, item["session_id"])).fetchone()
            if not existing or (expected_version is not None and existing["version"] != expected_version):
                return None, None
            task_id = existing["id"]
            ids = sorted(set(json.loads(existing["source_message_ids"])) | set(ids))
            if action == "RESOLVE":
                conn.execute("UPDATE tasks SET status=?,completed_at=?,updated_at=?,source_message_ids=?,"
                             "context_summary=?,version=version+1 WHERE id=?",
                             (instruction.resolution, now if instruction.resolution == "completed" else None,
                              now, json.dumps(ids), instruction.context_summary or existing["context_summary"], task_id))
                outcome = "resolved"
            else:
                fields = {key: value for key, value in instruction.model_dump(exclude_unset=True).items()
                          if key in {"title", "description", "owner", "priority", "due_at"} and
                          (value is not None or key in {"due_at", "owner"})}
                fields.update(updated_at=now, source_message_ids=json.dumps(ids),
                              context_summary=instruction.context_summary or existing["context_summary"])
                conn.execute("UPDATE tasks SET " + ",".join(f"{key}=?" for key in fields) + ",version=version+1 WHERE id=?",
                             (*fields.values(), task_id))
                outcome = "updated"
        conn.execute("INSERT INTO task_events(task_id,action,source,message_ids,detail_json,created_at) VALUES(?,?,?,?,?,?)",
                     (task_id, action, source, json.dumps(ids), instruction.model_dump_json(), now))
        return outcome, task_id

    def process(self, force=False, progress=None, should_stop=None, session_id=None):
        with self.lock:
            values = self._settings()
            extractor = LLMExtractor(values.get("ai_base_url", ""), values.get("ai_api_key", ""),
                                     values.get("ai_model", ""), timeout=values.get("ai_timeout_seconds", 25),
                                     enabled=values.get("ai_enabled", False))
            self.llm = extractor
            profile = self.profile.read() if self.profile else ""
            rules = self.db.rows("SELECT * FROM rules WHERE enabled=1 ORDER BY id")
            items = self.aggregate(self.pending_messages(force, session_id, values), values.get("merge_window_seconds", 60))
            scopes = defaultdict(list)
            for item in items:
                scopes[(item.get("account", ""), item["session_id"])].append(item)
            counts = dict(processed=0, created=0, updated=0, resolved=0, ignored=0, skipped=0, cancelled=0,
                          items=len(items), pre_filtered=0, llm_decided=0, rule_decided=0, batches=0, batched_items=0)
            done = 0
            if progress:
                progress(0, len(items), None)
            chunks = deque((scope, queue) for scope, group in scopes.items() for queue in [deque(group)])
            while chunks:
                if should_stop and should_stop():
                    counts["cancelled"] = len(items) - done
                    break
                scope, queue = chunks.popleft()
                chunk = [queue.popleft() for _ in range(min(BATCH_LIMIT, len(queue)))]
                if queue:
                    chunks.append((scope, queue))
                session = self.db.one("SELECT * FROM sessions WHERE id=?", (scope[1],))
                pending = self.db.rows("SELECT * FROM tasks WHERE session_id=? AND status='pending' ORDER BY id", (scope[1],))
                expected_versions = {task["id"]: task["version"] for task in pending}
                gates = {index: self._gate(item, rules) for index, item in enumerate(chunk)}
                eligible = [index for index in range(len(chunk)) if gates[index] is None]
                counts["pre_filtered"] += len(chunk) - len(eligible)
                answers = {}
                if eligible and extractor.enabled:
                    # Preserve original indexes for local CREATE references by sending
                    # the whole chunk; trivial decisions still apply locally below.
                    if len(eligible) == 1:
                        index = eligible[0]
                        answer = extractor.extract(chunk[index], self._history(chunk[index]), pending, profile,
                                                   session["name"] if session else "")
                        if answer is not None:
                            answers[index] = answer
                    else:
                        answers = extractor.extract_batch(chunk, self._history(chunk[0]), pending, profile,
                                                          session["name"] if session else "")
                        counts["batches"] += 1
                        counts["batched_items"] += len(chunk)
                references: dict[str, int] = {}
                for index, item in enumerate(chunk):
                    if should_stop and should_stop():
                        counts["cancelled"] = len(items) - done
                        break
                    answer = gates[index] if gates[index] is not None else answers.get(index)
                    source = "llm" if gates[index] is None and answer is not None else "rule"
                    # A preceding create may have been rejected or changed while the
                    # model was running. Re-extract locally against committed state.
                    invalid_ref = answer and any(command.related_task_ref and command.related_task_ref not in references
                                                 for command in answer)
                    if answer is None or invalid_ref:
                        pending_now = self.db.rows("SELECT * FROM tasks WHERE session_id=? AND status='pending' ORDER BY id", (scope[1],))
                        answer = self._fallback(item, pending_now, rules)
                        source = "rule"
                        expected_versions.update({task["id"]: task["version"] for task in pending_now})
                    item_counts = defaultdict(int)
                    with self.db.connect() as conn:
                        conn.execute("BEGIN IMMEDIATE")
                        active = conn.execute("SELECT monitored,account FROM sessions WHERE id=?", (scope[1],)).fetchone()
                        ids = item["message_ids"]
                        marks = ",".join("?" for _ in ids)
                        unprocessed = conn.execute(f"SELECT COUNT(*) FROM messages WHERE id IN ({marks}) AND processed=0", ids).fetchone()[0]
                        if not active or not active["monitored"] or active["account"] != scope[0] or unprocessed != len(ids):
                            item_counts["skipped"] += 1
                        else:
                            for command_index, instruction in enumerate(answer):
                                if instruction.related_task_ref:
                                    instruction = instruction.model_copy(update={"related_task_id": references[instruction.related_task_ref],
                                                                                 "related_task_ref": None})
                                blocked = ((item["is_self"] and instruction.action in {"CREATE", "UPDATE"}) or
                                           (instruction.action == "RESOLVE" and instruction.resolution == "completed" and
                                            not (item["is_self"] and identity_confident(item))))
                                if blocked:
                                    instruction = Instruction(action="IGNORE", context_summary="本人只核销；完成须有已确认的本人身份")
                                outcome, task_id = self._apply(conn, item, instruction, source,
                                                              expected_versions.get(instruction.related_task_id) if source == "llm" else None)
                                if outcome:
                                    item_counts[outcome] += 1
                                    version = conn.execute("SELECT version FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
                                    expected_versions[task_id] = version
                                    if outcome == "created":
                                        references[f"{index}:{command_index}"] = task_id
                                else:
                                    item_counts["ignored"] += 1
                                    reason = instruction.context_summary if instruction.action == "IGNORE" else "指令未落地：重复待办、目标失效或版本已变化"
                                    self._ignored(conn, item, source, reason or "无需行动")
                            conn.execute(f"UPDATE messages SET processed=1 WHERE id IN ({marks})", ids)
                            item_counts["processed"] += len(ids)
                            item_counts[f"{source}_decided"] += 1
                    for key, number in item_counts.items():
                        counts[key] += number
                    done += 1
                    if progress:
                        progress(done, len(items), session["name"] if session else scope[1])
                if counts["cancelled"]:
                    break
            counts.update(at=utc_now(), model_requests=extractor.stats["requests"], model_failures=extractor.stats["failures"],
                          model_seconds=round(extractor.stats["seconds"], 2), last_model_error=extractor.last_error,
                          queued=self.db.queued_count())
            self.last_result = counts
            return dict(counts)
