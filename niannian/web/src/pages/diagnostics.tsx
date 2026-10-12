import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Database,
  RefreshCw,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
} from "../components/ui";
import { Empty, ErrorNotice, Loading } from "../components/common";
import { ModelCallInspector } from "../components/model-call-inspector";
import { api, cn, dateText, secondsText } from "../lib/utils";
import type {
  CallRow,
  CallsPayload,
  DiagnosticsPayload,
  Notify,
  RunDetail,
  BacklogPayload,
  Session,
} from "../lib/types";

type Props = { revision: number; notify: Notify; sessions: Session[] };

const ranges = [
  { days: 1, label: "近 24 小时" },
  { days: 7, label: "近 7 天" },
  { days: 30, label: "近 30 天" },
  { days: 0, label: "全部记录" },
];
const tabs = [
  { id: "runs", label: "轮次追踪" },
  { id: "calls", label: "调用检索" },
  { id: "issues", label: "异常与拦截" },
  { id: "backlog", label: "滞留队列" },
] as const;
type TabId = (typeof tabs)[number]["id"];

const kindNames: Record<string, string> = {
  compress: "长文压缩",
  single: "单段提炼",
  batch: "多段批量",
};
const actionNames: Record<string, string> = {
  CREATE: "新建待办",
  UPDATE: "更新待办",
  RESOLVE: "核销待办",
  MERGE: "合并来源",
  IGNORE: "无需行动",
  MANUAL: "人工改动",
};
const statusNames: Record<string, string> = {
  completed: "已完成",
  running: "进行中",
  failed: "失败",
  cancelled: "已取消",
};
const triggerNames: Record<string, string> = {
  scheduled: "自动轮次",
  manual: "手动提炼",
};
const issueNames: Record<string, string> = {
  request_error: "模型请求失败",
  output_truncated: "输出被 token 上限截断",
  index_missing: "批量漏答段落",
  instruction_rejected: "返回结构不符合契约",
};
const selectClass =
  "h-9 max-w-full rounded-md border border-input bg-white px-2 text-xs text-stone-600 outline-none focus:border-ring focus:ring-2 focus:ring-ring/30";

function ago(value?: string | null) {
  if (!value) return "—";
  const seconds = Math.max(0, (Date.now() - new Date(value).valueOf()) / 1000);
  if (seconds < 90) return "刚刚";
  if (seconds < 5400) return `${Math.round(seconds / 60)} 分钟前`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} 小时前`;
  return `${Math.round(seconds / 86400)} 天前`;
}

function useJson<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(Boolean(path));
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((value) => value + 1), []);
  useEffect(() => {
    if (!path) {
      setData(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api<T>(path, { signal: controller.signal })
      .then(setData)
      .catch((e: Error) => {
        if (e.name !== "AbortError") setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, nonce]);
  return { data, error, loading, reload };
}

function Pill({
  children,
  tone = "stone",
}: {
  children: React.ReactNode;
  tone?: "stone" | "emerald" | "amber" | "rose" | "sky";
}) {
  return (
    <span
      className={cn(
        "rounded px-1.5 py-0.5 text-[11px]",
        tone === "stone" && "bg-stone-100 text-stone-600",
        tone === "emerald" && "bg-emerald-50 text-emerald-800",
        tone === "amber" && "bg-amber-50 text-amber-800",
        tone === "rose" && "bg-rose-50 text-rose-700",
        tone === "sky" && "bg-sky-50 text-sky-700",
      )}
    >
      {children}
    </span>
  );
}

function Head({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex flex-col">
      <span className="text-[10px] text-stone-400">{label}</span>
      <span className="tabular-nums text-stone-700">{value}</span>
    </span>
  );
}

function CallLine({
  call,
  onOpen,
  active,
}: {
  call: CallRow;
  onOpen: (id: number) => void;
  active?: boolean;
}) {
  const failed = call.status === "error";
  const truncated = call.finish_reason === "length";
  return (
    <button
      onClick={() => onOpen(call.id)}
      className={cn(
        "w-full rounded-lg border px-3 py-2.5 text-left text-xs transition-colors hover:bg-stone-50",
        failed ? "border-rose-200 bg-rose-50/40" : "border-stone-100 bg-white",
        active && "ring-2 ring-ring/40",
      )}
    >
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="tabular-nums text-stone-500">
          {dateText(call.started_at, true)}
        </span>
        <Pill tone={failed ? "rose" : "emerald"}>{kindNames[call.kind] || call.kind}</Pill>
        <span className="min-w-0 flex-1 truncate text-stone-600">
          {call.session_name}
        </span>
        <span className="tabular-nums text-stone-400">
          {secondsText(call.duration_seconds)}
        </span>
      </p>
      <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-stone-500">
        <span>{call.segments} 段</span>
        <span className="text-stone-300">·</span>
        <span className="tabular-nums">
          {(call.stats_json.payload_chars ?? 0).toLocaleString()} 字符正文
        </span>
        {call.answered_items > 0 && (
          <Pill>{`已答 ${call.answered_items}`}</Pill>
        )}
        {call.missing_items > 0 && (
          <Pill tone="amber">{`漏答 ${call.missing_items}`}</Pill>
        )}
        {call.rejected_items > 0 && (
          <Pill tone="amber">{`结构不符 ${call.rejected_items}`}</Pill>
        )}
        {call.guard_changes > 0 && (
          <Pill tone="sky">{`校验改写 ${call.guard_changes}`}</Pill>
        )}
        {truncated && <Pill tone="amber">输出被截断</Pill>}
        {call.stats_json.compressed_segments?.some((item) => item) && (
          <Pill tone="stone">含压缩摘要</Pill>
        )}
        {failed && (
          <Pill tone="rose">
            {call.error}
            {call.error_detail ? ` · ${call.error_detail.slice(0, 60)}` : ""}
          </Pill>
        )}
      </p>
    </button>
  );
}

function LedgerLine({
  at,
  label,
  tone,
  children,
}: {
  at: string;
  label: string;
  tone: "emerald" | "stone" | "amber";
  children: React.ReactNode;
}) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-1 border-t border-stone-100 px-1 py-2 text-[11px]">
      <span className="tabular-nums text-stone-400">{dateText(at, true)}</span>
      <Pill tone={tone}>{label}</Pill>
      <span className="min-w-0 flex-1 break-words leading-5 text-stone-600">
        {children}
      </span>
    </li>
  );
}

export default function DiagnosticsPage({ revision, notify, sessions }: Props) {
  const [days, setDays] = useState(30);
  const [tab, setTab] = useState<TabId>("runs");
  const [runStatus, setRunStatus] = useState("");
  const [runTrigger, setRunTrigger] = useState("");
  const [onlyWorkload, setOnlyWorkload] = useState(true);
  const [runId, setRunId] = useState("");
  const [callId, setCallId] = useState(0);
  const [callScope, setCallScope] = useState<number[]>([]);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [callStatus, setCallStatus] = useState("");
  const [onlyTruncated, setOnlyTruncated] = useState(false);
  const [onlyGuarded, setOnlyGuarded] = useState(false);
  const [sessionFilter, setSessionFilter] = useState("");
  const [messageFilter, setMessageFilter] = useState(0);
  const [offset, setOffset] = useState(0);

  const mainPath = `/api/diagnostics/runs?days=${days}&status=${runStatus}&trigger=${runTrigger}&stamp=${revision}`;
  const { data, error, loading, reload } = useJson<DiagnosticsPayload>(mainPath);
  const runPath =
    tab === "runs" && runId && !callId ? `/api/diagnostics/runs/${runId}` : "";
  const { data: runDetail, loading: runLoading } = useJson<RunDetail>(runPath);
  const searchPath =
    tab === "calls" && !callId
      ? `/api/diagnostics/calls?days=${days}&kind=${kind}&status=${callStatus}` +
        `&session_id=${sessionFilter}&q=${encodeURIComponent(query)}` +
        `&truncated=${onlyTruncated}&guarded=${onlyGuarded}&offset=${offset}` +
        (messageFilter ? `&message_id=${messageFilter}` : "")
      : "";
  const { data: search, loading: searching } = useJson<CallsPayload>(searchPath);
  const backlogPath = tab === "backlog" && !callId ? "/api/diagnostics/backlog" : "";
  const { data: backlog, loading: backlogLoading } = useJson<BacklogPayload>(
    backlogPath,
  );

  const runs = data?.runs || [];
  const shownRuns = useMemo(
    () => (onlyWorkload ? runs.filter((row) => row.active) : runs),
    [runs, onlyWorkload],
  );
  useEffect(() => {
    if (!shownRuns.length) return;
    setRunId((current) =>
      current && shownRuns.some((row) => row.id === current)
        ? current
        : shownRuns[0].id,
    );
  }, [shownRuns]);

  const running = runs[0]?.status === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(reload, 4000);
    return () => clearInterval(timer);
  }, [running, reload]);

  const openCall = (id: number, scope: number[]) => {
    setCallScope(scope);
    setCallId(id);
  };
  const stepCall = (delta: number) => {
    const index = callScope.indexOf(callId);
    const next = callScope[index + delta];
    if (next) setCallId(next);
  };

  const summary = data?.summary;
  const retention = data?.retention;
  const landed = useMemo(
    () =>
      (runDetail?.ledger.events || []).filter((event) => event.action !== "MANUAL"),
    [runDetail],
  );
  const ignoredGroups = useMemo(() => {
    const groups = new Map<
      string,
      { reason: string; source: string; segments: number; ids: number[]; last: string }
    >();
    for (const row of runDetail?.ledger.ignored || []) {
      const key = `${row.source}|${row.reason}`;
      const group = groups.get(key) || {
        reason: row.reason,
        source: row.source,
        segments: 0,
        ids: [],
        last: row.created_at,
      };
      group.segments += 1;
      group.ids.push(...row.message_ids);
      group.last = row.created_at > group.last ? row.created_at : group.last;
      groups.set(key, group);
    }
    return [...groups.values()].sort(
      (left, right) => right.segments - left.segments,
    );
  }, [runDetail]);
  const totals = useMemo(
    () =>
      runs.reduce(
        (accumulator, row) => {
          accumulator.calls += row.calls.calls || 0;
          accumulator.failures += row.calls.failures || 0;
          accumulator.truncated += row.calls.truncated || 0;
          accumulator.guarded += row.calls.guarded_calls || 0;
          accumulator.missing += row.calls.missing || 0;
          accumulator.rejected += row.calls.rejected || 0;
          return accumulator;
        },
        { calls: 0, failures: 0, truncated: 0, guarded: 0, missing: 0, rejected: 0 },
      ),
    [runs],
  );

  const monitored = sessions.filter((session) => Boolean(session.monitored));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-[10px] font-medium tracking-[.2em] text-stone-400">
            EXTRACTION TRACE
          </p>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            提炼诊断
          </h1>
          <p className="mt-3 max-w-2xl text-sm text-stone-500">
            逐次还原发给模型的正文与模型原样回复，看清每段落库、被业务校验改写还是留在队列里。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex gap-1 rounded-lg bg-stone-50 p-1" role="group" aria-label="统计时间范围">
            {ranges.map((range) => (
              <button
                key={range.days}
                aria-pressed={days === range.days}
                onClick={() => {
                  setDays(range.days);
                  setCallId(0);
                  setOffset(0);
                }}
                className={cn(
                  "rounded-md px-2.5 py-1.5 text-xs text-stone-400 transition-colors",
                  days === range.days && "bg-white font-medium text-emerald-800 shadow-sm",
                )}
              >
                {range.label}
              </button>
            ))}
          </div>
          <Button variant="outline" size="sm" disabled={loading} onClick={reload}>
            <RefreshCw className={cn("size-4", loading && "animate-spin motion-reduce:animate-none")} />
            重新读取
          </Button>
        </div>
      </div>

      {error && <ErrorNotice message={`诊断数据读取失败：${error}`} onRetry={reload} />}

      {loading && !data ? (
        <Loading />
      ) : !data || !summary ? (
        !error && (
          <Empty
            title="还没有提炼轮次"
            description="连接微信并启用模型后，这里会记录每次请求的完整正文、模型原样回复与落库结果。"
          />
        )
      ) : (
        <>
          <Card>
            <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3.5 text-[11px] sm:px-5">
              <span className="flex items-center gap-1.5 text-stone-500">
                <Sparkles className="size-3.5 text-stone-400" />
                模型 <strong className="font-medium text-stone-700">{data.model.name}</strong>
                <Pill tone={data.model.enabled ? "emerald" : "stone"}>
                  {data.model.enabled ? "已启用" : "未启用，仅本地判定"}
                </Pill>
              </span>
              <span className="text-stone-500">思考 关闭</span>
              <span className="text-stone-500">温度 {data.model.temperature}</span>
              <span className="text-stone-500">
                超时 {data.model.timeout_seconds} 秒 · 输出上限{" "}
                {data.model.max_tokens_batch}/{data.model.max_tokens_single} token
              </span>
              <span className="text-stone-500">
                正文预算 {data.model.batch_char_budget} · 压缩目标{" "}
                {data.model.new_message_chars} · 历史 {data.model.history_rows}条/{data.model.history_chars}字
              </span>
              <span className="ml-auto flex items-center gap-1.5 text-stone-400">
                <Database className="size-3.5" />
                调用记录保留最近 {retention?.keep} 次，已存 {retention?.stored} 次
                {retention?.oldest_at ? `（最早 ${dateText(retention.oldest_at, true)}）` : ""}
              </span>
            </CardContent>
          </Card>

          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border bg-white px-4 py-3 text-xs">
            <Head label="轮次" value={`${summary.rounds}（有工作量 ${summary.active_rounds}）`} />
            <Head label="模型调用" value={`${totals.calls}`} />
            <Head label="请求失败" value={`${totals.failures}`} />
            <Head label="输出截断" value={`${totals.truncated}`} />
            <Head label="漏答段落" value={`${totals.missing}`} />
            <Head label="结构不符" value={`${totals.rejected}`} />
            <Head label="被校验改写" value={`${totals.guarded}`} />
            <Head label="累计新建待办" value={`${summary.created}`} />
            <Head label="累计无需行动" value={`${summary.ignored}`} />
            <Head label="当前积压" value={`${data.queued} 条消息`} />
            <Head label="最近一轮" value={ago(summary.last_round_at)} />
            {running && <Pill tone="emerald">有一轮正在进行，4 秒自动更新</Pill>}
          </div>

          {callId ? (
            <ModelCallInspector
              callId={callId}
              notify={notify}
              onBack={() => setCallId(0)}
              onStep={callScope.length > 1 ? stepCall : undefined}
              stepLabel={
                callScope.length > 1
                  ? `${callScope.indexOf(callId) + 1} / ${callScope.length}`
                  : undefined
              }
            />
          ) : (
            <>
              {!retention?.stored && (
                <Card>
                  <CardContent className="flex items-start gap-2 px-4 py-3 text-xs text-stone-500 sm:px-5">
                    <Database className="mt-0.5 size-4 shrink-0 text-stone-400" />
                    <span>
                      调用追踪从本次升级开始记录：更早的轮次没有请求正文可回查，
                      新消息进入提炼后这里就会出现完整的请求、回复与校验对照。
                    </span>
                  </CardContent>
                </Card>
              )}
              <div
                role="tablist"
                aria-label="诊断视图"
                className="flex flex-wrap gap-1 border-b border-stone-200"
              >
                {tabs.map((item) => (
                  <button
                    key={item.id}
                    role="tab"
                    aria-selected={tab === item.id}
                    onClick={() => setTab(item.id)}
                    className={cn(
                      "-mb-px rounded-t-lg px-3 py-2 text-xs text-stone-500 transition-colors",
                      tab === item.id &&
                        "border-b-2 border-emerald-700 font-medium text-emerald-800",
                    )}
                  >
                    {item.label}
                  </button>
                ))}
              </div>

              {tab === "runs" && (
                <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,330px)_minmax(0,1fr)]">
                  <Card>
                    <CardHeader className="flex-wrap gap-2">
                      <CardTitle>轮次</CardTitle>
                      <div className="flex flex-wrap gap-1">
                        <div className="flex gap-1 rounded-lg bg-stone-50 p-1" role="group" aria-label="轮次筛选">
                          {[
                            { on: true, label: "有工作量" },
                            { on: false, label: "全部" },
                          ].map((option) => (
                            <button
                              key={option.label}
                              aria-pressed={onlyWorkload === option.on}
                              onClick={() => setOnlyWorkload(option.on)}
                              className={cn(
                                "rounded-md px-2 py-1 text-[11px] text-stone-400 transition-colors",
                                onlyWorkload === option.on &&
                                  "bg-white font-medium text-emerald-800 shadow-sm",
                              )}
                            >
                              {option.label}
                            </button>
                          ))}
                        </div>
                        <select
                          aria-label="轮次状态筛选"
                          value={runStatus}
                          onChange={(event) => setRunStatus(event.target.value)}
                          className={selectClass}
                        >
                          <option value="">全部状态</option>
                          {Object.entries(statusNames).map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                        <select
                          aria-label="触发方式筛选"
                          value={runTrigger}
                          onChange={(event) => setRunTrigger(event.target.value)}
                          className={selectClass}
                        >
                          <option value="">全部触发</option>
                          <option value="manual">手动提炼</option>
                          <option value="scheduled">自动轮次</option>
                        </select>
                      </div>
                    </CardHeader>
                    <CardContent className="max-h-[72vh] space-y-2 overflow-y-auto px-3 py-3 sm:px-4">
                      <p className="text-[11px] text-stone-400">
                        列出 {shownRuns.length} / {runs.length} 轮
                      </p>
                      {!shownRuns.length && (
                        <p className="py-8 text-center text-xs text-stone-400">
                          此范围内没有符合条件的轮次
                        </p>
                      )}
                      {shownRuns.map((row) => {
                        const calls = row.calls;
                        return (
                          <button
                            key={row.id}
                            onClick={() => setRunId(row.id)}
                            aria-current={row.id === runId ? "true" : undefined}
                            className={cn(
                              "w-full rounded-lg border px-3 py-2.5 text-left text-xs transition-colors hover:bg-stone-50",
                              row.status === "failed"
                                ? "border-rose-200"
                                : row.error
                                  ? "border-amber-200"
                                  : "border-stone-100",
                              row.id === runId && "border-emerald-300 bg-emerald-50/40",
                            )}
                          >
                            <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
                              <span className="tabular-nums font-medium text-stone-700">
                                {dateText(row.at, true)}
                              </span>
                              <Pill
                                tone={
                                  row.status === "failed"
                                    ? "rose"
                                    : row.status === "running"
                                      ? "emerald"
                                      : row.status === "cancelled"
                                        ? "amber"
                                        : "stone"
                                }
                              >
                                {statusNames[row.status] || row.status}
                              </Pill>
                              <span className="text-stone-400">
                                {triggerNames[row.trigger || ""] || "—"}
                              </span>
                              <span className="ml-auto tabular-nums text-stone-400">
                                {secondsText(row.duration_seconds)}
                              </span>
                            </p>
                            <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-stone-500">
                              <span>{row.items} 段</span>
                              <span className="text-stone-300">·</span>
                              <span>{row.processed} 条消息</span>
                              <span className="text-stone-300">·</span>
                              <span>新建 {row.created}</span>
                              <span className="text-stone-300">·</span>
                              <span>无需行动 {row.ignored}</span>
                              {calls.calls ? (
                                <>
                                  <span className="text-stone-300">·</span>
                                  <span>调用 {calls.calls} 次</span>
                                </>
                              ) : (
                                <Pill tone="stone">无模型调用</Pill>
                              )}
                              {(calls.failures || 0) > 0 && (
                                <Pill tone="rose">{`失败 ${calls.failures}`}</Pill>
                              )}
                              {(calls.truncated || 0) > 0 && (
                                <Pill tone="amber">{`截断 ${calls.truncated}`}</Pill>
                              )}
                              {(calls.guarded_calls || 0) > 0 && (
                                <Pill tone="sky">{`改写 ${calls.guarded_calls}`}</Pill>
                              )}
                              {(calls.missing || 0) > 0 && (
                                <Pill tone="amber">{`漏答 ${calls.missing}`}</Pill>
                              )}
                              {row.skipped > 0 && (
                                <Pill tone="amber">{`暂缓 ${row.skipped}`}</Pill>
                              )}
                            </p>
                            {(row.error || row.last_model_error) && (
                              <p className="mt-1 break-words text-[11px] text-rose-600">
                                {row.error || row.last_model_error}
                              </p>
                            )}
                          </button>
                        );
                      })}
                    </CardContent>
                  </Card>

                  <div className="space-y-4">
                    {!runId && <Empty title="选择左侧轮次" description="点一轮即可看它的请求序列。" />}
                    {runLoading && !runDetail && <Loading />}
                    {runDetail && (
                      <>
                        <Card>
                          <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3.5 text-xs sm:px-5">
                            <Head
                              label="开始"
                              value={dateText(runDetail.run.started_at, true)}
                            />
                            <Head
                              label="耗时"
                              value={secondsText(
                                runDetail.run.finished_at
                                  ? (new Date(runDetail.run.finished_at).valueOf() -
                                      new Date(runDetail.run.started_at).valueOf()) /
                                    1000
                                  : null,
                              )}
                            />
                            <Head label="进度" value={`${runDetail.run.done} / ${runDetail.run.total}`} />
                            <Head
                              label="结束时积压"
                              value={String(runDetail.run.result?.queued ?? "—")}
                            />
                            <Head
                              label="模型调用"
                              value={`${runDetail.calls.length} 次`}
                            />
                            <Head
                              label="落库变更"
                              value={`${runDetail.ledger.events.filter((event) => event.action !== "MANUAL").length} 条`}
                            />
                            <Head
                              label="无需行动"
                              value={`${runDetail.ledger.ignored.length} 条`}
                            />
                            {runDetail.run.error && (
                              <span className="text-rose-600">{runDetail.run.error}</span>
                            )}
                          </CardContent>
                        </Card>
                        <Card>
                          <CardHeader>
                            <CardTitle>模型调用序列</CardTitle>
                            <span className="text-[10px] text-stone-400">
                              {runDetail.calls.length
                                ? "点一次调用看完整请求正文、模型原样回复与校验对照"
                                : "这一轮没有发出模型请求"}
                            </span>
                          </CardHeader>
                          <CardContent className="space-y-2 px-3 py-3 sm:px-4">
                            {!runDetail.calls.length && (
                              <p className="py-6 text-center text-xs text-stone-400">
                                没有待提炼消息，或全部段落在请求前就被过滤规则拦下（看下方判定去向）。
                              </p>
                            )}
                            {runDetail.calls.map((call) => (
                              <CallLine
                                key={call.id}
                                call={call}
                                onOpen={(id) =>
                                  openCall(
                                    id,
                                    runDetail.calls.map((row) => row.id),
                                  )
                                }
                              />
                            ))}
                          </CardContent>
                        </Card>

                        <div className="grid items-start gap-4 xl:grid-cols-2">
                          <Card>
                            <CardHeader>
                              <CardTitle>落库变更</CardTitle>
                              <span className="text-[10px] text-stone-400">
                                {landed.length} 条真实任务事件
                              </span>
                            </CardHeader>
                            <CardContent className="px-3 py-2 sm:px-4">
                              {!landed.length ? (
                                <p className="py-6 text-center text-xs text-stone-400">
                                  这一轮没有新建、更新、核销或合并
                                </p>
                              ) : (
                                <ul>
                                  {landed.map((event, index) => (
                                    <LedgerLine
                                      key={`land-${index}`}
                                      at={event.created_at}
                                      label={actionNames[event.action] || event.action}
                                      tone="emerald"
                                    >
                                      待办 #{event.task_id ?? "—"} ·{" "}
                                      {event.source === "llm" ? "模型判定" : "本地判定"} · 消息{" "}
                                      {event.message_ids.join("、") || "—"}
                                    </LedgerLine>
                                  ))}
                                </ul>
                              )}
                            </CardContent>
                          </Card>
                          <Card>
                            <CardHeader>
                              <CardTitle>无需行动与请求前拦截</CardTitle>
                              <span className="text-[10px] text-stone-400">
                                {runDetail.ledger.ignored.length} 段，按原因归类
                              </span>
                            </CardHeader>
                            <CardContent className="px-3 py-2 sm:px-4">
                              {!ignoredGroups.length ? (
                                <p className="py-6 text-center text-xs text-stone-400">
                                  这一轮所有段落都落成了任务
                                </p>
                              ) : (
                                <ul>
                                  {ignoredGroups.map((group) => (
                                    <LedgerLine
                                      key={`${group.source}-${group.reason}`}
                                      at={group.last}
                                      label={
                                        group.source === "llm" ? "模型判无需行动" : "本地拦截"
                                      }
                                      tone="stone"
                                    >
                                      <span className="font-medium text-stone-600">
                                        {group.reason}
                                      </span>
                                      <span className="mt-0.5 block text-[11px] text-stone-400">
                                        {group.segments} 段 · 消息{" "}
                                        {group.ids.slice(0, 14).join("、")}
                                        {group.ids.length > 14
                                          ? ` 等 ${group.ids.length} 条`
                                          : ""}
                                      </span>
                                    </LedgerLine>
                                  ))}
                                </ul>
                              )}
                            </CardContent>
                          </Card>
                        </div>
                      </>
                    )}
                  </div>
                </div>
              )}

              {tab === "calls" && (
                <div className="space-y-4">
                  <Card>
                    <CardContent className="flex flex-wrap items-center gap-2 px-4 py-3 sm:px-5">
                      <Search className="size-4 text-stone-400" />
                      <Input
                        value={query}
                        onChange={(event) => {
                          setQuery(event.target.value);
                          setOffset(0);
                        }}
                        placeholder="搜索正文、模型回复或校验原因里的关键词"
                        className="h-9 min-w-48 max-w-md flex-1 text-xs"
                      />
                      <select
                        aria-label="调用类型"
                        value={kind}
                        onChange={(event) => setKind(event.target.value)}
                        className={selectClass}
                      >
                        <option value="">全部类型</option>
                        <option value="compress">长文压缩</option>
                        <option value="single">单段提炼</option>
                        <option value="batch">多段批量</option>
                      </select>
                      <select
                        aria-label="调用状态"
                        value={callStatus}
                        onChange={(event) => setCallStatus(event.target.value)}
                        className={selectClass}
                      >
                        <option value="">全部状态</option>
                        <option value="ok">成功</option>
                        <option value="error">失败</option>
                      </select>
                      <select
                        aria-label="会话筛选"
                        value={sessionFilter}
                        onChange={(event) => setSessionFilter(event.target.value)}
                        className={selectClass}
                      >
                        <option value="">全部会话</option>
                        {monitored.map((session) => (
                          <option key={session.id} value={session.id}>
                            {session.name}
                          </option>
                        ))}
                      </select>
                      <label className="flex items-center gap-1.5 text-[11px] text-stone-500">
                        <input
                          type="checkbox"
                          checked={onlyTruncated}
                          onChange={(event) => setOnlyTruncated(event.target.checked)}
                        />
                        只看输出截断
                      </label>
                      <label className="flex items-center gap-1.5 text-[11px] text-stone-500">
                        <input
                          type="checkbox"
                          checked={onlyGuarded}
                          onChange={(event) => setOnlyGuarded(event.target.checked)}
                        />
                        只看被校验改写
                      </label>
                      {messageFilter > 0 && (
                        <button
                          onClick={() => setMessageFilter(0)}
                          className="flex items-center gap-1 rounded bg-emerald-50 px-2 py-1 text-[11px] text-emerald-800"
                        >
                          仅消息 #{messageFilter}
                          <X className="size-3" />
                        </button>
                      )}
                    </CardContent>
                  </Card>
                  {searching && !search ? (
                    <Loading />
                  ) : !search?.calls.length ? (
                    <Empty
                      title="没有符合条件的调用"
                      description="放宽时间范围或换个关键词；模型未启用时不会有任何调用记录。"
                    />
                  ) : (
                    <>
                      <ul className="space-y-2">
                        {search.calls.map((call) => (
                          <li key={call.id}>
                            <CallLine
                              call={call}
                              onOpen={(id) => openCall(id, search.calls.map((row) => row.id))}
                            />
                          </li>
                        ))}
                      </ul>
                      <div className="flex items-center gap-3">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={offset <= 0}
                          onClick={() => setOffset(Math.max(0, offset - 60))}
                        >
                          上一页
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={!search.has_more}
                          onClick={() => setOffset(offset + 60)}
                        >
                          下一页
                        </Button>
                        <span className="text-[11px] text-stone-400">
                          从第 {offset + 1} 条开始，按时间倒序
                        </span>
                      </div>
                    </>
                  )}
                </div>
              )}

              {tab === "issues" && (
                <div className="grid items-start gap-5 lg:grid-cols-2">
                  <Card>
                    <CardHeader>
                      <CardTitle>请求与输出异常</CardTitle>
                      <span className="text-[10px] text-stone-400">
                        按类型归类，点“看样本”直接打开那次请求
                      </span>
                    </CardHeader>
                    <CardContent className="px-3 py-2 sm:px-4">
                      {!data.issues.buckets.length ? (
                        <p className="py-8 text-center text-xs text-stone-400">
                          此范围内没有失败请求、截断或漏答记录
                        </p>
                      ) : (
                        <ul className="divide-y divide-stone-100 text-xs">
                          {data.issues.buckets.map((bucket) => (
                            <li
                              key={`${bucket.type}-${bucket.value}`}
                              className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2"
                            >
                              <Pill tone={bucket.type === "request_error" ? "rose" : "amber"}>
                                {issueNames[bucket.type] || bucket.type}
                              </Pill>
                              <span className="min-w-0 flex-1 break-words text-stone-600">
                                {bucket.value}
                              </span>
                              <span className="tabular-nums text-stone-500">
                                {bucket.count} 次
                              </span>
                              <span className="text-[11px] text-stone-400">
                                最近 {ago(bucket.last_at)}
                              </span>
                              {bucket.last_call_id && (
                                <button
                                  onClick={() =>
                                    openCall(bucket.last_call_id as number, [
                                      bucket.last_call_id as number,
                                    ])
                                  }
                                  className="rounded bg-stone-100 px-2 py-1 text-[11px] text-stone-600 hover:bg-stone-200"
                                >
                                  看样本
                                </button>
                              )}
                            </li>
                          ))}
                        </ul>
                      )}
                    </CardContent>
                  </Card>
                  <Card>
                    <CardHeader>
                      <CardTitle>模型说了但被业务校验改写</CardTitle>
                      <span className="text-[10px] text-stone-400">
                        扫描最近 {data.issues.guard_scanned} 条有改写的调用
                      </span>
                    </CardHeader>
                    <CardContent className="px-3 py-2 sm:px-4">
                      {!data.issues.guard_reasons.length ? (
                        <p className="py-8 text-center text-xs text-stone-400">
                          此范围内模型结论都被原样采纳
                        </p>
                      ) : (
                        <ul className="divide-y divide-stone-100 text-xs">
                          {data.issues.guard_reasons.map((row) => (
                            <li
                              key={row.reason}
                              className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2"
                            >
                              <span className="min-w-0 flex-1 break-words text-stone-600">
                                {row.reason}
                              </span>
                              <span className="tabular-nums text-stone-500">
                                {row.count} 段
                              </span>
                              <span className="text-[11px] text-stone-400">
                                最近 {ago(row.last_at)}
                              </span>
                              {row.last_call_id && (
                                <button
                                  onClick={() =>
                                    openCall(row.last_call_id as number, [
                                      row.last_call_id as number,
                                    ])
                                  }
                                  className="rounded bg-stone-100 px-2 py-1 text-[11px] text-stone-600 hover:bg-stone-200"
                                >
                                  看改写对照
                                </button>
                              )}
                            </li>
                          ))}
                        </ul>
                      )}
                      <p className="mt-3 border-t border-stone-100 pt-3 text-[11px] leading-5 text-stone-400">
                        改写不会回滚模型结论：改写后的指令仍然进入同一事务，
                        原样 JSON 保存在“校验改写与落库结果”里可逐字回查。
                      </p>
                    </CardContent>
                  </Card>
                  <Card className="lg:col-span-2">
                    <CardHeader>
                      <CardTitle>请求前拦截与无需行动</CardTitle>
                      <span className="text-[10px] text-stone-400">
                        来自 ignored_messages 判定记录，按原因归类，最多 10 类
                      </span>
                    </CardHeader>
                    <CardContent className="px-3 py-2 sm:px-4">
                      {!data.ignored_reasons.length ? (
                        <p className="py-6 text-center text-xs text-stone-400">
                          此范围内没有无需行动判定记录
                        </p>
                      ) : (
                        <ul className="grid gap-x-6 divide-y divide-stone-100 text-xs sm:grid-cols-2 sm:divide-y-0">
                          {data.ignored_reasons.map((row) => (
                            <li
                              key={`${row.source}-${row.reason}`}
                              className="flex items-start gap-3 py-2 sm:border-b sm:border-stone-100"
                            >
                              <span className="min-w-0 flex-1 break-words text-stone-600">
                                {row.reason}
                              </span>
                              <span className="tabular-nums text-stone-500">
                                {row.count}
                              </span>
                              <span className="text-[11px] text-stone-400">
                                {row.source === "llm" ? "模型" : "本地"} · {ago(row.last_at)}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </CardContent>
                  </Card>
                </div>
              )}

              {tab === "backlog" && (
                <div className="space-y-4">
                  {!data.model.enabled && (
                    <Card>
                      <CardContent className="flex items-start gap-2 px-4 py-3 text-xs text-amber-800 sm:px-5">
                        <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                        <span>
                          模型当前未启用，队列里的消息只走本地规则判定，
                          不会产生调用记录。到设置页启用后再看这里。
                        </span>
                      </CardContent>
                    </Card>
                  )}
                  {backlogLoading && !backlog ? (
                    <Loading />
                  ) : !backlog ? (
                    <ErrorNotice message="滞留队列读取失败" onRetry={() => setTab("runs")} />
                  ) : !backlog.messages.length ? (
                    <Empty
                      title="队列已清空"
                      description={`关注会话的 ${backlog.queued} 条消息都已处理；新消息到达后会在下一轮进入提炼。`}
                    />
                  ) : (
                    <>
                      <Card>
                        <CardHeader>
                          <CardTitle>按会话看积压</CardTitle>
                          <span className="text-[10px] text-stone-400">
                            静默窗口 {backlog.settle_window_seconds} 秒：期间有新消息就整轮跳过该会话
                          </span>
                        </CardHeader>
                        <CardContent className="px-3 py-2 sm:px-4">
                          <ul className="divide-y divide-stone-100 text-xs">
                            {backlog.sessions.map((row) => {
                              const fresh =
                                row.last_message_at &&
                                Date.now() - new Date(row.last_message_at).valueOf() <
                                  backlog.settle_window_seconds * 1000;
                              return (
                                <li
                                  key={row.session_id}
                                  className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2"
                                >
                                  <span className="min-w-0 flex-1 truncate font-medium text-stone-700">
                                    {row.session_name}
                                  </span>
                                  <span className="tabular-nums text-stone-500">
                                    {row.messages} 条待提炼
                                  </span>
                                  <span className="text-[11px] text-stone-400">
                                    最早 {ago(row.oldest)}
                                  </span>
                                  {fresh && (
                                    <Pill tone="amber">
                                      刚有新消息，未满足静默窗口
                                    </Pill>
                                  )}
                                </li>
                              );
                            })}
                          </ul>
                        </CardContent>
                      </Card>
                      <Card>
                        <CardHeader>
                          <CardTitle>滞留消息明细</CardTitle>
                          <span className="text-[10px] text-stone-400">
                            被模型触及次数来自调用记录；一直失败会反复重试并留在队列
                          </span>
                        </CardHeader>
                        <CardContent className="px-3 py-2 sm:px-4">
                          <ul className="divide-y divide-stone-100 text-xs">
                            {backlog.messages.map((row) => (
                              <li key={row.id} className="space-y-1 py-2.5">
                                <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
                                  <span className="tabular-nums text-stone-400">
                                    #{row.id}
                                  </span>
                                  <span className="font-medium text-stone-600">
                                    {row.session_name}
                                  </span>
                                  <span className="text-stone-500">{row.sender_name}</span>
                                  <span className="ml-auto tabular-nums text-stone-400">
                                    {dateText(row.created_at, true)} · 滞留 {ago(row.created_at)}
                                  </span>
                                </p>
                                <p className="line-clamp-3 break-words leading-6 text-stone-600">
                                  {row.content}
                                </p>
                                <p className="flex flex-wrap items-center gap-1.5 text-[11px]">
                                  {row.calls ? (
                                    <>
                                      <Pill tone={row.failures ? "rose" : "stone"}>
                                        模型触及 {row.calls} 次
                                        {row.failures ? ` · 失败 ${row.failures}` : ""}
                                      </Pill>
                                      <span className="text-stone-400">
                                        最近 {ago(row.last_call_at)}
                                      </span>
                                    </>
                                  ) : (
                                    <Pill tone="amber">从未进入模型请求</Pill>
                                  )}
                                  <button
                                    onClick={() => {
                                      setMessageFilter(row.id);
                                      setTab("calls");
                                      setOffset(0);
                                      setQuery("");
                                    }}
                                    className="ml-auto rounded bg-stone-100 px-2 py-1 text-stone-600 hover:bg-stone-200"
                                  >
                                    查这条的调用
                                  </button>
                                </p>
                              </li>
                            ))}
                          </ul>
                        </CardContent>
                      </Card>
                    </>
                  )}
                </div>
              )}
            </>
          )}

          <p className="pb-2 text-[11px] leading-6 text-stone-400">
            口径说明：调用记录来自本机 <code className="rounded bg-stone-100 px-1">model_calls</code>{" "}
            表，逐次保存发出去的请求体与模型原样回复，只保留最近{" "}
            {retention?.keep} 次；落库变更与无需行动读自 <code className="rounded bg-stone-100 px-1">task_events</code>、
            <code className="rounded bg-stone-100 px-1">ignored_messages</code>。
            耗时是单次请求的网络加解析时间；输出 token 占用按字符数粗估，提供方返回 usage 时以 usage 为准。
            密钥与接口地址不入库也不下发。
          </p>
        </>
      )}
    </div>
  );
}
