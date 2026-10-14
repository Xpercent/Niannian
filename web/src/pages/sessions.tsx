import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  History,
  Loader2,
  MessageSquare,
  Plus,
  RefreshCw,
  Search,
} from "lucide-react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Dialog,
  Input,
} from "../components/ui";
import { Message } from "../components/message";
import {
  api,
  dateText,
  LONG_REQUEST_MS,
  post,
  type MessageRow,
  type MessagePage,
  type ReimportResult,
  type SessionRow,
} from "../lib/api";

type Props = {
  sessions: SessionRow[];
  onRefresh: () => Promise<void>;
  notify: (message: string, error?: boolean) => void;
};

export function SessionsPage({ sessions, onRefresh, notify }: Props) {
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageRow[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [beforeSeq, setBeforeSeq] = useState<number | null>(null);
  const [beforeId, setBeforeId] = useState<number | null>(null);
  const [reimporting, setReimporting] = useState(false);
  const [historyCount, setHistoryCount] = useState("100");
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState("");
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [catalog, setCatalog] = useState<SessionRow[]>([]);
  const [catalogSearch, setCatalogSearch] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const requestSeq = useRef(0);
  const conversationRef = useRef<HTMLDivElement>(null);
  const jumpToBottom = useRef(false);
  const nearBottom = useRef(true);
  const controller = useRef<AbortController | null>(null);
  const current = useRef({ id: selectedId });
  current.current = { id: selectedId };

  const selected = sessions.find((item) => item.id === selectedId) || null;
  const visible = sessions.filter((item) =>
    (item.name || item.id).toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );

  const loadConversation = useCallback(
    async (id: string, older = false) => {
      const seq = ++requestSeq.current;
      controller.current?.abort();
      const abort = new AbortController();
      controller.current = abort;
      setLoading(true);
      setLoadError("");
      if (!older) {
        setMessages([]);
        setHasMore(false);
        if (nearBottom.current) jumpToBottom.current = true;
      }
      try {
        const cursor =
          older && beforeSeq != null
            ? `&before_seq=${beforeSeq}&before_id=${beforeId ?? 0}`
            : "";
        const page = await api<MessagePage>(
          `/api/sessions/${encodeURIComponent(id)}/messages?limit=40${cursor}`,
          { signal: abort.signal },
        );
        if (seq !== requestSeq.current) return;
        setMessages((old) => {
          const fresh = page.items.slice().reverse();
          if (!older) return fresh;
          const seen = new Set(old.map((item) => item.id));
          return [...fresh.filter((item) => !seen.has(item.id)), ...old];
        });
        setBeforeSeq(page.before_seq);
        setBeforeId(page.before_id);
        setHasMore(page.has_more);
      } catch (error) {
        if (
          seq === requestSeq.current &&
          (error as Error).name !== "AbortError"
        )
          setLoadError((error as Error).message || "读取会话失败");
      } finally {
        if (seq === requestSeq.current) setLoading(false);
      }
    },
    [beforeSeq, beforeId],
  );

  useEffect(() => {
    if (selectedId) {
      // 条数输入跟随该会话自己上次导入的量，不跟全局配置串味。
      setHistoryCount(String(selected?.history_limit || 100));
      void loadConversation(selectedId);
    } else setMessages([]);
    nearBottom.current = true;
    return () => {
      requestSeq.current++;
      controller.current?.abort();
    };
    // 仅切换会话时重开对话；消息数变化由下面的效果增量刷新。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  // 关注后自动导入、轮询新消息都会推进 message_count；变化时静默刷新当前对话。
  const watchedCount = selected?.message_count ?? 0;
  const lastCount = useRef(new Map<string, number>());
  useEffect(() => {
    if (!selectedId) return;
    const previous = lastCount.current.get(selectedId);
    lastCount.current.set(selectedId, watchedCount);
    if (previous !== undefined && previous !== watchedCount && nearBottom.current)
      void loadConversation(selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, watchedCount]);

  useEffect(() => {
    if (selectedId && !selected) setSelectedId(null);
  }, [selected, selectedId]);

  // 新打开的对话滚动到最新一条；加载历史时保持原位。
  useEffect(() => {
    const panel = conversationRef.current;
    if (!panel || loading || !jumpToBottom.current) return;
    jumpToBottom.current = false;
    panel.scrollTop = panel.scrollHeight;
  }, [loading, messages]);

  // 用户翻看历史时不打断：只有贴在底部时才跟随新消息滚动。
  function trackScroll() {
    const panel = conversationRef.current;
    if (!panel) return;
    nearBottom.current =
      panel.scrollHeight - panel.scrollTop - panel.clientHeight < 120;
  }

  async function toggleFollow(session: SessionRow) {
    setBusy(session.id);
    try {
      const action = session.monitored ? "unfollow" : "follow";
      await api(`/api/sessions/${encodeURIComponent(session.id)}/${action}`, post());
      setCatalog((items) =>
        items.map((item) =>
          item.id === session.id ? { ...item, monitored: action === "follow" } : item,
        ),
      );
      await onRefresh();
      notify(
        action === "follow"
          ? "已关注，采集器将自动导入最近消息并持续读取新消息"
          : "已取消关注，停止采集该会话（历史消息保留）",
      );
    } catch (error) {
      notify((error as Error).message || "操作失败", true);
    } finally {
      setBusy("");
    }
  }

  async function reimportHistory(session: SessionRow) {
    const count = Math.max(0, Math.min(1000, Number(historyCount) || 0));
    setReimporting(true);
    try {
      const result = await api<ReimportResult>(
        `/api/sessions/${encodeURIComponent(session.id)}/reimport?limit=${count}`,
        post(),
        LONG_REQUEST_MS,
      );
      // 重新导入会把更旧的消息以更大的自增 id 插进来，必须整页重读而不是增量合并。
      await Promise.all([loadConversation(session.id), onRefresh()]);
      notify(
        `已按最近 ${result.limit} 条重新导入：读到 ${result.read} 条，` +
          `新增 ${result.inserted} 条（重复的自动跳过，原有消息未删除）`,
      );
    } catch (error) {
      notify((error as Error).message || "重新导入失败", true);
    } finally {
      setReimporting(false);
    }
  }

  async function openCatalog() {
    setCatalogOpen(true);
    setCatalogLoading(true);
    setCatalogError("");
    try {
      setCatalog(await api<SessionRow[]>("/api/wechat/catalog"));
    } catch (error) {
      setCatalogError((error as Error).message || "读取微信会话失败");
    } finally {
      setCatalogLoading(false);
    }
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
            关注会话后自动导入最近消息，并持续读取新消息。
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
              {visible.map((session) => (
                <button
                  type="button"
                  key={session.id}
                  onClick={() => setSelectedId(session.id)}
                  className={`flex w-full items-center gap-3 rounded-md border p-3 text-left ${
                    selectedId === session.id
                      ? "border-emerald-200 bg-emerald-50"
                      : "border-transparent hover:bg-stone-50"
                  }`}
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
                      {dateText(session.last_message_at)}
                    </span>
                  </span>
                  {session.unread ? (
                    <Badge className="border-none bg-emerald-100 text-emerald-800">
                      {session.unread > 99 ? "99+" : session.unread}
                    </Badge>
                  ) : null}
                  <span
                    className="size-2 shrink-0 rounded-full bg-emerald-600"
                    aria-label="正在关注"
                  />
                </button>
              ))}
              {!visible.length && (
                <p className="py-8 text-center text-sm text-stone-400">
                  {sessions.length
                    ? "没有匹配的会话"
                    : "暂无已关注会话，点击右上角“添加关注”。"}
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
                    {selected.message_count ?? 0} 条真实消息 · 正在关注
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <form
                    className="flex items-center gap-2"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void reimportHistory(selected);
                    }}
                  >
                    <label className="flex items-center gap-2 text-xs text-stone-500">
                      最近
                      <span className="inline-block w-20 align-middle">
                        <Input
                          type="number"
                          min={1}
                          max={1000}
                          aria-label="重新导入的最近条数"
                          value={historyCount}
                          onChange={(event) => setHistoryCount(event.target.value)}
                        />
                      </span>
                      条
                    </label>
                    <Button type="submit" variant="ghost" disabled={reimporting}>
                      {reimporting ? (
                        <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
                      ) : (
                        <History className="size-4" />
                      )}
                      {reimporting ? "正在导入" : "重新导入"}
                    </Button>
                  </form>
                  <Button
                    disabled={Boolean(busy)}
                    variant="secondary"
                    size="sm"
                    onClick={() => toggleFollow(selected)}
                  >
                    <Check className="size-4" />
                    取消关注
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
              </CardHeader>
              <CardContent
                ref={conversationRef}
                onScroll={trackScroll}
                className="max-h-[65vh] space-y-4 overflow-y-auto"
              >
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
                    还没有导入消息，稍候会自动补齐。
                  </p>
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
      <Dialog open={catalogOpen} onClose={() => setCatalogOpen(false)}>
        <h3 className="text-lg font-semibold">关注微信会话</h3>
        <p className="mt-2 text-sm leading-6 text-stone-500">
          从微信当前可读取的会话中选择。关注后自动导入最近消息并开始采集新消息。
        </p>
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
              <Button className="ml-2" variant="ghost" size="sm" onClick={openCatalog}>
                重试
              </Button>
            </div>
          )}
          {catalogLoading && (
            <div className="flex justify-center p-8" role="status" aria-label="正在读取微信目录">
              <Loader2 className="size-5 animate-spin motion-reduce:animate-none" />
            </div>
          )}
          {filteredCatalog.map((item) => (
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
                  {item.username} · {dateText(item.last_message_at)}
                </p>
              </div>
              <Button
                size="sm"
                variant={item.monitored ? "secondary" : "default"}
                disabled={item.monitored || Boolean(busy)}
                onClick={() => toggleFollow(item)}
              >
                {item.monitored ? <Check className="size-4" /> : <Plus className="size-4" />}
                {item.monitored ? "已关注" : "关注"}
              </Button>
            </div>
          ))}
          {!catalogLoading && !catalogError && !filteredCatalog.length && (
            <p className="py-8 text-center text-sm text-stone-400">没有匹配的微信会话</p>
          )}
        </div>
      </Dialog>
    </div>
  );
}
