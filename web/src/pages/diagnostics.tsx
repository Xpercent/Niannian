import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Copy,
  FileText,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { Button, Card, CardContent, CardHeader, CardTitle, cn } from "../components/ui";
import {
  api,
  dateText,
  type ExtractCallDetail,
  type ExtractRunDetail,
  type ExtractRunRow,
} from "../lib/api";

type Props = {
  /** 正在跑的那一轮的 id（App 从 `/api/extract/status` 拿），没有轮次在跑时为 null。 */
  liveRunId: number | null;
  /** 一轮从"在跑"变成"跑完"时 App bump 一下，这一页跟着重读，不然那行还停在"未结束"。 */
  revision: number;
  notify: (message: string, error?: boolean) => void;
};

type Tone = "stone" | "emerald" | "amber" | "rose";

function Pill({ children, tone = "stone" }: { children: ReactNode; tone?: Tone }) {
  return (
    <span
      className={cn(
        "rounded px-1.5 py-0.5 text-[11px]",
        tone === "stone" && "bg-stone-100 text-stone-600",
        tone === "emerald" && "bg-emerald-50 text-emerald-800",
        tone === "amber" && "bg-amber-50 text-amber-800",
        tone === "rose" && "bg-rose-50 text-rose-700",
      )}
    >
      {children}
    </span>
  );
}

/** 原始正文都收在这里：默认不看，点开就是逐字的内容。 */
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
      <CardContent className="space-y-3 px-4 py-4 text-xs sm:px-5">{children}</CardContent>
    </Card>
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

function durationText(ms: number) {
  return ms < 1000 ? `${ms} 毫秒` : `${(ms / 1000).toFixed(1)} 秒`;
}

/**
 * 回复里那段 JSON 缩进一下再给人看。**读不懂就交空串**，由界面按原样贴出来：
 * 半截 JSON 正是这一页最该看的形状，不能为了好看替服务商把括号补上。
 */
function prettyJson(text: string) {
  const body = text.trim().replace(/^```(?:json)?\s*([\s\S]*?)\s*```$/, "$1");
  try {
    const parsed = JSON.parse(body) as unknown;
    return parsed && typeof parsed === "object" ? JSON.stringify(parsed, null, 2) : "";
  } catch {
    return "";
  }
}

/** 一轮活了多久：没盖上结束时间就别说耗时，那一行本身在说的是"还没完"。 */
function runDuration(row: ExtractRunRow) {
  if (!row.finished_at) return "";
  const seconds = (new Date(row.finished_at).valueOf() - new Date(row.started_at).valueOf()) / 1000;
  return Number.isNaN(seconds) ? "" : `${seconds.toFixed(0)} 秒`;
}

function runState(row: ExtractRunRow, live: boolean): { text: string; tone: Tone } {
  if (live) return { text: "进行中", tone: "emerald" };
  if (!row.finished_at) return { text: "未结束", tone: "amber" };
  if (row.failed_batches) return { text: `有 ${row.failed_batches} 批失败`, tone: "rose" };
  return { text: "已完成", tone: "stone" };
}

/** 三次读取形状一样（列表 / 一轮 / 一次调用），差别只在 path，所以共用这一个出口。 */
function useJson<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(Boolean(path));

  useEffect(() => {
    if (!path) {
      setData(null);
      return;
    }
    const abort = new AbortController();
    setLoading(true);
    setError("");
    api<T>(path, { signal: abort.signal })
      .then(setData)
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") setError(reason.message || "读取失败");
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [path]);

  return { data, error, loading };
}

export function DiagnosticsPage({ liveRunId, revision, notify }: Props) {
  const [tick, setTick] = useState(0);
  const [runId, setRunId] = useState<number | null>(null);
  const [callId, setCallId] = useState<number | null>(null);
  // 一轮在跑时 4 秒重读一次：等整轮跑完才刷新，中间那几批看着就像什么都没发生
  useEffect(() => {
    if (liveRunId === null) return;
    const timer = setInterval(() => setTick((value) => value + 1), 4000);
    return () => clearInterval(timer);
  }, [liveRunId]);

  // 路径带上这两个数：App 一轮跑完 bump revision，这里跟着重读，那一行才不再是"未结束"
  const stamp = `${tick}.${revision}`;
  const runs = useJson<{ items: ExtractRunRow[] }>(`/api/diagnostics/runs?limit=30&stamp=${stamp}`);
  const rows = runs.data?.items ?? [];
  useEffect(() => {
    if (!rows.length) return;
    setRunId((current) =>
      current && rows.some((row) => row.id === current) ? current : rows[0].id,
    );
  }, [rows]);

  const detail = useJson<ExtractRunDetail>(
    runId ? `/api/diagnostics/runs/${runId}?stamp=${stamp}` : "",
  );
  const call = useJson<ExtractCallDetail>(
    callId ? `/api/diagnostics/calls/${callId}?stamp=${stamp}` : "",
  );
  const scope = (detail.data?.calls ?? []).map((row) => row.id);

  const copy = (text: string) =>
    navigator.clipboard
      .writeText(text)
      .then(() => notify("已复制到剪贴板"))
      .catch(() => notify("复制失败，浏览器未授权剪贴板", true));

  const loadFailure = runs.error || detail.error || call.error;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-[10px] font-medium tracking-[.2em] text-stone-400">
            EXTRACTION TRACE
          </p>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">提炼诊断</h1>
          <p className="mt-3 max-w-2xl text-sm text-stone-400">
            一轮一批一条：这里只看发出去的请求原文与模型的原样回复。
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          disabled={runs.loading}
          onClick={() => setTick((value) => value + 1)}
        >
          <RefreshCw className={cn("size-4", runs.loading && "animate-spin motion-reduce:animate-none")} />
          重新读取
        </Button>
      </div>

      {loadFailure && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          诊断数据读取失败：{loadFailure}
        </div>
      )}

      {runs.loading && !runs.data ? (
        <p className="p-6 text-sm text-stone-400">正在读取…</p>
      ) : !rows.length ? (
        <div className="rounded-xl border px-5 py-14 text-center">
          <p className="text-sm font-medium text-stone-600">还没有提炼轮次</p>
          <p className="mt-2 text-xs leading-6 text-stone-400">
            到待办页按「立即提炼」跑一轮，这里就会记下每一次请求发出的正文与模型回复的原文。
          </p>
        </div>
      ) : callId ? (
        <CallInspector
          call={call.data}
          loading={call.loading}
          position={scope.length > 1 ? `${scope.indexOf(callId) + 1} / ${scope.length}` : ""}
          onBack={() => setCallId(null)}
          onStep={(delta) => setCallId(scope[scope.indexOf(callId) + delta] ?? null)}
          onCopy={copy}
        />
      ) : (
        <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
          <Card className="min-w-0">
            <CardHeader>
              <CardTitle>轮次</CardTitle>
              <span className="text-[10px] text-stone-400">共 {rows.length} 轮</span>
            </CardHeader>
            <CardContent className="max-h-[70vh] space-y-2 overflow-y-auto px-3 py-3 sm:px-4">
              {rows.map((row) => {
                const state = runState(row, row.id === liveRunId);
                return (
                  <button
                    key={row.id}
                    type="button"
                    aria-current={row.id === runId ? "true" : undefined}
                    onClick={() => {
                      setRunId(row.id);
                      setCallId(null);
                    }}
                    className={cn(
                      "w-full rounded-lg border px-3 py-2.5 text-left text-xs transition-colors hover:bg-stone-50",
                      state.tone === "rose" ? "border-rose-200" : "border-stone-100",
                      row.id === runId && "border-emerald-300 bg-emerald-50/40",
                    )}
                  >
                    <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="tabular-nums font-medium text-stone-700">
                        {dateText(row.started_at, true)}
                      </span>
                      <Pill tone={state.tone}>{state.text}</Pill>
                      {row.id === runId && runDuration(row) && (
                        <span className="ml-auto tabular-nums text-stone-400">
                          {runDuration(row)}
                        </span>
                      )}
                    </p>
                    <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-stone-500">
                      <span>{row.total} 条消息</span>
                      <span className="text-stone-300">·</span>
                      <span>{row.calls} 次请求</span>
                      <span className="text-stone-300">·</span>
                      <span>建 {row.created} 条待办</span>
                      {!row.calls && <Pill>没发出请求</Pill>}
                    </p>
                    {row.error && (
                      <p className="mt-1 break-words text-[11px] text-rose-600">{row.error}</p>
                    )}
                  </button>
                );
              })}
            </CardContent>
          </Card>

          <Card className="min-w-0">
            <CardHeader className="flex-wrap gap-2">
              <CardTitle>这一轮的请求</CardTitle>
              <span className="text-[10px] text-stone-400">
                {detail.loading && !detail.data
                  ? "正在读取…"
                  : detail.data?.calls.length
                    ? "点一次请求看它发出去的正文与模型的回复"
                    : "这一轮没有发出模型请求"}
              </span>
            </CardHeader>
            <CardContent className="space-y-2 px-3 py-3 sm:px-4">
              {!detail.data?.calls.length && !detail.loading && (
                <p className="py-8 text-center text-xs text-stone-400">
                  这一轮没有发出模型请求：没有待提炼的消息，或者轮次在第一批之前就失败了。
                </p>
              )}
              {detail.data?.calls.map((row) => {
                const failed = row.status === "error";
                return (
                  <button
                    key={row.id}
                    type="button"
                    onClick={() => setCallId(row.id)}
                    className={cn(
                      "w-full rounded-lg border px-3 py-2.5 text-left text-xs transition-colors hover:bg-stone-50",
                      failed ? "border-rose-200 bg-rose-50/40" : "border-stone-100 bg-white",
                    )}
                  >
                    <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="tabular-nums text-stone-500">
                        {dateText(row.started_at, true)}
                      </span>
                      <Pill tone={failed ? "rose" : "emerald"}>{failed ? "失败" : "成功"}</Pill>
                      <span className="min-w-0 flex-1 truncate text-stone-600">
                        {row.session_name}
                      </span>
                      <span className="tabular-nums text-stone-400">
                        {durationText(row.duration_ms)}
                      </span>
                    </p>
                    <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-stone-500">
                      <span>{row.messages} 条消息</span>
                      <span className="text-stone-300">·</span>
                      <span className="tabular-nums">
                        请求 {row.request_chars.toLocaleString()} 字符
                      </span>
                      <span className="text-stone-300">·</span>
                      <span className="tabular-nums">
                        回复 {row.response_chars.toLocaleString()} 字符
                      </span>
                      {row.model && <Pill>{row.model}</Pill>}
                      {failed && <Pill tone="rose">{row.error}</Pill>}
                    </p>
                  </button>
                );
              })}
            </CardContent>
          </Card>
        </div>
      )}

      <p className="pb-2 text-[11px] leading-6 text-stone-400">
        口径说明：本机 <code className="rounded bg-stone-100 px-1">extract_runs</code> 与{" "}
        <code className="rounded bg-stone-100 px-1">model_calls</code> 两张表逐次保存发出去的
        正文与模型原样回复，只留最近 30 轮；请求失败那批也在里面，且不带任何额度与统计口径。
        服务商地址与 API Key 不入库、也不从这里下发。
      </p>
    </div>
  );
}

function CallInspector({
  call,
  loading,
  position,
  onBack,
  onStep,
  onCopy,
}: {
  call: ExtractCallDetail | null;
  loading: boolean;
  position: string;
  onBack: () => void;
  onStep: (delta: number) => void;
  onCopy: (text: string) => void;
}) {
  if (loading && !call) return <p className="p-6 text-sm text-stone-400">正在读取…</p>;
  if (!call) return <p className="p-6 text-sm text-stone-400">这条调用记录读不到了。</p>;

  const failed = call.status === "error";
  const system = call.request.find((item) => item.role === "system")?.content ?? "";
  const user = call.request.find((item) => item.role === "user")?.content ?? "";
  const pretty = prettyJson(call.response);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="size-4" />
          返回
        </Button>
        {position && (
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" aria-label="上一次请求" onClick={() => onStep(-1)}>
              <ChevronLeft className="size-4" />
            </Button>
            <span className="text-[11px] text-stone-400">{position}</span>
            <Button variant="ghost" size="icon" aria-label="下一次请求" onClick={() => onStep(1)}>
              <ChevronRight className="size-4" />
            </Button>
          </div>
        )}
        <p className="ml-auto text-[11px] text-stone-400">
          记录 #{call.id} · 发起于 {dateText(call.started_at, true)}
        </p>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3.5 text-xs sm:px-5">
          <Head label="会话" value={call.session_name || call.session_id} />
          <Head label="本批消息" value={`${call.messages} 条`} />
          <Head label="模型" value={call.model || "—"} />
          <Head label="耗时" value={durationText(call.duration_ms)} />
          <span>
            <span className="text-[10px] text-stone-400">结果</span>
            <Pill tone={failed ? "rose" : "emerald"}>
              {failed ? `请求失败：${call.error}` : "请求成功"}
            </Pill>
          </span>
        </CardContent>
      </Card>

      <div className="grid items-start gap-5 xl:grid-cols-2">
        <Panel
          title={
            <>
              <FileText className="size-4 text-stone-400" />
              发给模型的内容
            </>
          }
          hint={`system ${system.length.toLocaleString()} + user ${user.length.toLocaleString()} 字符`}
          action={
            <Button variant="ghost" size="sm" onClick={() => onCopy(JSON.stringify(call.request, null, 2))}>
              <Copy className="size-3.5" />
              复制完整请求
            </Button>
          }
        >
          <Fold label="本批正文（XML）" hint="发出去的那份，尖括号已实体转义" defaultOpen>
            <Code text={user} />
          </Fold>
          <Fold label="系统提示词" hint="这一轮所有批共用那一份">
            <Code text={system} />
          </Fold>
          <Fold label="原始请求体（JSON）" hint="逐字">
            <Code text={JSON.stringify(call.request, null, 2)} />
          </Fold>
        </Panel>

        <Panel
          title={
            <>
              <Sparkles className="size-4 text-stone-400" />
              模型回了什么
            </>
          }
          hint={`${call.response.length.toLocaleString()} 字符${failed ? " · 这次请求失败" : ""}`}
          action={
            <Button variant="ghost" size="sm" onClick={() => onCopy(call.response)}>
              <Copy className="size-3.5" />
              复制原始回复
            </Button>
          }
        >
          {call.response ? (
            <div>
              <p className="mb-1.5 text-[11px] tracking-wide text-stone-400">
                {pretty ? "回复的 JSON（缩进显示）" : "原始文本（读不出完整 JSON，按原样贴出）"}
              </p>
              <Code text={pretty || call.response} />
              {pretty ? (
                <Fold label="原始回复（逐字）" hint="库里存的就是这一份，缩进只是显示">
                  <Code text={call.response} />
                </Fold>
              ) : (
                <p className="mt-2 text-[11px] leading-5 text-amber-700">
                  这份回复读不出 JSON——半截 JSON 正是整批失败的常见形状，
                  所以这里不缩进、不加工，看到的就是服务商回的那串字。
                </p>
              )}
            </div>
          ) : (
            <p className="text-stone-400">
              没有任何返回文本：请求在拿到内容前就失败了{failed ? `（${call.error}）` : ""}。
            </p>
          )}
          {failed && (
            <p className="break-words rounded-lg bg-rose-50 px-3 py-2 text-[11px] leading-5 text-rose-700">
              {call.error}
            </p>
          )}
          {call.reasoning && (
            <Fold label="思考内容" hint="服务商回多少存多少">
              <Code text={call.reasoning} />
            </Fold>
          )}
        </Panel>
      </div>
    </div>
  );
}
