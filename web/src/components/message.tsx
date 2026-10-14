import { useState } from "react";
import type { MessageRow, QuoteTarget } from "../lib/api";
import { dateText, mediaUrl } from "../lib/api";
import { Dialog, cn } from "./ui";

export function Message({
  message,
  flash = false,
  onJump,
}: {
  message: MessageRow;
  flash?: boolean;
  onJump?: (target: QuoteTarget) => void;
}) {
  const [zoom, setZoom] = useState(false);
  const media = mediaUrl(message.metadata?.local_file);
  const attachment = message.metadata?.attachment;
  const tier = message.metadata?.tier;
  const tierLabel = message.metadata?.tier_label;
  const picture = attachment?.kind === "image";
  // 上游标签把文件、转账、拍一拍等一律叫"文件/链接/卡片"，徽标用后端解析出的真类型
  const typeLabel = message.type_label || message.message_type;
  return (
    <article
      id={`message-${message.id}`}
      className={cn(
        "rounded-lg border p-4",
        message.is_self
          ? "border-emerald-100 bg-emerald-50/50"
          : "border-stone-100 bg-stone-50/70",
        // 跳转落点：短暂加一圈描边，滚动位置本身已经够说明"就是这条"
        flash && "ring-2 ring-emerald-300",
      )}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
        <strong className="font-medium text-stone-600">
          {message.sender_name || (message.is_self ? "我" : "微信消息")}
        </strong>
        <span className="flex items-center gap-2 text-stone-400">
          {typeLabel && typeLabel !== "文本" && (
            <em className="not-italic rounded bg-stone-200/70 px-1.5 py-0.5 text-[10px] text-stone-500">
              {typeLabel}
            </em>
          )}
          <time>{dateText(message.created_at, true)}</time>
        </span>
      </div>
      <p className="break-words whitespace-pre-wrap text-sm leading-7 text-stone-700">
        {message.content}
      </p>
      {attachment?.kind === "quote" && (
        <QuoteLine
          name={attachment.quoted?.name}
          label={attachment.quoted?.label}
          target={message.quote_target}
          onJump={onJump}
        />
      )}
      {picture &&
        (media ? (
          <>
            <button
              type="button"
              onClick={() => setZoom(true)}
              aria-label="放大查看图片"
              className="relative mt-2 block w-fit cursor-zoom-in overflow-hidden rounded-md ring-stone-200 transition hover:ring-2 focus-visible:outline-none focus-visible:ring-2"
            >
              <img
                src={media}
                alt="微信图片"
                loading="lazy"
                className="max-h-64 max-w-full object-contain"
              />
              {tier === "thumb" && (
                <span className="absolute left-1.5 top-1.5 rounded bg-stone-900/65 px-1.5 py-0.5 text-[10px] leading-4 text-white">
                  预览图
                </span>
              )}
            </button>
            <Dialog
              open={zoom}
              onClose={() => setZoom(false)}
              padded={false}
              width="w-fit max-w-[min(1200px,94vw)]"
            >
              {/* 不给 w-full：预览图只有几百像素，拉满弹窗宽度只会糊成一团。
                  尺寸上限由弹窗扛，图片只用 max-w-full 保证不越界、不放大。 */}
              <img
                src={media}
                alt="微信图片"
                className="block h-auto max-h-[74vh] w-auto max-w-full bg-stone-100 object-contain"
              />
              <p className="flex items-center justify-between gap-3 border-t border-stone-100 px-4 py-2 text-xs text-stone-400">
                <span>{tierLabel || "本机图片"}</span>
                <span>按 Esc 关闭</span>
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

/**
 * 引用行：微信把被引内容显示成一小段，我们只留一行小字 + 跳转，全文不入库。
 * 目标没导入时照样显示这行字，只是不可点——读得到是谁说的、跳不过去。
 */
function QuoteLine({
  name,
  label,
  target,
  onJump,
}: {
  name?: string;
  label?: string;
  target?: QuoteTarget | null;
  onJump?: (target: QuoteTarget) => void;
}) {
  const text = `${name ? `${name}：` : ""}${label || "（无内容）"}`;
  const base =
    "mt-2 rounded-r-md border-l-2 px-2.5 py-1.5 text-left text-xs leading-5 " +
    "break-words text-stone-500";
  if (!target || !onJump) {
    return (
      <p className={cn(base, "border-stone-300 bg-stone-100/80")}>
        {text}
        <span className="ml-1.5 text-stone-400">· 原文未导入</span>
      </p>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onJump(target)}
      aria-label={`跳到 ${name || "被引消息"} 的原文`}
      className={cn(base, "block w-full cursor-pointer border-stone-300 bg-stone-100/80 hover:bg-stone-200/80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-emerald-400")}
    >
      {text}
    </button>
  );
}
