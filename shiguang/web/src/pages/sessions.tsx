import { useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  Download,
  Filter,
  Loader2,
  MessageSquare,
  Plus,
  RefreshCw,
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  Input,
} from "../components/ui";
import { Message } from "../components/message";
import { api, dateText, json, patchJson } from "../lib/utils";
import type {
  MessagePage,
  Message as MessageRecord,
  Session,
} from "../lib/types";

type CatalogSession = {
  id: string;
  name?: string;
  username?: string;
  account?: string;
  monitored?: boolean | number;
};
type Ignored = {
  id: number;
  reason?: string;
  created_at?: string;
  messages?: MessageRecord[];
};
type Props = {
  sessions: Session[];
  onRefresh: () => Promise<void>;
  notify: (message: string, error?: boolean) => void;
};

export function SessionsPage({ sessions, onRefresh, notify }: Props) {
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<"messages" | "ignored">("messages");
  const [messages, setMessages] = useState<MessageRecord[]>([]);
  const [ignored, setIgnored] = useState<Ignored[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [beforeId, setBeforeId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState("");
  const busyRef = useRef(false);
  const [historyLimit, setHistoryLimit] = useState("100");
  const historyEdited = useRef(false);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [catalog, setCatalog] = useState<CatalogSession[]>([]);
  const [catalogSearch, setCatalogSearch] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const requestSeq = useRef(0);
  const catalogSeq = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const current = useRef({ id: selectedId, tab });
  current.current = { id: selectedId, tab };

  const selected = sessions.find((session) => session.id === selectedId);
  const visibleSessions = useMemo(
    () =>
      sessions.filter((session) =>
        (session.name || session.id)
          .toLocaleLowerCase()
          .includes(query.toLocaleLowerCase()),
      ),
    [sessions, query],
  );

  async function loadConversation(id: string, older = false) {
    const seq = ++requestSeq.current;
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    const mode = current.current.tab;
    setLoading(true);
    setLoadError("");
    if (!older) {
      setMessages([]);
      setIgnored([]);
      setHasMore(false);
    }
    try {
      const path = `/api/sessions/${encodeURIComponent(id)}`;
      if (mode === "ignored") {
        const rows = await api<Ignored[]>(`${path}/ignored`, {
          signal: abort.signal,
        });
        if (seq === requestSeq.current) setIgnored(rows);
      } else {
        const cursor =
          older && beforeId != null ? `&before_id=${beforeId}` : "";
        const page = await api<MessagePage>(
          `${path}/messages?limit=40${cursor}`,
          { signal: abort.signal },
        );
        if (seq === requestSeq.current) {
          setMessages((old) => {
            const next = page.items.slice().reverse();
            const seen = new Set(old.map((item) => item.id));
            return older
              ? [...next.filter((item) => !seen.has(item.id)), ...old]
              : next;
          });
          setBeforeId(page.before_id);
          setHasMore(page.has_more);
        }
      }
    } catch (error) {
      if (
        (error as Error).name !== "AbortError" &&
        seq === requestSeq.current
      ) {
        setLoadError((error as Error).message || "读取会话失败");
      }
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }

  useEffect(() => {
    if (selectedId) void loadConversation(selectedId);
    else {
      setMessages([]);
      setIgnored([]);
    }
    return () => {
      requestSeq.current++;
      controller.current?.abort();
    };
    // Resource changes reload; session-count refreshes preserve the current reading position.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, tab]);

  useEffect(() => {
    if (selectedId && !selected) setSelectedId(null);
  }, [selected, selectedId]);

  useEffect(() => {
    let active = true;
    void api<{ history_limit?: number }>("/api/settings")
      .then((settings) => {
        if (active && !historyEdited.current && settings.history_limit)
          setHistoryLimit(String(settings.history_limit));
      })
      .catch((error: Error) => {
        if (active) notify(`读取历史导入上限失败：${error.message}`, true);
      });
    return () => {
      active = false;
      catalogSeq.current++;
    };
  }, []);

  async function action(label: string, fn: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(label);
    try {
      await fn();
    } catch (error) {
      notify((error as Error).message || "操作失败", true);
    } finally {
      busyRef.current = false;
      setBusy("");
    }
  }

  async function toggleMonitor(session: Session | CatalogSession) {
    await action(`monitor:${session.id}`, async () => {
      const monitored = !session.monitored;
      const path = `/api/sessions/${encodeURIComponent(session.id)}`;
      await api(path, patchJson({ monitored }));
      const saved = await api<Session[]>("/api/sessions");
      if (
        Boolean(saved.find((item) => item.id === session.id)?.monitored) !==
        monitored
      ) {
        throw new Error("会话关注状态未读回，请刷新后重试");
      }
      setCatalog((items) =>
        items.map((item) =>
          item.id === session.id ? { ...item, monitored } : item,
        ),
      );
      await onRefresh();
      notify(monitored ? "已关注该会话" : "已暂停提炼；该会话仍会采集新消息");
    });
  }

  async function openCatalog() {
    const seq = ++catalogSeq.current;
    setCatalogOpen(true);
    setCatalogLoading(true);
    setCatalogError("");
    setCatalog([]);
    try {
      const rows = await api<CatalogSession[]>("/api/wechat/catalog");
      if (seq === catalogSeq.current) setCatalog(rows);
    } catch (error) {
      if (seq === catalogSeq.current)
        setCatalogError((error as Error).message || "读取微信会话失败");
    } finally {
      if (seq === catalogSeq.current) setCatalogLoading(false);
    }
  }

  async function importHistory() {
    if (!selected) return;
    const limit = Number(historyLimit);
    if (!Number.isInteger(limit) || limit < 1 || limit > 1000) {
      notify("历史导入上限须为 1 到 1000 的整数", true);
      return;
    }
    const id = selected.id;
    await action("history", async () => {
      const result = await api<{ inserted?: number; imported?: number }>(
        `/api/sessions/${encodeURIComponent(id)}/history`,
        json({ limit }),
      );
      notify(
        `历史读取完成，新增 ${result.inserted ?? result.imported ?? 0} 条真实消息`,
      );
      await onRefresh();
      if (current.current.id === id) await loadConversation(id);
    });
  }

  async function markRead() {
    if (!selected) return;
    const id = selected.id;
    await action("read", async () => {
      await api(`/api/sessions/${encodeURIComponent(id)}/read`, json({}));
      await onRefresh();
      notify("会话已标记为已读");
    });
  }

  async function processSession() {
    if (!selected) return;
    await action("process", async () => {
      await api("/api/process", json({ session_id: selected.id }));
      await onRefresh();
      notify("提炼请求已提交，可在顶部查看当前轮次");
    });
  }

  const filteredCatalog = catalog.filter((item) =>
    (item.name || item.username || "")
      .toLocaleLowerCase()
      .includes(catalogSearch.toLocaleLowerCase()),
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">微信会话</h1>
          <p className="mt-1 text-sm text-stone-500">
            读取真实消息，选择需要提炼待办的来源。
          </p>
        </div>
        <Button onClick={openCatalog}>
          <Plus className="size-4" />
          添加关注
        </Button>
      </div>
      <div className="grid items-start gap-5 lg:grid-cols-[300px_minmax(0,1fr)]">
        <Card>
          <CardHeader>
            <CardTitle>消息来源</CardTitle>
            <Badge>{sessions.length}</Badge>
          </CardHeader>
          <CardContent className="space-y-3 p-3">
            <div className="relative">
              <Search className="absolute left-3 top-2.5 size-4 text-stone-400" />
              <Input
                aria-label="搜索会话"
                className="pl-9"
                placeholder="搜索会话"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
            <div className="max-h-[65vh] space-y-1 overflow-y-auto">
              {visibleSessions.map((session) => (
                <button
                  type="button"
                  key={session.id}
                  onClick={() => setSelectedId(session.id)}
                  className={`flex w-full items-center gap-3 rounded-md border p-3 text-left ${selectedId === session.id ? "border-emerald-200 bg-emerald-50" : "border-transparent hover:bg-stone-50"}`}
                >
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-stone-100 font-medium text-stone-700">
                    {(session.name || "会").slice(0, 1)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">
                      {session.name || session.id}
                    </span>
                    <span className="text-xs text-stone-400">
                      {session.message_count ?? 0} 条消息 ·{" "}
                      {session.task_count ?? 0} 项待办
                    </span>
                  </span>
                  {session.unread ? (
                    <Badge className="border-none bg-emerald-100 text-emerald-800">
                      {session.unread > 99 ? "99+" : session.unread}
                    </Badge>
                  ) : null}
                  {session.monitored ? (
                    <span
                      className="size-2 shrink-0 rounded-full bg-emerald-600"
                      aria-label="正在关注"
                    />
                  ) : null}
                </button>
              ))}
              {!visibleSessions.length && (
                <p className="py-8 text-center text-sm text-stone-400">
                  没有匹配的会话
                </p>
              )}
            </div>
          </CardContent>
        </Card>
        <Card className="min-h-[520px] overflow-hidden">
          {selected ? (
            <>
              <CardHeader className="flex-wrap">
                <div className="min-w-0">
                  <CardTitle className="break-all">
                    {selected.name || selected.id}
                  </CardTitle>
                  <p className="mt-1 text-xs text-stone-500">
                    {selected.message_count ?? 0} 条真实消息 ·{" "}
                    {selected.monitored ? "正在关注" : "未提炼"}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    disabled={Boolean(busy)}
                    variant={selected.monitored ? "secondary" : "default"}
                    size="sm"
                    onClick={() => toggleMonitor(selected)}
                  >
                    {selected.monitored ? (
                      <Check className="size-4" />
                    ) : (
                      <Plus className="size-4" />
                    )}
                    {selected.monitored ? "暂停关注" : "关注会话"}
                  </Button>
                  <Button
                    disabled={Boolean(busy)}
                    variant="secondary"
                    size="sm"
                    onClick={markRead}
                  >
                    <Check className="size-4" />
                    标记已读
                  </Button>
                  <Button
                    disabled={Boolean(busy) || !selected.monitored}
                    variant="secondary"
                    size="sm"
                    onClick={processSession}
                  >
                    <Sparkles className="size-4" />
                    提炼会话
                  </Button>
                </div>
              </CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-stone-100 px-4 py-3">
                <div className="flex gap-1">
                  <Button
                    variant={tab === "messages" ? "secondary" : "ghost"}
                    size="sm"
                    onClick={() => setTab("messages")}
                  >
                    <MessageSquare className="size-4" />
                    原始消息
                  </Button>
                  <Button
                    variant={tab === "ignored" ? "secondary" : "ghost"}
                    size="sm"
                    onClick={() => setTab("ignored")}
                  >
                    <Filter className="size-4" />
                    过滤记录
                  </Button>
                </div>
                <div className="flex items-center gap-2">
                  <label
                    htmlFor="history-limit"
                    className="text-xs text-stone-500"
                  >
                    历史上限
                  </label>
                  <Input
                    id="history-limit"
                    type="number"
                    min={1}
                    max={1000}
                    className="h-8 w-20"
                    value={historyLimit}
                    onChange={(event) => {
                      historyEdited.current = true;
                      setHistoryLimit(event.target.value);
                    }}
                  />
                  <Button
                    disabled={Boolean(busy)}
                    variant="secondary"
                    size="sm"
                    onClick={importHistory}
                  >
                    {busy === "history" ? (
                      <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
                    ) : (
                      <Download className="size-4" />
                    )}
                    导入
                  </Button>
                  <Button
                    disabled={loading}
                    variant="ghost"
                    size="icon"
                    aria-label="刷新会话消息"
                    onClick={() => loadConversation(selected.id)}
                  >
                    <RefreshCw className="size-4" />
                  </Button>
                </div>
              </div>
              <CardContent className="max-h-[65vh] space-y-4 overflow-y-auto">
                {loadError && (
                  <div
                    role="alert"
                    className="rounded-md bg-rose-50 p-3 text-sm text-rose-700"
                  >
                    {loadError}
                    <Button
                      className="ml-2"
                      size="sm"
                      variant="ghost"
                      onClick={() => loadConversation(selected.id)}
                    >
                      重试
                    </Button>
                  </div>
                )}
                {loading && (
                  <div
                    className="flex justify-center py-4"
                    role="status"
                    aria-label="正在读取消息"
                  >
                    <Loader2 className="size-5 animate-spin motion-reduce:animate-none" />
                  </div>
                )}
                {tab === "ignored" ? (
                  <>
                    {ignored.map((item) => (
                      <section
                        key={item.id}
                        className="space-y-3 rounded-md border border-stone-200 p-3"
                      >
                        <p className="text-xs leading-5 text-stone-500">
                          {item.reason || "已过滤"} ·{" "}
                          {dateText(item.created_at, true)}
                        </p>
                        {item.messages?.map((message) => (
                          <Message key={message.id} message={message} />
                        ))}
                      </section>
                    ))}
                    {!loading && !loadError && !ignored.length && (
                      <p className="py-10 text-center text-sm text-stone-400">
                        暂无过滤记录
                      </p>
                    )}
                    {ignored.length > 0 && (
                      <p className="text-center text-xs text-stone-400">
                        展示最近 30 条过滤判定
                      </p>
                    )}
                  </>
                ) : (
                  <>
                    {hasMore && (
                      <div className="text-center">
                        <Button
                          disabled={loading}
                          variant="ghost"
                          size="sm"
                          onClick={() => loadConversation(selected.id, true)}
                        >
                          加载更早消息
                        </Button>
                      </div>
                    )}
                    {messages.map((message) => (
                      <Message key={message.id} message={message} />
                    ))}
                    {!loading && !loadError && !messages.length && (
                      <p className="py-10 text-center text-sm text-stone-400">
                        还没有导入消息，导入历史或等待微信新消息。
                      </p>
                    )}
                  </>
                )}
              </CardContent>
            </>
          ) : (
            <div className="flex min-h-[520px] flex-col items-center justify-center px-5 text-center text-stone-400">
              <MessageSquare className="mb-3 size-9" />
              <p className="text-sm">选择一个微信会话查看原始消息</p>
            </div>
          )}
        </Card>
      </div>
      <Dialog
        open={catalogOpen}
        onOpenChange={(open) => {
          setCatalogOpen(open);
          if (!open) catalogSeq.current++;
        }}
      >
        <DialogContent className="p-6">
          <DialogTitle className="text-lg font-semibold">
            关注微信会话
          </DialogTitle>
          <DialogDescription className="mt-2 text-sm leading-6 text-stone-500">
            从微信当前可读取的会话中选择。关注后读取新消息，历史消息可在会话页导入。
          </DialogDescription>
          <div className="relative my-4">
            <Search className="absolute left-3 top-2.5 size-4 text-stone-400" />
            <Input
              aria-label="搜索微信目录"
              className="pl-9"
              placeholder="搜索微信会话"
              value={catalogSearch}
              onChange={(event) => setCatalogSearch(event.target.value)}
            />
          </div>
          <div className="max-h-[420px] space-y-1 overflow-y-auto">
            {catalogError && (
              <div
                role="alert"
                className="rounded-md bg-rose-50 p-3 text-sm text-rose-700"
              >
                {catalogError}
                <Button
                  className="ml-2"
                  variant="ghost"
                  size="sm"
                  onClick={openCatalog}
                >
                  重试
                </Button>
              </div>
            )}
            {catalogLoading && (
              <div
                className="flex justify-center p-8"
                role="status"
                aria-label="正在读取微信目录"
              >
                <Loader2 className="size-5 animate-spin motion-reduce:animate-none" />
              </div>
            )}
            {filteredCatalog.map((item) => {
              const known = sessions.find((session) => session.id === item.id);
              const monitored = Boolean(known?.monitored ?? item.monitored);
              return (
                <div
                  key={item.id}
                  className="flex items-center gap-3 rounded-md px-2 py-3 hover:bg-stone-50"
                >
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-stone-100 text-stone-600">
                    {(item.name || item.username || "会").slice(0, 1)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {item.name || item.username || item.id}
                    </p>
                    <p className="truncate text-xs text-stone-400">
                      {item.account || "微信会话"}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant={monitored ? "secondary" : "default"}
                    disabled={monitored || Boolean(busy)}
                    onClick={() => toggleMonitor({ ...item, monitored })}
                  >
                    {monitored ? (
                      <Check className="size-4" />
                    ) : (
                      <Plus className="size-4" />
                    )}
                    {monitored ? "已关注" : "关注"}
                  </Button>
                </div>
              );
            })}
            {!catalogLoading && !catalogError && !filteredCatalog.length && (
              <p className="py-8 text-center text-sm text-stone-400">
                没有匹配的微信会话
              </p>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
