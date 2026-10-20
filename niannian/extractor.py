"""提炼轮次：手动触发、一次只跑一轮的后台线程。

这一层只管"批次怎么切、请求怎么发、结果怎么落"，输入契约与判定规则在 :mod:`extract`。
它只读自己的库、只打外部 HTTPS，不碰微信解密缓存也不碰图片钥匙，所以不需要走采集线程
的命令队列，也不会把谁的线路占住（"请求线程一律纯读"那条针对的是微信那份缓存）。
"""

from __future__ import annotations

import threading

from . import extract
from .llm import chat, error_text, split_config
from .storage import now_utc

# 一批最多 20 条消息要判，等待比"测试连接"长得多；开思考放宽到 180 秒
# （真机实测：单次要跑到 30 秒）。回复长度不设上限（用户 2026-10-20 定：一个
# ``max_tokens`` 都不发），所以超时是这一层唯一的护栏。
TIMEOUT_SECONDS = 60.0
TIMEOUT_THINKING_SECONDS = 180.0


def request_timeout(settings: dict) -> float:
    """这一批最多等多久（与 :func:`llm.test_timeout` 同一个道理）。"""
    return TIMEOUT_THINKING_SECONDS if bool(settings.get("llm_thinking")) \
        else TIMEOUT_SECONDS


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
                            "failed_batches": 0, "session": None, "error": None}

    def status(self) -> dict:
        with self._lock:
            return dict(self.state)

    def start(self, session_id: str = "") -> dict:
        """起一轮；已经在跑就原样回当前状态，不排队第二轮。

        配置没填齐在**出网之前**就抛中文错，别把"我没配好"拿去问服务商。
        """
        split_config(self.settings)
        # 先读一次积压再回话（纯读自己的库，毫秒级）：不然接口立刻回的那份状态里
        # total 是 0，界面看着像"这轮什么都没找到"，等线程填上要好几个来回。
        backlog = len(self.db.pending_extraction(session_id, extract.ROUND_LIMIT))
        with self._lock:
            if self.state["running"]:
                # 已经在跑：不排队第二轮，把当前状态原样回过去，accepted=False 说明
                # "你要的这轮没起，因为上一轮还没结束"
                return {**self.state, "accepted": False}
            self._stop.clear()
            self.state = {"running": True, "started_at": now_utc(), "finished_at": None,
                          "total": backlog, "done": 0, "batches": 0, "created": 0,
                          "failed_batches": 0, "session": None, "error": None}
            snapshot = {**self.state, "accepted": True}
        self._worker = threading.Thread(target=self._run, args=(session_id,),
                                        name="extractor", daemon=True)
        self._worker.start()
        return snapshot

    def stop(self) -> None:
        """取消当前轮次：批与批之间停，已经回来的那个批次照常落库。"""
        self._stop.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=1)

    def _run(self, session_id: str) -> None:
        error: str | None = None
        try:
            rows = self.db.pending_extraction(session_id, extract.ROUND_LIMIT)
            with self._lock:
                self.state["total"] = len(rows)
            for sid, name, group in group_by_session(rows):
                for chunk in batches(group, extract.BATCH_LIMIT):
                    if self._stop.is_set():
                        error = "已取消，未发出的批次一条都没标记，下次照旧重试"
                        break
                    self._one_batch(sid, name, chunk)
                if self._stop.is_set():
                    break
        except Exception as exc:                      # 查库崩了这类轮次级意外
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
        with self._lock:
            self.state["running"] = False
            self.state["finished_at"] = now_utc()
            if error:
                self.state["error"] = error

    def _one_batch(self, session_id: str, session_name: str, chunk: list[dict]) -> None:
        """一批一次请求。失败**不标记**这批：下一轮还会取到它们，不会静默丢消息。"""
        first = chunk[0]
        history = self.db.extraction_history(session_id, first["sort_seq"], first["id"],
                                             extract.HISTORY_LIMIT)
        messages = [{"role": "system", "content": extract.SYSTEM_PROMPT},
                    {"role": "user", "content": extract.render(session_name, history, chunk)}]
        timeout = request_timeout(self.settings)
        with self._lock:
            self.state["session"] = session_name or session_id
        try:
            reply = chat(self.settings, messages, timeout=timeout)
            tasks = extract.parse_tasks(reply["text"], len(chunk))
        except ValueError as exc:                     # 契约层的问题，不用翻服务商话术
            self._fail(str(exc))
            return
        except Exception as exc:
            self._fail(error_text(exc, str(self.settings.get("llm_api_key") or ""),
                                  timeout))
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

    def _fail(self, reason: str) -> None:
        """记下中文原因；``total`` 仍是本轮取到的条数，缺口由失败批数与错误解释。"""
        with self._lock:
            self.state["failed_batches"] += 1
            self.state["error"] = reason
