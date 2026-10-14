import { useCallback, useEffect, useRef, useState } from "react";
import { Leaf, MessageCircle, Menu, RefreshCw, Settings, X } from "lucide-react";
import { Button, cn } from "./components/ui";
import { SessionsPage } from "./pages/sessions";
import { SettingsPage } from "./pages/settings";
import { api, clockText, type SessionRow, type WechatStatus } from "./lib/api";

const NAV = [
  { hash: "#sessions", label: "微信会话", icon: MessageCircle },
  { hash: "#settings", label: "设置", icon: Settings },
] as const;

function pageFromHash(): string {
  return NAV.some((item) => item.hash === location.hash) ? location.hash : "#sessions";
}

const stateLabels: Record<string, { text: string; tone: string }> = {
  connected: { text: "微信已连接", tone: "bg-emerald-600" },
  starting: { text: "连接中", tone: "bg-amber-500" },
  error: { text: "连接失败", tone: "bg-rose-500" },
  stopped: { text: "采集未运行", tone: "bg-stone-400" },
  stopping: { text: "正在停止", tone: "bg-stone-400" },
};

export function App() {
  const [status, setStatus] = useState<WechatStatus | null>(null);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [loadError, setLoadError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [page, setPage] = useState<string>(() => pageFromHash());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const notify = useCallback((text: string, error = false) => {
    clearTimeout(noticeTimer.current);
    setNotice({ text, error });
    noticeTimer.current = setTimeout(() => setNotice(null), error ? 7000 : 4200);
  }, []);

  const refresh = useCallback(async () => {
    const [nextStatus, nextSessions] = await Promise.all([
      api<WechatStatus>("/api/status"),
      api<SessionRow[]>("/api/sessions"),
    ]);
    setStatus(nextStatus);
    setSessions(nextSessions);
    setLoadError("");
  }, []);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        if (active) await refresh();
      } catch (error) {
        if (active) setLoadError((error as Error).message || "无法读取看板数据");
      }
      if (active) timer = setTimeout(() => void poll(), 10000);
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
          {(loadError || (status && status.state === "error")) && (
            <div
              role="alert"
              className="mb-5 rounded-lg border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-700"
            >
              {status?.state === "error"
                ? `微信采集异常：${status.error || "未知错误"}`
                : `看板数据读取失败：${loadError}`}
            </div>
          )}
          {page === "#settings" ? (
            <SettingsPage notify={notify} />
          ) : (
            <SessionsPage
              sessions={sessions}
              onRefresh={refresh}
              notify={notify}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
          )}
        </main>
      </div>
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
