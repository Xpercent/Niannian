import { useId, type ReactNode } from "react";
import { AlertCircle, Inbox, Loader2 } from "lucide-react";
import { Button } from "./ui";

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <label className="grid gap-2 text-sm font-medium text-stone-700">
      {label}
      {children}
      {hint && (
        <span
          id={id}
          className="text-xs font-normal leading-relaxed text-stone-500"
        >
          {hint}
        </span>
      )}
    </label>
  );
}
export function Loading() {
  return (
    <div
      role="status"
      className="flex min-h-48 items-center justify-center gap-2 text-sm text-stone-400"
    >
      <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
      正在读取数据…
    </div>
  );
}
export function Empty({
  title,
  description,
}: {
  title: string;
  description?: string;
}) {
  return (
    <div className="flex min-h-60 flex-col items-center justify-center gap-3 px-6 text-center">
      <div className="rounded-2xl bg-stone-50 p-4">
        <Inbox className="size-7 text-stone-400" />
      </div>
      <p className="text-sm font-medium text-stone-600">{title}</p>
      {description && (
        <p className="max-w-sm text-xs leading-6 text-stone-400">
          {description}
        </p>
      )}
    </div>
  );
}
export function ErrorNotice({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700"
    >
      <AlertCircle className="size-4 shrink-0" />
      <span className="min-w-0 flex-1 break-words">{message}</span>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  );
}
