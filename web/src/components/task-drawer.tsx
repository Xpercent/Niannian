import { useEffect, useState } from "react";
import { Check, ExternalLink, Pencil, RotateCcw, Save, X } from "lucide-react";
import { Message } from "./message";
import { Badge, Button, Dialog, Input, Textarea, cn } from "./ui";
import {
  api,
  dateText,
  deadlineInput,
  deadlinePayload,
  patchJson,
  PRIORITY_NAMES,
  TASK_STATUS_NAMES,
  type QuoteTarget,
  type TaskDetail,
  type TaskPriority,
} from "../lib/api";

type Props = {
  taskId: number | null;
  onClose: () => void;
  /** 改完让外层重读列表与指标卡（数字与列表都归 App 轮询）。 */
  onSaved: () => void;
  notify: (text: string, error?: boolean) => void;
  /** 跳到会话页并定位那句依据：跨页跳转只有一个出口，见 `app.tsx`。 */
  onOpenSource: (sessionId: string, target: QuoteTarget) => void;
};

type Draft = {
  title: string;
  description: string;
  priority: TaskPriority;
  /** 表单里是上海挂钟时间 `2026-10-18T12:00`，交给后端前才补 `+08:00`。 */
  deadline: string;
};

const selectClass =
  "h-10 w-full rounded-lg border bg-white px-3 text-sm outline-none transition focus-visible:ring-2 focus-visible:ring-ring";

const draftOf = (task: TaskDetail): Draft => ({
  title: task.title,
  description: task.description,
  priority: task.priority,
  deadline: deadlineInput(task.deadline),
});

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-stone-500">{label}</span>
      {children}
    </label>
  );
}

/**
 * 待办详情抽屉：右侧整高一栏，看内容、看是哪几句消息让它成为待办、改状态。
 *
 * 依据消息直接用会话页那个气泡组件渲染——同一句话在两个地方长得不一样，
 * 人就分不清"模型看到的"和"微信里的"是不是同一条。
 */
export function TaskDrawer({ taskId, onClose, onSaved, notify, onOpenSource }: Props) {
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (taskId === null) {
      setTask(null);
      setDraft(null);
      setEditing(false);
      setError("");
      return;
    }
    const abort = new AbortController();
    setLoading(true);
    setError("");
    setEditing(false);
    api<TaskDetail>(`/api/tasks/${taskId}`, { signal: abort.signal })
      .then((value) => {
        setTask(value);
        setDraft(draftOf(value));
      })
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") setError(reason.message || "读取待办失败");
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [taskId]);

  async function patch(fields: Record<string, unknown>, message: string) {
    if (taskId === null) return;
    setBusy(true);
    setError("");
    try {
      const value = await api<TaskDetail>(`/api/tasks/${taskId}`, patchJson(fields));
      setTask(value);
      setDraft(draftOf(value));
      setEditing(false);
      onSaved();
      notify(message);
    } catch (reason) {
      setError((reason as Error).message || "保存失败");
      notify((reason as Error).message || "保存失败", true);
    } finally {
      setBusy(false);
    }
  }

  function saveEdit() {
    if (!draft) return;
    if (!draft.title.trim()) {
      setError("标题不能空着");
      return;
    }
    void patch(
      {
        title: draft.title.trim(),
        description: draft.description,
        priority: draft.priority,
        deadline: deadlinePayload(draft.deadline),
      },
      "已保存修改",
    );
  }

  const open = taskId !== null;
  return (
    <Dialog
      open={open}
      onClose={() => {
        if (!busy) onClose();
      }}
      placement="right"
      width="w-full max-w-2xl"
      padded={false}
    >
      <div className="border-b border-stone-100 px-6 py-5 pr-14">
        <h2 className="text-base font-semibold text-stone-900">待办详情</h2>
        <p className="mt-1 text-xs text-stone-500">看内容、看是哪几句消息让它成了待办。</p>
      </div>
      {loading && <p className="p-6 text-sm text-stone-400">正在读取…</p>}
      {!loading && error && (
        <div className="m-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </div>
      )}
      {!loading && task && draft && (
        <div className="space-y-7 p-6">
          <div>
            <div className="mb-3 flex flex-wrap gap-2">
              <Badge
                tone={task.status === "pending" ? "default" : "warning"}
                className={cn(
                  task.status === "completed" && "bg-emerald-100 text-emerald-800",
                  task.status === "cancelled" && "bg-stone-200 text-stone-500",
                )}
              >
                {TASK_STATUS_NAMES[task.status]}
              </Badge>
              <Badge>{PRIORITY_NAMES[task.priority]}优先级</Badge>
            </div>
            <h3 className="break-words text-xl font-semibold leading-8 text-stone-900">
              {task.title}
            </h3>
            <p className="mt-2 break-words text-xs text-stone-400">
              {task.session_name} · 提炼于 {dateText(task.created_at, true)}
            </p>
          </div>

          {editing ? (
            <fieldset disabled={busy} className="space-y-4">
              <Field label="标题">
                <Input
                  maxLength={120}
                  value={draft.title}
                  onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                />
              </Field>
              <Field label="说明">
                <Textarea
                  rows={4}
                  maxLength={2000}
                  value={draft.description}
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="优先级">
                  <select
                    className={selectClass}
                    value={draft.priority}
                    onChange={(e) =>
                      setDraft({ ...draft, priority: e.target.value as TaskPriority })
                    }
                  >
                    {Object.entries(PRIORITY_NAMES).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="截止时间（北京时间）">
                  <Input
                    type="datetime-local"
                    value={draft.deadline}
                    onChange={(e) => setDraft({ ...draft, deadline: e.target.value })}
                  />
                </Field>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button onClick={saveEdit} disabled={busy}>
                  <Save className="size-4" />
                  {busy ? "保存中" : "保存修改"}
                </Button>
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() => {
                    setDraft(draftOf(task));
                    setEditing(false);
                    setError("");
                  }}
                >
                  取消编辑
                </Button>
              </div>
            </fieldset>
          ) : (
            <>
              {task.deadline && (
                <p className="text-sm text-stone-600">
                  <span className="text-xs text-stone-400">截止时间</span>
                  <span className="ml-2">{dateText(task.deadline, true)}</span>
                </p>
              )}
              {task.description && (
                <section>
                  <h4 className="mb-2 text-sm font-semibold text-stone-800">任务说明</h4>
                  <p className="break-words whitespace-pre-wrap text-sm leading-7 text-stone-600">
                    {task.description}
                  </p>
                </section>
              )}
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" disabled={busy} onClick={() => setEditing(true)}>
                  <Pencil className="size-4" />
                  编辑
                </Button>
                {task.status !== "completed" && (
                  <Button disabled={busy} onClick={() => void patch({ status: "completed" }, "已标记完成")}>
                    <Check className="size-4" />
                    标记完成
                  </Button>
                )}
                {task.status !== "cancelled" && (
                  <Button variant="secondary" disabled={busy} onClick={() => void patch({ status: "cancelled" }, "已忽略这条待办")}>
                    <X className="size-4" />
                    忽略
                  </Button>
                )}
                {task.status !== "pending" && (
                  <Button variant="secondary" disabled={busy} onClick={() => void patch({ status: "pending" }, "已恢复为待办")}>
                    <RotateCcw className="size-4" />
                    恢复待办
                  </Button>
                )}
              </div>
            </>
          )}

          <section>
            <h4 className="mb-3 text-sm font-semibold text-stone-800">
              依据消息 · {task.messages.length}
            </h4>
            <div className="space-y-3">
              {task.messages.map((message) => (
                <div key={message.id}>
                  <Message message={message} />
                  <Button
                    size="sm"
                    variant="ghost"
                    className="mt-1"
                    onClick={() =>
                      onOpenSource(task.session_id, {
                        message_id: message.id,
                        sort_seq: message.sort_seq,
                      })
                    }
                  >
                    <ExternalLink className="size-3.5" />
                    到会话里看这句
                  </Button>
                </div>
              ))}
              {!task.messages.length && (
                <p className="text-sm text-stone-400">
                  这几句原文不在这个会话的导入范围里，看不到了；先在该会话「重新导入」更早的历史。
                </p>
              )}
            </div>
          </section>
        </div>
      )}
    </Dialog>
  );
}
