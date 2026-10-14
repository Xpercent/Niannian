import type { MessageRow } from "../lib/api";
import { dateText } from "../lib/api";
import { cn } from "./ui";

export function Message({ message }: { message: MessageRow }) {
  return (
    <article
      className={cn(
        "rounded-lg border p-4",
        message.is_self
          ? "border-emerald-100 bg-emerald-50/50"
          : "border-stone-100 bg-stone-50/70",
      )}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
        <strong className="font-medium text-stone-600">
          {message.sender_name || (message.is_self ? "我" : "微信消息")}
        </strong>
        <span className="flex items-center gap-2 text-stone-400">
          {message.message_type && message.message_type !== "文本" && (
            <em className="not-italic rounded bg-stone-200/70 px-1.5 py-0.5 text-[10px] text-stone-500">
              {message.message_type}
            </em>
          )}
          <time>{dateText(message.created_at, true)}</time>
        </span>
      </div>
      <p className="break-words whitespace-pre-wrap text-sm leading-7 text-stone-700">
        {message.content}
      </p>
    </article>
  );
}
