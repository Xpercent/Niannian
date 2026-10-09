import { useEffect, useRef, useState } from "react";
import { Check, Pencil, RotateCcw, Save, X } from "lucide-react";
import type { Notify, Task, TaskEvent, TaskStatus } from "../lib/types";
import {
  api,
  dateText,
  localInputTime,
  patchJson,
  priorityNames,
  statusNames,
} from "../lib/utils";
import { Message } from "./message";
import { ErrorNotice, Field, Loading } from "./common";
import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  Input,
  Textarea,
} from "./ui";

type Draft = Pick<Task, "title" | "description" | "owner" | "priority"> & {
  due_at: string;
};
const actionNames: Record<string, string> = {
  CREATE: "创建待办",
  UPDATE: "更新待办",
  RESOLVE: "处理待办",
  MERGE: "合并重复来源",
  MANUAL: "手动修改",
};
const fieldNames: Record<string, string> = {
  title: "标题",
  description: "说明",
  owner: "负责人",
  priority: "优先级",
  due_at: "截止时间",
  status: "状态",
  context_summary: "上下文",
  version: "版本",
};
const reasonNames: Record<string, string> = {
  same_action_and_distinctive_object: "动作与具体对象一致，保留来源后合并",
  duplicate_task: "重复待办",
  same_task: "同一事项",
};
const sourceNames: Record<string, string> = {
  ai: "模型提炼",
  manual: "手动",
  llm: "模型提炼",
  rule: "规则处理",
  cloud: "云端提炼",
  local: "本地提炼",
  fallback: "本地回退",
};
const draftOf = (task: Task): Draft => ({
  title: task.title,
  description: task.description,
  owner: task.owner || "",
  priority: task.priority,
  due_at: localInputTime(task.due_at),
});

function auditValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "未设置";
  if (key === "priority") return priorityNames[String(value)] || String(value);
  if (key === "status") return statusNames[String(value)] || String(value);
  if (key === "due_at") return dateText(String(value), true);
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function EventEntry({ event }: { event: TaskEvent }) {
  const detail = event.detail || {};
  return (
    <article className="border-l-2 border-stone-200 pl-4">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <strong className="font-medium text-stone-700">
          {actionNames[event.action] || "待办变更"}
        </strong>
        <time className="text-stone-400">
          {dateText(event.created_at, true)}
        </time>
      </div>
      <p className="mt-1 text-xs text-stone-400">
        {sourceNames[event.source] || event.source}
      </p>
      {event.action === "RESOLVE" && detail.resolution && (
        <p className="mt-2 text-sm text-stone-600">
          处理结果：{statusNames[detail.resolution] || detail.resolution}
        </p>
      )}
      {event.action === "MERGE" && detail.reason && (
        <p className="mt-2 text-sm text-stone-600">
          合并原因：{reasonNames[detail.reason] || detail.reason}
        </p>
      )}
      {(
        [
          ["修改前", detail.before],
          ["修改后", detail.after],
        ] as const
      ).map(
        ([label, values]) =>
          values && (
            <div
              key={label}
              className="mt-2 rounded-md bg-stone-50 p-3 text-xs leading-6"
            >
              <h4 className="font-medium text-stone-600">{label}</h4>
              {Object.entries(values).map(([key, value]) => (
                <p
                  key={key}
                  className="whitespace-pre-wrap break-words text-stone-500"
                >
                  {fieldNames[key] || key}：{auditValue(key, value)}
                </p>
              ))}
            </div>
          ),
      )}
    </article>
  );
}

export function TaskDrawer({
  taskId,
  onClose,
  onSaved,
  notify,
}: {
  taskId: number | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
  notify: Notify;
}) {
  const [task, setTask] = useState<Task | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [busy, setBusy] = useState(false);
  const currentId = useRef(taskId);
  const mutationBusy = useRef(false);
  currentId.current = taskId;

  useEffect(() => {
    const abort = new AbortController();
    setTask(null);
    setDraft(null);
    setEditing(false);
    setError("");
    setSaveError("");
    if (taskId === null) {
      setLoading(false);
      return () => abort.abort();
    }
    setLoading(true);
    api<Task>(`/api/tasks/${taskId}`, { signal: abort.signal })
      .then((value) => {
        if (!abort.signal.aborted) {
          setTask(value);
          setDraft(draftOf(value));
        }
      })
      .catch((e: unknown) => {
        if (!abort.signal.aborted) setError(String(e));
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [taskId]);

  const reload = async () => {
    if (taskId === null || mutationBusy.current) return;
    const id = taskId;
    setLoading(true);
    setError("");
    try {
      const value = await api<Task>(`/api/tasks/${id}`);
      if (currentId.current !== id) return;
      setTask(value);
      setDraft(draftOf(value));
      setEditing(false);
      setSaveError("");
    } catch (e) {
      if (currentId.current === id) setError(String(e));
    } finally {
      if (currentId.current === id) setLoading(false);
    }
  };

  const update = async (status?: TaskStatus) => {
    if (!task || !draft || mutationBusy.current) return;
    if (!status && !draft.title.trim()) {
      setSaveError("标题不能为空");
      return;
    }
    const id = task.id;
    mutationBusy.current = true;
    setBusy(true);
    setSaveError("");
    const fields = status
      ? { status }
      : {
          ...draft,
          title: draft.title.trim(),
          owner: draft.owner,
          due_at: draft.due_at ? `${draft.due_at}:00+08:00` : null,
        };
    try {
      await api(
        `/api/tasks/${id}`,
        patchJson({ ...fields, version: task.version }),
      );
      const value = await api<Task>(`/api/tasks/${id}`);
      await onSaved();
      if (currentId.current === id) {
        setTask(value);
        setDraft(draftOf(value));
        setEditing(false);
      }
      notify("待办已保存");
    } catch (e) {
      const conflict = (e as Error & { status?: number }).status === 409;
      const message = conflict
        ? "待办已被更新，输入已保留。请取消编辑并重新读取后再保存。"
        : String(e);
      if (currentId.current === id) setSaveError(message);
      notify(message, true);
    } finally {
      mutationBusy.current = false;
      setBusy(false);
    }
  };

  const cancelEdit = () => {
    if (task) setDraft(draftOf(task));
    setEditing(false);
    setSaveError("");
  };
  return (
    <Dialog
      open={taskId !== null}
      onOpenChange={(open) => !open && !busy && onClose()}
    >
      <DialogContent className="left-auto right-0 top-0 flex h-dvh max-h-dvh w-full max-w-2xl translate-x-0 translate-y-0 flex-col overflow-y-auto rounded-none border-y-0 border-r-0 p-0 sm:max-w-2xl">
        <div className="border-b border-stone-100 px-6 py-5 pr-14">
          <DialogTitle className="text-base font-semibold text-stone-900">
            待办详情
          </DialogTitle>
          <DialogDescription className="mt-1 text-xs text-stone-500">
            查看、编辑待办及其真实微信来源。
          </DialogDescription>
        </div>
        {loading ? (
          <Loading />
        ) : error ? (
          <div className="p-6">
            <ErrorNotice message={error} onRetry={() => void reload()} />
          </div>
        ) : (
          task &&
          draft && (
            <div className="space-y-7 p-6">
              <div>
                <div className="mb-3 flex flex-wrap gap-2">
                  <Badge>{statusNames[task.status]}</Badge>
                  <Badge>{priorityNames[task.priority]}优先级</Badge>
                </div>
                <h2 className="break-words text-xl font-semibold leading-8 text-stone-900">
                  {task.title}
                </h2>
                <p className="mt-2 text-xs text-stone-400">
                  {task.session_name} · 创建于 {dateText(task.created_at, true)}{" "}
                  · 版本 {task.version}
                </p>
              </div>
              {saveError && <ErrorNotice message={saveError} />}
              {editing ? (
                <fieldset disabled={busy} className="space-y-4">
                  <Field label="标题">
                    <Input
                      maxLength={120}
                      value={draft.title}
                      onChange={(e) =>
                        setDraft({ ...draft, title: e.target.value })
                      }
                    />
                  </Field>
                  <Field label="说明">
                    <Textarea
                      maxLength={4000}
                      value={draft.description}
                      onChange={(e) =>
                        setDraft({ ...draft, description: e.target.value })
                      }
                    />
                  </Field>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <Field label="负责人">
                      <Input
                        maxLength={100}
                        value={draft.owner}
                        onChange={(e) =>
                          setDraft({ ...draft, owner: e.target.value })
                        }
                      />
                    </Field>
                    <Field label="优先级">
                      <select
                        className="h-9 rounded-md border border-stone-200 bg-white px-3"
                        value={draft.priority}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            priority: e.target.value as Task["priority"],
                          })
                        }
                      >
                        {Object.entries(priorityNames).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </Field>
                  </div>
                  <Field label="截止时间" hint="北京时间（Asia/Shanghai）">
                    <Input
                      type="datetime-local"
                      value={draft.due_at}
                      onChange={(e) =>
                        setDraft({ ...draft, due_at: e.target.value })
                      }
                    />
                  </Field>
                  <div className="flex flex-wrap gap-2">
                    <Button onClick={() => void update()}>
                      <Save className="size-4" />
                      {busy ? "保存中" : "保存修改"}
                    </Button>
                    <Button variant="secondary" onClick={cancelEdit}>
                      取消编辑
                    </Button>
                  </div>
                </fieldset>
              ) : (
                <>
                  <dl className="grid gap-4 text-sm sm:grid-cols-2">
                    <div>
                      <dt className="text-xs text-stone-400">负责人</dt>
                      <dd className="mt-1 text-stone-700">
                        {task.owner || "未设置"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs text-stone-400">截止时间</dt>
                      <dd className="mt-1 text-stone-700">
                        {dateText(task.due_at, true)}
                      </dd>
                    </div>
                  </dl>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="secondary"
                      disabled={busy}
                      onClick={() => setEditing(true)}
                    >
                      <Pencil className="size-4" />
                      编辑
                    </Button>
                    {task.status !== "completed" && (
                      <Button
                        disabled={busy}
                        onClick={() => void update("completed")}
                      >
                        <Check className="size-4" />
                        标记完成
                      </Button>
                    )}
                    {task.status !== "cancelled" && (
                      <Button
                        variant="secondary"
                        disabled={busy}
                        onClick={() => void update("cancelled")}
                      >
                        <X className="size-4" />
                        忽略
                      </Button>
                    )}
                    {task.status !== "pending" && (
                      <Button
                        variant="secondary"
                        disabled={busy}
                        onClick={() => void update("pending")}
                      >
                        <RotateCcw className="size-4" />
                        恢复待办
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      disabled={busy}
                      onClick={() => void reload()}
                    >
                      <RotateCcw className="size-4" />
                      重新读取
                    </Button>
                  </div>
                </>
              )}
              {task.description && !editing && (
                <section>
                  <h3 className="mb-2 text-sm font-semibold text-stone-800">
                    任务说明
                  </h3>
                  <p className="whitespace-pre-wrap break-words text-sm leading-7 text-stone-600">
                    {task.description}
                  </p>
                </section>
              )}
              {task.context_summary && (
                <section>
                  <h3 className="mb-2 text-sm font-semibold text-stone-800">
                    上下文
                  </h3>
                  <p className="whitespace-pre-wrap break-words rounded-lg bg-emerald-50/60 p-4 text-sm leading-7 text-stone-600">
                    {task.context_summary}
                  </p>
                </section>
              )}
              <section>
                <h3 className="mb-3 text-sm font-semibold text-stone-800">
                  来源消息 · {task.messages?.length || 0}
                </h3>
                <div className="space-y-3">
                  {task.messages?.map((message) => (
                    <Message key={message.id} message={message} />
                  ))}
                  {!task.messages?.length && (
                    <p className="text-sm text-stone-400">暂无可读取的原文。</p>
                  )}
                </div>
              </section>
              <section>
                <h3 className="mb-4 text-sm font-semibold text-stone-800">
                  变更记录
                </h3>
                <div className="space-y-5">
                  {task.events?.map((event) => (
                    <EventEntry key={event.id} event={event} />
                  ))}
                  {!task.events?.length && (
                    <p className="text-sm text-stone-400">暂无变更记录。</p>
                  )}
                </div>
              </section>
            </div>
          )
        )}
      </DialogContent>
    </Dialog>
  );
}
