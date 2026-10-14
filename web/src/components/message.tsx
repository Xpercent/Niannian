import { useState } from "react";
import type { MessageRow } from "../lib/api";
import { dateText, mediaUrl } from "../lib/api";
import { Dialog, cn } from "./ui";

export function Message({ message }: { message: MessageRow }) {
  const [zoom, setZoom] = useState(false);
  const media = mediaUrl(message.metadata?.local_file);
  const picture = message.metadata?.attachment?.kind === "image";
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
      {picture &&
        (media ? (
          <>
            <button
              type="button"
              onClick={() => setZoom(true)}
              aria-label="放大查看图片"
              className="mt-2 block w-fit cursor-zoom-in overflow-hidden rounded-md ring-stone-200 transition hover:ring-2 focus-visible:outline-none focus-visible:ring-2"
            >
              <img
                src={media}
                alt="微信图片"
                loading="lazy"
                className="max-h-64 max-w-full object-contain"
              />
            </button>
            <Dialog open={zoom} onClose={() => setZoom(false)} className="max-w-3xl">
              <img
                src={media}
                alt="微信图片"
                className="max-h-[70vh] w-full rounded-md bg-stone-100 object-contain"
              />
              <p className="mt-2 text-center text-xs text-stone-400">
                {message.metadata?.tier_label || "本机图片"} · 按 Esc 关闭
              </p>
            </Dialog>
          </>
        ) : (
          <p className="mt-2 text-xs text-stone-400">
            本机暂无该图片文件，在微信里翻到这条记录后会自动补上
          </p>
        ))}
    </article>
  );
}
