import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), 90000);
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
      const detail =
        data?.detail ?? data?.error ?? `请求失败 (${response.status})`;
      const message = Array.isArray(detail)
        ? detail.map((item) => item.msg).join("；")
        : detail;
      const error = new Error(String(message)) as Error & { status: number };
      error.status = response.status;
      throw error;
    }
    return data as T;
  } catch (e) {
    if (timeout.signal.aborted && !init.signal?.aborted)
      throw new Error("请求超时，请稍后刷新确认结果");
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

export const json = (body: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
});
export const patchJson = (body: unknown): RequestInit => ({
  method: "PATCH",
  body: JSON.stringify(body),
});
export const putJson = (body: unknown): RequestInit => ({
  method: "PUT",
  body: JSON.stringify(body),
});
export const dateText = (value?: string | null, full = false) => {
  if (!value) return "未设置";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "未设置";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    ...(full ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
  }).format(date);
};
export const localInputTime = (value?: string | null) => {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(date)
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
};
export const statusNames: Record<string, string> = {
  pending: "进行中",
  completed: "已完成",
  cancelled: "已忽略",
};
export const secondsText = (value: number | null | undefined) => {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (value < 1) return `${Math.round(value * 1000)} 毫秒`;
  if (value < 60) return `${value.toFixed(2)} 秒`;
  return `${Math.floor(value / 60)} 分 ${Math.round(value % 60)} 秒`;
};
export const percentText = (value: number | null | undefined) => {
  if (value === null || value === undefined) return "—";
  const percent = value * 100;
  return `${percent >= 10 ? Math.round(percent) : percent.toFixed(1)}%`;
};
export const priorityNames: Record<string, string> = {
  high: "高",
  medium: "普通",
  low: "低",
};

export function safeMedia(value?: string) {
  if (!value) return "";
  const relative = value.startsWith("/media/") ? value.slice(7) : value;
  if (
    /^(?:[a-z]:|[\\/]{2})/i.test(relative) ||
    relative
      .split(/[\\/]/)
      .some((part) => !part || part === "." || part === "..")
  )
    return "";
  return `/media/${relative.replace(/\\/g, "/").split("/").map(encodeURIComponent).join("/")}`;
}
