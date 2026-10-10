import { FileText, ExternalLink } from "lucide-react";
import type { Message as MessageRecord } from "../lib/types";
import { dateText, safeMedia, cn } from "../lib/utils";

export function Message({ message }: { message: MessageRecord }) {
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
        <a
          href={media}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-3 block w-fit"
        >
          <img
            src={media}
            alt="微信图片附件"
            loading="lazy"
            className="max-h-64 max-w-full rounded-md object-contain"
          />
        </a>
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
