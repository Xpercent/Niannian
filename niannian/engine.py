"""Fair message processing, conservative local extraction and atomic task changes."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from .llm import (Instruction, LLMExtractor, HISTORY_CHARS, HISTORY_ROWS, MAX_TOKENS_BATCH,
                  MAX_TOKENS_SINGLE, NEW_MESSAGE_CHARS, TASK_CHARS, TEMPERATURE,
                  requires_review, guard_decisions, CONDITIONAL_PROHIBITION, ACTION_VERB, COMPRESSION_VERSION)
from .media import attachment_only
from .storage import canonical_time, utc_now

SHANGHAI = timezone(timedelta(hours=8))
BATCH_CHAR_BUDGET = 4200
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

    def _session_ready(self, session_id, account, force, values):
        session = self.db.one("SELECT monitored,account FROM sessions WHERE id=?", (session_id,))
        if not session or not session["monitored"] or session["account"] != account:
            return False
        if force:
            return True
        latest = self.db.one("SELECT MAX(created_at) AS latest FROM messages WHERE session_id=? AND account=?",
                             (session_id, account))
        return not latest["latest"] or (
            datetime.now(timezone.utc) - parse_time(latest["latest"])
        ).total_seconds() >= values.get("settle_window_seconds", 60)

    def pending_messages(self, force=False, session_id=None, values=None):
        """Freeze this round's backlog; new arrivals are considered next round."""
        values = self._settings() if values is None else values
        where = " AND s.id=?" if session_id else ""
        rows = self.db.rows(
            "SELECT m.* FROM messages m JOIN sessions s ON s.id=m.session_id "
            "WHERE s.monitored=1 AND m.processed=0 AND m.account=s.account" + where +
            " ORDER BY m.created_at,m.id", (session_id,) if session_id else ())
        scopes = {(row["session_id"], row["account"]) for row in rows}
        ready = {scope for scope in scopes if self._session_ready(*scope, force, values)}
        return [row for row in rows if (row["session_id"], row["account"]) in ready]

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
        generic = re.compile(r"@?所有人|全体(?:同学|成员|人员)?|同学们?|大家|各位|负责人|通知|注意事项|报名方式|申请条件|截止(?:日期|时间)")
        text = generic.sub("", re.sub(r"\s", "", content).lower())
        for task in pending:
            title = re.sub(r"^(?:请|帮我|麻烦|需要|确认|更新|补齐|完成|提交|整理|提供|发送|检查)+", "", task["title"])
            title = generic.sub("", re.sub(r"\s", "", title).lower()).strip("@，。:：、")
            tokens = set(re.findall(r"[a-z][a-z0-9_+-]{2,}", title))
            for run in re.findall(r"[\u4e00-\u9fff]{4,}", title):
                tokens.update(run[start:start + size] for size in range(4, min(len(run), 8) + 1)
                              for start in range(len(run) - size + 1))
            score = max([len(token) for token in tokens if token in text] + [0])
            if len(title) >= 4 and title in text:
                score = max(score, len(title))
            if score >= 3:
                ranked.append((score, task))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        if ranked and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
            return ranked[0][1]
        return None

    CANCEL = re.compile(r"(?:已)?取消|不用(?:处理|做|提交)|无需(?:处理|做|提交)|撤销")
    COMPLETE = re.compile(r"已完成|完成了|已发送|已发给|已经发|发给你了|处理好了|搞定了")
    CHANGE = re.compile(r"改成|改为|延期|推迟|提前|变更")
    REQUEST = re.compile(r"(?:请|帮我|麻烦|务必|记得|别忘|需要你|能否|能不能|可以).{0,25}(?:提交|发送|发给|填写|完成|确认|修改|更新|补齐|检查|整理|提供|反馈|转发|看一下|看下)")
    CONDITIONAL = re.compile(r"感兴趣|有意愿|有意向|自愿|符合.{0,12}条件|有需要的|有需求的|欢迎.{0,12}(?:报名|参加)|可自行|如需|如有|如果|若有|申请条件|报名条件")
    SECTION = re.compile(r"^(?:[一二三四五六七八九十\d]+[、.．)）]\s*)?(?:报名方式|报名条件|申请条件|截止(?:日期|时间)|活动(?:时间|地点|介绍)|联系方式|参赛对象|参与对象|目录|附件|注意事项)\s*[:：]?")
    ACKNOWLEDGEMENT = re.compile(r"(?:收到|收到啦|好的|好|嗯|明白|谢谢|了解)[！!。 .\s]*")
    ACTION_OBJECT = re.compile(r"(?:提交|发送|发给|填写|完成|确认|修改|更新|补齐|检查|整理|提供|反馈|转发|注册|接龙|签字|缴纳|上传|下载|领取|观看|核对).{2,}")

    def _gate(self, item, rules, profile=""):
        content = item["content"].strip()
        if not content or attachment_only(content):
            return [Instruction(action="IGNORE", context_summary="附件没有文字行动要求")]
        if requires_review(item, profile):
            return [Instruction(action="IGNORE", context_summary="需复核：群条件邀请，画像尚未确认本人角色与参与意愿")]
        for rule in rules:
            try:
                if rule["action"] == "ignore" and re.fullmatch(rule["pattern"], content, re.I):
                    return [Instruction(action="IGNORE", context_summary="命中完整忽略规则")]
            except re.error:
                continue
        if item["is_self"] and self.ACKNOWLEDGEMENT.fullmatch(content):
            return [Instruction(action="IGNORE", context_summary="本人承诺不是完成凭证")]
        return None

    def _fallback(self, item, pending, rules, profile=""):
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
        if self.CONDITIONAL.search(content) or requires_review(item, profile):
            return [Instruction(action="IGNORE", context_summary="需复核：条件邀请或群公告，尚未确认本人角色与参与意愿")]
        # Rules without a model may only create from an explicit imperative with
        # its object. Dates, application labels and isolated headings never count.
        clauses = re.split(r"[。；;！？]+", re.sub(r"\s*\n\s*", " ", content))
        instructions = []
        for clause in clauses:
            clause = clause.strip()
            if CONDITIONAL_PROHIBITION.search(clause) or re.search(r"(?:如|如果).{0,15}(?:填错|错误|重签|重填)", clause):
                continue
            request = self.REQUEST.search(clause)
            rule_match = False
            for rule in rules:
                try:
                    rule_match |= rule["action"] == "extract_task" and bool(re.search(rule["pattern"], clause, re.I))
                except re.error:
                    continue
            explicit_rule_action = rule_match and self.ACTION_OBJECT.search(clause)
            if self.SECTION.match(clause) or len(clause) > 220 or not (request or explicit_rule_action):
                continue
            action = clause[request.start():].strip() if request else clause
            if len(action) < 8:
                continue
            title = re.sub(r"^(?:请|帮我|麻烦|务必|记得|别忘|需要你|能否|能不能|可以)\s*", "", action)
            title = re.split(r"[，,（(]", title, maxsplit=1)[0].strip()
            if len(title) > 40 or not ACTION_VERB.search(title):
                continue
            if clause:
                instructions.append(Instruction(action="CREATE", title=title, description=action[:4000],
                                                owner="我", priority="high" if re.search(r"紧急|尽快|今天|下班前", clause) else "medium",
                                                due_at=self.due_at(clause, item["created_at"]), context_summary=clause[:4000]))
        return guard_decisions(item, instructions[:3], profile) if instructions else [
            Instruction(action="IGNORE", context_summary="需复核：模型不可用且本地未识别明确完整行动")]

    @staticmethod
    def _duplicate(title, candidates):
        """Merge only a unique distinctive object within the same action family."""
        families = (("register", r"注册"), ("rename", r"改(?:群)?备注|修改.*备注|备注.*修改"),
                    ("submit", r"提交|交付|上交"), ("fill", r"填写|填报"), ("sign", r"签字|签署"))
        def fingerprint(value):
            family = next((name for name, pattern in families if re.search(pattern, value)), None)
            text = re.sub(r"^(?:请|帮我|麻烦|务必|完成|尽快)+", "", value.lower())
            text = re.sub(r"注册|修改|改备注|群备注|提交|上交|交付|填写|填报|签字|签署|完成|流程|并|截图|登录|为|一下", "", text)
            text = re.sub(r"@?所有人|同学们?|大家|各位|负责人|通知", "", text)
            text = re.sub(r"[\s，。:：、@]", "", text)
            tokens = set()
            for run in re.findall(r"[a-z0-9\u4e00-\u9fff]+", text):
                if len(run) >= 3 and re.search(r"[a-z]", run) and re.search(r"[\u4e00-\u9fff]", run):
                    tokens.add(run)
                if len(run) >= 4:
                    tokens.update(run[start:start+4] for start in range(len(run)-3))
            return family, text, tokens
        family, object_text, tokens = fingerprint(title)
        if not family or not tokens:
            return None
        matches = []
        for candidate in candidates:
            other_family, other_object, other_tokens = fingerprint(candidate["title"])
            common = tokens & other_tokens
            if family == other_family and object_text == other_object and common:
                matches.append((candidate, {object_text}))
        return matches[0] if len(matches) == 1 else None

    def _history(self, item):
        return self.db.rows("SELECT * FROM messages WHERE session_id=? AND account=? AND id<? AND created_at<=? "
                            "ORDER BY created_at DESC,id DESC LIMIT 15",
                            (item["session_id"], item.get("account", ""), min(item["message_ids"]),
                             canonical_time(item.get("first_created_at", item["created_at"]))))[::-1]

    def _ignored(self, conn, item, source, reason, keep=IGNORED_KEEP_PER_SESSION):
        conn.execute("INSERT INTO ignored_messages(session_id,message_ids,source,reason,created_at) VALUES(?,?,?,?,?)",
                     (item["session_id"], json.dumps(item["message_ids"]), source, reason[:4000], utc_now()))
        conn.execute("DELETE FROM ignored_messages WHERE session_id=? AND id NOT IN "
                     "(SELECT id FROM ignored_messages WHERE session_id=? ORDER BY id DESC LIMIT ?)",
                     (item["session_id"], item["session_id"], keep))

    def _apply(self, conn, item, instruction, source, expected_version=None):
        action = instruction.action
        if action == "IGNORE":
            return None, None
        ids, now = item["message_ids"], utc_now()
        if action == "CREATE":
            title = instruction.title.strip()
            candidates = [dict(row) for row in conn.execute("SELECT * FROM tasks WHERE session_id=? AND status='pending'",
                                                           (item["session_id"],))]
            exact = next((row for row in candidates if row["title"] == title), None)
            duplicate = self._duplicate(title, candidates)
            if exact or duplicate:
                existing = exact or duplicate[0]
                anchors = duplicate[1] if duplicate else {re.sub(r"\s", "", title.lower())}
                current_content = re.sub(r"\s", "", item["content"].lower())
                if not any(anchor in current_content for anchor in anchors):
                    return None, None  # A history-only duplicate is not a new source.
                merged = sorted(set(json.loads(existing["source_message_ids"])) | set(ids))
                description = existing["description"]
                if instruction.description and instruction.description not in description:
                    description = (description + "\n补充要求：" + instruction.description)[-4000:]
                conn.execute("UPDATE tasks SET source_message_ids=?,description=?,updated_at=?,version=version+1 WHERE id=?",
                             (json.dumps(merged), description, now, existing["id"]))
                conn.execute("INSERT INTO task_events(task_id,action,source,message_ids,detail_json,created_at) VALUES(?,?,?,?,?,?)",
                             (existing["id"], "MERGE", source, json.dumps(ids),
                              json.dumps({"reason": "same_action_and_distinctive_object", "proposed": instruction.model_dump()}, ensure_ascii=False), now))
                return "merged", existing["id"]
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

    def _compressed_item(self, item, extractor, values):
        budget = values.get("new_message_chars", NEW_MESSAGE_CHARS)
        if len(item["content"]) <= budget:
            return item
        identity = {"content": item["content"], "ids": item["message_ids"],
                    "account": item.get("account", ""), "session": item["session_id"],
                    "budget": budget, "model": values.get("ai_model", ""),
                    "endpoint": values.get("ai_base_url", ""), "prompt_version": COMPRESSION_VERSION}
        fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        summary = self.db.compressed_text(item["message_ids"][-1], fingerprint)
        if summary is None:
            summary = extractor.compress(item)
            if summary is None or not self.db.save_compressed_text(item, fingerprint, summary):
                return None
        return {**item, "model_content": summary}

    def process(self, force=False, progress=None, should_stop=None, session_id=None):
        with self.lock:
            values = self._settings()
            batch_budget = values.get("batch_char_budget", BATCH_CHAR_BUDGET)
            new_chars = values.get("new_message_chars", NEW_MESSAGE_CHARS)
            ignored_keep = values.get("ignored_keep_per_session", IGNORED_KEEP_PER_SESSION)
            extractor = LLMExtractor(values.get("ai_base_url", ""), values.get("ai_api_key", ""),
                                     values.get("ai_model", ""), timeout=values.get("ai_timeout_seconds", 25),
                                     enabled=values.get("ai_enabled", False),
                                     temperature=values.get("ai_temperature", TEMPERATURE),
                                     max_tokens_single=values.get("ai_max_tokens_single", MAX_TOKENS_SINGLE),
                                     max_tokens_batch=values.get("ai_max_tokens_batch", MAX_TOKENS_BATCH),
                                     new_message_chars=values.get("new_message_chars", NEW_MESSAGE_CHARS),
                                     history_chars=values.get("history_chars", HISTORY_CHARS),
                                     history_rows=values.get("history_rows", HISTORY_ROWS),
                                     task_chars=values.get("task_chars", TASK_CHARS))
            self.llm = extractor
            profile = self.profile.read() if self.profile else ""
            rules = self.db.rows("SELECT * FROM rules WHERE enabled=1 ORDER BY id")
            items = self.aggregate(self.pending_messages(force, session_id, values), values.get("merge_window_seconds", 60))
            scopes = defaultdict(list)
            for item in items:
                scopes[(item.get("account", ""), item["session_id"])].append(item)
            counts = dict(processed=0, created=0, updated=0, resolved=0, ignored=0, skipped=0, cancelled=0,
                          merged=0, items=len(items), pre_filtered=0, llm_decided=0, rule_decided=0, batches=0, batched_items=0)
            done = 0
            if progress:
                progress(0, len(items), None)
            chunks = deque((scope, queue) for scope, group in scopes.items() for queue in [deque(group)])
            while chunks:
                if should_stop and should_stop():
                    counts["cancelled"] = len(items) - done
                    break
                scope, queue = chunks.popleft()
                if not self._session_ready(scope[1], scope[0], force, values):
                    counts["skipped"] += len(queue)
                    done += len(queue)
                    if progress:
                        progress(done, len(items), None)
                    continue
                chunk, characters = [], 0
                while queue:
                    size = min(len(queue[0]["content"]), new_chars)
                    if chunk and characters + size > batch_budget:
                        break
                    chunk.append(queue.popleft())
                    characters += size
                if queue:
                    chunks.appendleft((scope, queue))
                session = self.db.one("SELECT * FROM sessions WHERE id=?", (scope[1],))
                pending = self.db.rows("SELECT * FROM tasks WHERE session_id=? AND status='pending' ORDER BY id", (scope[1],))
                expected_versions = {task["id"]: task["version"] for task in pending}
                gates = {index: self._gate(item, rules, profile) for index, item in enumerate(chunk)}
                eligible = [index for index in range(len(chunk)) if gates[index] is None]
                counts["pre_filtered"] += len(chunk) - len(eligible)
                answers = {}
                if eligible and extractor.enabled:
                    prepared = []
                    for item in chunk:
                        if should_stop and should_stop():
                            break
                        compressed = self._compressed_item(item, extractor, values)
                        if compressed is None:
                            break
                        prepared.append(compressed)
                    if len(prepared) != len(chunk):
                        if should_stop and should_stop():
                            counts["cancelled"] = len(items) - done
                            break
                        # A failed summary must not silently become a clipped decision.
                        counts["skipped"] += len(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, len(items), session["name"] if session else scope[1])
                        continue
                    if not self._session_ready(scope[1], scope[0], force, values):
                        counts["skipped"] += len(chunk) + len(queue)
                        done += len(chunk) + len(queue)
                        if queue:
                            chunks.popleft()
                        if progress:
                            progress(done, len(items), session["name"] if session else scope[1])
                        continue
                    # Preserve original indexes for local CREATE references by sending
                    # the whole chunk; trivial decisions still apply locally below.
                    if len(eligible) == 1:
                        index = eligible[0]
                        answer = extractor.extract(prepared[index], self._history(chunk[index]), pending, profile,
                                                   session["name"] if session else "")
                        if answer is not None:
                            answers[index] = answer
                    else:
                        answers = extractor.extract_batch(prepared, self._history(chunk[0]), pending, profile,
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
                    # Failed requests and references to rejected creates remain pending.
                    invalid_ref = answer and any(command.related_task_ref and command.related_task_ref not in references
                                                 for command in answer)
                    if answer is None or invalid_ref:
                        if extractor.enabled:
                            counts["skipped"] += 1
                            done += 1
                            if progress:
                                progress(done, len(items), session["name"] if session else scope[1])
                            continue
                        pending_now = self.db.rows("SELECT * FROM tasks WHERE session_id=? AND status='pending' ORDER BY id", (scope[1],))
                        answer = self._fallback(item, pending_now, rules, profile)
                        source = "rule"
                    answer = guard_decisions(item, answer, profile)
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
                                    if outcome in {"created", "merged"}:
                                        references[f"{index}:{command_index}"] = task_id
                                else:
                                    item_counts["ignored"] += 1
                                    reason = instruction.context_summary if instruction.action == "IGNORE" else "指令未落地：重复待办、目标失效或版本已变化"
                                    self._ignored(conn, item, source, reason or "无需行动", ignored_keep)
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
