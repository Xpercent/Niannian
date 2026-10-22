"""提炼轮次：手动触发、一次只跑一轮的后台线程。

这一层只管"批次怎么切、请求怎么发、结果怎么落"，输入契约与判定规则在 :mod:`extract`。
它只读自己的库、只打外部 HTTPS，不碰微信解密缓存也不碰图片钥匙，所以不需要走采集线程
的命令队列，也不会把谁的线路占住（"请求线程一律纯读"那条针对的是微信那份缓存）。
每一次请求发出去的正文与模型原样回复都逐字记进 `model_calls`，供提炼诊断页回查。
"""

from __future__ import annotations

import json
import threading
import time

from . import extract
from .llm import chat, error_text, raw_reply, request_body, split_config
from .storage import now_utc

# 一批消息要判一整屏，等待比"测试连接"长得多；只有一档 180 秒（真机实测单批开思考
# 能跑到 30 秒以上，而 2026-10-21 起请求会不会思考由用户那一框决定，这一层读不出来）。
# 于是一轮的最坏耗时就是「批数 × 180 秒」——回复长度不设上限（用户 2026-10-20 定：
# ``max_tokens`` 一个都不发），超时是仅剩的护栏。
TIMEOUT_SECONDS = 180.0


def batches(rows: list[dict], size: int) -> list[list[dict]]:
    """按 ``size`` 切批，保持从旧到新；编号就是批内下标，切错一条就对不上。"""
    size = max(1, int(size))
    return [rows[start:start + size] for start in range(0, len(rows), size)]


def group_by_session(rows: list[dict]) -> list[tuple[str, str, list[dict]]]:
    """同一个会话的消息挨在一起（查库时已按会话排序），顺带把会话名拿出来。"""
    out: list[tuple[str, str, list[dict]]] = []
    for row in rows:
        sid = str(row["session_id"])
        if not out or out[-1][0] != sid:
            out.append((sid, str(row.get("session_name") or ""), [row]))
        else:
            out[-1][2].append(row)
    return out


def _started_state(mode: str) -> dict:
    """起一轮/起一次合并时的初始状态。

    两处共用一份：字段少一个，``/api/extract/status`` 就会在"这一轮"和"下一次"之间
    时有时无，前端读出来的形状跟着抖。``mode`` 让界面能说清这次在干什么
    （``extract`` 提炼消息 / ``merge`` 只合并已有待办）。
    """
    return {"running": True, "started_at": now_utc(), "finished_at": None,
            "total": 0, "done": 0, "batches": 0, "created": 0, "merged": 0,
            "failed_batches": 0, "session": None, "error": None, "run_id": None,
            "mode": mode}


class Extractor:
    """跑一轮提炼：取未处理的消息 → 逐批发给模型 → 任务与标记同事务落库。

    同时只允许一轮在跑（模型那边是按会话顺序一条条问，并行只会互相拖慢与重复计费）。
    """

    def __init__(self, db, settings: dict):
        self.db = db
        # 与 api 里那份 settings 是同一个对象：保存设置后下一轮自然读到新值，
        # 不需要 Collector 那种 update_settings 通道。
        self.settings = settings
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self.state: dict = {"running": False, "started_at": None, "finished_at": None,
                            "total": 0, "done": 0, "batches": 0, "created": 0,
                            "merged": 0, "failed_batches": 0, "session": None,
                            "error": None, "run_id": None, "mode": "extract"}

    def status(self) -> dict:
        with self._lock:
            return dict(self.state)

    def start(self, session_id: str = "") -> dict:
        """起一轮；已经在跑就原样回当前状态，不排队第二轮。

        配置没填齐在**出网之前**就抛中文错，别把"我没配好"拿去问服务商。
        """
        split_config(self.settings)
        # 起轮时取一次策略快照，整轮用它：一轮要跑好几分钟，中途在设置页改批次或
        # 提示词不该让后半批换个问法（那样失败重跑时也说不清是哪条规则写坏的 JSON）。
        plan = extract.policy(self.settings)
        # 先读一次积压再回话（纯读自己的库，毫秒级）：不然接口立刻回的那份状态里
        # total 是 0，界面看着像"这轮什么都没找到"，等线程填上要好几个来回。
        backlog = len(self.db.pending_extraction(session_id, plan.round_limit))
        with self._lock:
            if self.state["running"]:
                # 已经在跑：不排队第二轮，把当前状态原样回过去，accepted=False 说明
                # "你要的这轮没起，因为上一轮还没结束"
                return {**self.state, "accepted": False}
            if not backlog:
                # 没有待提炼的消息就**不起线程、也不建轮次**：与 ``start_merge`` 同一个
                # 理由——一条 ``total=0`` 的空轮次只会挤占诊断页那 30 轮额度，还让人以为
                # 它跑过什么（真机 run 1 就是这么来的）。要合并重复有单独那颗按钮。
                return {**self.state, "accepted": False,
                        "reason": "没有待提炼的消息（都已提炼过；要合并重复请点「合并重复」）"}
            self._stop.clear()
            self.state = {**_started_state("extract"), "total": backlog}
            snapshot = {**self.state, "accepted": True}
        self._worker = threading.Thread(target=self._run, args=(session_id, plan),
                                        name="extractor", daemon=True)
        self._worker.start()
        return snapshot

    def start_merge(self) -> dict:
        """**只合并、不提炼**：在已有待办上试合并用（待办页那颗「合并重复」）。

        合并本来就在每轮末尾自动跑，这个入口只是省掉"再造一批消息"的麻烦——比如刚改完
        合并提示词，想看看它在现有 40 条待办上会怎么判。它记成**新的一轮**
        （0 条消息、只有 `stage='merge'` 的调用），诊断页照常能回看发了什么、回了什么。
        """
        split_config(self.settings)
        plan = extract.policy(self.settings)
        targets = self.db.mergeable_sessions()
        with self._lock:
            if self.state["running"]:
                return {**self.state, "accepted": False}
            if not targets:
                # 没有可合并的会话就**不起线程、也不建轮次**：建一条"0 条消息 0 次请求"
                # 的空轮次只会挤占诊断页那 30 轮额度，还让人以为它跑过什么。
                return {**self.state, "accepted": False,
                        "reason": "没有可合并的会话（每个会话都只有一条待办）"}
            self._stop.clear()
            self.state = _started_state("merge")
            snapshot = {**self.state, "accepted": True, "targets": len(targets)}
        self._worker = threading.Thread(target=self._run_merge, args=(plan, targets),
                                        name="merger", daemon=True)
        self._worker.start()
        return snapshot

    def stop(self) -> None:
        """取消当前轮次：批与批之间停，已经回来的那个批次照常落库。"""
        self._stop.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=1)

    def _run(self, session_id: str, plan: extract.Policy) -> None:
        error: str | None = None
        run_id = 0
        try:
            # 先落一行轮次，请求才有地方挂；id 也要放进状态，诊断页才知道哪一轮是当下这轮
            run_id = self.db.start_extract_run(session_id)
            with self._lock:
                self.state["run_id"] = run_id
            rows = self.db.pending_extraction(session_id, plan.round_limit)
            with self._lock:
                self.state["total"] = len(rows)
            self._sync_run(run_id)
            for sid, name, group in group_by_session(rows):
                for chunk in batches(group, plan.batch_limit):
                    if self._stop.is_set():
                        error = "已取消，未发出的批次一条都没标记，下次照旧重试"
                        break
                    self._one_batch(sid, name, chunk, plan, run_id)
                # 这个会话的批次都落库了，接着把重复的待办合掉。**同一个 run_id**：
                # 合并是第一轮的附加，诊断页要看成同一轮（用户 2026-10-22 定）。
                # 取消时不跑——半轮的消息还没提炼完，这时候合出来的清单是半截的。
                if not self._stop.is_set():
                    self._merge_session(sid, name, plan, run_id)
                if self._stop.is_set():
                    break
        except Exception as exc:                      # 查库崩了这类轮次级意外
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
        with self._lock:
            self.state["running"] = False
            self.state["finished_at"] = now_utc()
            if error:
                self.state["error"] = error
            # 批级失败（含合并失败）记在 state 里，收尾时一并写进轮次行：不然界面上
            # 只有内存状态那一句，翻诊断页看不见这一轮为什么是坏的。
            final_error = self.state["error"]
        if run_id:
            self.db.finish_extract_run(run_id, self._progress() | {"error": final_error})

    def _run_merge(self, plan: extract.Policy, targets: list[dict]) -> None:
        """手动合并那一轮：逐个会话跑一次合并，不碰消息、不标记任何东西。

        ``total`` 留 0：这一轮没有"要提炼的消息"，界面按 ``mode`` 换说法，不去凑一个
        假的分母。取消停在会话之间——已经合完的组照常留着，没轮到的一个都没动。
        """
        error: str | None = None
        run_id = 0
        try:
            run_id = self.db.start_extract_run("")
            with self._lock:
                self.state["run_id"] = run_id
            self._sync_run(run_id)
            for item in targets:
                if self._stop.is_set():
                    error = "已取消，没合完的那些任务原样留着"
                    break
                sid = str(item["id"])
                self._merge_session(sid, str(item.get("name") or sid), plan, run_id)
        except Exception as exc:                      # 查库崩了这类轮次级意外
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
        with self._lock:
            self.state["running"] = False
            self.state["finished_at"] = now_utc()
            if error:
                self.state["error"] = error
            final_error = self.state["error"]
        if run_id:
            self.db.finish_extract_run(run_id, self._progress() | {"error": final_error})

    def _progress(self) -> dict:
        with self._lock:
            return {key: self.state[key] for key in ("total", "done", "created",
                                                     "failed_batches", "merged")}

    def _sync_run(self, run_id: int) -> None:
        """把进度写回轮次那一行：一轮跑到一半时诊断页也能看到已经发出的那几批。"""
        self.db.update_extract_run(run_id, self._progress())

    def _one_batch(self, session_id: str, session_name: str, chunk: list[dict],
                   plan: extract.Policy, run_id: int) -> None:
        """一批一次请求。失败**不标记**这批：下一轮还会取到它们，不会静默丢消息。"""
        first = chunk[0]
        history = self.db.extraction_history(session_id, first["sort_seq"], first["id"],
                                             plan.history_limit)
        messages = [{"role": "system", "content": plan.prompt},
                    {"role": "user",
                     "content": extract.render(session_name, history, chunk, plan.profile)}]
        with self._lock:
            self.state["session"] = session_name or session_id
        started_at = now_utc()          # 记的是**发出去那一刻**，不是这一行写库那一刻
        started = time.perf_counter()
        reply = {"text": "", "reasoning": "", "model": ""}
        error = ""
        secret = str(self.settings.get("llm_api_key") or "")
        # 留档留的是**整份请求体**（模型名 + 正文 + 那一框里的字段），不是只有 messages：
        # 参数形状现在由用户自己写，"这批为什么 400"要看得见他发出去的那几项。
        body: dict = {"messages": messages}     # 连请求体都没组出来时的兜底：正文照样留档
        try:
            body = request_body(self.settings, messages)
            reply = chat(self.settings, messages, timeout=TIMEOUT_SECONDS)
            tasks = extract.parse_tasks(reply["text"], len(chunk))
        except Exception as exc:
            # 契约层的问题（JSON 读不懂那类）直接说人话，服务商的错才翻成中文。
            error = (str(exc) if isinstance(exc, ValueError)
                     else error_text(exc, secret, TIMEOUT_SECONDS))
            # 失败那批也要把**服务商原样回的那一份**留在回复列里（界面要看的就是这个）；
            # 已经拿到正文的不动它——半截 JSON 正是这一页最该照原样看的东西。
            reply = {**reply, "text": reply["text"] or raw_reply(exc, secret)}
        self.db.add_extract_call(run_id, {
            "session_id": session_id, "session_name": session_name,
            "started_at": started_at,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "status": "error" if error else "ok", "stage": "extract",
            "model": reply["model"], "messages": len(chunk),
            "request": json.dumps(body, ensure_ascii=False),
            "response": reply["text"], "reasoning": reply["reasoning"], "error": error})
        if error:
            self._fail(error)
            self._sync_run(run_id)
            return
        created = self.db.commit_extraction(
            [{**task, "session_id": session_id,
              "sources": [chunk[index]["sort_seq"] for index in task["ids"]]}
             for task in tasks],
            [row["id"] for row in chunk])
        with self._lock:
            self.state["batches"] += 1
            self.state["done"] += len(chunk)
            self.state["created"] += created
        self._sync_run(run_id)

    def _fail(self, reason: str) -> None:
        """记下中文原因；``total`` 仍是本轮取到的条数，缺口由失败批数与错误解释。"""
        with self._lock:
            self.state["failed_batches"] += 1
            self.state["error"] = reason

    def _merge_session(self, session_id: str, session_name: str,
                       plan: extract.Policy, run_id: int) -> None:
        """把这一轮之后仍然重复的待办合掉：一次请求，回执里只有要合并的组。

        只喂 ``pending`` 任务（见 :meth:`Database.merge_candidates`），一条都不合并是
        正常结果。**失败不毁第一轮**：任务已经落库了，这里只记一次失败调用并把原因写进
        轮次，下一轮还能再合。

        提示词不走设置页（那五项归第一轮），所以直接用常量；它跟第一轮那份一样在起进程
        时读进来，一轮里不会变。
        """
        rows = self.db.merge_candidates(session_id)
        if len(rows) < 2:
            return
        messages = [{"role": "system", "content": extract.MERGE_SYSTEM_PROMPT},
                    {"role": "user",
                     "content": extract.render_merge(session_name, rows, plan.profile)}]
        started_at = now_utc()
        started = time.perf_counter()
        reply = {"text": "", "reasoning": "", "model": ""}
        error = ""
        body: dict = {"messages": messages}
        groups: list[dict] = []
        secret = str(self.settings.get("llm_api_key") or "")
        try:
            body = request_body(self.settings, messages)
            reply = chat(self.settings, messages, timeout=TIMEOUT_SECONDS)
            groups = extract.parse_merge(reply["text"], len(rows))
        except Exception as exc:
            error = (str(exc) if isinstance(exc, ValueError)
                     else error_text(exc, secret, TIMEOUT_SECONDS))
            reply = {**reply, "text": reply["text"] or raw_reply(exc, secret)}
        # 与第一轮同一条表：`messages` 这一列在这边记的是**待办条数**，界面按 stage 换标签。
        self.db.add_extract_call(run_id, {
            "session_id": session_id, "session_name": session_name,
            "started_at": started_at,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "status": "error" if error else "ok", "stage": "merge",
            "model": reply["model"], "messages": len(rows),
            "request": json.dumps(body, ensure_ascii=False),
            "response": reply["text"], "reasoning": reply["reasoning"], "error": error})
        if error:
            self._fail(f"合并失败：{error}")
            return
        merged = 0
        for group in groups:
            picked = [rows[index] for index in group["ids"]]
            fields = extract.merge_fields(picked)
            self.db.replace_tasks(session_id, [row["id"] for row in picked],
                                  {"title": group["title"],
                                   "description": group["description"], **fields})
            merged += 1
        with self._lock:
            self.state["merged"] += merged
        self._sync_run(run_id)
