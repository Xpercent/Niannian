/** 轻量请求层：统一错误文案、超时与 FastAPI detail 解析。 */

export type SessionRow = {
  id: string;
  account: string;
  username: string;
  name: string;
  monitored: boolean;
  unread: number;
  last_message_at: string | null;
  updated_at: string;
  message_count?: number;
  /** 该会话自己设的导入条数；0 表示跟随全局默认。 */
  history_limit?: number;
};

export type ReimportResult = {
  limit: number;
  inserted: number;
  read: number;
  monitored: boolean;
};

export type AttachmentInfo = { kind: string; text?: string; quoted?: QuotedRef };

/** 引用消息的被引定位：全文不入库，只留一行小字和跳转用的 sort_seq。 */
export type QuotedRef = {
  type?: number;
  svrid?: string;
  author?: string;
  name?: string;
  at?: number;
  label?: string;
  sort_seq?: number;
};

export type QuoteTarget = { message_id: number; sort_seq: number };

/** 消息 metadata：采集线程写入，图片只存本机相对链接（不存二进制）。 */
export type MessageMeta = {
  local_id?: number;
  server_id?: number | string | null;
  sort_seq?: number;
  type?: string;
  sender_id?: number;
  origin_sender_username?: string;
  resolved_sender_username?: string;
  attachment?: AttachmentInfo;
  media_md5?: string;
  local_file?: string;
  tier?: string;
  tier_label?: string;
  bytes?: number;
};

export type MessageRow = {
  id: number;
  session_id: string;
  source_id: string;
  sender_key: string;
  sender_name: string;
  is_self: boolean;
  content: string;
  message_type: string;
  /** 后端按解析出的 kind 现算的类型名，比上游粗标签准；缺省时回落到 message_type。 */
  type_label?: string;
  created_at: string;
  sort_seq: number;
  metadata: MessageMeta;
  /** 引用行的跳转目标；被引那条没导入时为 null（前端显示成不可点）。 */
  quote_target?: QuoteTarget | null;
};

export type MessagePage = {
  items: MessageRow[];
  before_seq: number | null;
  before_id: number | null;
  has_more: boolean;
};

/** 会话内正文搜索结果：items 新→旧，query 是后端 strip 过的关键词。 */
export type MessageSearch = { items: MessageRow[]; query: string };

export type TaskPriority = "high" | "medium" | "low";
export type TaskStatus = "pending" | "completed" | "cancelled";

/** 一条待办。`deadline` 是东八区定宽 ISO（`2026-10-18T12:00+08:00`），没期限是 null。 */
export type TaskRow = {
  id: number;
  session_id: string;
  session_name: string;
  title: string;
  description: string;
  deadline: string | null;
  priority: TaskPriority;
  status: TaskStatus;
  /** 依据消息的 sort_seq 列表：会话内唯一，也正是翻页游标。 */
  sources: number[];
  created_at: string;
  updated_at: string;
};

/** 详情多带依据消息的原文（行形状与消息列表一致，所以气泡组件能直接复用）。 */
export type TaskDetail = TaskRow & { messages: MessageRow[] };

/** 待办页那四张指标卡与导航角标上的数字。 */
export type TaskStats = {
  pending: number;
  completed: number;
  cancelled: number;
  due_today: number;
  overdue: number;
  messages: number;
  /** 关注会话里还没喂给模型的条数。 */
  queued: number;
  monitored: number;
};

/** 提炼轮次的进度：`accepted` 只在 POST 的即时回应里有，false 表示上一轮还没结束。 */
export type ExtractStatus = {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  total: number;
  done: number;
  batches: number;
  created: number;
  failed_batches: number;
  session: string | null;
  error: string | null;
  accepted?: boolean;
  /** 本轮在 `extract_runs` 里的那一行；诊断页据此知道哪一轮是当下这轮。 */
  run_id: number | null;
};

/** 诊断页左侧的轮次：每轮一行，`calls`/`failures` 是那轮记下来的请求数与失败数。 */
export type ExtractRun = {
  id: number;
  session_id: string;
  started_at: string;
  finished_at: string | null;
  total: number;
  done: number;
  created: number;
  failed_batches: number;
  error: string | null;
};

export type ExtractRunRow = ExtractRun & { calls: number; failures: number };

/**
 * 一轮里的某一次请求：列表**不带正文**（一轮十几批、每批几 KB），
 * 字符数由后端用 `LENGTH()` 现算，点开哪一条才把那一条的正文取回来。
 */
export type ExtractCallRow = {
  id: number;
  session_id: string;
  session_name: string;
  started_at: string;
  duration_ms: number;
  status: "ok" | "error";
  model: string;
  messages: number;
  error: string;
  request_chars: number;
  response_chars: number;
};

export type ExtractRunDetail = { run: ExtractRun; calls: ExtractCallRow[] };

/** 一次调用的完整正文：`request` 就是发出去的那两条 message，逐字。 */
export type ExtractCallDetail = Omit<
  ExtractCallRow,
  "request_chars" | "response_chars"
> & {
  request: { role: string; content: string }[];
  response: string;
  reasoning: string;
};

export const PRIORITY_NAMES: Record<TaskPriority, string> = {
  high: "高",
  medium: "普通",
  low: "低",
};

export const TASK_STATUS_NAMES: Record<TaskStatus, string> = {
  pending: "进行中",
  completed: "已完成",
  cancelled: "已忽略",
};

export type WechatStatus = {
  state: string;
  account: string;
  error: string | null;
  running: boolean;
  last_sync_at: string | null;
  sessions: number;
  imported: number;
};

/** 普通请求都是纯读自己的库，毫秒级返回：等不到就该早点说话，别转圈两分钟。 */
const REQUEST_TIMEOUT_MS = 20000;

/** 会话目录要在采集线程上现场同步微信库，后端给 90 秒，前端要等得比它久。 */
export const CATALOG_REQUEST_MS = 100000;

/** 重新导入深历史会同步读上游库，后端超时 300 秒，前端要等得比它久。 */
export const LONG_REQUEST_MS = 310000;

/**
 * 测试连接在后端最多等服务商 20 秒；开着思考实测能到 21 秒，后端为此放宽到 45 秒。
 * 前端要等得比它久，否则用户看到的是"请求超时"而不是服务商的答复。
 */
export const LLM_TEST_REQUEST_MS = 60000;

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<T> {
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      ...init,
      signal: init.signal
        ? AbortSignal.any([init.signal, timeout.signal])
        : timeout.signal,
      headers: {
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("json")
      ? await response.json()
      : await response.text();
    if (!response.ok) {
      const raw = (data as { detail?: unknown })?.detail ?? data;
      const message = Array.isArray(raw)
        ? raw.map((item) => String(item)).join("；")
        : String(raw || `请求失败 (${response.status})`);
      throw new Error(message);
    }
    return data as T;
  } catch (error) {
    if (timeout.signal.aborted && !init.signal?.aborted)
      throw new Error("请求超时，请稍后刷新确认结果");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export const post = (): RequestInit => ({ method: "POST" });

/** 本机时间一律按东八区显示；认不出的值返回空串而不是 "Invalid Date"。 */
function zhnDate(value: string | null | undefined, parts: Intl.DateTimeFormatOptions) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    ...parts,
  }).format(date);
}

/** 会话与消息上的日期：默认只到「月-日」，`full` 带上时分。 */
export const dateText = (value?: string | null, full = false) =>
  zhnDate(value, {
    month: "2-digit",
    day: "2-digit",
    ...(full ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
  });

/** 状态条上的"上次同步"：只到秒，不带日期。 */
export const clockText = (value?: string | null) =>
  zhnDate(value, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });

/**
 * 库里只存 `<账号指纹>/<文件名>` 形式的本机相对链接，这里拼成 /media 可访问的 URL。
 * 绝对路径、盘符、UNC、`..` 一律不拼（后端 safe_media_path 是最终把关）。
 */
export function mediaUrl(value?: string | null) {
  if (!value) return "";
  const stored = (value.startsWith("/media/") ? value.slice(7) : value).replace(
    /\\/g,
    "/",
  );
  if (/^(?:[a-z]:|\/\/)/i.test(stored)) return "";
  const parts = stored.split("/").filter(Boolean);
  if (!parts.length || parts.length > 2) return "";
  if (parts.some((part) => part === "." || part === "..")) return "";
  return `/media/${parts.map(encodeURIComponent).join("/")}`;
}

export type SettingsValues = {
  history_limit: number;
  poll_seconds: number;
  port: number;
  llm_base_url: string;
  llm_model: string;
  /** 后端**永远**回空串：真密钥只待在 config.json，界面靠 api_key_masked 显示存了哪一把。 */
  llm_api_key: string;
  llm_thinking: boolean;
  llm_include_images: boolean;
  /** null 的意思是**请求体里不发 temperature**（交回服务商默认），不是"0 度"。 */
  llm_temperature: number | null;
  extract_round_limit: number;
  extract_batch_limit: number;
  extract_history_limit: number;
  /** 回的是**真正生效的那段**：库里存空串意思是"用内置"，界面得看到全文才谈得上改。 */
  extract_prompt: string;
  /** 用户画像：库里存什么就回什么，它没有"内置默认"（没人能替用户编自己的身份）。 */
  extract_profile: string;
};

export type SettingsResponse = {
  values: SettingsValues;
  restart_required: string[];
  /** 每个开放键的中文名，由后端给（界面不再抄第二张表）。 */
  labels: Record<string, string>;
  /** 内置提示词原文：「恢复默认」那颗按钮把文本框设成它。 */
  extract_prompt_default: string;
  config_file: string;
  config_exists: boolean;
  api_key_masked: string;
  api_key_set: boolean;
};

export type SettingsSaveResult = {
  values: SettingsValues;
  /** 已写入配置且即时生效的键。 */
  applied: string[];
  /** 已写入配置但要重启才生效的键。 */
  restart: string[];
  unchanged: boolean;
  api_key_masked: string;
  api_key_set: boolean;
};

/** 测试连接的结果：通不通都回 200，界面只认 ok 这一个字段决定这行字变绿还是变红。 */
export type LlmTestResult = {
  ok: boolean;
  elapsed_ms: number;
  model?: string;
  reply?: string;
  thinking?: boolean;
  /** 开关与服务商实际表现不一致时的提醒（例如开了思考却没回思考内容）。 */
  note?: string;
  error?: string;
};

export const putJson = (body: unknown): RequestInit => ({
  method: "PUT",
  body: JSON.stringify(body),
});

/** 带体的 POST：设置页的「测试连接」用它把界面上当前那份草稿送去，不要求先保存。 */
export const postJson = (body: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
});

/** 改待办（标题/说明/优先级/期限/状态）用它。 */
export const patchJson = (body: unknown): RequestInit => ({
  method: "PATCH",
  body: JSON.stringify(body),
});

/**
 * 期限输入框要的是**上海挂钟时间**那种没时区的 `2026-10-18T12:00`：
 * 库里存的就是定宽东八区，截前 16 位正是它在上海读起来的样子，不做二次换算。
 * 认不出来（旧数据没有偏移之类）就返回空串，让人重新选，不猜。
 */
export function deadlineInput(value?: string | null): string {
  if (!value) return "";
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}\+08:00$/.test(value) ? value.slice(0, 16) : "";
}

/** 反向：表单里那份挂钟时间补回东八区再交给后端（后端还会过一次 parse_deadline）。 */
export const deadlinePayload = (value: string): string | null =>
  value ? `${value}+08:00` : null;
