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

export type AttachmentInfo = { kind: string };

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
  created_at: string;
  sort_seq: number;
  metadata: MessageMeta;
};

export type MessagePage = {
  items: MessageRow[];
  before_seq: number | null;
  before_id: number | null;
  has_more: boolean;
};

export type WechatStatus = {
  state: string;
  account: string;
  error: string | null;
  running: boolean;
  last_sync_at: string | null;
  sessions: number;
  imported: number;
  message_count?: number;
  monitored_count?: number;
};

const REQUEST_TIMEOUT_MS = 120000;

/** 重新导入深历史会同步读上游库，后端超时 300 秒，前端要等得比它久。 */
export const LONG_REQUEST_MS = 310000;

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

export const dateText = (value?: string | null, full = false) => {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    ...(full ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
  }).format(date);
};

export const clockText = (value?: string | null) => {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
};

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
};

export type SettingsResponse = {
  values: SettingsValues;
  restart_required: string[];
  config_file: string;
  config_exists: boolean;
};

export type SettingsSaveResult = {
  values: SettingsValues;
  /** 已写入配置且即时生效的键。 */
  applied: string[];
  /** 已写入配置但要重启才生效的键。 */
  restart: string[];
  unchanged: boolean;
};

export const putJson = (body: unknown): RequestInit => ({
  method: "PUT",
  body: JSON.stringify(body),
});
