import { useEffect, useState } from "react";
import {
  CalendarDays,
  CheckCheck,
  ChevronRight,
  Download,
  Flag,
  Inbox,
  MessageCircle,
  Search,
  Sparkles,
} from "lucide-react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
} from "../components/ui";
import { Empty, ErrorNotice, Loading } from "../components/common";
import { WorkspaceChart } from "../components/workspace-chart";
import { api, cn, dateText, priorityNames } from "../lib/utils";
import type { Session, Stats, Task, TaskStatus } from "../lib/types";

type Props = {
  stats?: Stats;
  sessions: Session[];
  revision: number;
  onOpen: (id: number) => void;
  onProcess: () => void;
  processDisabled: boolean;
};
const statusTabs: { value: TaskStatus | "all"; label: string }[] = [
  { value: "pending", label: "进行中" },
  { value: "completed", label: "已完成" },
  { value: "cancelled", label: "已忽略" },
  { value: "all", label: "全部" },
];
const selectClass =
  "h-9 max-w-full rounded-md border border-input bg-white px-3 text-xs text-stone-600 outline-none focus:border-ring focus:ring-2 focus:ring-ring/30";

export function TasksPage({
  stats,
  sessions,
  revision,
  onOpen,
  onProcess,
  processDisabled,
}: Props) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [status, setStatus] = useState<TaskStatus | "all">("pending");
  const [query, setQuery] = useState("");
  const [priority, setPriority] = useState("");
  const [session, setSession] = useState("");
  const [due, setDue] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const timer = setTimeout(() => {
      const params = new URLSearchParams({
        status,
        q: query,
        priority,
        session_id: session,
        due,
      });
      void api<Task[]>(`/api/tasks?${params}`, { signal: controller.signal })
        .then(setTasks)
        .catch((e: Error) => {
          if (e.name !== "AbortError") setError(e.message);
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 200);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [status, query, priority, session, due, revision, retry]);
  const metrics = [
    {
      label: "进行中",
      value: stats?.pending,
      icon: Inbox,
      color: "bg-emerald-50 text-emerald-700",
    },
    {
      label: "今天截止",
      value: stats?.due_today,
      icon: CalendarDays,
      color: "bg-amber-50 text-amber-600",
    },
    {
      label: "已逾期",
      value: stats?.overdue,
      icon: Flag,
      color: "bg-rose-50 text-rose-600",
    },
    {
      label: "累计完成",
      value: stats?.completed,
      icon: CheckCheck,
      color: "bg-sky-50 text-sky-600",
    },
  ];
  return (
    <div className="space-y-7">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-[10px] font-medium tracking-[.2em] text-stone-400">
            YOUR DAILY FOCUS
          </p>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            待办事项
          </h1>
          <p className="mt-3 text-sm text-stone-400">
            从消息里拾起重要的事，一件件完成。
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" asChild>
            <a href="/api/export?format=md" download>
              <Download />
              导出
            </a>
          </Button>
          <Button onClick={onProcess} disabled={processDisabled}>
            <Sparkles />
            立即提炼
          </Button>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {metrics.map(({ label, value, icon: Icon, color }) => (
          <Card key={label}>
            <CardContent className="flex items-center gap-4 px-4 py-5 sm:px-5">
              <span
                className={cn(
                  "flex size-10 shrink-0 items-center justify-center rounded-xl",
                  color,
                )}
              >
                <Icon className="size-[18px]" />
              </span>
              <div>
                <p className="text-xs text-stone-400">{label}</p>
                <p className="mt-1 text-2xl font-semibold tabular-nums">
                  {value ?? "—"}
                </p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_280px]">
        <Card className="min-w-0 overflow-hidden">
          <CardHeader className="flex-wrap gap-4">
            <CardTitle>
              我的待办{" "}
              <span className="ml-1 font-normal text-stone-400">
                {loading ? "…" : tasks.length}
              </span>
            </CardTitle>
            <div className="flex gap-1 rounded-lg bg-stone-50 p-1">
              {statusTabs.map((tab) => (
                <button
                  key={tab.value}
                  aria-pressed={status === tab.value}
                  onClick={() => setStatus(tab.value)}
                  className={cn(
                    "rounded-md px-2.5 py-1.5 text-xs text-stone-400 transition-colors",
                    status === tab.value &&
                      "bg-white font-medium text-emerald-800 shadow-sm",
                  )}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </CardHeader>
          <div className="grid gap-2 border-b border-border/60 p-4 sm:grid-cols-2">
            <div className="relative sm:col-span-2">
              <Search className="absolute left-3 top-2.5 size-4 text-stone-400" />
              <Input
                aria-label="搜索待办"
                placeholder="搜索标题、说明或来源会话…"
                className="pl-9"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <select
              aria-label="优先级筛选"
              className={selectClass}
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
            >
              <option value="">全部优先级</option>
              <option value="high">高优先级</option>
              <option value="medium">普通优先级</option>
              <option value="low">低优先级</option>
            </select>
            <select
              aria-label="截止时间筛选"
              className={selectClass}
              value={due}
              onChange={(e) => setDue(e.target.value)}
            >
              <option value="all">全部截止日期</option>
              <option value="today">今天截止</option>
              <option value="overdue">已逾期</option>
              <option value="upcoming">即将到期</option>
            </select>
            <select
              aria-label="来源会话筛选"
              className={cn(selectClass, "sm:col-span-2")}
              value={session}
              onChange={(e) => setSession(e.target.value)}
            >
              <option value="">全部来源会话</option>
              {sessions
                .filter((item) => item.message_count || item.task_count)
                .map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.name}
                  </option>
                ))}
            </select>
          </div>
          {loading ? (
            <Loading />
          ) : error ? (
            <div className="p-4">
              <ErrorNotice
                message={error}
                onRetry={() => setRetry((value) => value + 1)}
              />
            </div>
          ) : tasks.length ? (
            <div className="divide-y divide-border/60">
              {tasks.map((task) => (
                <button
                  className="group flex w-full items-start gap-3 px-5 py-5 text-left transition-colors hover:bg-stone-50/70"
                  key={task.id}
                  onClick={() => onOpen(task.id)}
                >
                  <span
                    className={cn(
                      "mt-1.5 size-2 shrink-0 rounded-full",
                      task.priority === "high"
                        ? "bg-rose-400"
                        : task.priority === "medium"
                          ? "bg-emerald-500"
                          : "bg-stone-300",
                    )}
                  />
                  <span className="min-w-0 flex-1">
                    <span
                      className={cn(
                        "block break-words text-sm font-medium leading-6",
                        task.status === "completed" &&
                          "text-stone-400 line-through",
                      )}
                    >
                      {task.title}
                    </span>
                    {(task.description || task.context_summary) && (
                      <span className="mt-1 line-clamp-2 block text-xs leading-6 text-stone-400">
                        {task.description || task.context_summary}
                      </span>
                    )}
                    <span className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-stone-400">
                      <span className="inline-flex min-w-0 items-center gap-1">
                        <MessageCircle className="size-3 shrink-0" />
                        <span className="max-w-52 truncate">
                          {task.session_name}
                        </span>
                      </span>
                      <span>
                        {task.due_at
                          ? `${dateText(task.due_at)} 截止`
                          : "暂无截止时间"}
                      </span>
                      <span>{task.source_message_ids.length} 条来源</span>
                    </span>
                  </span>
                  <Badge
                    variant="outline"
                    className={cn(
                      "mt-1 text-[10px]",
                      task.priority === "high" &&
                        "border-rose-200 bg-rose-50 text-rose-600",
                    )}
                  >
                    {priorityNames[task.priority]}
                  </Badge>
                  <ChevronRight className="mt-2 size-3.5 shrink-0 text-stone-300 group-hover:text-emerald-700" />
                </button>
              ))}
            </div>
          ) : (
            <Empty
              title="这里暂时没有待办"
              description={
                query || priority || session || due !== "all"
                  ? "试试调整筛选条件。"
                  : "关注微信会话并提炼后，明确的行动会出现在这里。"
              }
            />
          )}
          <div className="flex flex-wrap justify-between gap-2 border-t border-border/60 px-5 py-3 text-[10px] text-stone-400">
            <span>按优先级与截止时间排列</span>
            <span>点击事项查看真实消息来源</span>
          </div>
        </Card>
        <aside className="grid gap-5 sm:grid-cols-2 xl:grid-cols-1">
          <WorkspaceChart stats={stats} />
          <Card className="border-emerald-100 bg-emerald-50/60">
            <CardContent className="space-y-4">
              <Sparkles className="size-5 text-emerald-700" />
              <h2 className="text-sm font-medium">拾起清晰的行动</h2>
              <p className="text-xs leading-7 text-stone-500">
                接住消息，滤过闲聊，让你把注意力留给重要的事。
              </p>
              <div className="space-y-3 border-t border-emerald-100 pt-4 text-xs">
                <p className="flex justify-between text-stone-500">
                  真实消息
                  <span className="font-medium text-emerald-800">
                    {stats?.messages ?? "—"}
                  </span>
                </p>
                <p className="flex justify-between text-stone-500">
                  等待提炼
                  <span className="font-medium text-emerald-800">
                    {stats?.queued ?? "—"}
                  </span>
                </p>
                <p className="flex justify-between text-stone-500">
                  关注会话
                  <span className="font-medium text-emerald-800">
                    {stats?.monitored ?? "—"}
                  </span>
                </p>
              </div>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
