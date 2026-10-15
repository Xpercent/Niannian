import { useEffect, useState } from "react";
import { Loader2, Save } from "lucide-react";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Input } from "../components/ui";
import {
  api,
  putJson,
  type SettingsResponse,
  type SettingsSaveResult,
} from "../lib/api";

type Props = {
  notify: (message: string, error?: boolean) => void;
};

const LABELS: Record<string, string> = {
  history_limit: "导入条数",
  poll_seconds: "轮询间隔",
  port: "监听端口",
};

type Field = {
  key: keyof SettingsValuesShape;
  title: string;
  min: number;
  max: number;
  step?: number;
  hint: React.ReactNode;
};

type SettingsValuesShape = {
  history_limit: string;
  poll_seconds: string;
  port: string;
};

// 哪些键要重启由**后端**说（`restart_required` 来自 config.RESTART_KEYS），这里不写死：
// 以前每条字段挂一个 live 布尔，后端那份 RESTART_KEYS 改了这边不会跟着改。
const FIELDS: Field[] = [
  {
    key: "history_limit",
    title: "新关注会话导入条数",
    min: 1,
    max: 1000,
    hint: (
      <>
        只影响之后
        <strong className="font-medium">新关注</strong>
        的会话首次自动导入多少条。已经关注的会话请在会话标题栏用「重新导入」单独设置，两者互不影响。
      </>
    ),
  },
  {
    key: "poll_seconds",
    title: "采集轮询间隔（秒）",
    min: 1,
    max: 3600,
    step: 0.5,
    hint: "每隔多久读一次微信本地库并推进目录与增量消息。太小会频繁解密读取，太大新消息出现得慢。",
  },
  {
    key: "port",
    title: "监听端口",
    min: 1024,
    max: 65535,
    hint: "只监听 127.0.0.1，不会对外提供访问。保存时会检查端口是否被占用；改完需重启服务，之后用新端口访问。",
  },
];

export function SettingsPage({ notify }: Props) {
  const [values, setValues] = useState<SettingsValuesShape>({
    history_limit: "100",
    poll_seconds: "3",
    port: "8765",
  });
  const [meta, setMeta] = useState<SettingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [pendingPort, setPendingPort] = useState<number | null>(null);

  useEffect(() => {
    let active = true;
    api<SettingsResponse>("/api/settings")
      .then((data) => {
        if (!active) return;
        setMeta(data);
        setValues({
          history_limit: String(data.values.history_limit),
          poll_seconds: String(data.values.poll_seconds),
          port: String(data.values.port),
        });
        setError("");
      })
      .catch((reason) => active && setError((reason as Error).message || "读取设置失败"))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  async function save() {
    setSaving(true);
    try {
      const result = await api<SettingsSaveResult>(
        "/api/settings",
        putJson({
          history_limit: Number(values.history_limit),
          poll_seconds: Number(values.poll_seconds),
          port: Number(values.port),
        }),
      );
      if (result.unchanged) {
        notify("没有改动需要保存");
      } else {
        const done = result.applied.map((key) => LABELS[key] || key).join("、");
        if (done) notify(`已保存并即时生效：${done}`);
        if (result.restart.length) {
          setPendingPort(Number(values.port));
          notify(`${LABELS[result.restart[0]] || result.restart[0]} 已写入配置，重启后生效`, true);
        }
      }
    } catch (reason) {
      notify((reason as Error).message || "保存失败", true);
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-10" role="status" aria-label="正在读取设置">
        <Loader2 className="size-5 animate-spin motion-reduce:animate-none" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-widest">设置</h1>
        <p className="mt-1 text-sm text-stone-500">
          改动写入工程根目录的 <code className="rounded bg-stone-100 px-1">{meta?.config_file}</code>
          {meta?.config_exists ? "（已存在）" : "（保存后创建）"}。
        </p>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-lg border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-700"
        >
          {error}
        </div>
      )}

      {pendingPort !== null && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          端口已保存为 {pendingPort}，但当前进程仍在旧端口上监听。重启后请用
          <code className="mx-1 rounded bg-white/70 px-1">http://127.0.0.1:{pendingPort}</code>
          访问；若新端口起不来，手工改回该配置文件即可。
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>采集与服务</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-6 sm:grid-cols-2 xl:grid-cols-3">
            {FIELDS.map((item) => {
              const restart = (meta?.restart_required ?? []).includes(item.key);
              return (
              <label key={item.key} className="block">
                <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-stone-700">
                  {item.title}
                  <Badge tone={restart ? "warning" : "default"}>
                    {restart ? "需重启" : "即时生效"}
                  </Badge>
                </span>
                <Input
                  type="number"
                  min={item.min}
                  max={item.max}
                  step={item.step}
                  className="mt-2"
                  value={values[item.key]}
                  onChange={(event) =>
                    setValues((old) => ({ ...old, [item.key]: event.target.value }))
                  }
                />
                <span className="mt-1 block text-xs leading-5 text-stone-500">{item.hint}</span>
              </label>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <div className="flex justify-end">
        <Button disabled={saving} onClick={() => void save()}>
          {saving ? (
            <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
          ) : (
            <Save className="size-4" />
          )}
          {saving ? "正在保存" : "保存设置"}
        </Button>
      </div>
    </div>
  );
}
