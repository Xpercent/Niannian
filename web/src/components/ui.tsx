/** 极简 UI 组件集：沿用原版亮色绿色调的等价样式，全部本地实现。 */

import { useEffect } from "react";
import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
} from "react";
import type { Ref } from "react";
import { X } from "lucide-react";

export function cn(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "secondary" | "ghost" | "danger";
  size?: "default" | "sm" | "icon";
};

export function Button({
  variant = "default",
  size = "default",
  className,
  ...props
}: ButtonProps) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium outline-none transition focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50";
  const sizes = { default: "h-10 px-4", sm: "h-8 px-3 text-xs", icon: "size-9" };
  const variants = {
    default: "bg-primary text-primary-foreground hover:bg-primary/90",
    secondary: "bg-secondary text-secondary-foreground hover:bg-accent",
    ghost: "text-stone-600 hover:bg-stone-100",
    danger: "bg-destructive text-white hover:bg-destructive/90",
  };
  return (
    <button
      type="button"
      className={cn(base, sizes[size], variants[variant], className)}
      {...props}
    />
  );
}

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-xl border bg-card shadow-sm", className)}
      {...props}
    />
  );
}

export function CardHeader({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-between gap-3 border-b p-4",
        className,
      )}
      {...props}
    />
  );
}

export function CardTitle({
  className,
  ...props
}: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h2
      className={cn("text-base font-semibold tracking-tight", className)}
      {...props}
    />
  );
}

export function CardContent({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & { ref?: Ref<HTMLDivElement> }) {
  return <div className={cn("p-4", className)} {...props} />;
}

export function Badge({
  className,
  tone = "default",
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: "default" | "warning" }) {
  const tones = {
    default: "bg-secondary text-secondary-foreground",
    warning: "bg-amber-100 text-amber-800",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}

export function Input({
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-10 w-full rounded-lg border bg-white px-3 text-sm outline-none transition placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
      {...props}
    />
  );
}

export function Dialog({
  open,
  onClose,
  children,
  className,
  width = "w-full max-w-lg",
  padded = true,
  closable = true,
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  className?: string;
  /** 整档宽度类（含 w-*）：基类不设宽度，避免与调用方传入的宽度互相打架。 */
  width?: string;
  padded?: boolean;
  /** 右上角那颗 ×；关掉后仍可点遮罩或按 Esc 关闭（图片弹窗就这么用）。 */
  closable?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="关闭弹窗"
        className="absolute inset-0 bg-stone-900/30"
        onClick={onClose}
      />
      {/* 宽度与内边距只能由调用方给一档：cn 是纯拼接，基类再写死 max-w-lg
          就会和传入的 max-w-* 同时出现在 class 上，谁生效取决于样式表顺序。 */}
      <div
        role="dialog"
        aria-modal="true"
        className={cn(
          "relative max-h-[85vh] overflow-hidden rounded-xl border bg-card shadow-xl",
          width,
          padded ? "p-6" : "p-0",
          className,
        )}
      >
        {closable && (
          <button
            type="button"
            aria-label="关闭"
            className="absolute right-3 top-3 z-10 rounded-md bg-white/70 p-1 text-stone-500 hover:bg-stone-100"
            onClick={onClose}
          >
            <X className="size-4" />
          </button>
        )}
        {children}
      </div>
    </div>
  );
}
