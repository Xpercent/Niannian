import { useEffect, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Copy,
  FileText,
  Scissors,
  Sparkles,
} from "lucide-react";
import { Button, Card, CardContent, CardHeader, CardTitle } from "./ui";
import { ErrorNotice, Loading } from "./common";
import { api, cn, dateText, secondsText } from "../lib/utils";
import type {
  BriefMessage,
  CallDetail,
  InstructionJson,
  ModelData,
  Notify,
  OutcomeItem,
} from "../lib/types";

const kindNames: Record<string, string> = {
  compress: "长文压缩",
  single: "单段提炼",
  batch: "多段批量提炼",
};
const actionNames: Record<string, string> = {
  CREATE: "新建待办",
  UPDATE: "更新待办",
  RESOLVE: "核销待办",
  IGNORE: "无需行动",
  MERGE: "合并来源",
};
const truncationMarker = "\n[…原文过长，完整来源保存在本地…]\n";

function copyText(value: string, notify: Notify) {
  navigator.clipboard
    .writeText(value)
    .then(() => notify("已复制到剪贴板"))
    .catch(() => notify("复制失败，浏览器未授权剪贴板", true));
}

function Bar({
  label,
  value,
  budget,
  unit = "字符",
}: {
  label: string;
  value: number;
  budget: number | null;
  unit?: string;
}) {
  const ratio = budget && budget > 0 ? Math.min(value / budget, 1) : 0;
  const tight = ratio >= 0.95;
  return (
    <div className="min-w-0">
      <p className="flex justify-between gap-2 text-[11px] text-stone-500">
        <span className="truncate">{label}</span>
        <span className="shrink-0 tabular-nums text-stone-400">
          {value.toLocaleString()}
          {budget ? ` / ${budget.toLocaleString()} ${unit}` : ` ${unit}`}
        </span>
      </p>
      <div
        aria-hidden
        className="mt-1 h-1 w-full overflow-hidden rounded-full bg-stone-100"
      >
        <div
          className={cn(
            "h-full rounded-full",
            tight ? "bg-amber-500" : "bg-emerald-600/70",
          )}
          style={{ width: `${Math.max(ratio, value ? 0.02 : 0) * 100}%` }}
        />
      </div>
    </div>
  );
}

function Body({ text, className }: { text: string; className?: string }) {
  const parts = text.split(truncationMarker);
  return (
    <div className={cn("space-y-1 text-xs leading-6 text-stone-700", className)}>
      {parts.map((part, index) => (
        <span key={index}>
          {index > 0 && (
            <span className="my-1 block rounded bg-amber-50 px-2 py-1 text-[11px] text-amber-700">
              此处被截断：模型没看到中间内容，完整原文仍在来源消息里
            </span>
          )}
          <span className="whitespace-pre-wrap break-words">{part}</span>
        </span>
      ))}
    </div>
  );
}

function Panel({
  title,
  hint,
  action,
  children,
}: {
  title: ReactNode;
  hint?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <CardTitle className="flex items-center gap-2">{title}</CardTitle>
        <span className="text-[10px] text-stone-400">{hint}</span>
        {action}
      </CardHeader>
      <CardContent className="space-y-4 px-4 py-4 text-xs sm:px-5">
        {children}
      </CardContent>
    </Card>
  );
}

function Fold({
  label,
  hint,
  children,
  defaultOpen = false,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details open={defaultOpen} className="rounded-lg border border-stone-100">
      <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-stone-600">
        {label}
        {hint && <span className="ml-2 text-[11px] text-stone-400">{hint}</span>}
      </summary>
      <div className="border-t border-stone-100 px-3 py-3">{children}</div>
    </details>
  );
}

function Code({ text }: { text: string }) {
  return (
    <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-stone-50 p-3 font-mono text-[11px] leading-5 text-stone-700">
      {text}
    </pre>
  );
}

function InstructionRow({
  instruction,
  tone = "text-stone-700",
}: {
  instruction: InstructionJson;
  tone?: string;
}) {
  const parts = [
    instruction.title,
    instruction.description,
    instruction.context_summary,
    instruction.related_task_ref
      ? `批内引用 ${instruction.related_task_ref}`
      : instruction.related_task_id
        ? `待办 #${instruction.related_task_id}`
        : null,
    instruction.due_at ? `截止 ${dateText(instruction.due_at, true)}` : null,
    instruction.priority ? `优先级 ${instruction.priority}` : null,
    // resolution 只在核销时有意义；其他动作的默认值不能显示成“已完成”。
    instruction.action === "RESOLVE" && instruction.resolution
      ? `核销为 ${instruction.resolution === "cancelled" ? "取消" : "完成"}`
      : null,
  ].filter(Boolean);
  return (
    <li className={cn("leading-6", tone)}>
      <span className="mr-1.5 rounded bg-stone-100 px-1.5 py-0.5 text-[11px] font-medium">
        {actionNames[instruction.action] || instruction.action}
      </span>
      <span className="break-words">{parts.join(" · ") || "（空指令）"}</span>
    </li>
  );
}

function SegmentCard({ brief, index }: { brief: BriefMessage; index: number }) {
  return (
    <li className="rounded-lg border border-stone-100 bg-white p-3">
      <p className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-stone-500">
        <span className="rounded bg-emerald-50 px-1.5 py-0.5 font-medium text-emerald-800">
          index {index}
        </span>
        <span className="font-medium text-stone-600">{brief.sender_name}</span>
        {brief.is_self && (
          <span className="rounded bg-stone-100 px-1.5 py-0.5">本人</span>
        )}
        {brief.is_self && (
          <span
            className={cn(
              "rounded px-1.5 py-0.5",
              brief.identity_confident
                ? "bg-emerald-50 text-emerald-700"
                : "bg-stone-100 text-stone-500",
            )}
          >
            身份{brief.identity_confident ? "已确认" : "未确认"}
          </span>
        )}
        {brief.content_is_summary && (
          <span className="rounded bg-sky-50 px-1.5 py-0.5 text-sky-700">
            压缩摘要，非原文
          </span>
        )}
        <span className="ml-auto tabular-nums text-stone-400">
          {brief.created_at ? dateText(brief.created_at, true) : ""} ·{" "}
          {brief.content.length} 字符
        </span>
      </p>
      <Body text={brief.content} />
    </li>
  );
}

function Warning({ tone, children }: { tone: "amber" | "rose"; children: ReactNode }) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-lg border p-3 text-xs leading-6",
        tone === "amber"
          ? "border-amber-200 bg-amber-50 text-amber-800"
          : "border-rose-200 bg-rose-50 text-rose-800",
      )}
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <span className="min-w-0 break-words">{children}</span>
    </div>
  );
}

function LandingLine({
  label,
  tone,
  children,
}: {
  label: string;
  tone: "emerald" | "stone" | "amber";
  children: ReactNode;
}) {
  return (
    <li className="flex flex-wrap items-start gap-x-2 gap-y-1 border-b border-stone-100 pb-2 last:border-0">
      <span
        className={cn(
          "shrink-0 rounded px-1.5 py-0.5 text-[11px]",
          tone === "emerald" && "bg-emerald-50 text-emerald-800",
          tone === "amber" && "bg-amber-50 text-amber-800",
          tone === "stone" && "bg-stone-100 text-stone-600",
        )}
      >
        {label}
      </span>
      <span className="min-w-0 flex-1 break-words leading-6">{children}</span>
    </li>
  );
}

type Props = {
  callId: number;
  notify: Notify;
  onBack: () => void;
  onStep?: (delta: number) => void;
  stepLabel?: string;
};

export function ModelCallInspector({
  callId,
  notify,
  onBack,
  onStep,
  stepLabel,
}: Props) {
  const [call, setCall] = useState<CallDetail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setCall(null);
    api<CallDetail>(`/api/diagnostics/calls/${callId}`, { signal: controller.signal })
      .then(setCall)
      .catch((e: Error) => {
        if (e.name !== "AbortError") setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [callId]);

  if (loading) return <Loading />;
  if (error || !call)
    return (
      <ErrorNotice
        message={`诊断记录读取失败：${error}`}
        onRetry={() => {
          setError("");
          setLoading(true);
          api<CallDetail>(`/api/diagnostics/calls/${callId}`)
            .then(setCall)
            .catch((e: Error) => setError(e.message))
            .finally(() => setLoading(false));
        }}
      />
    );

  const sent = call.request_json;
  const system = sent?.messages.find((item) => item.role === "system")?.content || "";
  const userRaw = sent?.messages.find((item) => item.role === "user")?.content || "";
  let data: ModelData | null = null;
  try {
    data = JSON.parse(userRaw) as ModelData;
  } catch {
    data = null;
  }
  const segments: BriefMessage[] =
    data?.new_messages || (data?.new_message ? [data.new_message] : []);
  const stats = call.stats_json || {};
  const items = call.outcome_json?.items || [];
  const diagnostics = (call.outcome_json?.diagnostics || {}) as Record<
    string,
    unknown
  >;
  const indexLists = ["missing_indexes", "rejected_indexes", "duplicate_indexes"]
    .map((key) => `${key}:${JSON.stringify(diagnostics[key] || [])}`)
    .join("|");
  const missing = diagnostics.missing_indexes as number[] | undefined;
  const rejected = diagnostics.rejected_indexes as number[] | undefined;
  const duplicates = diagnostics.duplicate_indexes as number[] | undefined;
  const usage = call.usage_json || null;
  const failed = call.status === "error";
  const truncated = call.finish_reason === "length";
  const outputRatio = call.max_tokens ? Math.min(call.output_chars / call.max_tokens, 1) : 0;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="size-4" />
          返回
        </Button>
        {onStep && (
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" onClick={() => onStep(-1)}>
              <ChevronLeft className="size-4" />
            </Button>
            <span className="text-[11px] text-stone-400">{stepLabel}</span>
            <Button variant="ghost" size="icon" onClick={() => onStep(1)}>
              <ChevronRight className="size-4" />
            </Button>
          </div>
        )}
        <p className="ml-auto text-[11px] text-stone-400">
          记录 #{call.id} · 发起于 {dateText(call.started_at, true)}
        </p>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-center gap-x-5 gap-y-3 px-4 py-3.5 text-xs sm:px-5">
          <span className="flex items-center gap-1.5 font-medium text-stone-700">
            <Sparkles className="size-3.5 text-stone-400" />
            {kindNames[call.kind] || call.kind}
          </span>
          <span className="text-stone-500">{call.session_name}</span>
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-[11px]",
              failed
                ? "bg-rose-50 text-rose-700"
                : "bg-emerald-50 text-emerald-700",
            )}
          >
            {failed ? `请求失败：${call.error}` : "请求成功"}
          </span>
          {truncated && (
            <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-800">
              finish_reason=length
            </span>
          )}
          <span className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-2 text-[11px] text-stone-500">
            <span>模型 {call.model || "—"}</span>
            <span>温度 {call.temperature ?? "—"}</span>
            <span>思考 关闭</span>
            <span>
              段落 {call.segments} · 输入 {call.input_chars.toLocaleString()} 字符
            </span>
            <span>耗时 {secondsText(call.duration_seconds)}</span>
            {usage?.total_tokens !== undefined && (
              <span>
                tokens {usage.prompt_tokens ?? "?"}+{usage.completion_tokens ?? "?"}=
                {usage.total_tokens}
              </span>
            )}
          </span>
        </CardContent>
      </Card>

      {failed && (
        <Warning tone="rose">
          这次请求没有拿到可用结果（{call.error}
          {call.error_detail ? ` · ${call.error_detail}` : ""}
          ）。对应段落保持未处理，下一轮会重新请求；不会退回本地规则兜底。
        </Warning>
      )}
      {truncated && (
        <Warning tone="amber">
          模型输出撞到 max_tokens={call.max_tokens ?? "—"}
          上限被截断，下面的 JSON 可能不完整。这是解析失败常见原因：调高“提炼输出长度”或缩小每段正文。
        </Warning>
      )}
      {!failed && !truncated && outputRatio >= 0.9 && (
        <Warning tone="amber">
          输出已用到上限的 {Math.round(outputRatio * 100)}%（约{" "}
          {call.output_chars.toLocaleString()} 字符 / {call.max_tokens ?? "?"} token
          ），再多几条要求就可能被截断。
        </Warning>
      )}

      <div className="grid items-start gap-5 xl:grid-cols-2">
        <Panel
          title={
            <>
              <FileText className="size-4 text-stone-400" />
              发给模型的内容
            </>
          }
          hint={
            <span>
              system {stats.system_chars?.toLocaleString() ?? 0} + user{" "}
              {stats.payload_chars?.toLocaleString() ?? 0} 字符
            </span>
          }
          action={
            <Button
              variant="ghost"
              size="sm"
              onClick={() => copyText(JSON.stringify(sent, null, 2), notify)}
            >
              <Copy className="size-3.5" />
              复制完整请求
            </Button>
          }
        >
          {call.kind === "compress" ? (
            <div className="space-y-3">
              <Bar
                label="待压缩原文"
                value={stats.source_chars ?? (data?.source_text?.length || 0)}
                budget={null}
              />
              <Bar
                label="摘要目标长度"
                value={data?.max_summary_chars || 0}
                budget={null}
              />
              <Fold label="完整原文（压缩输入）" hint="压缩不做截断">
                <Body text={data?.source_text || ""} />
              </Fold>
            </div>
          ) : (
            <>
              <div>
                <p className="mb-2 text-[11px] tracking-wide text-stone-400">
                  本次要求判断的新段落（{segments.length} 段）
                </p>
                <ul className="space-y-2">
                  {segments.map((brief, index) => (
                    <SegmentCard key={index} brief={brief} index={index} />
                  ))}
                </ul>
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <Bar
                  label="历史参考"
                  value={stats.history_chars ?? 0}
                  budget={stats.history_budget ?? null}
                />
                <Bar
                  label="已有待办参考"
                  value={stats.tasks_chars ?? 0}
                  budget={stats.tasks_budget ?? null}
                />
                <Bar label="个人画像" value={stats.profile_chars ?? 0} budget={null} />
              </div>
              <Fold
                label={`同会话历史 ${stats.history_sent ?? data?.history?.length ?? 0} 条`}
                hint={`候选 ${stats.history_candidates ?? 0} / 可用 ${stats.history_available ?? 0} 条，放不下整条省略`}
              >
                {data?.history?.length ? (
                  <ul className="space-y-2">
                    {data.history.map((brief, index) => (
                      <SegmentCard key={index} brief={brief} index={index} />
                    ))}
                  </ul>
                ) : (
                  <p className="text-stone-400">模型这次没有拿到任何历史消息。</p>
                )}
              </Fold>
              <Fold
                label={`已有待办 ${data?.pending_tasks?.length ?? 0} 项`}
                hint={`会话内未完成 ${stats.tasks_available ?? 0} 项，按相关度取入`}
              >
                {data?.pending_tasks?.length ? (
                  <ul className="flex flex-wrap gap-1.5">
                    {data.pending_tasks.map((task) => (
                      <li
                        key={task.id}
                        className="rounded bg-stone-50 px-2 py-1 text-[11px] text-stone-600"
                      >
                        #{task.id} {task.title}
                        {task.priority ? ` · ${task.priority}` : ""}
                        {task.due_at ? ` · ${dateText(task.due_at, true)}` : ""}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-stone-400">没有给模型任何已有待办。</p>
                )}
              </Fold>
              <Fold
                label="个人画像"
                hint={`${stats.profile_chars ?? data?.user_profile?.length ?? 0} 字符，完整提供`}
              >
                {data?.user_profile?.trim() ? (
                  <Body text={data.user_profile} />
                ) : (
                  <p className="text-stone-400">
                    画像为空：模型不能据此判断本人角色与参与意愿。
                  </p>
                )}
              </Fold>
            </>
          )}
          <Fold label="系统指令全文" hint="决定模型判断口径">
            <Body text={system} />
          </Fold>
          <Fold label="原始请求体（发出去的 JSON）" hint="与 user 正文逐字一致">
            <Code text={JSON.stringify(sent, null, 2)} />
          </Fold>
        </Panel>

        <Panel
          title={
            <>
              <Sparkles className="size-4 text-stone-400" />
              模型回了什么
            </>
          }
          hint={
            <span>
              {call.output_chars.toLocaleString()} 字符 ·{" "}
              {call.finish_reason || "无 finish_reason"}
            </span>
          }
          action={
            <Button
              variant="ghost"
              size="sm"
              onClick={() => copyText(call.response_text || "", notify)}
            >
              <Copy className="size-3.5" />
              复制原始回复
            </Button>
          }
        >
          {call.response_text ? (
            <>
              <div>
                <p className="mb-1.5 text-[11px] tracking-wide text-stone-400">
                  原始文本（未去代码围栏，逐字保留）
                </p>
                <Code text={call.response_text} />
              </div>
              <div>
                <p className="mb-1.5 text-[11px] tracking-wide text-stone-400">
                  解析后的 JSON
                </p>
                <Code text={JSON.stringify(call.parsed_json, null, 2)} />
              </div>
            </>
          ) : (
            <p className="text-stone-400">
              没有任何返回文本：请求在拿到内容前就失败了{call.error_detail ? `（${call.error_detail}）` : ""}。
            </p>
          )}
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-stone-500">
            <span>
              usage：
              {usage
                ? `输入 ${usage.prompt_tokens ?? "—"} · 输出 ${usage.completion_tokens ?? "—"} · 合计 ${usage.total_tokens ?? "—"}`
                : "该提供方未返回 usage"}
            </span>
            <span>
              输出上限 {call.max_tokens ?? "—"} token
              {call.max_tokens
                ? ` · 已用约 ${Math.round(outputRatio * 100)}%（按字符粗估）`
                : ""}
            </span>
          </div>
        </Panel>
      </div>

      <Panel
        title={
          <>
            <Scissors className="size-4 text-stone-400" />
            校验改写与落库结果
          </>
        }
        hint={
          <span>
            模型说的 vs 业务校验后写进待办的；未返回索引{" "}
            {missing?.length ?? 0} · 结构不符 {rejected?.length ?? 0} · 索引重复{" "}
            {duplicates?.length ?? 0}
          </span>
        }
      >
        {call.kind === "compress" ? (
          <div className="space-y-3">
            {items[0]?.summary ? (
              <>
                <p className="text-[11px] text-stone-400">
                  摘要 {items[0].summary.length} 字符 · complete 自报为真 ·
                  原文 {(stats.source_chars ?? 0).toLocaleString()} 字符
                </p>
                <Body text={items[0].summary} />
              </>
            ) : (
              <Warning tone="amber">
                压缩结果未通过完整性校验（超预算、不完整或长度不小于原文），
                本段没有进入提炼请求，消息保留未处理。
              </Warning>
            )}
          </div>
        ) : (
          <>
            {indexLists && (missing?.length || rejected?.length || duplicates?.length) ? (
              <p className="rounded bg-stone-50 px-3 py-2 text-[11px] leading-5 text-stone-500">
                批量索引覆盖：缺少 {JSON.stringify(missing || [])} · 结构不符拒绝{" "}
                {JSON.stringify(rejected || [])} · 重复{" "}
                {JSON.stringify(duplicates || [])}
                。被拒绝或缺失的段落本轮不写库，留到下轮重新请求。
              </p>
            ) : null}
            <ul className="space-y-3">
              {items.map((item: OutcomeItem) => {
                const changed =
                  JSON.stringify(item.proposed) !== JSON.stringify(item.final);
                return (
                  <li
                    key={item.index}
                    className={cn(
                      "rounded-lg border p-3",
                      changed ? "border-amber-200 bg-amber-50/40" : "border-stone-100",
                    )}
                  >
                    <p className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-stone-500">
                      <span className="rounded bg-stone-100 px-1.5 py-0.5 font-medium text-stone-600">
                        index {item.index}
                      </span>
                      <span>来源消息 {item.message_ids.join("、") || "—"}</span>
                      {item.rejected && (
                        <span className="rounded bg-rose-50 px-1.5 py-0.5 text-rose-700">
                          结构不符合契约，整段留待下轮
                        </span>
                      )}
                      {item.note && (
                        <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-800">
                          {item.note}
                        </span>
                      )}
                      {changed && !item.rejected && (
                        <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-800">
                          业务校验改写了模型结论
                        </span>
                      )}
                    </p>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <div>
                        <p className="mb-1 text-[11px] tracking-wide text-stone-400">
                          模型返回
                        </p>
                        <ul className="space-y-1">
                          {(item.proposed || []).map((instruction, position) => (
                            <InstructionRow
                              key={position}
                              instruction={instruction}
                            />
                          ))}
                          {!item.proposed && (
                            <li className="text-stone-400">—（未通过结构校验）</li>
                          )}
                        </ul>
                      </div>
                      <div>
                        <p className="mb-1 text-[11px] tracking-wide text-stone-400">
                          校验后进入事务
                        </p>
                        <ul className="space-y-1">
                          {(item.final || []).map((instruction, position) => (
                            <InstructionRow
                              key={position}
                              instruction={instruction}
                              tone={changed ? "text-amber-800" : "text-stone-700"}
                            />
                          ))}
                          {!item.final && <li className="text-stone-400">—</li>}
                        </ul>
                      </div>
                    </div>
                    <Fold label="模型该段原始 JSON" hint="逐字">
                      <Code text={JSON.stringify(item.raw, null, 2)} />
                    </Fold>
                  </li>
                );
              })}
              {!items.length && (
                <li className="text-stone-400">
                  这次请求没有逐段对照记录：可能是请求失败，或返回整体不符合契约。
                </li>
              )}
            </ul>
            <div className="space-y-2 border-t border-stone-100 pt-3">
              <p className="text-[11px] tracking-wide text-stone-400">
                这些来源消息最终落成了什么
              </p>
              <ul className="space-y-2">
                {call.events.map((event, position) => (
                  <LandingLine
                    key={`event-${position}`}
                    label={actionNames[event.action] || event.action}
                    tone={event.action === "IGNORE" ? "stone" : "emerald"}
                  >
                    待办 #{event.task_id ?? "—"}
                    {event.title ? `「${event.title}」` : ""}
                    <span className="ml-1 text-stone-400">
                      {event.source === "llm" ? "模型判定" : "本地判定"} ·{" "}
                      {dateText(event.created_at, true)}
                    </span>
                  </LandingLine>
                ))}
                {call.ignored.map((row) => (
                  <LandingLine
                    key={`ignored-${row.id}`}
                    label="无需行动"
                    tone="stone"
                  >
                    {row.reason}
                    <span className="ml-1 text-stone-400">
                      {row.source === "llm" ? "模型判定" : "本地判定"} ·{" "}
                      {dateText(row.created_at, true)}
                    </span>
                  </LandingLine>
                ))}
                {!call.events.length && !call.ignored.length && (
                  <LandingLine label="未落库" tone="amber">
                    {call.messages.some((message) => !message.processed)
                      ? "来源消息仍是未处理：这次请求没有可用结论，下一轮重试。"
                      : "没有任务变更事件，也没有无需行动记录（可能被同批其他段落合并处理）。"}
                  </LandingLine>
                )}
              </ul>
            </div>
          </>
        )}
      </Panel>

      {call.kind !== "compress" && (
        <Panel
          title="原文对照：本机记录 vs 模型看到的正文"
          hint="压缩摘要丢了要求时，这里能直接比对出来"
        >
          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <p className="mb-2 text-[11px] tracking-wide text-stone-400">
                真实消息（{call.messages.length} 条）
              </p>
              <ul className="space-y-2">
                {call.messages.map((message) => (
                  <li key={message.id} className="rounded-lg border border-stone-100 p-3">
                    <p className="mb-1.5 flex flex-wrap gap-x-2 text-[11px] text-stone-500">
                      <span className="font-medium text-stone-600">
                        {message.sender_name}
                      </span>
                      {message.is_self && (
                        <span className="rounded bg-stone-100 px-1.5 py-0.5">本人</span>
                      )}
                      <span className="ml-auto tabular-nums text-stone-400">
                        #{message.id} · {dateText(message.created_at, true)} ·{" "}
                        {message.processed ? "已处理" : "未处理"}
                      </span>
                    </p>
                    <Body text={message.content} />
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="mb-2 text-[11px] tracking-wide text-stone-400">
                模型实际收到的正文
              </p>
              <ul className="space-y-2">
                {segments.map((brief, index) => (
                  <SegmentCard key={index} brief={brief} index={index} />
                ))}
                {!segments.length && (
                  <li className="text-stone-400">请求体解析失败，只有原始 JSON 可看。</li>
                )}
              </ul>
              {call.segment_summary && (
                <Fold label="该段落缓存的压缩摘要" hint="挂在来源末条 metadata">
                  <Body text={call.segment_summary} />
                </Fold>
              )}
            </div>
          </div>
        </Panel>
      )}
    </div>
  );
}
