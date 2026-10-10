import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  CheckCircle2,
  ChevronRight,
  Filter,
  Leaf,
  Loader2,
  Menu,
  MessageCircle,
  RefreshCw,
  Settings,
  ShieldCheck,
  UserRound,
  X,
  AlertCircle,
} from "lucide-react";
import { Button } from "./components/ui";
import { ErrorNotice } from "./components/common";
import { TaskDrawer } from "./components/task-drawer";
import { TasksPage } from "./pages/tasks";
import { SessionsPage } from "./pages/sessions";
import RulesPage from "./pages/rules";
import ProfilePage from "./pages/profile";
import SettingsPage from "./pages/settings";
import MetricsPage from "./pages/metrics";
import { api, cn, json } from "./lib/utils";
import type { Overview, Notify } from "./lib/types";

const navigation = [
  { id: "tasks", label: "待办事项", icon: CheckCircle2 },
  { id: "sessions", label: "微信会话", icon: MessageCircle },
  { id: "metrics", label: "处理状态", icon: Activity },
  { id: "rules", label: "过滤规则", icon: Filter },
  { id: "profile", label: "我的画像", icon: UserRound },
  { id: "settings", label: "设置与数据", icon: Settings },
] as const;
type View = (typeof navigation)[number]["id"];
function currentView(): View {
  const hash = location.hash.slice(1);
  return navigation.find((item) => item.id === hash)?.id || "tasks";
}

export function App() {
  const [view, setView] = useState<View>(currentView);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [revision, setRevision] = useState(0);
  const [taskId, setTaskId] = useState<number | null>(null);
  const [mobileNav, setMobileNav] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(
    null,
  );
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const mounted = useRef(true);
  const notify: Notify = useCallback((text, error = false) => {
    clearTimeout(noticeTimer.current);
    setNotice({ text, error });
    noticeTimer.current = setTimeout(
      () => setNotice(null),
      error ? 7000 : 4200,
    );
  }, []);
  const refresh = useCallback(async () => {
    const data = await api<Overview>("/api/overview");
    if (mounted.current) {
      setOverview(data);
      setError("");
      setRevision((value) => value + 1);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let previousRun = "";
    const poll = async () => {
      let delay = 12000;
      try {
        const data = await api<Overview>("/api/overview");
        if (!active) return;
        setOverview(data);
        setError("");
        const marker = data.run
          ? `${data.run.id}:${data.run.status}:${data.run.done}`
          : "";
        if (previousRun && previousRun !== marker)
          setRevision((value) => value + 1);
        previousRun = marker;
        if (
          data.run &&
          ["running", "queued", "pending"].includes(data.run.status)
        )
          delay = 2500;
      } catch (e) {
        if (active) setError((e as Error).message);
      }
      if (active) timer = setTimeout(() => void poll(), delay);
    };
    void poll();
    return () => {
      active = false;
      mounted.current = false;
      clearTimeout(timer);
      clearTimeout(noticeTimer.current);
    };
  }, []);
  useEffect(() => {
    const change = () => {
      setView(currentView());
      setMobileNav(false);
    };
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);

  const connected = ["running", "connected", "listening", "active"].includes(
    overview?.wechat.state || "",
  );
  const run = overview?.run;
  const running = Boolean(
    run && ["running", "queued", "pending"].includes(run.status),
  );
  const process = async () => {
    if (busy || running) return;
    setBusy(true);
    try {
      await api("/api/process", json({}));
      await refresh();
      notify("提炼请求已提交");
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const reload = async () => {
    setRefreshing(true);
    try {
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  };
  const activeNav = navigation.find((item) => item.id === view)!;

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
        <a href="#tasks" className="mb-10 flex items-center gap-3 px-3">
          <span className="flex size-10 items-center justify-center rounded-xl bg-primary text-white">
            <Leaf className="size-5" />
          </span>
          <span>
            <strong className="block text-xl tracking-widest">拾光</strong>
            <span className="block text-[9px] tracking-[.2em] text-stone-400">
              SHIGUANG
            </span>
          </span>
        </a>
        <p className="mb-3 px-3 text-[10px] tracking-widest text-stone-400">
          个人工作空间
        </p>
        <nav aria-label="主导航" className="space-y-1">
          {navigation.map(({ id, label, icon: Icon }) => (
            <a
              key={id}
              href={`#${id}`}
              onClick={() => setMobileNav(false)}
              aria-current={id === view ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-3 text-sm text-stone-500 hover:bg-stone-50",
                id === view &&
                  "bg-emerald-50 font-medium text-emerald-800 hover:bg-emerald-50",
              )}
            >
              <Icon className="size-[18px]" />
              <span>{label}</span>
              {id === "tasks" && (
                <span className="ml-auto text-xs tabular-nums text-emerald-700">
                  {overview?.stats.pending ?? "—"}
                </span>
              )}
            </a>
          ))}
        </nav>
        <div className="mt-auto pt-8">
          <div className="rounded-xl border bg-stone-50/70 p-3.5">
            <p className="flex items-center gap-2 text-xs font-medium">
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  connected ? "bg-emerald-500" : "bg-stone-400",
                )}
              />
              {connected ? "微信已连接" : "微信未连接"}
            </p>
            <p className="mt-2 break-words text-[11px] leading-5 text-stone-400">
              {overview?.wechat.error ||
                (connected
                  ? `${overview?.stats.monitored} 个会话正在关注`
                  : "在设置中管理微信连接")}
            </p>
          </div>
          <p className="mt-4 flex items-center justify-center gap-1.5 text-[10px] text-stone-400">
            <ShieldCheck className="size-3" />
            本地数据 · 只读微信
          </p>
        </div>
      </aside>

      <main className="flex min-h-screen flex-col md:ml-56">
        <header className="flex h-[68px] shrink-0 items-center justify-between border-b px-5 lg:px-10">
          <div className="flex items-center gap-2 text-xs">
            <Button
              size="icon"
              variant="ghost"
              className="md:hidden"
              onClick={() => setMobileNav(true)}
              aria-label="打开导航"
            >
              <Menu />
            </Button>
            <span className="hidden text-stone-400 sm:inline">
              个人工作空间
            </span>
            <ChevronRight className="hidden size-3 text-stone-300 sm:inline" />
            <span>{activeNav.label}</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden items-center gap-2 text-[11px] text-stone-400 sm:flex">
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  connected ? "bg-emerald-500" : "bg-stone-400",
                )}
              />
              {connected ? "微信同步已开启" : "等待微信连接"}
            </span>
            <Button
              variant="ghost"
              size="icon"
              disabled={refreshing}
              onClick={() => void reload()}
              aria-label="刷新数据"
            >
              <RefreshCw
                className={cn(
                  "size-4 text-stone-500",
                  refreshing && "animate-spin motion-reduce:animate-none",
                )}
              />
            </Button>
          </div>
        </header>
        <div className="mx-auto w-full max-w-[1500px] flex-1 p-5 lg:p-10">
          {error && (
            <div className="mb-6">
              <ErrorNotice
                message={`服务读取失败：${error}`}
                onRetry={() => void reload()}
              />
            </div>
          )}
          {run && (running || run.status === "failed") && (
            <div
              role="status"
              className="mb-6 flex flex-wrap items-center gap-3 rounded-lg border border-emerald-200 bg-emerald-50/80 p-4 text-xs text-emerald-800"
            >
              {running ? (
                <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
              ) : (
                <AlertCircle className="size-4" />
              )}
              <div className="min-w-0 flex-1">
                <p>
                  {running
                    ? run.session
                      ? `正在提炼 · ${run.session}`
                      : "正在从真实消息中提炼待办"
                    : `提炼未完成：${run.error || "请重试"}`}
                </p>
                {running && (
                  <progress
                    className="mt-2 h-1 w-full accent-emerald-700"
                    value={run.done || 0}
                    max={Math.max(1, run.total || 0)}
                  />
                )}
              </div>
              <span>
                {run.done || 0} / {run.total || 0}
              </span>
              {running && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      await api(
                        `/api/process/${encodeURIComponent(run.id)}/cancel`,
                        json({}),
                      );
                      await refresh();
                      notify("已请求取消，正在处理的请求会在安全边界退出");
                    } catch (e) {
                      notify((e as Error).message, true);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  取消
                </Button>
              )}
            </div>
          )}
          {view === "tasks" && (
            <TasksPage
              stats={overview?.stats}
              sessions={overview?.sessions || []}
              revision={revision}
              onOpen={setTaskId}
              onProcess={() => void process()}
              processDisabled={busy || running}
            />
          )}
          {view === "sessions" && (
            <SessionsPage
              sessions={overview?.sessions || []}
              onRefresh={refresh}
              notify={notify}
            />
          )}
          {view === "rules" && <RulesPage notify={notify} />}
          {view === "metrics" && <MetricsPage revision={revision} />}
          {view === "profile" && <ProfilePage notify={notify} />}
          {view === "settings" && (
            <SettingsPage
              connected={connected}
              onRefresh={refresh}
              notify={notify}
            />
          )}
        </div>
        <footer className="flex flex-wrap justify-between gap-2 px-5 pb-6 text-[10px] text-stone-400 lg:px-10">
          <span className="flex items-center gap-1.5">
            <Leaf className="size-3" />
            让消息流过，让事情留下。
          </span>
          <span>
            {overview
              ? `${overview.stats.messages} 条真实消息`
              : "正在读取真实数据"}{" "}
            · 本地工作空间
          </span>
        </footer>
      </main>
      <TaskDrawer
        taskId={taskId}
        onClose={() => setTaskId(null)}
        onSaved={refresh}
        notify={notify}
      />
      {notice && (
        <div
          role={notice.error ? "alert" : "status"}
          className={cn(
            "fixed bottom-5 right-5 z-[100] flex max-w-[calc(100vw-2.5rem)] items-start gap-3 rounded-lg border bg-white p-4 text-sm shadow-lg",
            notice.error
              ? "border-rose-200 text-rose-700"
              : "border-emerald-200 text-emerald-800",
          )}
        >
          {notice.error ? (
            <AlertCircle className="mt-0.5 size-4 shrink-0" />
          ) : (
            <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
          )}
          <span className="break-words">{notice.text}</span>
          <button onClick={() => setNotice(null)} aria-label="关闭提示">
            <X className="size-4" />
          </button>
        </div>
      )}
    </div>
  );
}
