"""云端未启用时的本地降级提炼：只从显式命令句取候选，消息含义与适用范围交给模型判断。"""

from __future__ import annotations

import re
from datetime import timedelta

from .llm import CONDITIONAL_PROHIBITION, Instruction
from .storage import SHANGHAI, parse_time

CANCEL = re.compile(r"(?:已)?取消|不用(?:处理|做|提交)|无需(?:处理|做|提交)|撤销")
COMPLETE = re.compile(r"已完成|完成了|已发送|已发给|已经发|发给你了|处理好了|搞定了")
CHANGE = re.compile(r"改成|改为|延期|推迟|提前|变更")
REQUEST = re.compile(r"(?:请|帮我|麻烦|务必|记得|别忘|需要你|能否|能不能|可以).{0,25}(?:提交|发送|发给|填写|完成|确认|修改|更新|补齐|检查|整理|提供|反馈|转发|看一下|看下)")
CONDITIONAL = re.compile(r"感兴趣|有意愿|有意向|自愿|符合.{0,12}条件|有需要的|有需求的|欢迎.{0,12}(?:报名|参加)|可自行|如需|如有|如果|若有|申请条件|报名条件")
SECTION = re.compile(r"^(?:[一二三四五六七八九十\d]+[、.．)）]\s*)?(?:报名方式|报名条件|申请条件|截止(?:日期|时间)|活动(?:时间|地点|介绍)|联系方式|参赛对象|参与对象|目录|附件|注意事项)\s*[:：]?")
ACTION_OBJECT = re.compile(r"(?:提交|发送|发给|填写|完成|确认|修改|更新|补齐|检查|整理|提供|反馈|转发|注册|接龙|签字|缴纳|上传|下载|领取|观看|核对).{2,}")


def due_at(content: str, timestamp: str) -> str | None:
    """只从字面相对日期与钟点推断截止；推不出来就留空，不猜日期。"""
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


def related(content: str, pending: list):
    """按独特对象匹配现有待办；多个候选分数并列时不猜，返回 None。"""
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


def decisions(item, pending, rules, settings=None) -> list[Instruction]:
    settings = settings or {}
    content = item["content"]
    target = related(content, pending)
    if CANCEL.search(content):
        if target:
            return [Instruction(action="RESOLVE", related_task_id=target["id"], resolution="cancelled", context_summary=content)]
        return [Instruction(action="IGNORE", context_summary="取消缺少唯一可确认的当前待办")]
    if COMPLETE.search(content):
        if item["is_self"] and target:
            return [Instruction(action="RESOLVE", related_task_id=target["id"], context_summary=content)]
        return [Instruction(action="IGNORE", context_summary="完成只认本人且须关联现有待办")]
    if item["is_self"]:
        return [Instruction(action="IGNORE", context_summary="本人消息不创建或修改待办")]
    if CHANGE.search(content) and target:
        fields = {"due_at": due_at(content, item["created_at"])}
        if not fields["due_at"]:
            fields = {}
        return [Instruction(action="UPDATE", related_task_id=target["id"], description=content,
                            context_summary=content, **fields)]
    if CONDITIONAL.search(content):
        return [Instruction(action="IGNORE", context_summary="本地规则无法确认条件邀请适用于本人")]
    # Rules without a model may only create from an explicit imperative with
    # its object. Dates, application labels and isolated headings never count.
    clauses = re.split(r"[。；;！？]+", re.sub(r"\s*\n\s*", " ", content))
    instructions = []
    for clause in clauses:
        clause = clause.strip()
        if CONDITIONAL_PROHIBITION.search(clause) or re.search(r"(?:如|如果).{0,15}(?:填错|错误|重签|重填)", clause):
            continue
        request = REQUEST.search(clause)
        rule_match = False
        for rule in rules:
            try:
                rule_match |= rule["action"] == "extract_task" and bool(re.search(rule["pattern"], clause, re.I))
            except re.error:
                continue
        explicit_rule_action = rule_match and ACTION_OBJECT.search(clause)
        if SECTION.match(clause) or len(clause) > 220 or not (request or explicit_rule_action):
            continue
        action = clause[request.start():].strip() if request else clause
        if len(action) < 8:
            continue
        title = re.sub(r"^(?:请|帮我|麻烦|务必|记得|别忘|需要你|能否|能不能|可以)\s*", "", action)
        title = re.split(r"[，,（(]", title, maxsplit=1)[0].strip()
        if not title or len(title) > settings.get("task_title_max_chars", 120):
            continue
        if clause:
            instructions.append(Instruction(action="CREATE", title=title, description=action[:4000],
                                            owner="我", priority="high" if re.search(r"紧急|尽快|今天|下班前", clause) else "medium",
                                            due_at=due_at(clause, item["created_at"]), context_summary=clause[:4000]))
    return instructions if instructions else [
        Instruction(action="IGNORE", context_summary="需复核：模型不可用且本地未识别明确完整行动")]
