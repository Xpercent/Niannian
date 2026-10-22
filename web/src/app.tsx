import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  CheckCircle2,
  Leaf,
  Loader2,
  MessageCircle,
  Menu,
  RefreshCw,
  Settings,
  X,
} from "lucide-react";
import { Button, cn } from "./components/ui";
import { TaskDrawer } from "./components/task-drawer";
import { SessionsPage } from "./pages/sessions";
import { DiagnosticsPage } from "./pages/diagnostics";
import { SettingsPage } from "./pages/settings";
import { TasksPage } from "./pages/tasks";
import {
  api,
  clockText,
  postJson,
  type ExtractStatus,
  type QuoteTarget,
  type SessionRow,
  type TaskStats,
  type WechatStatus,
} from "./lib/api";

// 待办事项排在最前并且是默认页：这个产品存在的理由就是"消息里剩下的那几件事"，
// 会话列表是它的来源，不是门面。
const NAV = [
  { hash: "#tasks", label: "待办事项", icon: CheckCircle2 },
  { hash: "#sessions", label: "微信会话", icon: MessageCircle },
  { hash: "#diagnostics", label: "提炼诊断", icon: Activity },
  { hash: "#settings", label: "设置", icon: Settings },
] as const;

function pageFromHash(): string {
  return NAV.some((item) => item.hash === location.hash) ? location.hash : "#tasks";
}

const stateLabels: Record<string, { text: string; tone: string }> = {
  connected: { text: "微信已连接", tone: "bg-emerald-600" },
  starting: { text: "连接中", tone: "bg-amber-500" },
  // 微信没开不算故障：采集器会自己等它，界面要说"等着"而不是"坏了"
  waiting_wechat: { text: "等待微信启动", tone: "bg-amber-500" },
  error: { text: "连接失败", tone: "bg-rose-500" },
  stopped: { text: "采集未运行", tone: "bg-stone-400" },
  stopping: { text: "正在停止", tone: "bg-stone-400" },
};

/** 顶部横条一次只说一件当下要做的事：等微信不是错误，读不到看板才是。 */
function bannerFor(status: WechatStatus | null, loadError: string) {
  if (status?.state === "waiting_wechat")
    return {
      tone: "border-amber-200 bg-amber-50 text-amber-800",
      text: "未连接到微信：打开微信后会自动接上，不用重启服务。已入库的历史消息仍可正常查看。",
    };
  if (status?.state === "error")
    return {
      tone: "border-rose-100 bg-rose-50 text-rose-700",
      text: `微信采集异常：${status.error || "未知错误"}`,
    };
  if (loadError)
    return {
      tone: "border-rose-100 bg-rose-50 text-rose-700",
      text: `看板数据读取失败：${loadError}`,
    };
  return null;
}

export function App() {
  const [status, setStatus] = useState<WechatStatus | null>(null);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [stats, setStats] = useState<TaskStats | null>(null);
  const [extract, setExtract] = useState<ExtractStatus | null>(null);
  const [loadError, setLoadError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [page, setPage] = useState<string>(() => pageFromHash());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<number | null>(null);
  const [revision, setRevision] = useState(0);
  const [busyProcess, setBusyProcess] = useState(false);
  /** 待办抽屉跳到会话页：这里只放"要去哪一条"，落地由 SessionsPage 用它那唯一的出口做。 */
  const [pendingJump, setPendingJump] = useState<{ session_id: string; target: QuoteTarget } | null>(null);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  /** 跑没跑完要在下一轮才看得出来，所以这两个记在 ref 上，不进渲染。 */
  const runningRef = useRef(false);
  const wasRunning = useRef(false);

  const notify = useCallback((text: string, error = false) => {
    clearTimeout(noticeTimer.current);
    setNotice({ text, error });
    noticeTimer.current = setTimeout(() => setNotice(null), error ? 7000 : 4200);
  }, []);

  const refresh = useCallback(async () => {
    const [nextStatus, nextSessions, nextStats, nextExtract] = await Promise.all([
      api<WechatStatus>("/api/status"),
      api<SessionRow[]>("/api/sessions"),
      api<TaskStats>("/api/tasks/stats"),
      api<ExtractStatus>("/api/extract/status"),
    ]);
    setStatus(nextStatus);
    setSessions(nextSessions);
    setStats(nextStats);
    setLoadError("");
    // 一轮从"在跑"变成"跑完"：待办列表与指标卡要立刻重读，不然人得等下一个 10 秒
    if (wasRunning.current && !nextExtract.running) setRevision((value) => value + 1);
    wasRunning.current = nextExtract.running;
    runningRef.current = nextExtract.running;
    setExtract(nextExtract);
  }, []);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        await refresh();
      } catch (error) {
        if (active) setLoadError((error as Error).message || "无法读取看板数据");
      }
      // 提炼在跑时 2.5 秒问一次：一轮十几批，10 秒一轮的刷新条根本看不出进度
      if (active) timer = setTimeout(() => void poll(), runningRef.current ? 2500 : 10000);
    };
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
      clearTimeout(noticeTimer.current);
    };
  }, [refresh]);

  useEffect(() => {
    const onHash = () => {
      setPage(pageFromHash());
      setMobileNav(false);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const indicator = stateLabels[status?.state || "stopped"] || {
    text: status?.state || "未知",
    tone: "bg-stone-400",
  };
  const banner = bannerFor(status, loadError);

  const reload = async () => {
    setRefreshing(true);
    try {
      await refresh();
      notify("已刷新");
    } catch (error) {
      notify((error as Error).message || "刷新失败", true);
    } finally {
      setRefreshing(false);
    }
  };

  /**
   * 起一轮提炼。接口只负责"起"，立刻回 200 与这一轮的初始状态——
   * 一轮十几批、每批最长等 180 秒，把请求挂着等人不可能。
   */
  const startRound = async () => {
    setBusyProcess(true);
    try {
      const got = await api<ExtractStatus>("/api/extract", postJson({ session_id: "" }));
      await refresh();
      notify(
        got.accepted
          ? `已起一轮提炼：${got.total} 条待处理`
          : "上一轮还在跑，这一轮没排队",
        got.accepted ? false : true,
      );
    } catch (error) {
      notify((error as Error).message || "起提炼失败", true);
    } finally {
      setBusyProcess(false);
    }
  };

  /**
   * 待办抽屉里点"到会话里看这句"：切页 + 选会话 + 留一个待跳转。
   *
   * 落地不是这里做的——会话页取屏只有一个出口（`fetchPage`），由它消费 `pendingJump`；
   * 自己在这里发一次请求，就会出现"上一个会话那一屏盖到刚选中的会话上"那个老 bug。
   */
  const openSource = (sessionId: string, target: QuoteTarget) => {
    if (!sessions.some((item) => item.id === sessionId)) {
      // 会话已经不再关注：/api/sessions 只列关注的会话，那边没有可选的会话可跳
      notify("这个会话已不再关注，只能在这里看依据原文。", true);
      return;
    }
    setTaskId(null);
    setSelectedId(sessionId);
    setPendingJump({ session_id: sessionId, target });
    location.hash = "#sessions";
    setPage("#sessions");
  };

  const running = Boolean(extract?.running);
  const extractFailed = !running && Boolean(extract?.error);
  // 消息都跑完了但这一轮还没结束，说明轮到轮末那次合并了（它自动接在第一轮后面）。
  const merging = Boolean(running && extract && extract.total > 0
    && extract.done >= extract.total);
  return (
    <div className="min-h-screen">
      {mobileNav && (
        <button
          aria-label="关闭导航"
          className="fixed inset-0 z-30 bg-stone-900/25 md:hidden"
          onClick={() => setMobileNav(false)}
        />
      )}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-56 flex-col border-r bg-white px-4 py-7 transition-transform md:translate-x-0",
          mobileNav ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="mb-10 flex items-center gap-3 px-3">
          <span className="flex size-10 items-center justify-center rounded-xl bg-primary text-white">
            <Leaf className="size-5" />
          </span>
          <span>
            <strong className="block text-xl tracking-widest">念念</strong>
            <span className="block text-[9px] tracking-[.2em] text-stone-400">
              NIANNIAN
            </span>
          </span>
        </div>
        <p className="mb-3 px-3 text-[10px] tracking-widest text-stone-400">
          个人工作空间
        </p>
        <nav aria-label="主导航" className="space-y-1">
          {NAV.map((item) => (
            <a
              key={item.hash}
              href={item.hash}
              aria-current={page === item.hash ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-3 text-sm font-medium",
                page === item.hash
                  ? "bg-emerald-50 text-emerald-800"
                  : "text-stone-600 hover:bg-stone-100",
              )}
            >
              <item.icon className="size-[18px]" />
              <span>{item.label}</span>
              {item.hash === "#tasks" && stats && (
                // 角标只说"还有几件没做完"，逾期与否留给页面里那张卡，别在导航上吓人
                <span className="ml-auto text-xs tabular-nums text-emerald-700">
                  {stats.pending}
                </span>
              )}
            </a>
          ))}
        </nav>
        <div className="mt-auto rounded-lg bg-stone-50 p-3 text-xs leading-5 text-stone-500">
          只读采集本机微信记录，不发消息、不改动微信数据。
          {status?.account ? (
            <span className="mt-1 block break-all text-stone-400">
              账号 {status.account}
            </span>
          ) : null}
        </div>
      </aside>
      <div className="md:pl-56">
        <header className="sticky top-0 z-20 flex flex-wrap items-center gap-3 border-b bg-white/90 px-4 py-3 backdrop-blur md:px-8">
          <button
            aria-label="打开导航"
            className="rounded-md p-2 text-stone-500 hover:bg-stone-100 md:hidden"
            onClick={() => setMobileNav(true)}
          >
            <Menu className="size-5" />
          </button>
          <span className="flex items-center gap-2 text-sm text-stone-600">
            <span className={cn("size-2 rounded-full", indicator.tone)} />
            {indicator.text}
            {status?.last_sync_at ? (
              <span className="text-xs text-stone-400">
                · 上次同步 {clockText(status.last_sync_at)}
              </span>
            ) : null}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              aria-label="刷新"
              disabled={refreshing}
              onClick={reload}
            >
              <RefreshCw
                className={cn("size-4", refreshing && "animate-spin motion-reduce:animate-none")}
              />
            </Button>
          </div>
        </header>
        <main className="px-4 py-6 md:px-8">
          {(running || extractFailed) && (
            <div
              role="status"
              className={cn(
                "mb-5 flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3 text-xs",
                running
                  ? "border-emerald-200 bg-emerald-50/80 text-emerald-800"
                  : "border-rose-100 bg-rose-50 text-rose-700",
              )}
            >
              {running ? (
                <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
              ) : (
                <X className="size-4" />
              )}
              <div className="min-w-0 flex-1">
                <p>
                  {running
                    ? merging
                      ? "消息都提炼完了，正在合并重复的待办"
                      : extract?.session
                        ? `正在提炼 · ${extract.session}`
                        : "正在从消息里提炼待办"
                    : `上一轮有 ${extract?.failed_batches ?? 0} 批没成功：${extract?.error || "未知原因"}`}
                </p>
                {running && (
                  <progress
                    className="mt-2 h-1 w-full accent-emerald-700"
                    value={extract?.done ?? 0}
                    max={Math.max(1, extract?.total ?? 0)}
                  />
                )}
                {extractFailed && (
                  <p className="mt-1 leading-5 text-rose-500">
                    没成功的那几批<em className="font-medium not-italic underline decoration-rose-300">没有</em>被标记，下次点「立即提炼」还会再问一遍。
                  </p>
                )}
              </div>
              <span className="tabular-nums">
                {extract?.done ?? 0} / {extract?.total ?? 0}
              </span>
            </div>
          )}
          {banner && (
            <div
              role="alert"
              className={`mb-5 rounded-lg border px-4 py-3 text-sm ${banner.tone}`}
            >
              {banner.text}
            </div>
          )}
          {page === "#tasks" ? (
            <TasksPage
              stats={stats}
              sessions={sessions}
              revision={revision}
              onOpen={setTaskId}
              onProcess={() => void startRound()}
              processDisabled={busyProcess || running}
            />
          ) : page === "#settings" ? (
            <SettingsPage notify={notify} />
          ) : page === "#diagnostics" ? (
            <DiagnosticsPage
              liveRunId={running ? extract?.run_id ?? null : null}
              revision={revision}
              notify={notify}
            />
          ) : (
            <SessionsPage
              sessions={sessions}
              onRefresh={refresh}
              notify={notify}
              selectedId={selectedId}
              onSelect={setSelectedId}
              jumpTo={pendingJump}
              onJumped={() => setPendingJump(null)}
            />
          )}
        </main>
      </div>
      <TaskDrawer
        taskId={taskId}
        onClose={() => setTaskId(null)}
        onSaved={() => {
          setRevision((value) => value + 1);
          void refresh();
        }}
        notify={notify}
        onOpenSource={openSource}
      />
      {notice && (
        <div
          role="status"
          className={cn(
            "fixed bottom-6 right-6 z-50 max-w-sm rounded-lg border px-4 py-3 text-sm shadow-lg",
            notice.error
              ? "border-rose-200 bg-rose-50 text-rose-700"
              : "border-emerald-200 bg-emerald-50 text-emerald-800",
          )}
        >
          {notice.text}
          <button
            aria-label="关闭提示"
            className="ml-3 inline-flex align-middle text-current/60 hover:text-current"
            onClick={() => setNotice(null)}
          >
            <X className="size-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}
