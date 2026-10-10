import { useState } from "react";
import { FileText, ExternalLink } from "lucide-react";
import type { Message as MessageRecord } from "../lib/types";
import { dateText, safeMedia, cn } from "../lib/utils";
import { Button, Dialog, DialogContent, DialogTitle } from "./ui";

export function Message({ message }: { message: MessageRecord }) {
  const [preview, setPreview] = useState(false);
  const attachment = message.metadata?.attachment;
  const media = safeMedia(
    message.metadata?.local_file || attachment?.local_file || attachment?.path,
  );
  const picture =
    attachment?.kind === "image" ||
    ["image", "图片"].includes(message.message_type);
  return (
    <article
      className={cn(
        "rounded-lg border border-stone-100 bg-stone-50/70 p-4",
        message.is_self && "border-emerald-100 bg-emerald-50/50",
      )}
    >
      <div className="mb-2 flex flex-wrap justify-between gap-2 text-xs">
        <strong className="font-medium text-stone-600">
          {message.sender_name || (message.is_self ? "我" : "微信消息")}
        </strong>
        <time className="text-stone-400">
          {dateText(message.created_at, true)}
        </time>
      </div>
      <p className="whitespace-pre-wrap break-words text-sm leading-7 text-stone-700">
        {message.content}
      </p>
      {picture && media ? (
        <>
          <button
            type="button"
            onClick={() => setPreview(true)}
            aria-label="放大查看微信图片附件"
            className="mt-3 block w-fit cursor-zoom-in overflow-hidden rounded-md ring-stone-200 transition hover:ring-2 focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
          >
            <img
              src={media}
              alt="微信图片附件"
              loading="lazy"
              className="max-h-64 max-w-full object-contain"
            />
          </button>
          <Dialog open={preview} onOpenChange={setPreview}>
            <DialogContent
              showCloseButton={false}
              overlayClassName="bg-stone-950/50 backdrop-blur-md"
              className="gap-0 overflow-hidden border-stone-200 p-0 sm:max-w-[min(1100px,92vw)]"
            >
              <DialogTitle className="sr-only">微信图片附件</DialogTitle>
              <img
                src={media}
                alt="微信图片附件"
                className="max-h-[72dvh] w-full bg-stone-50 object-contain"
              />
              <div className="flex items-center justify-between gap-3 border-t border-stone-100 px-4 py-2">
                <span className="text-xs text-stone-400">
                  按 Esc 或点击图片外区域关闭
                </span>
                <Button variant="ghost" size="sm" onClick={() => setPreview(false)}>
                  关闭
                </Button>
              </div>
            </DialogContent>
          </Dialog>
        </>
      ) : (
        (attachment || media) && (
          <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-stone-200/70 bg-white px-3 py-2 text-xs text-stone-500">
            <FileText className="size-4" />
            <span className="min-w-0 flex-1 break-all">
              {attachment?.name || attachment?.kind || "消息附件"}
            </span>
            {media ? (
              <a
                href={media}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-emerald-700"
              >
                查看
                <ExternalLink className="size-3" />
              </a>
            ) : (
              <span>尚未下载到本机</span>
            )}
          </div>
        )
      )}
    </article>
  );
}
