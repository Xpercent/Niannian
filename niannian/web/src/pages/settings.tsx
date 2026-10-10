import { useEffect, useState } from "react";
import {
  Download,
  Plug,
  RotateCcw,
  Save,
  Server,
  HardDrive,
  TriangleAlert,
} from "lucide-react";
import { api, patchJson } from "../lib/utils";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Badge,
} from "../components/ui";
import { ErrorNotice, Field, Loading } from "../components/common";

type NumericKey =
  | "ai_timeout_seconds"
  | "ai_temperature"
  | "ai_max_tokens_single"
  | "ai_max_tokens_batch"
  | "batch_interval_seconds"
  | "settle_window_seconds"
  | "merge_window_seconds"
  | "round_limit"
  | "per_session_limit"
  | "batch_limit"
  | "batch_char_budget"
  | "new_message_chars"
  | "history_chars"
  | "history_rows"
  | "task_chars"
  | "profile_chars"
  | "wechat_poll_seconds"
  | "history_limit"
  | "ignored_keep_per_session";

type Settings = {
  ai_enabled: boolean;
  ai_base_url: string;
  ai_model: string;
  wechat_enabled: boolean;
  ai_key_configured: boolean;
  thinking_enabled: boolean;
  defaults: Record<string, number | boolean | string>;
  warning?: string | null;
} & Record<NumericKey, number>;

type Draft = Omit<
  Settings,
  "ai_key_configured" | "thinking_enabled" | "defaults" | "warning"
> & { ai_api_key: string };

type Option = {
  key: NumericKey;
  label: string;
  hint: string;
  min: number;
  max: number;
  step?: number;
  // 本轮实际验证过的取值；超过就提示，但不禁止保存。
  verified?: number;
};

const groups: Array<{ title: string; note: string; items: Option[] }> = [
  {
    title: "模型请求",
    note: "思考模式固定关闭，不在此处开放。",
    items: [
      {
        key: "ai_timeout_seconds",
        label: "接口超时（秒）",
        hint: "单次请求等待上限，超时该段回退本地判定",
        min: 5,
        max: 180,
      },
      {
        key: "ai_temperature",
        label: "采样温度",
        hint: "0 最稳定；调高后同一条消息可能给出不同判定",
        min: 0,
        max: 2,
        step: 0.1,
        verified: 0,
      },
      {
        key: "ai_max_tokens_single",
        label: "单条输出上限（token）",
        hint: "太小会截断 JSON 导致整段回退",
        min: 64,
        max: 8000,
        verified: 700,
      },
      {
        key: "ai_max_tokens_batch",
        label: "批量输出上限（token）",
        hint: "一批多段时的输出上限",
        min: 64,
        max: 16000,
        verified: 1200,
      },
    ],
  },
  {
    title: "提炼节奏",
    note: "决定多久跑一轮、一轮吃多少条。",
    items: [
      {
        key: "batch_interval_seconds",
        label: "自动提炼间隔（秒）",
        hint: "调度器启动下一轮的最小间隔",
        min: 15,
        max: 86400,
      },
      {
        key: "settle_window_seconds",
        label: "稳定窗口（秒）",
        hint: "消息静默这么久才提炼，自动轮次等待",
        min: 0,
        max: 600,
      },
      {
        key: "merge_window_seconds",
        label: "合并窗口（秒）",
        hint: "同一人相邻消息隔多久内合成一段",
        min: 0,
        max: 600,
      },
      {
        key: "round_limit",
        label: "单轮最多段数（条）",
        hint: "一轮从队列里取多少条消息",
        min: 1,
        max: 2000,
        verified: 240,
      },
      {
        key: "per_session_limit",
        label: "单会话每轮上限（条）",
        hint: "防止一个会话长期占住整轮",
        min: 1,
        max: 500,
        verified: 80,
      },
    ],
  },
  {
    title: "上下文预算",
    note: "送进模型的内容规模，直接影响耗时与费用。",
    items: [
      {
        key: "batch_limit",
        label: "每次请求段数",
        hint: "一次模型请求带几段",
        min: 1,
        max: 20,
        verified: 4,
      },
      {
        key: "batch_char_budget",
        label: "每次请求字数预算",
        hint: "一批段落的正文字数上限",
        min: 400,
        max: 60000,
        verified: 4200,
      },
      {
        key: "new_message_chars",
        label: "每段保留字数",
        hint: "单段正文超出即截断",
        min: 200,
        max: 12000,
        verified: 2600,
      },
      {
        key: "history_chars",
        label: "历史上下文字数",
        hint: "本轮之前的会话内容预算",
        min: 0,
        max: 12000,
        verified: 1200,
      },
      {
        key: "history_rows",
        label: "历史上下文条数",
        hint: "最多回看几条历史消息，0 为不带历史",
        min: 0,
        max: 20,
        verified: 6,
      },
      {
        key: "task_chars",
        label: "待办上下文字数",
        hint: "相关未完成任务的注入预算",
        min: 0,
        max: 12000,
        verified: 1600,
      },
      {
        key: "profile_chars",
        label: "画像上下文字数",
        hint: "我的画像注入预算",
        min: 0,
        max: 12000,
        verified: 1200,
      },
    ],
  },
  {
    title: "采集与记录",
    note: "只影响本机读取，不会向微信发送任何内容。",
    items: [
      {
        key: "wechat_poll_seconds",
        label: "微信轮询（秒）",
        hint: "采集线程扫描新消息的间隔",
        min: 1,
        max: 60,
      },
      {
        key: "history_limit",
        label: "首次历史导入条数",
        hint: "新关注会话一次性回读多少条",
        min: 10,
        max: 1000,
      },
      {
        key: "ignored_keep_per_session",
        label: "每会话保留过滤记录",
        hint: "“无需行动”的判定记录保留条数",
        min: 10,
        max: 2000,
        verified: 100,
      },
    ],
  },
];

const options = groups.flatMap((group) => group.items);
const isFloat = (option: Option) => Boolean(option.step && option.step < 1);
const invalidText = (option: Option) =>
  isFloat(option)
    ? `${option.min} 至 ${option.max} 之间的数（步进 ${option.step}）`
    : `${option.min} 至 ${option.max} 的整数`;

export default function SettingsPage({
  notify,
  onRefresh,
  connected,
}: {
  notify: (message: string, error?: boolean) => void;
  onRefresh: () => Promise<void>;
  connected: boolean;
}) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [defaults, setDefaults] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [probe, setProbe] = useState("");
  const [wechatBusy, setWechatBusy] = useState(false);
  const [keyConfigured, setKeyConfigured] = useState(false);
  const [warning, setWarning] = useState<string | null>(null);
  const readSettings = async () => {
    const value = await api<Settings>("/api/settings");
    const numbers = Object.fromEntries(
      options.map(({ key }) => [key, value[key]]),
    ) as Record<NumericKey, number>;
    setDraft({
      ...numbers,
      ai_enabled: value.ai_enabled,
      ai_base_url: value.ai_base_url,
      ai_model: value.ai_model,
      wechat_enabled: value.wechat_enabled,
      ai_api_key: "",
    });
    const numericDefaults: Record<string, number> = {};
    for (const { key } of options) {
      const candidate = value.defaults?.[key];
      if (typeof candidate === "number") numericDefaults[key] = candidate;
    }
    setDefaults(numericDefaults);
    setKeyConfigured(value.ai_key_configured);
    setWarning(value.warning || null);
  };
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      await readSettings();
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  const overVerified = options.filter(
    (option) =>
      draft &&
      option.verified !== undefined &&
      Number(draft[option.key]) > option.verified,
  );
  const restoreDefaults = () => {
    setDraft((current) => {
      if (!current) return current;
      const next = { ...current };
      for (const option of options) {
        if (defaults[option.key] !== undefined)
          next[option.key] = defaults[option.key];
      }
      return next;
    });
    notify("已填入默认值，保存后生效");
  };
  const save = async () => {
    if (!draft) return;
    const invalid = options.find((option) => {
      const value = draft[option.key];
      if (!Number.isFinite(value) || value < option.min || value > option.max)
        return true;
      return !isFloat(option) && !Number.isInteger(value);
    });
    if (invalid)
      return notify(`${invalid.label}应为 ${invalidText(invalid)}`, true);
    setSaving(true);
    try {
      const { ai_api_key, ...rest } = draft;
      const payload: Record<string, unknown> = { ...rest };
      if (ai_api_key.trim()) payload.ai_api_key = ai_api_key;
      await api("/api/settings", patchJson(payload));
      await readSettings();
      await onRefresh();
      notify("设置已保存");
    } catch (e) {
      notify(String(e), true);
    } finally {
      setSaving(false);
    }
  };
  const probeApi = async () => {
    if (probe === "测试中…") return;
    setProbe("测试中…");
    try {
      const result = await api<{ ok: boolean; error?: string; model?: string }>(
        "/api/settings/probe",
        { method: "POST" },
      );
      setProbe(
        result.ok
          ? `连接成功 · ${result.model || ""}`
          : result.error || "连接失败",
      );
    } catch (e) {
      setProbe(String(e));
    }
  };
  const wechat = async (action: "start" | "stop") => {
    setWechatBusy(true);
    try {
      await api(`/api/wechat/${action}`, { method: "POST" });
      const value = await api<Settings>("/api/settings");
      setDraft((current) =>
        current
          ? { ...current, wechat_enabled: value.wechat_enabled }
          : current,
      );
      await onRefresh();
      notify(action === "start" ? "微信采集已启动" : "微信采集已停止");
    } catch (e) {
      notify(String(e), true);
    } finally {
      setWechatBusy(false);
    }
  };
  const backup = async () => {
    try {
      const result = await api<{ file: string }>("/api/backup", {
        method: "POST",
      });
      notify(`备份已创建：${result.file}`);
    } catch (e) {
      notify(String(e), true);
    }
  };
  const exportFile = async (format: "json" | "md") => {
    try {
      const response = await fetch(`/api/export?format=${format}`);
      if (!response.ok) throw new Error(`导出失败 (${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `workspace-export.${format}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      notify(String(e), true);
    }
  };
  if (loading) return <Loading />;
  if (error || !draft)
    return (
      <ErrorNotice
        message={error || "设置不可用"}
        onRetry={() => void load()}
      />
    );
  return (
    <fieldset disabled={saving || wechatBusy} className="space-y-5 min-w-0">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-stone-900">设置</h1>
          <p className="mt-1 text-sm text-stone-500">
            调整提炼、采集和数据维护选项。
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={restoreDefaults}>
          <RotateCcw className="size-4" />
          恢复默认值
        </Button>
      </div>
      {warning && <ErrorNotice message={warning} />}
      {overVerified.length > 0 && (
        <div
          role="status"
          className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50/70 p-4 text-xs text-amber-800"
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <span className="min-w-0">
            已超出本轮验证过的取值：
            {overVerified.map((option, index) => (
              <strong key={option.key} className="font-medium">
                {index > 0 ? "、" : ""}
                {option.label} {draft[option.key]}（验证值 {option.verified}）
              </strong>
            ))}
            。保存后单轮可能更慢、更贵或更容易超时；处理状态页可以看到实际影响。
          </span>
        </div>
      )}
      <Card>
        <CardHeader>
          <CardTitle>模型服务</CardTitle>
          <Badge
            className={
              draft.ai_enabled
                ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                : "border-stone-200 text-stone-500"
            }
          >
            {draft.ai_enabled ? "已启用" : "已停用"}
          </Badge>
        </CardHeader>
        <CardContent className="space-y-4">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.ai_enabled}
              onChange={(e) => set("ai_enabled", e.target.checked)}
            />
            启用模型提炼
          </label>
          <Field label="接口地址">
            <Input
              value={draft.ai_base_url}
              onChange={(e) => set("ai_base_url", e.target.value)}
            />
          </Field>
          <Field label="模型">
            <Input
              value={draft.ai_model}
              onChange={(e) => set("ai_model", e.target.value)}
            />
          </Field>
          <Field
            label="API 密钥"
            hint={keyConfigured ? "已配置，留空则保留原密钥" : "尚未配置"}
          >
            <Input
              type="password"
              value={draft.ai_api_key}
              onChange={(e) => set("ai_api_key", e.target.value)}
              placeholder={keyConfigured ? "留空保持不变" : "输入密钥"}
            />
          </Field>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" onClick={() => void probeApi()}>
              <Plug className="size-4" />
              测试连接
            </Button>
            {probe && <span className="text-sm text-stone-600">{probe}</span>}
            <span className="text-xs text-stone-400">模型思考固定关闭</span>
          </div>
        </CardContent>
      </Card>
      {groups.map((group) => (
        <Card key={group.title}>
          <CardHeader>
            <CardTitle>{group.title}</CardTitle>
            <span className="text-[10px] text-stone-400">{group.note}</span>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {group.items.map((item) => (
              <Field
                key={item.key}
                label={item.label}
                hint={`${item.hint} · ${item.min}–${item.max}${
                  defaults[item.key] !== undefined
                    ? ` · 默认 ${defaults[item.key]}`
                    : ""
                }`}
              >
                <Input
                  type="number"
                  min={item.min}
                  max={item.max}
                  step={item.step ?? 1}
                  value={draft[item.key]}
                  onChange={(e) => set(item.key, Number(e.target.value))}
                />
              </Field>
            ))}
          </CardContent>
        </Card>
      ))}
      <Card>
        <CardHeader>
          <CardTitle>微信与数据</CardTitle>
          <Badge
            className={
              connected
                ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                : "border-stone-200 text-stone-500"
            }
          >
            {connected ? "已连接" : "未连接"}
          </Badge>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <label className="mr-auto flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.wechat_enabled}
              onChange={(e) => set("wechat_enabled", e.target.checked)}
            />
            启用微信采集
          </label>
          <Button
            variant="secondary"
            disabled={wechatBusy}
            onClick={() => void wechat("start")}
          >
            <Server className="size-4" />
            启动采集
          </Button>
          <Button
            variant="secondary"
            disabled={wechatBusy}
            onClick={() => void wechat("stop")}
          >
            停止采集
          </Button>
          <Button variant="secondary" onClick={() => void backup()}>
            <HardDrive className="size-4" />
            创建备份
          </Button>
          <Button variant="secondary" onClick={() => exportFile("md")}>
            <Download className="size-4" />
            导出 Markdown
          </Button>
          <Button variant="secondary" onClick={() => exportFile("json")}>
            <Download className="size-4" />
            导出 JSON
          </Button>
        </CardContent>
      </Card>
      <p className="text-[11px] leading-6 text-stone-400">
        后端参数在下一轮提炼时生效，不需要重启；接口地址、模型与密钥改动同样在下一轮读取。
        业务校验、身份判定与“只读微信”的边界不受这些参数影响。
      </p>
      <div className="flex justify-end">
        <Button onClick={() => void save()} disabled={saving}>
          <Save className="size-4" />
          {saving ? "保存中" : "保存设置"}
        </Button>
      </div>
    </fieldset>
  );
}
