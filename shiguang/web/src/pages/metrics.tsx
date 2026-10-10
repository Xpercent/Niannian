import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BrainCircuit,
  Clock3,
  Database,
  Gauge,
  Layers,
  RefreshCw,
  Sparkles,
  Timer,
} from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "../components/ui";
import { Empty, ErrorNotice, Loading } from "../components/common";
import { api, cn, dateText } from "../lib/utils";
import type { MetricsPayload, RoundRow } from "../lib/types";

type Props = { revision: number };

const ranges = [
  { days: 1, label: "近 24 小时" },
  { days: 7, label: "近 7 天" },
  { days: 30, label: "近 30 天" },
  { days: 0, label: "全部记录" },
];

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
const actionNames: Record<string, string> = {
  CREATE: "新建待办",
  UPDATE: "更新待办",
  RESOLVE: "核销待办",
  MERGE: "合并来源",
  MANUAL: "人工改动",
};
const sourceNames: Record<string, string> = {
  llm: "模型判定",
  rule: "本地判定",
  manual: "人工操作",
};

const missing = (value: number | null | undefined) =>
  value === null || value === undefined;

function secondsText(value: number | null | undefined) {
  if (missing(value)) return "—";
  if (value < 1) return `${Math.round(value * 1000)} 毫秒`;
  if (value < 60) return `${value.toFixed(1)} 秒`;
  return `${Math.floor(value / 60)} 分 ${Math.round(value % 60)} 秒`;
}

function percentText(value: number | null | undefined) {
  if (missing(value)) return "—";
  const percent = (value as number) * 100;
  return `${percent >= 10 ? Math.round(percent) : percent.toFixed(1)}%`;
}

function Metric({
  label,
  value,
  hint,
  icon: Icon,
  tone = "bg-emerald-50 text-emerald-700",
}: {
  label: string;
  value: string;
  hint: string;
  icon: typeof Gauge;
  tone?: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 px-4 py-5 sm:px-5">
        <span
          className={cn(
            "flex size-10 shrink-0 items-center justify-center rounded-xl",
            tone,
          )}
        >
          <Icon className="size-[18px]" />
        </span>
        <div className="min-w-0">
          <p className="text-xs text-stone-400">{label}</p>
          <p className="mt-1 truncate text-2xl font-semibold tabular-nums">
            {value}
          </p>
          <p className="mt-1 truncate text-[11px] text-stone-400">{hint}</p>
        </div>
      </CardContent>
    </Card>
  );
}

function Bar({
  label,
  value,
  max,
  hint,
}: {
  label: string;
  value: number;
  max: number;
  hint?: string;
}) {
  return (
    <div className="text-xs">
      <div className="flex justify-between gap-3">
        <span className="text-stone-500">{label}</span>
        <span className="tabular-nums text-stone-600">
          {value}
          {hint && <span className="ml-1 text-stone-400">{hint}</span>}
        </span>
      </div>
      <div
        aria-hidden
        className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-stone-100"
      >
        <div
          className="h-full rounded-full bg-emerald-600/80"
          style={{ width: `${max ? Math.max(value / max, 0.02) * 100 : 0}%` }}
        />
      </div>
    </div>
  );
}

function DurationChart({ rounds }: { rounds: RoundRow[] }) {
  const shown = rounds
    .filter((row) => row.active && !missing(row.duration_seconds))
    .slice(-24);
  const peak = Math.max(
    ...shown.map((row) => row.duration_seconds as number),
    1,
  );
  if (!shown.length) {
    return (
      <p className="py-10 text-center text-xs text-stone-400">
        此时间范围内还没有有工作量的轮次
      </p>
    );
  }
  return (
    <div>
      <div aria-hidden className="flex h-40 items-end gap-1.5">
        {shown.map((row) => {
          const total = row.duration_seconds as number;
          const model = Math.min(row.model_seconds, total);
          return (
            <div
              key={row.id}
              className="flex h-full flex-1 flex-col justify-end gap-px"
            >
              <span
                className="w-full rounded-t bg-stone-300"
                style={{ height: `${((total - model) / peak) * 100}%` }}
              />
              <span
                className="w-full rounded-b bg-emerald-600/85"
                style={{ height: `${(model / peak) * 100}%` }}
              />
            </div>
          );
        })}
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-[11px] text-stone-400">
        <span className="flex items-center gap-3">
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-sm bg-emerald-600/85" aria-hidden />
            模型请求耗时
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-sm bg-stone-300" aria-hidden />
            本地校验与写入
          </span>
        </span>
        <span>
          最近 {shown.length} 个有工作量轮次，峰值 {secondsText(peak)}
          ；精确数值见下方明细
        </span>
      </div>
    </div>
  );
}

function RoundTable({ rounds }: { rounds: RoundRow[] }) {
  const cell = "px-3 py-2 tabular-nums whitespace-nowrap";
  return (
    <div className="-mx-5 overflow-x-auto px-5 lg:-mx-6 lg:px-6">
      <table className="w-full min-w-[860px] border-collapse text-xs">
        <caption className="sr-only">最近轮次的耗时与决策明细</caption>
        <thead>
          <tr className="text-left text-[11px] text-stone-400">
            {[
              "时间",
              "触发",
              "状态",
              "段落",
              "消息",
              "新建",
              "忽略",
              "总耗时",
              "模型耗时",
              "请求/失败",
              "结束时积压",
            ].map((label) => (
              <th key={label} scope="col" className={cn(cell, "font-medium")}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rounds.map((row) => (
            <tr
              key={row.id}
              className="border-t border-stone-100 text-stone-600"
            >
              <td className={cell}>{dateText(row.at, true)}</td>
              <td className={cell}>{triggerNames[row.trigger || ""] || "—"}</td>
              <td className={cell}>
                <span
                  className={cn(
                    "rounded px-1.5 py-0.5 text-[11px]",
                    row.status === "completed" && "bg-stone-100 text-stone-600",
                    row.status === "running" &&
                      "bg-emerald-50 text-emerald-700",
                    row.status === "failed" && "bg-rose-50 text-rose-700",
                    row.status === "cancelled" && "bg-amber-50 text-amber-700",
                  )}
                >
                  {statusNames[row.status] || row.status}
                </span>
                {row.error && (
                  <span className="mt-1 block max-w-56 whitespace-normal text-[11px] text-rose-600">
                    {row.error}
                  </span>
                )}
              </td>
              <td className={cell}>{row.items || "—"}</td>
              <td className={cell}>{row.processed || "—"}</td>
              <td className={cell}>{row.created || "—"}</td>
              <td className={cell}>{row.ignored || "—"}</td>
              <td className={cn(cell, "font-medium text-stone-800")}>
                {secondsText(row.duration_seconds)}
              </td>
              <td className={cell}>
                {secondsText(row.model_seconds)}
                {row.last_model_error && (
                  <span className="ml-1 text-[11px] text-amber-600">
                    {row.last_model_error}
                  </span>
                )}
              </td>
              <td className={cell}>
                {row.model_requests}
                {row.model_failures > 0 && (
                  <span className="text-rose-600"> / {row.model_failures}</span>
                )}
              </td>
              <td className={cell}>
                {missing(row.queued_after) ? "—" : row.queued_after}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function MetricsPage({ revision }: Props) {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<MetricsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [nonce, setNonce] = useState(0);
  const [onlyWorkload, setOnlyWorkload] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api<MetricsPayload>(`/api/metrics?days=${days}`, {
      signal: controller.signal,
    })
      .then((payload) => setData(payload))
      .catch((e: Error) => {
        if (e.name !== "AbortError") setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [days, revision, nonce]);

  const reload = () => setNonce((value) => value + 1);
  const metrics = data?.metrics;
  const summary = metrics?.summary;
  const model = data?.model;
  const decisions = summary
    ? [
        { label: "新建待办", value: summary.created },
        { label: "更新待办", value: summary.updated },
        { label: "核销待办", value: summary.resolved },
        { label: "合并来源", value: summary.merged },
        { label: "判为无需行动", value: summary.ignored },
        { label: "提炼前过滤", value: summary.pre_filtered },
        { label: "会话已暂停跳过", value: summary.skipped },
        { label: "取消时未处理", value: summary.cancelled },
      ]
    : [];
  const maxDecision = Math.max(1, ...decisions.map((item) => item.value));
  const sources = summary
    ? [
        { label: "模型判定", value: summary.llm_decided },
        { label: "本地判定", value: summary.rule_decided },
      ]
    : [];
  const maxSource = Math.max(1, ...sources.map((item) => item.value));
  const recent = metrics
    ? [...metrics.rounds]
        .reverse()
        .filter((row) => !onlyWorkload || row.active)
        .slice(0, 20)
    : [];
  const trouble = metrics
    ? metrics.rounds
        .filter(
          (row) => row.error || row.model_failures > 0 || row.last_model_error,
        )
        .reverse()
        .slice(0, 8)
    : [];

  return (
    <div className="space-y-7">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-[10px] font-medium tracking-[.2em] text-stone-400">
            PROCESSING TELEMETRY
          </p>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            处理状态
          </h1>
          <p className="mt-3 text-sm text-stone-400">
            模型提炼真实跑过的轮次：耗时、决策去向与回退情况。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div
            className="flex gap-1 rounded-lg bg-stone-50 p-1"
            role="group"
            aria-label="统计时间范围"
          >
            {ranges.map((range) => (
              <button
                key={range.days}
                aria-pressed={days === range.days}
                onClick={() => setDays(range.days)}
                className={cn(
                  "rounded-md px-2.5 py-1.5 text-xs text-stone-400 transition-colors",
                  days === range.days &&
                    "bg-white font-medium text-emerald-800 shadow-sm",
                )}
              >
                {range.label}
              </button>
            ))}
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={loading}
            onClick={reload}
          >
            <RefreshCw
              className={cn(
                "size-4",
                loading && "animate-spin motion-reduce:animate-none",
              )}
            />
            重新统计
          </Button>
        </div>
      </div>

      {error && (
        <ErrorNotice message={`统计读取失败：${error}`} onRetry={reload} />
      )}

      {loading && !data ? (
        <Loading />
      ) : !metrics || !summary || !model ? (
        !error && (
          <Empty
            title="还没有提炼轮次"
            description="连接微信并开启提炼后，这里会记录每一轮的真实耗时、模型请求与决策去向。"
          />
        )
      ) : (
        <>
          <Card>
            <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3.5 text-[11px] text-stone-500 sm:px-5">
              <span className="flex items-center gap-1.5">
                <BrainCircuit className="size-3.5 text-stone-400" />
                模型{" "}
                <strong className="font-medium text-stone-700">
                  {model.name}
                </strong>
                <span
                  className={cn(
                    "rounded px-1.5 py-0.5",
                    model.enabled
                      ? "bg-emerald-50 text-emerald-700"
                      : "bg-stone-100 text-stone-500",
                  )}
                >
                  {model.enabled ? "已启用" : "未启用，仅本地判定"}
                </span>
              </span>
              <span>思考模式：关闭</span>
              <span
                className={cn(
                  "rounded px-1.5 py-0.5",
                  model.temperature > 0
                    ? "bg-amber-50 text-amber-700"
                    : "bg-stone-100 text-stone-500",
                )}
              >
                采样温度 {model.temperature}
                {model.temperature > 0 ? "（判定可能不稳定）" : ""}
              </span>
              <span>自动间隔 {model.batch_interval_seconds} 秒</span>
              <span>静默窗口 {model.settle_window_seconds} 秒</span>
              <span>请求超时 {model.timeout_seconds} 秒</span>
              <span>
                每批最多 {model.batch_limit} 段 / {model.batch_char_budget} 字 ·
                输出上限 {model.max_tokens_batch}
              </span>
              <span>
                单轮上限 {model.round_limit} 条 · 单会话{" "}
                {model.per_session_limit} 条
              </span>
              <span className="ml-auto flex items-center gap-1.5">
                <Database className="size-3.5 text-stone-400" />
                统计基于最近 {metrics.scanned} 轮记录
                {metrics.scanned >= metrics.scanned_limit ? "（已达上限）" : ""}
                ；上方为当前设置，历史轮次可能用了不同参数
              </span>
            </CardContent>
          </Card>

          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <Metric
              label="平均轮次耗时"
              value={secondsText(summary.average_seconds)}
              hint={`中位数 ${secondsText(summary.median_seconds)} · ${summary.active_rounds} 个有工作量轮次`}
              icon={Timer}
            />
            <Metric
              label="P95 耗时"
              value={secondsText(summary.p95_seconds)}
              hint={`最快 ${secondsText(summary.fastest_seconds)} · 最慢 ${secondsText(summary.slowest_seconds)}`}
              icon={Gauge}
              tone="bg-sky-50 text-sky-600"
            />
            <Metric
              label="模型耗时占比"
              value={percentText(summary.model_share)}
              hint={`网络 ${secondsText(summary.model_seconds)} / 合计 ${secondsText(summary.wall_seconds)}`}
              icon={Clock3}
              tone="bg-stone-100 text-stone-600"
            />
            <Metric
              label="单段平均耗时"
              value={secondsText(summary.seconds_per_item)}
              hint={`${summary.items} 段聚合消息 · ${summary.processed} 条原始消息`}
              icon={Layers}
              tone="bg-stone-100 text-stone-600"
            />
            <Metric
              label="模型请求"
              value={String(summary.model_requests)}
              hint={`失败 ${summary.model_failures} 次 · ${percentText(summary.model_failure_rate)}`}
              icon={Sparkles}
              tone={
                summary.model_failures
                  ? "bg-amber-50 text-amber-600"
                  : "bg-emerald-50 text-emerald-700"
              }
            />
            <Metric
              label="本地判定占比"
              value={percentText(summary.local_fallback_rate)}
              hint={`模型 ${summary.llm_decided} / 本地 ${summary.rule_decided} 段`}
              icon={Activity}
              tone={
                summary.local_fallback_rate && summary.local_fallback_rate > 0.3
                  ? "bg-amber-50 text-amber-600"
                  : "bg-emerald-50 text-emerald-700"
              }
            />
            <Metric
              label="提炼前拦截"
              value={percentText(summary.pre_filter_rate)}
              hint={`${summary.pre_filtered} 段未进模型（附件、条件邀请、规则）`}
              icon={AlertTriangle}
              tone="bg-stone-100 text-stone-600"
            />
            <Metric
              label="当前待提炼队列"
              value={String(metrics.queued)}
              hint={`累计处理 ${summary.processed} 条 · 新建 ${summary.created} 项`}
              icon={BrainCircuit}
              tone="bg-stone-100 text-stone-600"
            />
          </div>

          <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
            <Card>
              <CardHeader>
                <CardTitle>轮次耗时构成</CardTitle>
                <span className="text-[10px] text-stone-400">
                  仅统计有工作量的轮次
                </span>
              </CardHeader>
              <CardContent className="px-4 py-4 sm:px-5">
                <DurationChart rounds={metrics.rounds} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>决策去向</CardTitle>
                <span className="text-[10px] text-stone-400">按聚合段落计</span>
              </CardHeader>
              <CardContent className="space-y-3 px-4 py-4 sm:px-5">
                {decisions.map((item) => (
                  <Bar
                    key={item.label}
                    label={item.label}
                    value={item.value}
                    max={maxDecision}
                  />
                ))}
              </CardContent>
            </Card>
          </div>

          <div className="grid items-start gap-5 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>判定来源</CardTitle>
                <span className="text-[10px] text-stone-400">
                  批量请求 {summary.batches} 次 · 每次平均{" "}
                  {summary.items_per_batch ?? "—"} 段
                </span>
              </CardHeader>
              <CardContent className="space-y-3 px-4 py-4 sm:px-5">
                {sources.map((item) => (
                  <Bar
                    key={item.label}
                    label={item.label}
                    value={item.value}
                    max={maxSource}
                  />
                ))}
                <div className="mt-4 space-y-2 border-t border-stone-100 pt-3 text-xs text-stone-500">
                  {metrics.events.length ? (
                    metrics.events.map((event) => (
                      <p
                        key={`${event.action}-${event.source}`}
                        className="flex justify-between gap-3"
                      >
                        <span>
                          {actionNames[event.action] || event.action}
                          {event.source !== "manual" && (
                            <span className="ml-1 text-stone-400">
                              · {sourceNames[event.source] || event.source}
                            </span>
                          )}
                        </span>
                        <span className="tabular-nums">
                          {event.count} 次 · {dateText(event.last_at, true)}
                        </span>
                      </p>
                    ))
                  ) : (
                    <p className="text-stone-400">
                      此范围内没有任务变更事件记录
                    </p>
                  )}
                </div>
                <p className="mt-3 text-[11px] leading-5 text-stone-400">
                  任务事件记录已落地的变更；模型判为无需行动或本地拦截的段落不产生事件，因此事件数少于决策数。
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>无需行动的原因</CardTitle>
                <span className="text-[10px] text-stone-400">
                  按原因归类统计，最多列 10 项，长文本已截断
                </span>
              </CardHeader>
              <CardContent className="px-4 py-4 sm:px-5">
                {metrics.ignored_reasons.length ? (
                  <ul className="space-y-2.5">
                    {metrics.ignored_reasons.map((item) => (
                      <li
                        key={`${item.source}-${item.reason}`}
                        className="text-xs"
                      >
                        <div className="flex justify-between gap-3">
                          <span className="min-w-0 break-words text-stone-600">
                            {item.reason}
                          </span>
                          <span className="shrink-0 tabular-nums text-stone-400">
                            {item.count}
                          </span>
                        </div>
                        <p className="mt-0.5 text-[11px] text-stone-400">
                          {sourceNames[item.source] || item.source} · 最近{" "}
                          {dateText(item.last_at, true)}
                        </p>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="py-8 text-center text-xs text-stone-400">
                    此范围内没有过滤记录
                  </p>
                )}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>每日汇总</CardTitle>
              <span className="text-[10px] text-stone-400">按上海时区切日</span>
            </CardHeader>
            <CardContent className="px-4 py-4 sm:px-5">
              <div className="-mx-4 overflow-x-auto px-4 sm:-mx-5 sm:px-5">
                <table className="w-full min-w-[720px] border-collapse text-xs">
                  <caption className="sr-only">每日轮次与处理量汇总</caption>
                  <thead>
                    <tr className="text-left text-[11px] text-stone-400">
                      {[
                        "日期",
                        "轮次",
                        "有工作量",
                        "段落",
                        "消息",
                        "新建",
                        "无需行动",
                        "请求/失败",
                        "模型耗时",
                        "总耗时",
                      ].map((label) => (
                        <th
                          key={label}
                          scope="col"
                          className="px-3 py-2 font-medium"
                        >
                          {label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {metrics.daily.map((row) => (
                      <tr
                        key={row.date}
                        className="border-t border-stone-100 text-stone-600"
                      >
                        <td className="px-3 py-2 whitespace-nowrap">
                          {row.date}
                        </td>
                        <td className="px-3 py-2 tabular-nums">{row.rounds}</td>
                        <td className="px-3 py-2 tabular-nums">
                          {row.active_rounds}
                        </td>
                        <td className="px-3 py-2 tabular-nums">
                          {row.items || "—"}
                        </td>
                        <td className="px-3 py-2 tabular-nums">
                          {row.processed || "—"}
                        </td>
                        <td className="px-3 py-2 tabular-nums">
                          {row.created || "—"}
                        </td>
                        <td className="px-3 py-2 tabular-nums">
                          {row.ignored || "—"}
                        </td>
                        <td className="px-3 py-2 tabular-nums">
                          {row.model_requests}
                          {row.model_failures > 0 && (
                            <span className="text-rose-600">
                              {" "}
                              / {row.model_failures}
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2 tabular-nums">
                          {secondsText(row.model_seconds)}
                        </td>
                        <td className="px-3 py-2 tabular-nums">
                          {secondsText(row.wall_seconds)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          {trouble.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>需要留意的轮次</CardTitle>
                <span className="text-[10px] text-stone-400">
                  出现模型失败、拒绝或轮次错误的记录
                </span>
              </CardHeader>
              <CardContent className="px-4 py-4 sm:px-5">
                <ul className="space-y-2 text-xs text-stone-600">
                  {trouble.map((row) => (
                    <li
                      key={row.id}
                      className="flex flex-wrap items-center gap-x-3 gap-y-1"
                    >
                      <span className="tabular-nums text-stone-500">
                        {dateText(row.at, true)}
                      </span>
                      <span>{triggerNames[row.trigger || ""] || "—"}</span>
                      {statusNames[row.status] || row.status}
                      {row.model_failures > 0 && (
                        <span className="rounded bg-rose-50 px-1.5 py-0.5 text-[11px] text-rose-700">
                          请求失败 {row.model_failures} 次
                        </span>
                      )}
                      {row.last_model_error && (
                        <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-700">
                          {row.last_model_error}
                        </span>
                      )}
                      {row.error && (
                        <span className="break-words text-rose-700">
                          {row.error}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader className="flex-wrap gap-3">
              <CardTitle>轮次明细</CardTitle>
              <div className="flex items-center gap-3">
                <div
                  className="flex gap-1 rounded-lg bg-stone-50 p-1"
                  role="group"
                  aria-label="轮次筛选"
                >
                  {[
                    { on: true, label: "仅有工作量" },
                    { on: false, label: "全部轮次" },
                  ].map((option) => (
                    <button
                      key={option.label}
                      aria-pressed={onlyWorkload === option.on}
                      onClick={() => setOnlyWorkload(option.on)}
                      className={cn(
                        "rounded-md px-2.5 py-1.5 text-xs text-stone-400 transition-colors",
                        onlyWorkload === option.on &&
                          "bg-white font-medium text-emerald-800 shadow-sm",
                      )}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                <span className="text-[10px] text-stone-400">
                  显示 {recent.length} / {metrics.rounds.length} 轮
                </span>
              </div>
            </CardHeader>
            <CardContent className="px-4 py-4 sm:px-5">
              {recent.length ? (
                <RoundTable rounds={recent} />
              ) : (
                <p className="py-8 text-center text-xs text-stone-400">
                  此范围内还没有轮次记录
                </p>
              )}
            </CardContent>
          </Card>

          <p className="pb-2 text-[11px] leading-6 text-stone-400">
            口径说明：耗时取轮次首尾时间戳，只统计有实际段落的轮次，空转轮次计入轮次数但不计入耗时与分位数；
            模型耗时是请求网络与解析时间的合计，其余为本地校验与写入；比率缺少样本时显示“—”，不做估算。
            所有数字来自本机{" "}
            <code className="rounded bg-stone-100 px-1">runs</code>、
            <code className="rounded bg-stone-100 px-1">task_events</code> 与
            <code className="rounded bg-stone-100 px-1">ignored_messages</code>{" "}
            的真实记录。
          </p>
        </>
      )}
    </div>
  );
}
