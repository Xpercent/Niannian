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
  CATALOG_REQUEST_MS,
  dateText,
  LONG_REQUEST_MS,
  post,
  type MessageRow,
  type MessagePage,
  type MessageSearch,
  type QuoteTarget,
  type ReimportResult,
  type SessionRow,
} from "../lib/api";

type Props = {
  sessions: SessionRow[];
  onRefresh: () => Promise<void>;
  notify: (message: string, error?: boolean) => void;
  /** 选中会话提升到 App：切到设置页再回来不该丢掉正在看的会话。 */
  selectedId: string | null;
  onSelect: (id: string | null) => void;
};

export function SessionsPage({ sessions, onRefresh, notify, selectedId, onSelect }: Props) {
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState<MessageRow[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [beforeSeq, setBeforeSeq] = useState<number | null>(null);
  const [beforeId, setBeforeId] = useState<number | null>(null);
  const [reimporting, setReimporting] = useState(false);
  const [historyCount, setHistoryCount] = useState("100");
  const [loading, setLoading] = useState(false);
  const [flashId, setFlashId] = useState<number | null>(null);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState("");
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [catalog, setCatalog] = useState<SessionRow[]>([]);
  const [catalogSearch, setCatalogSearch] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  /** 消息搜索：`hits` 为 null 表示不在搜索态（正常看对话），空数组是"搜了没搜到"。 */
  const [searchTerm, setSearchTerm] = useState("");
  const [hits, setHits] = useState<MessageRow[] | null>(null);
  const [hitsLoading, setHitsLoading] = useState(false);
  const [hitsError, setHitsError] = useState("");
  const searchController = useRef<AbortController | null>(null);
  const requestSeq = useRef(0);
  const conversationRef = useRef<HTMLDivElement>(null);
  const loadingOlder = useRef(false);
  const scrollAnchor = useRef<{ height: number; top: number } | null>(null);
  const jumpToBottom = useRef(false);
  const nearBottom = useRef(true);
  const controller = useRef<AbortController | null>(null);

  const selected = sessions.find((item) => item.id === selectedId) || null;
  const visible = sessions.filter((item) =>
    (item.name || item.id).toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );

  /**
   * 取一屏消息。**翻页与跳转必须共用这一条出口**：抢同一条 abort 链、同一个请求序号。
   *
   * 之前 jumpToMessage 自己发请求，既不 abort 也不校验序号，于是"点引用 → 立刻切会话"
   * 会把上一个会话的那一屏盖到刚选中的会话上；请求失败还完全没声音。
   * 返回 null 表示这次结果不该再用（被后来的请求抢了，或已经出错报了）。
   */
  const fetchPage = useCallback(
    async (id: string, cursor: string): Promise<MessagePage | null> => {
      const seq = ++requestSeq.current;
      controller.current?.abort();
      const abort = new AbortController();
      controller.current = abort;
      setLoading(true);
      setLoadError("");
      try {
        const page = await api<MessagePage>(
          `/api/sessions/${encodeURIComponent(id)}/messages?limit=40${cursor}`,
          { signal: abort.signal },
        );
        return seq === requestSeq.current ? page : null;
      } catch (error) {
        if (seq === requestSeq.current && (error as Error).name !== "AbortError")
          setLoadError((error as Error).message || "读取会话失败");
        return null;
      } finally {
        if (seq === requestSeq.current) setLoading(false);
      }
    },
    [],
  );

  const loadConversation = useCallback(
    async (id: string, older = false) => {
      if (!older) {
        setMessages([]);
        setHasMore(false);
        if (nearBottom.current) jumpToBottom.current = true;
      }
      const cursor =
        older && beforeSeq != null
          ? `&before_seq=${beforeSeq}&before_id=${beforeId ?? 0}`
          : "";
      const page = await fetchPage(id, cursor);
      if (!page) return;
      setMessages((old) => {
        const fresh = page.items.slice().reverse();
        if (!older) return fresh;
        const seen = new Set(old.map((item) => item.id));
        return [...fresh.filter((item) => !seen.has(item.id)), ...old];
      });
      setBeforeSeq(page.before_seq);
      setBeforeId(page.before_id);
      setHasMore(page.has_more);
    },
    [beforeSeq, beforeId, fetchPage],
  );

  useEffect(() => {
    // 换会话就退出搜索态：关键词是属于上一个会话的上下文。
    setSearchTerm("");
    setHits(null);
    setHitsError("");
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

  /**
   * 搜当前会话里已入库的消息正文。
   *
   * 停 300 毫秒再发请求：边打字连发几十次查询没有意义。清空关键词就回到正常对话视图。
   */
  useEffect(() => {
    const text = searchTerm.trim();
    searchController.current?.abort();
    if (!selectedId || !text) {
      setHits(null);
      setHitsError("");
      setHitsLoading(false);
      return;
    }
    const abort = new AbortController();
    searchController.current = abort;
    const timer = setTimeout(async () => {
      setHitsLoading(true);
      try {
        const got = await api<MessageSearch>(
          `/api/sessions/${encodeURIComponent(selectedId)}/search?q=${encodeURIComponent(text)}`,
          { signal: abort.signal },
        );
        setHits(got.items);
        setHitsError("");
      } catch (error) {
        if ((error as Error).name !== "AbortError")
          setHitsError((error as Error).message || "搜索失败");
      } finally {
        setHitsLoading(false);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [searchTerm, selectedId]);

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
    if (selectedId && !selected) onSelect(null);
  }, [selected, selectedId]);

  // 新打开的对话滚动到最新一条；加载历史时保持原位。
  useEffect(() => {
    const panel = conversationRef.current;
    if (!panel || loading || !jumpToBottom.current) return;
    jumpToBottom.current = false;
    panel.scrollTop = panel.scrollHeight;
  }, [loading, messages]);

  // 往上补了一屏，按新增高度把视口顶回去——读的位置不能跳。
  useEffect(() => {
    const panel = conversationRef.current;
    const anchor = scrollAnchor.current;
    if (!panel || !anchor) return;
    scrollAnchor.current = null;
    panel.scrollTop = anchor.top + (panel.scrollHeight - anchor.height);
  }, [messages]);

  // 一屏还没占满就没有"滚到顶部"的机会，继续补更早的，直到占满或翻到最旧。
  useEffect(() => {
    const panel = conversationRef.current;
    if (!panel || loading || !hasMore) return;
    if (panel.scrollHeight <= panel.clientHeight + 8) void loadOlder();
  }, [loading, messages, hasMore]);

  // 用户翻看历史时不打断：只有贴在底部时才跟随新消息滚动。
  function trackScroll() {
    const panel = conversationRef.current;
    if (!panel) return;
    nearBottom.current =
      panel.scrollHeight - panel.scrollTop - panel.clientHeight < 120;
    // 滚到接近顶部就自动取更早的一屏：原来那颗"加载更早消息"按钮就是干这个的，
    // 但没人愿意连着点第二下。
    if (panel.scrollTop < 200) void loadOlder();
  }

  /**
   * 往上取更早的一屏。
   *
   * 滚动事件会连着触发几十次，所以除了 `loading`/`hasMore`，还要一把自己的锁；
   * 同时记下当前视口，补完内容后按新增高度顶回去，否则读着读着会被跳走。
   */
  async function loadOlder() {
    if (!selected || !hasMore || loading || loadingOlder.current) return;
    loadingOlder.current = true;
    const panel = conversationRef.current;
    if (panel) scrollAnchor.current = { height: panel.scrollHeight, top: panel.scrollTop };
    try {
      await loadConversation(selected.id, true);
    } finally {
      loadingOlder.current = false;
    }
  }

  /**
   * 跳到某条消息并描边：点引用的原文、点搜索结果，走同一条路。
   *
   * 目标不在当前这一屏时，用**现有**游标参数取"包含它的那一屏"就够了：`sort_seq`
   * 在会话内唯一，`before_seq = 目标 + 1, before_id = 1` 会把目标本身带进来
   * （它是这一屏最新的一条），不需要为跳转再加一个新接口。
   * `before_id` 不能给 0 —— 接口的校验是 `ge=1`。
   */
  async function jumpToMessage(target: QuoteTarget, fromSearch = false) {
    if (!selected) return;
    if (fromSearch) {
      // 回到对话看上下文：结果列表和关键词都收掉，下次输入从头开始。
      setHits(null);
      setSearchTerm("");
    }
    if (!messages.some((item) => item.id === target.message_id)) {
      const page = await fetchPage(
        selected.id,
        `&before_seq=${target.sort_seq + 1}&before_id=1`,
      );
      if (!page) return;
      setMessages(page.items.slice().reverse());
      setBeforeSeq(page.before_seq);
      setBeforeId(page.before_id);
      setHasMore(page.has_more);
      nearBottom.current = false;
    }
    setFlashId(target.message_id);
  }

  // 跳转落点：滚到那条消息并短暂描边一下。只认 flashId——挂着 messages 的话，
  // 描边还没掉色就翻了屏，计时会被重置，那条一直亮着看不出来是"刚跳到的"。
  useEffect(() => {
    if (flashId == null) return;
    document
      .getElementById(`message-${flashId}`)
      ?.scrollIntoView({ block: "center" });
    const timer = setTimeout(() => setFlashId(null), 1800);
    return () => clearTimeout(timer);
  }, [flashId]);

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
      setCatalog(await api<SessionRow[]>("/api/wechat/catalog", {}, CATALOG_REQUEST_MS));
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
        <div className="flex flex-wrap items-center gap-2">
          {/* 宽度由外层 span 控制：Input 基类是 w-full，同槽位塞两个宽度类会看样式表顺序 */}
          <span className="relative block w-64">
            <Search className="absolute left-3 top-3 size-4 text-stone-400" />
            <Input
              aria-label="搜索当前会话的消息"
              className="pl-9"
              disabled={!selected}
              placeholder={selected ? `在「${selected.name || selected.username}」里搜消息` : "先选一个会话"}
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
            />
          </span>
          <Button onClick={openCatalog}>
            <Plus className="size-4" />
            添加关注
          </Button>
        </div>
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
                  onClick={() => onSelect(session.id)}
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
              {hits === null ? (
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
                  {messages.map((message) => (
                    <Message
                      key={message.id}
                      message={message}
                      flash={flashId === message.id}
                      onJump={jumpToMessage}
                    />
                  ))}
                  {!loading && !loadError && !messages.length && (
                    <p className="py-10 text-center text-sm text-stone-400">
                      还没有导入消息，稍候会自动补齐。
                    </p>
                  )}
                </CardContent>
              ) : (
                <CardContent className="max-h-[65vh] space-y-1 overflow-y-auto">
                  <p className="text-xs text-stone-500">
                    {hitsLoading
                      ? "正在搜索…"
                      : `找到 ${hits.length} 条 · 关键词「${searchTerm.trim()}」`}
                    <span className="ml-2 text-stone-400">
                      只能搜到已导入本库的消息，更早的先用「重新导入」
                    </span>
                  </p>
                  {hitsError && (
                    <div
                      role="alert"
                      className="rounded-md bg-rose-50 p-3 text-sm text-rose-700"
                    >
                      {hitsError}
                    </div>
                  )}
                  {hits.map((hit) => (
                    <button
                      type="button"
                      key={hit.id}
                      onClick={() =>
                        void jumpToMessage(
                          { message_id: hit.id, sort_seq: hit.sort_seq },
                          true,
                        )
                      }
                      className="flex w-full items-start gap-3 rounded-md border border-transparent p-3 text-left hover:bg-stone-50"
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm">{hit.content}</span>
                        <span className="text-xs text-stone-400">
                          {hit.sender_name} · {dateText(hit.created_at, true)}
                        </span>
                      </span>
                      <Badge>{hit.type_label || hit.message_type}</Badge>
                    </button>
                  ))}
                  {!hitsLoading && !hitsError && !hits.length && (
                    <p className="py-10 text-center text-sm text-stone-400">没有匹配的消息</p>
                  )}
                </CardContent>
              )}
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
