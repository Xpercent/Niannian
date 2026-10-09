import { useEffect, useState } from "react";
import { Download, Plug, Save, Server, HardDrive } from "lucide-react";
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

type Settings = {
  ai_enabled: boolean;
  ai_base_url: string;
  ai_model: string;
  ai_timeout_seconds: number;
  merge_window_seconds: number;
  settle_window_seconds: number;
  batch_interval_seconds: number;
  wechat_enabled: boolean;
  wechat_poll_seconds: number;
  history_limit: number;
  ai_key_configured: boolean;
  thinking_enabled: boolean;
  warning?: string | null;
};
type Draft = Omit<
  Settings,
  "ai_key_configured" | "thinking_enabled" | "warning"
> & { ai_api_key: string };
type NumericKey =
  | "ai_timeout_seconds"
  | "merge_window_seconds"
  | "settle_window_seconds"
  | "batch_interval_seconds"
  | "wechat_poll_seconds"
  | "history_limit";
function toDraft(value: Settings): Draft {
  return {
    ai_enabled: value.ai_enabled,
    ai_base_url: value.ai_base_url,
    ai_model: value.ai_model,
    ai_timeout_seconds: value.ai_timeout_seconds,
    merge_window_seconds: value.merge_window_seconds,
    settle_window_seconds: value.settle_window_seconds,
    batch_interval_seconds: value.batch_interval_seconds,
    wechat_enabled: value.wechat_enabled,
    wechat_poll_seconds: value.wechat_poll_seconds,
    history_limit: value.history_limit,
    ai_api_key: "",
  };
}
const numeric: Array<{
  key: NumericKey;
  label: string;
  min: number;
  max: number;
}> = [
  { key: "ai_timeout_seconds", label: "接口超时（秒）", min: 5, max: 180 },
  { key: "merge_window_seconds", label: "合并窗口（秒）", min: 0, max: 600 },
  { key: "settle_window_seconds", label: "稳定窗口（秒）", min: 0, max: 600 },
  {
    key: "batch_interval_seconds",
    label: "提炼间隔（秒）",
    min: 15,
    max: 86400,
  },
  { key: "wechat_poll_seconds", label: "微信轮询（秒）", min: 1, max: 60 },
  { key: "history_limit", label: "历史消息上限", min: 10, max: 1000 },
];

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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [probe, setProbe] = useState("");
  const [wechatBusy, setWechatBusy] = useState(false);
  const [keyConfigured, setKeyConfigured] = useState(false);
  const [warning, setWarning] = useState<string | null>(null);
  const readSettings = async () => {
    const value = await api<Settings>("/api/settings");
    setDraft(toDraft(value));
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
  const save = async () => {
    if (!draft) return;
    const invalid = numeric.find(
      ({ key, min, max }) =>
        !Number.isInteger(draft[key]) || draft[key] < min || draft[key] > max,
    );
    if (invalid)
      return notify(
        `${invalid.label}应为 ${invalid.min} 至 ${invalid.max} 的整数`,
        true,
      );
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
      link.download = `shiguang-export.${format}`;
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
      <div>
        <h1 className="text-xl font-semibold text-stone-900">设置</h1>
        <p className="mt-1 text-sm text-stone-500">
          调整提炼、采集和数据维护选项。
        </p>
      </div>
      {warning && <ErrorNotice message={warning} />}
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
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>运行参数</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          {numeric.map((item) => (
            <Field
              key={item.key}
              label={item.label}
              hint={`${item.min} - ${item.max}`}
            >
              <Input
                type="number"
                min={item.min}
                max={item.max}
                value={draft[item.key] as number}
                onChange={(e) => set(item.key, Number(e.target.value))}
              />
            </Field>
          ))}
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.wechat_enabled}
              onChange={(e) => set("wechat_enabled", e.target.checked)}
            />
            启用微信采集
          </label>
        </CardContent>
      </Card>
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
        <CardContent className="flex flex-wrap gap-2">
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
      <div className="flex justify-end">
        <Button onClick={() => void save()} disabled={saving}>
          <Save className="size-4" />
          {saving ? "保存中" : "保存设置"}
        </Button>
      </div>
    </fieldset>
  );
}
