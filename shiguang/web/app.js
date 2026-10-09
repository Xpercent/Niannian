import {
  $,
  $$,
  escape as e,
  icon,
  hydrateIcons,
  api,
  date,
  relative,
  priorities,
  statuses,
  dueText,
  localDateTime,
  initials,
  options,
  messageMarkup,
} from "./helpers.js";

const views = {
  tasks: "待办事项",
  sessions: "微信会话",
  rules: "过滤规则",
  profile: "我的画像",
  settings: "设置与数据",
};
const state = {
  view: "tasks",
  overview: null,
  tasks: [],
  sessions: [],
  rules: [],
  settings: {},
  profile: "",
  profileLoaded: false,
  selectedSession: null,
  sessionTab: "messages",
  sessionSearch: "",
  messages: [],
  hasMore: false,
  beforeId: null,
  ignored: [],
  task: null,
  drawerEdit: false,
  filter: {
    status: "pending",
    q: "",
    priority: "",
    session_id: "",
    due: "all",
  },
  run: null,
  routeVersion: 0,
  taskRequest: 0,
  drawerRequest: 0,
  conversationRequest: 0,
  refreshing: false,
  first: true,
};
let filterTimer, commandTimer, pollTimer, modalReturnFocus, drawerReturnFocus;
let modalType = null;
let commandResults = [];

function toast(message, error = false) {
  const el = document.createElement("div");
  el.className = `toast${error ? " error" : ""}`;
  el.innerHTML = `${icon(error ? "info" : "check-circle")}<span>${e(message)}</span>`;
  $("#toast-region").append(el);
  setTimeout(() => el.remove(), error ? 6500 : 3800);
}
function loading(message = "正在加载") {
  return `<div class="loading-state"><span class="spinner"></span><span>${e(message)}</span></div>`;
}
function empty(title, copy, action = "", art = "inbox") {
  return `<div class="empty-state"><div class="empty-art">${icon(art)}</div><h3>${e(title)}</h3><p>${e(copy)}</p>${action}</div>`;
}
function errorMarkup(error, action = "retry") {
  return `<div class="error-state">${icon("info")} ${e(error.message)}<br><button class="button small" data-action="${action}">重新加载${icon("refresh")}</button></div>`;
}
function pageHeader(title, caption, actions = "", eyebrow = "WORKSPACE") {
  return `<div class="page-header"><div><div class="page-eyebrow">${e(eyebrow)}</div><h1>${e(title)}</h1><p>${e(caption)}</p></div><div class="header-actions">${actions}</div></div>`;
}
const processButton = () =>
  `<button class="button primary" data-action="process" ${state.run?.status === "running" ? "disabled" : ""}>${icon("sparkles")}立即提炼</button>`;
function switchMarkup(id, checked, label, extra = "") {
  return `<label class="switch"><input type="checkbox" ${id ? `id="${e(id)}"` : ""} ${checked ? "checked" : ""} aria-label="${e(label)}" ${extra}><span></span></label>`;
}
function activeTabs(root = document) {
  $$(".tabs", root).forEach((el) => {
    let indicator = $(".tab-indicator", el);
    if (!indicator) {
      indicator = document.createElement("span");
      indicator.className = "tab-indicator";
      el.prepend(indicator);
    }
    const active = $(".tab.active", el);
    if (active) {
      indicator.style.width = `${active.offsetWidth}px`;
      indicator.style.transform = `translateX(${active.offsetLeft - 3}px)`;
    }
  });
}
async function busy(button, operation) {
  if (button?.disabled) return;
  if (button) button.disabled = true;
  try {
    return await operation();
  } catch (error) {
    toast(error.message, true);
    return null;
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
}
function array(data) {
  return Array.isArray(data)
    ? data
    : Array.isArray(data?.items)
      ? data.items
      : [];
}

function updateOverview(overview) {
  state.overview = overview;
  state.sessions = array(overview.sessions);
  state.run = overview.run ?? null;
  const wechat = overview.wechat || {};
  const running = ["running", "connected", "listening", "active"].includes(
    wechat.state,
  );
  const failed = ["error", "failed", "unavailable", "disconnected"].includes(
    wechat.state,
  );
  $("#nav-pending").textContent = overview.stats?.pending ?? "—";
  $("#connection-dot").className =
    `connection-dot${running ? " connected" : failed ? " error" : ""}`;
  $("#connection-label").textContent = running
    ? "微信已连接"
    : failed
      ? "连接需要检查"
      : ["starting", "connecting"].includes(wechat.state)
        ? "正在连接微信"
        : "微信未连接";
  $("#connection-note").textContent = wechat.error
    ? String(wechat.error).slice(0, 55)
    : running
      ? `${overview.stats?.monitored ?? 0} 个会话正在关注`
      : "连接后自动拾取真实消息";
  $("#connection-note").title = wechat.error || "";
  $("#connection-action").innerHTML =
    `${running ? "暂停连接" : "连接微信"}${icon(running ? "pause" : "arrow-right")}`;
  $("#connection-action").dataset.mode = running ? "stop" : "start";
  $(".live-indicator").classList.toggle("connected", running);
  $("#sync-label").textContent = running
    ? `同步于${relative(wechat.last_sync_at || wechat.last_sync)}`
    : ["starting", "connecting"].includes(wechat.state)
      ? "正在连接微信"
      : "微信未连接";
  $("#account-avatar").textContent = initials(wechat.account || "拾");
  $("#account-name").innerHTML =
    `${e(wechat.account || "个人工作空间")}<small>${running ? "微信同步已开启" : "把重要的事留下"}</small>`;
  $("#footer-message-count").textContent =
    `${overview.stats?.messages ?? 0} 条真实消息 · 本地工作空间`;
  renderRun();
  if (state.view === "tasks") updateStats();
}
function updateStats() {
  const stats = state.overview?.stats || {};
  const values = {
    pending: stats.pending,
    completed: stats.completed,
    due_today: stats.due_today,
    overdue: stats.overdue,
  };
  for (const [key, value] of Object.entries(values))
    $$(`[data-stat="${key}"]`).forEach((el) => (el.textContent = value ?? "—"));
  $('[data-stat="queued"]')?.replaceChildren(
    document.createTextNode(String(stats.queued ?? 0)),
  );
  $('[data-stat="messages"]')?.replaceChildren(
    document.createTextNode(String(stats.messages ?? 0)),
  );
  $('[data-stat="monitored"]')?.replaceChildren(
    document.createTextNode(String(stats.monitored ?? 0)),
  );
  const total = (stats.pending || 0) + (stats.completed || 0);
  const ratio = total ? Math.round(((stats.completed || 0) / total) * 100) : 0;
  if ($("#completion-bar")) $("#completion-bar").style.width = `${ratio}%`;
  if ($("#completion-ratio")) $("#completion-ratio").textContent = `${ratio}%`;
}
function renderRun() {
  const run = state.run;
  const el = $("#run-strip");
  $$('[data-action="process"]').forEach(
    (button) =>
      (button.disabled = ["running", "queued", "pending"].includes(
        run?.status,
      )),
  );
  if (
    !run ||
    !["running", "queued", "pending", "failed"].includes(run.status)
  ) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  const progress = run.total
    ? Math.min(100, Math.round(((run.done || 0) / run.total) * 100))
    : 0;
  el.innerHTML = `${run.status === "failed" ? icon("info") : '<span class="spinner"></span>'}<div class="run-main"><strong>${run.status === "failed" ? "提炼未完成" : run.session ? `正在梳理 · ${e(run.session)}` : "正在从微信消息中提炼待办"}</strong>${run.status === "failed" ? `<span> · ${e(run.error || "请重试")}</span>` : `<div class="progress-track"><span style="width:${progress}%"></span></div>`}</div><span class="run-count">${run.done ?? 0} / ${run.total ?? 0}</span><button class="button small text" data-action="${run.status === "failed" ? "dismiss-run" : "cancel-process"}">${run.status === "failed" ? "收起" : "取消"}</button>`;
  $$('[data-action="process"]').forEach(
    (button) =>
      (button.disabled = ["running", "queued", "pending"].includes(run.status)),
  );
}
async function overview() {
  const result = await api("/api/overview");
  updateOverview(result);
  return result;
}

function taskView() {
  const s = state.overview?.stats || {};
  const statCards = [
    ["pending", "进行中的待办", "check-circle", "个", "pending"],
    ["completed", "已完成", "archive", "个", "completed"],
    ["due_today", "今天截止", "calendar", "个", "today"],
    ["overdue", "已逾期", "clock", "个", "overdue"],
  ];
  const query = state.filter;
  return `${pageHeader("待办事项", "从消息里拾起重要的事，一件件完成。", `<a class="button" href="/api/export?format=md" download>${icon("download")}导出</a>${processButton()}`, "YOUR DAILY FOCUS")}
    <div class="stats-row">${statCards.map(([key, label, ic, unit, filter]) => `<button class="stat-card" data-action="stat-filter" data-filter="${filter}"><span class="stat-icon">${icon(ic)}</span><span><span class="stat-title">${label}</span><strong class="stat-value"><span data-stat="${key}">${s[key] ?? "—"}</span><span class="stat-label">${unit}</span></strong></span></button>`).join("")}</div>
    <div class="tasks-layout"><section class="panel" aria-label="待办列表"><div class="panel-header"><h2>我的待办 <span class="count" id="task-count">—</span></h2><div class="tabs" role="tablist" aria-label="待办状态">${Object.entries(
      statuses,
    )
      .map(
        ([key, label]) =>
          `<button class="tab ${query.status === key ? "active" : ""}" role="tab" aria-selected="${query.status === key}" data-action="task-status" data-value="${key}">${label}</button>`,
      )
      .join("")}</div></div>
    <div class="filter-bar"><label class="inline-search">${icon("search")}<input id="task-search" type="search" placeholder="搜索待办事项…" aria-label="搜索待办事项" value="${e(query.q)}"></label><span class="filter-separator"></span><select id="priority-filter" class="compact-select" aria-label="优先级">${options([["", "全部优先级"], ...Object.entries(priorities)], query.priority)}</select><select id="session-filter" class="compact-select" aria-label="来源会话">${options([["", "全部会话"], ...state.sessions.map((s) => [s.id, s.name])], query.session_id)}</select><select id="due-filter" class="compact-select" aria-label="截止时间">${options(
      [
        ["all", "全部时间"],
        ["today", "今天截止"],
        ["overdue", "已逾期"],
        ["upcoming", "即将截止"],
      ],
      query.due,
    )}</select></div>
    <div id="task-list" class="task-list">${loading("正在整理待办")}</div><div class="list-footer"><span id="task-list-caption">按优先级与截止时间排列</span><span>点击事项查看消息来源</span></div></section>
    <aside class="side-column"><section class="side-panel"><div class="side-title">${icon("calendar")}今日概览</div><p class="date-caption">${e(new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "long", day: "numeric", weekday: "long" }).format(new Date()))}</p><div class="focus-number"><span data-stat="due_today" style="font:inherit;color:inherit;margin:0">${s.due_today ?? "—"}</span><span>件事在今天截止</span></div><p class="focus-copy">留一点专注，给真正重要的事情。</p><div class="focus-divider"></div><div class="mini-row"><span>进行中</span><strong data-stat="pending">${s.pending ?? "—"}</strong></div><div class="mini-row"><span>累计完成</span><strong data-stat="completed">${s.completed ?? "—"}</strong></div><div class="progress-track"><span id="completion-bar" style="width:0%"></span></div><div class="mini-row"><span>完成比例</span><strong id="completion-ratio">0%</strong></div></section>
    ${!state.profileLoaded || !state.profile.trim() ? `<a class="side-panel profile-prompt" href="#profile"><div class="side-title">${icon("user")}补充我的身份</div><p>帮助拾光判断哪些群公告和消息与你有关。</p><span class="text-link">去填写画像${icon("arrow-right")}</span></a>` : ""}
    <section class="side-panel"><div class="side-title">${icon("sparkles")}拾光正在做的事</div><div class="flow-stage"><span class="flow-mark">${icon("message")}</span><span><strong>接住消息</strong><small><span data-stat="messages">${s.messages ?? 0}</span> 条来自微信的真实消息</small></span></div><div class="flow-connector"></div><div class="flow-stage"><span class="flow-mark">${icon("filter")}</span><span><strong>滤过闲聊</strong><small>只留下与你有关的行动</small></span></div><div class="flow-connector"></div><div class="flow-stage"><span class="flow-mark">${icon("check-circle")}</span><span><strong>拾起待办</strong><small><span data-stat="queued">${s.queued ?? 0}</span> 条消息等待提炼</small></span></div></section><div class="side-note">${icon("shield")}每项待办都能回到原始消息，判断与修改始终由你决定。</div></aside></div>`;
}
function taskRow(task) {
  const due = dueText(task.due_at);
  const dueClass = due.startsWith("逾期")
    ? "overdue"
    : due === "今天截止"
      ? "today"
      : "";
  return `<article class="task-row ${state.task?.id === task.id ? "selected" : ""}" data-task-id="${e(task.id)}"><button class="task-check ${task.status === "completed" ? "done" : ""}" data-action="toggle-task" data-id="${e(task.id)}" aria-label="${task.status === "completed" ? "恢复待办" : "完成待办"}：${e(task.title)}">${task.status === "completed" ? icon("check") : ""}</button><div class="task-main"><button class="task-title ${task.status === "completed" ? "completed" : ""}" data-action="open-task" data-id="${e(task.id)}">${e(task.title)}</button><div class="task-meta"><span class="task-session" title="${e(task.session_name || "微信会话")}">${icon("message")} ${e(task.session_name || "微信会话")}</span>${task.owner ? `<span>${icon("user")}${e(task.owner)}</span>` : ""}${due ? `<span class="due ${dueClass}">${icon("calendar")}${e(due)}</span>` : `<span>${icon("clock")}${e(date(task.created_at))}拾取</span>`}</div></div><div class="task-trailing"><span class="priority-tag ${e(task.priority || "medium")}">${e(priorities[task.priority] || "普通")}</span>${icon("chevron")}</div></article>`;
}
async function loadTasks() {
  const request = ++state.taskRequest;
  const filter = { ...state.filter };
  const params = new URLSearchParams(
    Object.entries(filter).filter(([, value]) => value !== ""),
  );
  try {
    const tasks = array(await api(`/api/tasks?${params}`));
    if (request !== state.taskRequest || state.view !== "tasks") return;
    state.tasks = tasks;
    const list = $("#task-list");
    if (!list) return;
    $("#task-count").textContent = String(tasks.length);
    const filtered =
      filter.q || filter.priority || filter.session_id || filter.due !== "all";
    list.innerHTML = tasks.length
      ? tasks.map(taskRow).join("")
      : filtered
        ? empty(
            "没有找到符合条件的待办",
            "试试其他关键词，或重置筛选条件。",
            '<button class="button small" data-action="clear-filters">重置筛选</button>',
            "search",
          )
        : filter.status === "completed"
          ? empty(
              "完成的事，会在这里留下",
              "完成一件待办，让每次行动都看得见。",
              "",
              "check-circle",
            )
          : filter.status === "cancelled"
            ? empty(
                "这里还没有忽略的事项",
                "不需要执行的待办，可以忽略并随时恢复。",
                "",
                "archive",
              )
            : empty(
                "暂时没有需要处理的事",
                "关注微信会话后，拾光会从真实消息中整理待办。",
                `<button class="button small" data-action="go-sessions">${icon("message")}关注微信会话${icon("arrow-right")}</button>`,
                "leaf",
              );
    $("#task-list-caption").textContent = tasks.length
      ? `共 ${tasks.length} 项 · ${statuses[filter.status] || "全部"}`
      : "重要的事，一件都不漏";
  } catch (error) {
    if (request === state.taskRequest && $("#task-list"))
      $("#task-list").innerHTML = errorMarkup(error, "reload-tasks");
  }
}
function updateTaskFilterUI() {
  const f = state.filter;
  $$('.tab[data-action="task-status"]').forEach((el) => {
    el.classList.toggle("active", el.dataset.value === f.status);
    el.setAttribute("aria-selected", String(el.dataset.value === f.status));
  });
  if ($("#task-search")) $("#task-search").value = f.q;
  if ($("#priority-filter")) $("#priority-filter").value = f.priority;
  if ($("#session-filter")) $("#session-filter").value = f.session_id;
  if ($("#due-filter")) $("#due-filter").value = f.due;
  activeTabs();
}

function sessionView() {
  return `${pageHeader("微信会话", "关注需要的会话，给消息一个清晰的去处。", `<button class="button" data-action="catalog">${icon("plus")}添加关注</button>${processButton()}`, "MESSAGE SOURCES")}<div class="sessions-layout"><section class="panel session-list-panel"><div class="panel-header"><h2>消息来源 <span class="count" id="session-count">${state.sessions.length}</span></h2><button class="icon-button" data-action="catalog" aria-label="添加关注会话">${icon("plus")}</button></div><div class="session-list-toolbar"><input id="session-search" type="search" placeholder="搜索会话…" aria-label="搜索会话" value="${e(state.sessionSearch)}"></div><div class="session-list" id="session-list"></div></section><section class="panel conversation-panel" id="conversation">${empty("选择一个微信会话", "查看原始消息、过滤记录，以及关注状态。", "", "message")}</section></div>`;
}
function renderSessions() {
  const list = $("#session-list");
  if (!list) return;
  const query = state.sessionSearch.toLocaleLowerCase();
  const sessions = state.sessions.filter((s) =>
    `${s.name} ${s.username || ""}`.toLocaleLowerCase().includes(query),
  );
  $("#session-count").textContent = String(state.sessions.length);
  list.innerHTML = sessions.length
    ? sessions
        .map(
          (session) =>
            `<button class="session-row ${String(state.selectedSession) === String(session.id) ? "active" : ""}" data-action="select-session" data-id="${e(session.id)}"><span class="session-avatar">${e(initials(session.name))}</span><span class="session-info"><strong>${e(session.name)}</strong><small>${session.message_count ?? 0} 条消息 · ${session.task_count ?? 0} 项待办</small></span><span class="session-indicators">${session.unread ? `<span class="unread-count">${session.unread > 99 ? "99+" : e(session.unread)}</span>` : ""}${session.monitored ? '<span class="monitored-dot" title="正在关注"></span>' : ""}</span></button>`,
        )
        .join("")
    : empty(
        query ? "没有匹配的会话" : "还没有消息来源",
        query ? "换一个关键词试试。" : "连接微信，添加你想关注的会话。",
        !query
          ? '<button class="button small" data-action="catalog">添加关注</button>'
          : "",
        "message",
      );
}
async function loadSessions() {
  state.sessions = array(await api("/api/sessions"));
  if (state.view !== "sessions") return;
  renderSessions();
  if (
    state.selectedSession &&
    state.sessions.some((s) => String(s.id) === String(state.selectedSession))
  )
    await selectSession(state.selectedSession);
}
async function selectSession(id) {
  state.selectedSession = id;
  const request = ++state.conversationRequest;
  const session = state.sessions.find((s) => String(s.id) === String(id));
  if (!session) return;
  renderSessions();
  $("#conversation").innerHTML =
    `<div class="conversation-header"><div class="conversation-name"><span class="session-avatar">${e(initials(session.name))}</span><div><strong>${e(session.name)}</strong><p>${session.message_count ?? 0} 条真实消息 · ${session.task_count ?? 0} 项待办</p></div></div><div class="conversation-actions"><button class="button small ${session.monitored ? "subtle" : ""}" data-action="monitor-session" data-id="${e(id)}" data-enabled="${!session.monitored}">${icon(session.monitored ? "check" : "plus")}${session.monitored ? "已关注" : "关注会话"}</button><button class="button small" data-action="history" data-id="${e(id)}">${icon("download")}导入历史</button><button class="icon-button" data-action="read-session" data-id="${e(id)}" aria-label="标记会话已读" title="标记已读">${icon("check-circle")}</button></div></div><div class="conversation-tabs"><div class="tabs" role="tablist" aria-label="会话内容"><button class="tab ${state.sessionTab === "messages" ? "active" : ""}" data-action="session-tab" data-value="messages" role="tab" aria-selected="${state.sessionTab === "messages"}">原始消息</button><button class="tab ${state.sessionTab === "ignored" ? "active" : ""}" data-action="session-tab" data-value="ignored" role="tab" aria-selected="${state.sessionTab === "ignored"}">过滤记录</button></div><button class="button small text" data-action="process-session" data-id="${e(id)}">${icon("sparkles")}提炼会话</button></div><div class="message-scroll" id="message-scroll">${loading("正在读取消息")}</div><div class="conversation-footer">${icon("shield")}消息仅用于读取、过滤与提炼</div>`;
  activeTabs();
  await loadConversation(request);
}
async function loadConversation(request = ++state.conversationRequest) {
  const id = state.selectedSession;
  const tab = state.sessionTab;
  try {
    if (tab === "ignored") {
      const ignored = array(
        await api(`/api/sessions/${encodeURIComponent(id)}/ignored`),
      );
      if (request !== state.conversationRequest || state.view !== "sessions")
        return;
      state.ignored = ignored;
      renderMessages();
    } else {
      const result = await api(
        `/api/sessions/${encodeURIComponent(id)}/messages?limit=40`,
      );
      if (request !== state.conversationRequest || state.view !== "sessions")
        return;
      state.messages = array(result.items).reverse();
      state.hasMore = !!result.has_more;
      state.beforeId = result.before_id;
      renderMessages();
    }
  } catch (error) {
    if (request === state.conversationRequest && $("#message-scroll"))
      $("#message-scroll").innerHTML = errorMarkup(
        error,
        "reload-conversation",
      );
  }
}
function renderMessages() {
  const el = $("#message-scroll");
  if (!el) return;
  if (state.sessionTab === "ignored") {
    el.innerHTML = state.ignored.length
      ? state.ignored
          .map(
            (item) =>
              `<section class="ignored-group"><div class="ignored-head">${icon("filter")}<span>${e(item.reason || "已过滤")} · ${e(date(item.created_at, true))}</span></div>${array(item.messages).map(messageMarkup).join("")}</section>`,
          )
          .join("")
      : empty(
          "没有被过滤的消息",
          "过滤记录会保留原文和判断原因。",
          "",
          "filter",
        );
    return;
  }
  el.innerHTML = `${state.hasMore ? '<button class="load-more" data-action="load-more">加载更早的消息</button>' : ""}${state.messages.length ? state.messages.map(messageMarkup).join("") : empty("还没有导入消息", "导入该会话历史，或等待微信中的新消息。", '<button class="button small" data-action="history" data-id="' + e(state.selectedSession) + '">' + icon("download") + "导入历史</button>", "message")}`;
}
async function loadMore(button) {
  const id = state.selectedSession;
  const tab = state.sessionTab;
  const request = state.conversationRequest;
  await busy(button, async () => {
    const result = await api(
      `/api/sessions/${encodeURIComponent(id)}/messages?limit=40&before_id=${encodeURIComponent(state.beforeId || "")}`,
    );
    if (
      id !== state.selectedSession ||
      tab !== state.sessionTab ||
      request !== state.conversationRequest
    )
      return;
    const container = $("#message-scroll");
    const height = container.scrollHeight;
    const seen = new Set(state.messages.map((m) => m.id));
    state.messages = [
      ...array(result.items)
        .reverse()
        .filter((m) => !seen.has(m.id)),
      ...state.messages,
    ];
    state.hasMore = !!result.has_more;
    state.beforeId = result.before_id;
    renderMessages();
    container.scrollTop = container.scrollHeight - height;
  });
}

function rulesView() {
  return `${pageHeader("过滤规则", "减少无关的声音，保留清晰的行动。", `<button class="button primary" data-action="new-rule">${icon("plus")}新建规则</button>`, "SIGNAL OVER NOISE")}<section class="panel"><div class="rules-toolbar"><span>按你的偏好过滤消息与识别行动</span><span id="rules-enabled-count">— 条规则已启用</span></div><div id="rules-list" class="rules-list">${loading("正在读取规则")}</div></section>`;
}
async function loadRules() {
  state.rules = array(await api("/api/rules"));
  if (state.view === "rules") renderRules();
}
function renderRules() {
  $("#rules-enabled-count").textContent =
    `${state.rules.filter((r) => r.enabled).length} 条规则已启用`;
  $("#rules-list").innerHTML = state.rules.length
    ? state.rules
        .map(
          (rule) =>
            `<article class="rule-row"><span class="rule-icon">${icon(rule.action === "extract_task" ? "bolt" : "filter")}</span><div class="rule-content"><h3>${e(rule.name)} <span class="priority-tag ${rule.action === "extract_task" ? "low" : ""}">${rule.action === "extract_task" ? "提炼待办" : "忽略消息"}</span></h3><p>${e(rule.description || "按正则表达式匹配消息内容")}</p><code class="rule-pattern">${e(rule.pattern)}</code></div><div class="rule-controls">${switchMarkup("", rule.enabled, `启用规则：${rule.name}`, `data-action="toggle-rule" data-id="${e(rule.id)}"`)}<button class="icon-button" data-action="edit-rule" data-id="${e(rule.id)}" aria-label="编辑规则：${e(rule.name)}">${icon("edit")}</button><button class="icon-button" data-action="delete-rule" data-id="${e(rule.id)}" aria-label="删除规则：${e(rule.name)}">${icon("trash")}</button></div></article>`,
        )
        .join("")
    : empty(
        "把你的判断，变成规则",
        "过滤群内通知、固定闲聊，或优先提炼行动消息。",
        '<button class="button small" data-action="new-rule">' +
          icon("plus") +
          "创建第一条规则</button>",
        "filter",
      );
}
function profileView() {
  return `${pageHeader("我的画像", "让拾光理解你，判断哪些事情值得留下。", "", "PERSONAL CONTEXT")}<div class="profile-layout"><section class="panel form-panel"><div class="section-header"><h2>给拾光的一封简短说明</h2><p>用 Markdown 记录你的身份、工作范围与待办偏好。</p></div><form id="profile-form"><label class="field"><span class="sr-only" style="position:absolute;clip-path:inset(50%);width:1px;height:1px;overflow:hidden">个人画像 Markdown</span><textarea class="editor" id="profile-editor" name="markdown" spellcheck="false" maxlength="30000" placeholder="描述你的工作角色、正在关注的项目，以及你希望保留或忽略的消息类型。">${e(state.profile)}</textarea></label><div class="form-footer"><span class="profile-status" id="profile-status">修改后保存，下一次提炼时生效</span><button class="button primary" type="submit">${icon("check")}保存画像</button></div><div id="profile-error"></div></form></section><aside class="panel profile-tips"><h3>什么样的信息更有帮助？</h3><div class="tip-section"><strong>你的身份与职责</strong><p>工作角色、负责的事情，以及需要你作出决定的范围。</p></div><div class="tip-section"><strong>正在关注的目标</strong><p>项目名称、协作关系、近期需要推进的工作。</p></div><div class="tip-section"><strong>你的判断偏好</strong><p>哪些消息算待办，哪些通知可以略过，如何确定优先级。</p></div><div class="tip-section"><strong>保持简短与准确</strong><p>画像会随消息一起参与提炼，变动时再更新即可。</p></div></aside></div>`;
}
const numericFields = [
  [
    "merge_window_seconds",
    "消息合并窗口",
    "秒",
    "将相邻消息串联为完整语境",
    0,
    600,
  ],
  [
    "settle_window_seconds",
    "新消息等待时间",
    "秒",
    "等对话落定后再判断",
    0,
    600,
  ],
  [
    "batch_interval_seconds",
    "自动提炼间隔",
    "秒",
    "定期处理等待中的消息",
    15,
    86400,
  ],
  [
    "history_limit",
    "每次历史导入上限",
    "条",
    "读取真实会话中可用的历史消息",
    10,
    1000,
  ],
];
function settingsView() {
  const s = state.settings;
  return `${pageHeader("设置与数据", "让拾光按照你的节奏工作。", "", "PREFERENCES")}<div class="settings-layout"><section class="panel form-panel"><form id="settings-form"><div class="section-header"><h2>智能提炼</h2><p>使用兼容 OpenAI 的模型服务，提炼请求关闭思考模式。</p></div><div class="switch-row"><div><strong>启用云端模型</strong><p>从完整会话语境中判断与合并待办</p></div>${switchMarkup("ai-enabled", s.ai_enabled, "启用云端模型", 'name="ai_enabled"')}</div><div class="form-grid" style="margin-top:15px"><label class="field wide">服务地址<input name="ai_base_url" value="${e(s.ai_base_url || "")}" placeholder="https://api.example.com/v1" type="url" autocomplete="off"></label><label class="field">模型名称<input name="ai_model" value="${e(s.ai_model || "")}" placeholder="模型 ID" autocomplete="off"></label><label class="field">API Key<input name="ai_api_key" value="" placeholder="${s.ai_key_configured ? "已配置 · 留空保留现有密钥" : "输入 API Key"}" type="password" autocomplete="new-password"><span class="field-note">密钥随配置保存，留空不会清除。</span></label></div><div class="form-footer"><span class="hint" id="probe-result">保存配置后可检查连接</span><button type="button" class="button small" data-action="probe">${icon("bolt")}检查模型连接</button></div><div class="form-divider"></div><div class="section-header"><h2>消息与处理节奏</h2><p>避免逐条打断，让连贯的消息一起被理解。</p></div><div class="switch-row"><div><strong>启动时连接微信</strong><p>打开拾光后自动建立微信读取连接</p></div>${switchMarkup("wechat-enabled", s.wechat_enabled, "启动时连接微信", 'name="wechat_enabled"')}</div><div class="form-grid" style="margin-top:15px">${numericFields.map(([key, label, unit, note, min, max]) => `<label class="field">${label}（${unit}）<input name="${key}" type="number" min="${min}" max="${max}" value="${e(s[key] ?? (key === "history_limit" ? 100 : key === "batch_interval_seconds" ? 30 : 5))}" required><span class="field-note">${note}</span></label>`).join("")}</div><div id="settings-error"></div><div class="form-footer"><span class="hint" id="settings-save-hint">配置变更会在保存后生效</span><button class="button primary" type="submit">${icon("check")}保存设置</button></div></form></section><aside class="settings-aside"><section class="side-panel"><h3>微信连接</h3><p id="settings-wechat-state">${e(state.overview?.wechat?.error || (["connected", "running", "listening", "active"].includes(state.overview?.wechat?.state) ? "已连接，可读取关注会话的真实消息。" : "连接前请保持微信客户端已登录。"))}</p><button class="button small" data-action="wechat" style="margin-top:14px">${icon("message")}连接 / 暂停微信</button></section><section class="side-panel"><h3>带走你的工作记录</h3><p>导出待办与原始消息，或为当前工作空间创建一份数据库备份。</p><div class="data-actions" style="margin-top:14px"><a href="/api/export?format=md" class="button small" download>${icon("download")}Markdown</a><a href="/api/export?format=json" class="button small" download>${icon("file")}JSON</a><button class="button small" data-action="backup">${icon("archive")}创建备份</button></div><p id="backup-result" style="margin-top:12px"></p></section><section class="side-panel"><h3>始终知道依据</h3><p>提炼结果保留原始消息与事件记录。完成、忽略与恢复都能在待办详情中追溯。</p></section></aside></div>`;
}

async function navigate(view) {
  if (!views[view]) view = "tasks";
  const version = ++state.routeVersion;
  state.view = view;
  closeDrawer();
  closeModal();
  $$(".main-nav a,.settings-nav").forEach((el) => {
    const active = el.dataset.nav === view;
    el.classList.toggle("active", active);
    if (active) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  $("#breadcrumb-current").textContent = views[view];
  document.title = `${views[view]} · 拾光`;
  const content = $("#content");
  content.innerHTML = loading();
  try {
    if (!state.overview) await overview();
    if (version !== state.routeVersion) return;
    if (view === "tasks") {
      if (!state.profileLoaded) {
        try {
          const profile = await api("/api/profile");
          state.profile = profile.markdown || "";
          state.profileLoaded = true;
        } catch {
          state.profileLoaded = true;
        }
      }
      if (version !== state.routeVersion) return;
      content.innerHTML = taskView();
      activeTabs();
      updateStats();
      if (state.first) {
        content.classList.add("first-entry");
        setTimeout(() => content.classList.remove("first-entry"), 700);
        state.first = false;
      }
      await loadTasks();
    }
    if (view === "sessions") {
      content.innerHTML = sessionView();
      renderSessions();
      await loadSessions();
    }
    if (view === "rules") {
      content.innerHTML = rulesView();
      await loadRules();
    }
    if (view === "profile") {
      const result = await api("/api/profile");
      if (version !== state.routeVersion) return;
      state.profile = result.markdown || "";
      state.profileLoaded = true;
      content.innerHTML = profileView();
    }
    if (view === "settings") {
      const result = await api("/api/settings");
      if (version !== state.routeVersion) return;
      state.settings = result;
      content.innerHTML = settingsView();
    }
  } catch (error) {
    if (version === state.routeVersion) content.innerHTML = errorMarkup(error);
  }
}

async function openTask(id) {
  drawerReturnFocus = document.activeElement;
  state.drawerEdit = false;
  state.task = null;
  const request = ++state.drawerRequest;
  $("#drawer-backdrop").hidden = false;
  const drawer = $("#task-drawer");
  drawer.hidden = false;
  drawer.innerHTML = `<div class="drawer-header"><span>${icon("check-circle")}待办详情</span><button class="icon-button" data-action="close-drawer" aria-label="关闭详情">${icon("close")}</button></div>${loading("正在读取待办与来源")}`;
  document.body.style.overflow = "hidden";
  drawer.focus();
  try {
    const task = await api(`/api/tasks/${encodeURIComponent(id)}`);
    if (request !== state.drawerRequest || drawer.hidden) return;
    state.task = task;
    renderTaskDetail();
  } catch (error) {
    if (request === state.drawerRequest && !drawer.hidden)
      drawer.innerHTML = `<div class="drawer-header"><span>待办详情</span><button class="icon-button" data-action="close-drawer" aria-label="关闭详情">${icon("close")}</button></div><div class="drawer-scroll">${errorMarkup(error, "retry-task")}</div>`;
    drawer.dataset.failedId = String(id);
  }
}
function closeDrawer() {
  ++state.drawerRequest;
  $("#task-drawer").hidden = true;
  $("#drawer-backdrop").hidden = true;
  state.drawerEdit = false;
  state.task = null;
  document.body.style.overflow = "";
  if (drawerReturnFocus?.isConnected) drawerReturnFocus.focus();
  drawerReturnFocus = null;
}
const eventLabels = {
  CREATE: "拾取为待办",
  UPDATE: "合并更新",
  MERGE: "合并新消息",
  RESOLVE: "根据消息更新状态",
  MANUAL: "手动修改",
  IGNORE: "忽略消息",
  created: "拾取为待办",
  updated: "更新待办",
  completed: "标记完成",
  cancelled: "忽略待办",
  restored: "恢复待办",
  merged: "合并新消息",
  edited: "手动编辑",
  status_changed: "更新状态",
  create: "拾取为待办",
  update: "更新待办",
};
const sourceLabels = {
  ai: "智能提炼",
  llm: "智能提炼",
  rule: "规则识别",
  manual: "手动操作",
  user: "手动操作",
};
function eventDetail(event) {
  const detail = event.detail || {};
  const labels = {
    title: "标题",
    description: "说明",
    owner: "负责人",
    priority: "优先级",
    due_at: "截止时间",
    status: "状态",
  };
  const show = (key, value) =>
    value == null || value === ""
      ? "未设置"
      : key === "status"
        ? statuses[value] || value
        : key === "priority"
          ? priorities[value] || value
          : key === "due_at"
            ? date(value, true)
            : String(value).slice(0, 80);
  if (detail.before && detail.after) {
    return Object.keys(detail.after)
      .filter((key) => labels[key] && detail.before[key] !== detail.after[key])
      .map(
        (key) =>
          `<div class="event-detail">${e(labels[key])}：${e(show(key, detail.before[key]))} → ${e(show(key, detail.after[key]))}</div>`,
      )
      .join("");
  }
  if (detail.resolution)
    return `<div class="event-detail">状态：${e(statuses[detail.resolution] || detail.resolution)}</div>`;
  return detail.reason
    ? `<div class="event-detail">${e(detail.reason === "same_action_and_distinctive_object" ? "相同行动与对象，追加消息来源" : detail.reason)}</div>`
    : "";
}
function longCopy(text, label, cls = "context-box") {
  const value = String(text || "");
  return value.length <= 400
    ? `<div class="${cls}">${e(value)}</div>`
    : `<div class="${cls}">${e(value.slice(0, 360))}…</div><details class="long-copy"><summary>${e(label)}</summary><div class="${cls}">${e(value)}</div></details>`;
}
function renderTaskDetail() {
  const t = state.task;
  if (!t) return;
  $("#task-drawer").innerHTML =
    `<div class="drawer-header"><span>${icon("check-circle")}待办详情</span><button class="icon-button" data-action="close-drawer" aria-label="关闭详情">${icon("close")}</button></div><div class="drawer-scroll"><div class="drawer-tags"><span class="priority-tag ${e(t.priority)}">${e(priorities[t.priority] || "普通")}</span><span class="status-tag">${icon(t.status === "completed" ? "check" : t.status === "cancelled" ? "archive" : "clock")}${e(statuses[t.status] || t.status)}</span></div><h2>${e(t.title)}</h2><dl class="detail-meta"><dt>来源会话</dt><dd>${e(t.session_name || "微信会话")}</dd><dt>负责人</dt><dd>${e(t.owner || "未指定")}</dd><dt>截止时间</dt><dd>${t.due_at ? e(date(t.due_at, true)) : "未设置"}</dd><dt>拾取时间</dt><dd>${e(date(t.created_at, true))}</dd></dl>${t.description && t.description.trim() !== String(t.context_summary || "").trim() ? longCopy(t.description, "查看完整说明", "detail-description") : ""}${t.context_summary ? `<section class="detail-section"><h3>${icon("sparkles")}上下文摘要</h3>${longCopy(t.context_summary, "查看完整上下文")}</section>` : ""}<section class="detail-section"><h3>${icon("message")}原始消息 <span class="count">${array(t.messages).length}</span></h3>${array(t.messages).length ? array(t.messages).map(messageMarkup).join("") : '<div class="context-box">该事项尚无可用的来源消息。</div>'}</section><section class="detail-section"><h3>${icon("clock")}事件轨迹</h3><div class="event-list">${
      array(t.events).length
        ? array(t.events)
            .map(
              (event) =>
                `<div class="event-item">${e(eventLabels[event.action] || event.action)}${event.source ? ` · ${e(sourceLabels[event.source] || event.source)}` : ""}<time>${e(date(event.created_at, true))}</time>${eventDetail(event)}</div>`,
            )
            .join("")
        : '<div class="event-item">暂无事件记录</div>'
    }</div></section></div><div class="drawer-footer"><button class="button" data-action="edit-task">${icon("edit")}编辑</button>${t.status === "pending" ? '<button class="button" data-action="drawer-status" data-status="cancelled">' + icon("archive") + '忽略</button><button class="button primary" data-action="drawer-status" data-status="completed">' + icon("check") + "完成待办</button>" : '<button class="button primary" data-action="drawer-status" data-status="pending">' + icon("undo") + "恢复为进行中</button>"}</div>`;
}
function renderTaskEdit() {
  const t = state.task;
  if (!t) return;
  state.drawerEdit = true;
  $("#task-drawer").innerHTML =
    `<div class="drawer-header"><span>${icon("edit")}编辑待办</span><button class="icon-button" data-action="close-drawer" aria-label="关闭编辑">${icon("close")}</button></div><div class="drawer-scroll"><form class="drawer-edit-form" id="task-form"><label class="field">事项标题<input name="title" value="${e(t.title)}" required maxlength="120"></label><label class="field">补充说明<textarea name="description" rows="5" maxlength="4000">${e(t.description || "")}</textarea></label><div class="form-grid"><label class="field">负责人<input name="owner" value="${e(t.owner || "")}" maxlength="100"></label><label class="field">优先级<select name="priority">${options(Object.entries(priorities), t.priority)}</select></label><label class="field wide">截止时间<input name="due_at" type="datetime-local" value="${e(localDateTime(t.due_at))}"><span class="field-note">留空表示没有明确截止时间。</span></label></div><div id="task-edit-error" style="margin-top:15px"></div><div class="drawer-save-footer"><button type="button" class="button" data-action="cancel-task-edit">取消</button><button class="button primary" type="submit">${icon("check")}保存修改</button></div></form></div>`;
  $('input[name="title"]', $("#task-drawer")).focus();
}
async function patchTask(id, body) {
  const result = await api(`/api/tasks/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body,
  });
  if (state.task && String(state.task.id) === String(id)) {
    const fresh = await api(`/api/tasks/${encodeURIComponent(id)}`);
    state.task = fresh;
    renderTaskDetail();
    state.drawerEdit = false;
  }
  if (state.view === "tasks") await loadTasks();
  await overview();
  return result;
}

function showModal(title, body, footer = "", type = "generic") {
  if (!modalType) modalReturnFocus = document.activeElement;
  modalType = type;
  $("#modal-root").innerHTML =
    `<div class="modal-backdrop"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title" tabindex="-1"><div class="modal-header"><h2 id="modal-title">${e(title)}</h2><button class="icon-button" data-action="close-modal" aria-label="关闭窗口">${icon("close")}</button></div><div class="modal-body">${body}</div>${footer ? `<div class="modal-footer">${footer}</div>` : ""}</section></div>`;
  document.body.style.overflow = "hidden";
  $(".modal").focus();
}
function closeModal() {
  if (!modalType) return;
  modalType = null;
  $("#modal-root").innerHTML = "";
  document.body.style.overflow = $("#task-drawer").hidden ? "" : "hidden";
  if (modalReturnFocus?.isConnected) modalReturnFocus.focus();
  modalReturnFocus = null;
  commandResults = [];
}
async function openCatalog() {
  showModal("关注微信会话", `${loading("正在读取微信会话")}`, "", "catalog");
  try {
    const sessions = array(await api("/api/wechat/catalog"));
    if (modalType !== "catalog") return;
    $(".modal-body").innerHTML =
      `<p class="modal-description">从微信当前可读取的会话中选择。关注后，拾光会读取新消息；也可以在会话页导入历史。</p><label class="inline-search" style="border-bottom:1px solid var(--line);padding-bottom:10px;margin-bottom:6px">${icon("search")}<input id="catalog-search" type="search" placeholder="搜索微信会话…" aria-label="搜索微信当前会话"></label><div class="catalog-list">${
        sessions.length
          ? sessions
              .map((s) => {
                const known = state.sessions.find(
                  (x) => String(x.id) === String(s.id),
                );
                const monitored = known?.monitored || s.monitored;
                return `<div class="catalog-row" data-catalog-name="${e(s.name || s.username || "")}"><span class="session-avatar">${e(initials(s.name || s.username))}</span><span class="session-info"><strong>${e(s.name || s.username)}</strong><small>${e(s.account || "微信会话")}</small></span><button class="button small ${monitored ? "subtle" : ""}" data-action="catalog-monitor" data-id="${e(s.id)}" ${monitored ? "disabled" : ""}>${icon(monitored ? "check" : "plus")}${monitored ? "已关注" : "关注"}</button></div>`;
              })
              .join("")
          : empty(
              "暂时没有可读取的会话",
              "请保持微信已登录，并在微信中打开需要关注的会话。",
              "",
              "message",
            )
      }</div>`;
    $("#catalog-search").focus();
  } catch (error) {
    if (modalType === "catalog")
      $(".modal-body").innerHTML = errorMarkup(error, "reload-catalog");
  }
}
function openRule(rule = null) {
  showModal(
    rule ? "编辑过滤规则" : "新建过滤规则",
    `<form id="rule-form" data-id="${e(rule?.id || "")}"><p class="modal-description">忽略规则匹配整段内容，提炼规则辅助识别完整行动。判定依据会保留在会话的过滤记录中。</p><label class="field">规则名称<input name="name" value="${e(rule?.name || "")}" required maxlength="100" placeholder="为这条规则起一个名字"></label><label class="field" style="margin-top:15px">规则说明<input name="description" value="${e(rule?.description || "")}" maxlength="500" placeholder="记下这条规则的用途"></label><label class="field" style="margin-top:15px">匹配表达式<textarea name="pattern" required maxlength="400" rows="3" style="font-family:Consolas,monospace" placeholder="输入正则表达式">${e(rule?.pattern || "")}</textarea><span class="field-note">匹配每条消息的文本内容，保存时验证表达式。</span></label><label class="field" style="margin-top:15px">命中后的动作<select name="action">${options(
      [
        ["ignore", "忽略消息"],
        ["extract_task", "提炼待办"],
      ],
      rule?.action || "ignore",
    )}</select></label><div class="switch-row"><strong>启用规则</strong>${switchMarkup("", rule?.enabled ?? true, "启用此规则", 'name="enabled"')}</div><div id="rule-error"></div></form>`,
    `<button class="button" data-action="close-modal">取消</button><button class="button primary" type="submit" form="rule-form">${icon("check")}保存规则</button>`,
    "rule",
  );
  $('input[name="name"]', $(".modal")).focus();
}
function confirmDeleteRule(rule) {
  showModal(
    "删除过滤规则",
    `<p class="modal-description">删除「${e(rule.name)}」后，这条规则将不再参与消息过滤。</p><div id="delete-rule-error"></div>`,
    `<button class="button" data-action="close-modal">取消</button><button class="button danger" data-action="confirm-delete-rule" data-id="${e(rule.id)}">${icon("trash")}删除规则</button>`,
    "delete-rule",
  );
}
function openSearch() {
  if (modalType === "command") return;
  showModal("搜索与快速跳转", "", "", "command");
  const modal = $(".modal");
  modal.innerHTML = `<h2 id="modal-title" style="position:absolute;clip-path:inset(50%);width:1px;height:1px;overflow:hidden">搜索与快速跳转</h2><input id="command-search" class="command-search" placeholder="搜索待办，或跳转工作区…" aria-label="搜索待办或工作区" autocomplete="off"><div class="command-results" id="command-results">${Object.entries(
    views,
  )
    .map(
      ([key, label]) =>
        `<button class="command-result" data-action="command-nav" data-view="${key}">${icon(key === "tasks" ? "check-circle" : key === "sessions" ? "message" : key === "rules" ? "filter" : key === "profile" ? "user" : "settings")}<span><strong>${label}</strong><small>跳转到${label}</small></span></button>`,
    )
    .join(
      "",
    )}</div><div class="command-footer"><span><kbd>↑ ↓</kbd> 选择</span><span><kbd>Enter</kbd> 打开</span><span><kbd>Esc</kbd> 关闭</span></div>`;
  $("#command-search").focus();
}
async function commandSearch(query) {
  if (!query) {
    closeModal();
    openSearch();
    return;
  }
  const commandVersion = query;
  $("#command-results").innerHTML = loading("正在搜索");
  try {
    const result = array(
      await api(`/api/tasks?status=all&q=${encodeURIComponent(query)}`),
    );
    if (
      modalType !== "command" ||
      $("#command-search")?.value !== commandVersion
    )
      return;
    commandResults = result;
    $("#command-results").innerHTML = result.length
      ? result
          .map(
            (task) =>
              `<button class="command-result" data-action="command-task" data-id="${e(task.id)}">${icon("check-circle")}<span><strong>${e(task.title)}</strong><small>${e(task.session_name || "微信会话")} · ${e(statuses[task.status] || task.status)}</small></span></button>`,
          )
          .join("")
      : empty("没有匹配的待办", "换一个关键词试试。", "", "search");
  } catch (error) {
    if (modalType === "command")
      $("#command-results").innerHTML = errorMarkup(error, "reload-command");
  }
}

async function startProcess(button, sessionId) {
  await busy(button, async () => {
    const run = await api("/api/process", {
      method: "POST",
      body: sessionId ? { session_id: sessionId } : {},
    });
    state.run = run;
    renderRun();
    toast("已开始梳理真实消息");
    await overview();
  });
}
async function poll() {
  if (document.hidden || state.refreshing) return;
  state.refreshing = true;
  try {
    const prev = state.run?.status;
    await overview();
    if (
      state.run?.id &&
      ["running", "queued", "pending"].includes(state.run.status)
    ) {
      const run = await api(`/api/process/${encodeURIComponent(state.run.id)}`);
      state.run = run;
      renderRun();
    }
    if (
      ["running", "queued", "pending"].includes(prev) &&
      state.run?.status === "completed"
    ) {
      toast("提炼完成，待办已更新");
      if (state.view === "tasks") await loadTasks();
      $$('[data-action="process"]').forEach((el) => (el.disabled = false));
    } else if (prev === "running" && state.run?.status === "cancelled") {
      toast("已取消本次提炼");
      $$('[data-action="process"]').forEach((el) => (el.disabled = false));
    }
    if (
      state.view === "tasks" &&
      !$("#task-drawer").contains(document.activeElement) &&
      !modalType &&
      document.activeElement?.id !== "task-search"
    )
      await loadTasks();
    if (state.view === "sessions") renderSessions();
  } catch (error) {
    $("#sync-label").textContent = "服务暂时不可用";
  } finally {
    state.refreshing = false;
  }
}

document.addEventListener("click", async (event) => {
  const row = event.target.closest(".task-row");
  const button = event.target.closest("[data-action]");
  if (row && !button) {
    await openTask(row.dataset.taskId);
    return;
  }
  if (!button) return;
  const action = button.dataset.action,
    id = button.dataset.id;
  const value = button.dataset.value;
  if (action === "search") {
    openSearch();
    return;
  }
  if (action === "close-modal") {
    closeModal();
    return;
  }
  if (action === "close-drawer") {
    closeDrawer();
    return;
  }
  if (action === "command-nav") {
    const view = button.dataset.view;
    closeModal();
    if (state.view === view) await navigate(view);
    else location.hash = view;
    return;
  }
  if (action === "command-task") {
    closeModal();
    await openTask(id);
    return;
  }
  if (action === "reload-command") {
    await commandSearch($("#command-search").value.trim());
    return;
  }
  if (action === "refresh") {
    await busy(button, async () => {
      await overview();
      if (state.view === "tasks") await loadTasks();
      if (state.view === "sessions") {
        state.sessions = array(await api("/api/sessions"));
        renderSessions();
        if (state.selectedSession) await loadConversation();
      }
      if (state.view === "rules") await loadRules();
      toast("数据已刷新");
    });
    return;
  }
  if (action === "retry") {
    await navigate(state.view);
    return;
  }
  if (action === "retry-task") {
    await openTask($("#task-drawer").dataset.failedId);
    return;
  }
  if (action === "reload-tasks") {
    await loadTasks();
    return;
  }
  if (action === "task-status") {
    state.filter.status = value;
    updateTaskFilterUI();
    await loadTasks();
    return;
  }
  if (action === "stat-filter") {
    const f = button.dataset.filter;
    if (f === "pending" || f === "completed") {
      state.filter.status = f;
      state.filter.due = "all";
    } else {
      state.filter.status = "pending";
      state.filter.due = f;
    }
    updateTaskFilterUI();
    await loadTasks();
    return;
  }
  if (action === "clear-filters") {
    state.filter = {
      status: state.filter.status,
      q: "",
      priority: "",
      session_id: "",
      due: "all",
    };
    updateTaskFilterUI();
    await loadTasks();
    return;
  }
  if (action === "go-sessions") {
    location.hash = "sessions";
    return;
  }
  if (action === "open-task") {
    await openTask(id);
    return;
  }
  if (action === "toggle-task") {
    const t = state.tasks.find((t) => String(t.id) === String(id));
    if (t)
      await busy(button, async () => {
        const status = t.status === "completed" ? "pending" : "completed";
        await patchTask(id, { status, version: t.version });
        toast(status === "completed" ? "已完成，做得很好。" : "已恢复为进行中");
      });
    return;
  }
  if (action === "drawer-status") {
    const t = state.task;
    if (t)
      await busy(button, async () => {
        await patchTask(t.id, {
          status: button.dataset.status,
          version: t.version,
        });
        toast(
          `已${button.dataset.status === "pending" ? "恢复为进行中" : button.dataset.status === "completed" ? "完成待办" : "忽略待办"}`,
        );
      });
    return;
  }
  if (action === "edit-task") {
    renderTaskEdit();
    return;
  }
  if (action === "cancel-task-edit") {
    state.drawerEdit = false;
    renderTaskDetail();
    return;
  }
  if (action === "wechat") {
    const connected = ["running", "connected", "active", "listening"].includes(
      state.overview?.wechat?.state,
    );
    await busy(button, async () => {
      await api(`/api/wechat/${connected ? "stop" : "start"}`, {
        method: "POST",
      });
      await overview();
      toast(connected ? "微信连接已暂停" : "微信连接已启动");
    });
    return;
  }
  if (action === "process") {
    await startProcess(button);
    return;
  }
  if (action === "process-session") {
    await startProcess(button, id);
    return;
  }
  if (action === "cancel-process") {
    if (state.run?.id)
      await busy(button, async () => {
        state.run = await api(
          `/api/process/${encodeURIComponent(state.run.id)}/cancel`,
          { method: "POST" },
        );
        renderRun();
        toast("已请求取消提炼");
        await overview();
      });
    return;
  }
  if (action === "dismiss-run") {
    state.run = null;
    $("#run-strip").hidden = true;
    return;
  }
  if (action === "select-session") {
    await selectSession(id);
    return;
  }
  if (action === "session-tab") {
    state.sessionTab = value;
    $$('[data-action="session-tab"]').forEach((el) => {
      el.classList.toggle("active", el.dataset.value === value);
      el.setAttribute("aria-selected", String(el.dataset.value === value));
    });
    activeTabs();
    if ($("#message-scroll"))
      $("#message-scroll").innerHTML = loading("正在读取");
    await loadConversation();
    return;
  }
  if (action === "reload-conversation") {
    await loadConversation();
    return;
  }
  if (action === "load-more") {
    await loadMore(button);
    return;
  }
  if (action === "monitor-session" || action === "catalog-monitor") {
    await busy(button, async () => {
      const monitored =
        action === "catalog-monitor" || button.dataset.enabled === "true";
      await api(`/api/sessions/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: { monitored },
      });
      await overview();
      toast(monitored ? "已关注该会话" : "已取消关注");
      if (action === "catalog-monitor") {
        button.innerHTML = icon("check") + "已关注";
        button.classList.add("subtle");
        button.dataset.action = "";
        button.disabled = true;
      }
      if (state.view === "sessions") {
        renderSessions();
        if (String(state.selectedSession) === String(id))
          await selectSession(id);
      }
    });
    if (action === "catalog-monitor" && button.dataset.action === "")
      button.disabled = true;
    return;
  }
  if (action === "read-session") {
    await busy(button, async () => {
      await api(`/api/sessions/${encodeURIComponent(id)}/read`, {
        method: "POST",
      });
      await overview();
      renderSessions();
      toast("已标记为已读");
    });
    return;
  }
  if (action === "history") {
    const original = button.innerHTML;
    const sessionId = id || state.selectedSession;
    button.innerHTML =
      '<span class="spinner" style="width:12px;height:12px"></span>正在导入…';
    button.setAttribute("aria-busy", "true");
    await busy(button, async () => {
      const response = await api(
        `/api/sessions/${encodeURIComponent(sessionId)}/history`,
        {
          method: "POST",
          body: { limit: state.settings.history_limit || 100 },
          timeoutMs: 120000,
        },
      );
      toast(
        `历史读取完成，新增 ${response.inserted ?? response.imported ?? 0} 条真实消息`,
      );
      await overview();
      if (
        state.view === "sessions" &&
        String(state.selectedSession) === String(sessionId)
      ) {
        renderSessions();
        await loadConversation();
      }
    });
    if (button.isConnected) {
      button.innerHTML = original;
      button.removeAttribute("aria-busy");
    }
    return;
  }
  if (action === "catalog" || action === "reload-catalog") {
    await openCatalog();
    return;
  }
  if (action === "new-rule") {
    openRule();
    return;
  }
  if (action === "edit-rule") {
    const rule = state.rules.find((r) => String(r.id) === String(id));
    if (rule) openRule(rule);
    return;
  }
  if (action === "delete-rule") {
    const rule = state.rules.find((r) => String(r.id) === String(id));
    if (rule) confirmDeleteRule(rule);
    return;
  }
  if (action === "confirm-delete-rule") {
    await busy(button, async () => {
      await api(`/api/rules/${encodeURIComponent(id)}`, { method: "DELETE" });
      closeModal();
      await loadRules();
      toast("过滤规则已删除");
    });
    return;
  }
  if (action === "probe") {
    await busy(button, async () => {
      const result = await api("/api/settings/probe", {
        method: "POST",
        timeoutMs: 190000,
      });
      if ($("#probe-result"))
        $("#probe-result").textContent = result.ok
          ? `${result.model || state.settings.ai_model || "模型"}连接正常`
          : result.error || "模型连接失败";
      toast(
        result.ok ? "模型服务连接正常" : result.error || "模型连接失败",
        !result.ok,
      );
    });
    return;
  }
  if (action === "backup") {
    await busy(button, async () => {
      const result = await api("/api/backup", { method: "POST" });
      $("#backup-result").textContent = result.file
        ? `备份已创建：${result.file}`
        : "数据库备份已创建";
      toast("工作空间备份已创建");
    });
    return;
  }
});

document.addEventListener("change", async (event) => {
  const target = event.target;
  const key = {
    "priority-filter": "priority",
    "session-filter": "session_id",
    "due-filter": "due",
  }[target.id];
  if (key) {
    state.filter[key] = target.value;
    await loadTasks();
  }
  if (target.dataset.action === "toggle-rule") {
    const id = target.dataset.id;
    target.disabled = true;
    const checked = target.checked;
    try {
      await api(`/api/rules/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: { enabled: checked },
      });
      const rule = state.rules.find((r) => String(r.id) === String(id));
      if (rule) rule.enabled = checked;
      $("#rules-enabled-count").textContent =
        `${state.rules.filter((r) => r.enabled).length} 条规则已启用`;
      toast(checked ? "规则已启用" : "规则已暂停");
    } catch (error) {
      target.checked = !checked;
      toast(error.message, true);
    } finally {
      target.disabled = false;
    }
  }
});
document.addEventListener("input", (event) => {
  const input = event.target;
  if (input.id === "task-search") {
    state.filter.q = input.value;
    clearTimeout(filterTimer);
    filterTimer = setTimeout(loadTasks, 250);
  }
  if (input.id === "session-search") {
    state.sessionSearch = input.value;
    renderSessions();
  }
  if (input.id === "catalog-search") {
    const q = input.value.toLocaleLowerCase();
    $$(".catalog-row").forEach(
      (el) =>
        (el.hidden = !el.dataset.catalogName.toLocaleLowerCase().includes(q)),
    );
  }
  if (input.id === "command-search") {
    clearTimeout(commandTimer);
    commandTimer = setTimeout(() => commandSearch(input.value.trim()), 220);
  }
  if (input.id === "profile-editor")
    $("#profile-status").textContent = "有未保存的修改";
  if (input.closest("#settings-form"))
    $("#settings-save-hint").textContent = "有未保存的修改";
});

document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (
    !["task-form", "settings-form", "profile-form", "rule-form"].includes(
      form.id,
    )
  )
    return;
  event.preventDefault();
  const submit =
    $(`button[type="submit"][form="${form.id}"]`) ||
    $('button[type="submit"]', form);
  if (submit?.disabled) return;
  submit.disabled = true;
  const data = new FormData(form);
  const errorEl = $(
    {
      "task-form": "#task-edit-error",
      "settings-form": "#settings-error",
      "profile-form": "#profile-error",
      "rule-form": "#rule-error",
    }[form.id],
  );
  if (errorEl) errorEl.innerHTML = "";
  try {
    if (form.id === "task-form") {
      const body = Object.fromEntries(data);
      body.title = body.title.trim();
      if (!body.title) throw new Error("请填写事项标题");
      body.due_at = body.due_at
        ? new Date(body.due_at + "+08:00").toISOString()
        : null;
      body.version = state.task.version;
      await patchTask(state.task.id, body);
      toast("待办修改已保存");
    }
    if (form.id === "profile-form") {
      const markdown = String(data.get("markdown") || "");
      await api("/api/profile", { method: "PUT", body: { markdown } });
      state.profile = markdown;
      $("#profile-status").textContent = "画像已保存，下次提炼时生效";
      toast("个人画像已保存");
    }
    if (form.id === "settings-form") {
      const body = Object.fromEntries(data);
      body.ai_enabled = form.elements.ai_enabled.checked;
      body.wechat_enabled = form.elements.wechat_enabled.checked;
      for (const [key] of numericFields) body[key] = Number(body[key]);
      if (!body.ai_api_key.trim()) delete body.ai_api_key;
      state.settings = await api("/api/settings", { method: "PATCH", body });
      $("#settings-save-hint").textContent = "设置已保存";
      form.elements.ai_api_key.value = "";
      form.elements.ai_api_key.placeholder = state.settings.ai_key_configured
        ? "已配置 · 留空保留现有密钥"
        : "输入 API Key";
      toast("设置已保存");
    }
    if (form.id === "rule-form") {
      const body = Object.fromEntries(data);
      body.enabled = form.elements.enabled.checked;
      body.name = body.name.trim();
      if (!body.name) throw new Error("请填写规则名称");
      const id = form.dataset.id;
      await api(id ? `/api/rules/${encodeURIComponent(id)}` : "/api/rules", {
        method: id ? "PATCH" : "POST",
        body,
      });
      closeModal();
      if (state.view === "rules") await loadRules();
      toast("过滤规则已保存");
    }
  } catch (error) {
    if (errorEl)
      errorEl.innerHTML = `<div class="inline-error" style="margin-top:14px">${e(error.status === 409 ? "待办已被其他操作更新。你的修改仍保留，请先查看最新内容后再保存。" : error.message)}${error.status === 409 ? '<br><button type="button" class="button small" data-action="conflict-reload" style="margin-top:7px">查看最新版本</button>' : ""}</div>`;
    toast(error.message, true);
  } finally {
    if (submit?.isConnected) submit.disabled = false;
  }
});
document.addEventListener("click", async (event) => {
  if (event.target.closest('[data-action="conflict-reload"]')) {
    await openTask(state.task.id);
    toast("已读取最新版本");
  }
});
$("#drawer-backdrop").addEventListener("click", closeDrawer);
$("#modal-root").addEventListener("click", (event) => {
  if (event.target.classList.contains("modal-backdrop")) closeModal();
});
document.addEventListener("keydown", (event) => {
  if (
    (event.ctrlKey || event.metaKey) &&
    event.key.toLocaleLowerCase() === "k"
  ) {
    event.preventDefault();
    openSearch();
    return;
  }
  if (event.key === "Escape") {
    if (modalType) closeModal();
    else if (!$("#task-drawer").hidden) closeDrawer();
    return;
  }
  if (
    modalType === "command" &&
    ["ArrowDown", "ArrowUp", "Enter"].includes(event.key)
  ) {
    const results = $$(".command-result");
    if (!results.length) return;
    const index = results.indexOf(document.activeElement);
    if (event.key === "Enter") {
      if (document.activeElement.id === "command-search") {
        event.preventDefault();
        results[0].click();
      }
      return;
    }
    event.preventDefault();
    const next =
      event.key === "ArrowDown"
        ? (index + 1) % results.length
        : index < 1
          ? results.length - 1
          : index - 1;
    results[next].focus();
    return;
  }
  if (event.key === "Tab") {
    const root = modalType
      ? $(".modal")
      : !$("#task-drawer").hidden
        ? $("#task-drawer")
        : null;
    if (!root) return;
    const focusable = $$(
      'button:not(:disabled),a[href],input:not(:disabled),textarea:not(:disabled),select:not(:disabled),[tabindex="0"]',
      root,
    ).filter((el) => el.getClientRects().length);
    if (!focusable.length) {
      event.preventDefault();
      root.focus();
      return;
    }
    const first = focusable[0],
      last = focusable.at(-1);
    if (
      event.shiftKey &&
      (document.activeElement === first || document.activeElement === root)
    ) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
});
window.addEventListener("hashchange", () => navigate(location.hash.slice(1)));
window.addEventListener("resize", () => activeTabs());
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) poll();
});
hydrateIcons();
await navigate(location.hash.slice(1) || "tasks");
pollTimer = setInterval(poll, 7000);
