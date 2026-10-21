import { useEffect, useState } from "react";
import { Loader2, PlugZap, RotateCcw, Save } from "lucide-react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Switch,
  Textarea,
} from "../components/ui";
import {
  api,
  LLM_TEST_REQUEST_MS,
  postJson,
  putJson,
  type LlmTestResult,
  type SettingsResponse,
  type SettingsSaveResult,
} from "../lib/api";

type Props = {
  notify: (message: string, error?: boolean) => void;
};

// 键的中文名读后端那一份（`GET /api/settings` 的 `labels`）：这边再抄一张表的话，
// 后端改了叫法界面不会跟着改，保存后的提示就会说出旧名字。
type NumberKey =
  | "history_limit"
  | "poll_seconds"
  | "port"
  | "extract_round_limit"
  | "extract_batch_limit"
  | "extract_history_limit";

type Field = {
  key: NumberKey;
  title: string;
  min: number;
  max: number;
  step?: number;
  hint: React.ReactNode;
};

/** 表单里一律用字符串存：数字输入框要能把用户正在敲的东西原样放着，别边打字边改写。 */
type Form = {
  history_limit: string;
  poll_seconds: string;
  port: string;
  llm_base_url: string;
  llm_model: string;
  llm_api_key: string;
  llm_include_images: boolean;
  /** 「请求体参数」那一框：文本框里存**原文**，空串 = 请求体里只带模型名与正文。 */
  llm_extra_body: string;
  extract_round_limit: string;
  extract_batch_limit: string;
  extract_history_limit: string;
  extract_prompt: string;
  extract_profile: string;
};

const EMPTY: Form = {
  history_limit: "100",
  poll_seconds: "3",
  port: "8765",
  llm_base_url: "",
  llm_model: "",
  llm_api_key: "",
  llm_include_images: false,
  llm_extra_body: "",
  extract_round_limit: "200",
  extract_batch_limit: "20",
  extract_history_limit: "15",
  extract_prompt: "",
  extract_profile: "",
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

// 一轮取多少条、一批问几条、附带几条上文——这三个数各管一件事，合到一起就没法说"这轮跑到哪了"。
const EXTRACT_FIELDS: Field[] = [
  {
    key: "extract_round_limit",
    title: "一轮提炼条数",
    min: 1,
    max: 1000,
    hint: (
      <>
        点一次「立即提炼」最多处理这么多条，攒多了分几轮跑。一条都没处理过的消息永远排在前面，
        不会被新的挤掉。
      </>
    ),
  },
  {
    key: "extract_batch_limit",
    title: "一批请求条数",
    min: 1,
    max: 50,
    hint: (
      <>
        一次请求带几条消息，模型看到的依据编号就是 0 到
        <strong className="font-medium">条数减一</strong>
        。调大省请求数，但一批越长越容易整批读不懂（回了半截 JSON），那一批会原样留给下一轮。20
        是真机试出来的值。
      </>
    ),
  },
  {
    key: "extract_history_limit",
    title: "上文条数",
    min: 0,
    max: 100,
    hint: (
      <>
        每批附带几条旧消息当参照：只帮模型看懂"这事之前说过没有"，
        <strong className="font-medium">不带编号、不能据它建任务</strong>。填 0 就是不给上文。
      </>
    ),
  },
];

function SwitchRow({
  title,
  hint,
  checked,
  onChange,
}: {
  title: string;
  hint: React.ReactNode;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <span className="block text-sm font-medium text-stone-700">{title}</span>
        <span className="mt-1 block text-xs leading-5 text-stone-500">{hint}</span>
      </div>
      <Switch label={title} checked={checked} onChange={onChange} />
    </div>
  );
}

/** 数字格留空 → 交 null，后端那一份的意思是"用默认"；0 必须原样交出去（上文 0 条是合法值）。 */
function numberOrNull(value: string): number | null {
  const text = value.trim();
  if (!text) return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : null;
}

/** 一个数字输入格：采集那三项与提炼那三项长一样，出一次就好。 */
function NumberField({
  item,
  value,
  restart,
  onChange,
}: {
  item: Field;
  value: string;
  restart: boolean;
  onChange: (next: string) => void;
}) {
  return (
    <label className="block">
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
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <span className="mt-1 block text-xs leading-5 text-stone-500">{item.hint}</span>
    </label>
  );
}

export function SettingsPage({ notify }: Props) {
  const [form, setForm] = useState<Form>(EMPTY);
  const [meta, setMeta] = useState<SettingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<LlmTestResult | null>(null);
  const [clearKey, setClearKey] = useState(false);
  const [error, setError] = useState("");
  const [pendingPort, setPendingPort] = useState<number | null>(null);

  useEffect(() => {
    let active = true;
    api<SettingsResponse>("/api/settings")
      .then((data) => {
        if (!active) return;
        setMeta(data);
        setForm({
          history_limit: String(data.values.history_limit),
          poll_seconds: String(data.values.poll_seconds),
          port: String(data.values.port),
          llm_base_url: data.values.llm_base_url,
          llm_model: data.values.llm_model,
          // 后端回的是空串（密钥不出网关），这里保持空 = 不改
          llm_api_key: "",
          llm_include_images: data.values.llm_include_images,
          llm_extra_body: data.values.llm_extra_body,
          extract_round_limit: String(data.values.extract_round_limit),
          extract_batch_limit: String(data.values.extract_batch_limit),
          extract_history_limit: String(data.values.extract_history_limit),
          extract_prompt: data.values.extract_prompt,
          extract_profile: data.values.extract_profile,
        });
        setError("");
      })
      .catch((reason) => active && setError((reason as Error).message || "读取设置失败"))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  /** 提交给后端的整份设置：密钥那格按"留空即不改/勾了清除才删"翻译。 */
  function saveBody() {
    return {
      history_limit: Number(form.history_limit),
      poll_seconds: Number(form.poll_seconds),
      port: Number(form.port),
      llm_base_url: form.llm_base_url.trim(),
      llm_model: form.llm_model.trim(),
      llm_api_key: clearKey ? "" : form.llm_api_key.trim(),
      clear_llm_api_key: clearKey,
      llm_include_images: form.llm_include_images,
      // 那一框交**原文**：后端解析过一遍才落盘，写坏了会在保存这一下就说出是哪一项
      llm_extra_body: form.llm_extra_body.trim(),
      extract_round_limit: numberOrNull(form.extract_round_limit),
      extract_batch_limit: numberOrNull(form.extract_batch_limit),
      extract_history_limit: numberOrNull(form.extract_history_limit),
      extract_prompt: form.extract_prompt,
      extract_profile: form.extract_profile.trim(),
    };
  }

  /** 保存后的提示用后端那份中文名（读不到时退回键名，不至于显示 undefined）。 */
  const label = (key: string) => meta?.labels?.[key] ?? key;

  async function save() {
    setSaving(true);
    try {
      const result = await api<SettingsSaveResult>(
        "/api/settings",
        putJson(saveBody()),
      );
      setMeta((old) =>
        old ? { ...old, values: result.values, api_key_masked: result.api_key_masked,
                api_key_set: result.api_key_set } : old,
      );
      setForm((old) => ({ ...old, llm_api_key: "" }));
      setClearKey(false);
      if (result.unchanged) {
        notify("没有改动需要保存");
      } else {
        const done = result.applied.map(label).join("、");
        if (done) notify(`已保存并即时生效：${done}`);
        if (result.restart.length) {
          setPendingPort(Number(form.port));
          notify(`${label(result.restart[0])} 已写入配置，重启后生效`, true);
        }
      }
    } catch (reason) {
      notify((reason as Error).message || "保存失败", true);
    } finally {
      setSaving(false);
    }
  }

  /**
   * 用界面上**当前这份**去问服务商，不需要先保存。
   * 只提交模型那几项：采集与端口的值由后端从库里回落，免得改端口改了一半测连不上模型。
   */
  async function runTest() {
    setTesting(true);
    setTest(null);
    const body = {
      llm_base_url: form.llm_base_url.trim(),
      llm_model: form.llm_model.trim(),
      // 框里没重填就沿用库里那把；"清除"只影响保存，不影响这一次试
      llm_api_key: form.llm_api_key.trim(),
      llm_include_images: form.llm_include_images,
      // 那一框也照界面上这份交：改了一半没保存，测的仍然是界面上看到的参数
      llm_extra_body: form.llm_extra_body.trim(),
    };
    try {
      setTest(await api<LlmTestResult>("/api/settings/test-connection", postJson(body),
                                      LLM_TEST_REQUEST_MS));
    } catch (reason) {
      setTest({ ok: false, elapsed_ms: 0, error: (reason as Error).message || "测试失败" });
    } finally {
      setTesting(false);
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
            {FIELDS.map((item) => (
              <NumberField
                key={item.key}
                item={item}
                value={form[item.key]}
                restart={(meta?.restart_required ?? []).includes(item.key)}
                onChange={(next) => setForm((old) => ({ ...old, [item.key]: next }))}
              />
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>模型接入</CardTitle>
          <Button variant="secondary" size="sm" disabled={testing} onClick={() => void runTest()}>
            {testing ? (
              <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
            ) : (
              <PlugZap className="size-4" />
            )}
            {testing ? "正在等服务商" : "测试连接"}
          </Button>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid gap-6 sm:grid-cols-2">
            <label className="block">
              <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-stone-700">
                服务商地址
                <Badge>即时生效</Badge>
              </span>
              <Input
                className="mt-2"
                placeholder="https://api.siliconflow.cn/v1"
                value={form.llm_base_url}
                onChange={(event) =>
                  setForm((old) => ({ ...old, llm_base_url: event.target.value }))
                }
              />
              <span className="mt-1 block text-xs leading-5 text-stone-500">
                任何 OpenAI 兼容服务商都能用，末尾带上 <code className="rounded bg-stone-100 px-1">/v1</code>。
              </span>
            </label>
            <label className="block">
              <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-stone-700">
                模型名
                <Badge>即时生效</Badge>
              </span>
              <Input
                className="mt-2"
                placeholder="Qwen/Qwen3-8B"
                value={form.llm_model}
                onChange={(event) =>
                  setForm((old) => ({ ...old, llm_model: event.target.value }))
                }
              />
              <span className="mt-1 block text-xs leading-5 text-stone-500">
                写服务商那边的完整模型 ID，带组织前缀的那种。
              </span>
            </label>
          </div>

          <div>
            <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-stone-700">
              API Key
              {meta?.api_key_set ? (
                <Badge tone="default">已存 {meta.api_key_masked}</Badge>
              ) : (
                <Badge tone="warning">还没填</Badge>
              )}
            </span>
            {/* 清除那颗按钮放在这组之外：label 里嵌按钮的话，点它会连带把输入框叫出来，
                看起来像"没点上"。名字靠 aria-label 给，不靠 label 包。 */}
            <Input
              type="password"
              autoComplete="off"
              className="mt-2"
              aria-label="API Key"
              placeholder={
                clearKey
                  ? "保存后会清掉已存的 Key"
                  : meta?.api_key_set
                    ? "留空表示不改，填了就换掉"
                    : "填服务商给的密钥"
              }
              value={form.llm_api_key}
              onChange={(event) => {
                setForm((old) => ({ ...old, llm_api_key: event.target.value }));
                if (event.target.value) setClearKey(false);
              }}
            />
            <span className="mt-1 flex flex-wrap items-center gap-3 text-xs leading-5 text-stone-500">
              <span>
                密钥只写进 <code className="rounded bg-stone-100 px-1">{meta?.config_file}</code>
                ，接口与页面都不回显（这格读回来永远是空的）。
              </span>
              {meta?.api_key_set && (
                <button
                  type="button"
                  className="text-stone-600 underline decoration-dotted hover:text-rose-600"
                  onClick={() => setClearKey((old) => !old)}
                >
                  {clearKey ? "撤销清除" : "清除已存的 Key"}
                </button>
              )}
            </span>
          </div>

          <label className="block">
            <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-stone-700">
              请求体参数
              <Badge>即时生效</Badge>
            </span>
            <Textarea
              className="mt-2 font-mono text-xs leading-5"
              rows={4}
              aria-label="请求体参数"
              placeholder={'例如：{"temperature": 0.2, "reasoning_effort": "high"}'}
              value={form.llm_extra_body}
              onChange={(event) =>
                setForm((old) => ({ ...old, llm_extra_body: event.target.value }))
              }
            />
            <span className="mt-1 block text-xs leading-5 text-stone-500">
              一个 JSON 对象，里面的键原样并进请求体顶层：温度、思考、输出上限……
              各家挂的字段名都不一样（SiliconFlow 认 <code className="rounded bg-stone-100 px-1">enable_thinking</code>，
              Google 只认标准的 <code className="rounded bg-stone-100 px-1">reasoning_effort</code>，
              而且它家有些模型<strong className="font-medium">连 <code className="rounded bg-stone-100 px-1">"none"</code> 都不吃</strong>，
              写"关思考"就是整条 400），所以我们不再替服务商猜——<strong className="font-medium">照那家的文档写</strong>。
              <strong className="font-medium">留空就是只发模型名与正文</strong>，一个额外字段都不带，
              默认值全交回服务商；<code className="rounded bg-stone-100 px-1">model</code>、
              <code className="rounded bg-stone-100 px-1">messages</code>、
              <code className="rounded bg-stone-100 px-1">stream</code> 与密钥不能写在这里（前三样由程序拼，
              密钥不出网关）。写坏了<strong className="font-medium">保存这一下就拦</strong>，
              不会带着坏 JSON 去撞 400；发出去的到底是什么，看下面测试连接结果里那份原文。
            </span>
          </label>

          <SwitchRow
            title="图片喂给模型"
            checked={form.llm_include_images}
            onChange={(next) => setForm((old) => ({ ...old, llm_include_images: next }))}
            hint="开了才把图片一起给模型看。默认关：只喂气泡上那行文字。"
          />

          {testing && (
            <p className="text-sm text-stone-500" role="status">
              正在等服务商回话，最多 {Math.round(LLM_TEST_REQUEST_MS / 1000)} 秒…
            </p>
          )}
          {test && !testing && (
            <div
              role="status"
              className={
                test.ok
                  ? "rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800"
                  : "rounded-lg border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-700"
              }
            >
              {test.ok ? (
                <>
                  <span className="font-medium">通了</span>
                  {" · "}
                  {test.model}
                  {" · 耗时 "}
                  {(test.elapsed_ms / 1000).toFixed(1)} 秒
                  {test.reply ? <span className="block break-words">回「{test.reply}」</span> : null}
                  {test.note ? (
                    <span className="mt-1 block text-xs text-amber-700">{test.note}</span>
                  ) : null}
                </>
              ) : (
                <>
                  <span className="font-medium">没通</span>
                  {" · "}
                  {test.error}
                </>
              )}
              {/* 参数形状由用户自己写了，"到底发出去了什么"就成了唯一能核对的东西：
                  通不通都给这一份，撞 400 时才看得出是自己哪一项写坏的。 */}
              {test.request && (
                <details className="mt-2 rounded-lg border border-stone-100 bg-white/70">
                  <summary className="cursor-pointer px-3 py-1.5 text-[11px] text-stone-600">
                    这次实际发出去的请求体
                  </summary>
                  <pre className="overflow-x-auto border-t border-stone-100 px-3 py-2 font-mono text-[11px] leading-5 text-stone-700">
                    {test.request}
                  </pre>
                </details>
              )}
            </div>
          )}
          <p className="text-xs leading-5 text-stone-500">
            测试连接不打折扣地按界面上这份参数去问服务商（不用先保存）；
            但<strong className="font-medium">只有保存之后</strong>，下一轮的提炼才会读到它。
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>提炼</CardTitle>
          <Button
            variant="secondary"
            size="sm"
            onClick={() =>
              setForm((old) => ({ ...old, extract_prompt: meta?.extract_prompt_default ?? "" }))
            }
          >
            <RotateCcw className="size-4" />
            恢复默认提示词
          </Button>
        </CardHeader>
        <CardContent className="space-y-5">
          <label className="block">
            <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-stone-700">
              用户画像
              <Badge>即时生效</Badge>
            </span>
            <Textarea
              className="mt-2 text-sm leading-6"
              rows={4}
              aria-label="用户画像"
              placeholder="例如：我叫 YX，26 软工 3 班学生；心理课第 5 组、信创课第 3 组；4 号楼 301 宿舍；邮箱 12345678@qq.com。不参加社团和竞赛类招新。"
              value={form.extract_profile}
              onChange={(event) =>
                setForm((old) => ({ ...old, extract_profile: event.target.value }))
              }
            />
            <span className="mt-1 block text-xs leading-5 text-stone-500">
              一句话说明<strong className="font-medium">你是谁</strong>，模型拿它判断消息点的是不是你：
              名字与群里被叫的称呼、班级、小组、宿舍、职务、邮箱都写进去。
              <strong className="font-medium">留空的话它只认得出 sender="我"</strong>
              （也就是你自己发出去的那几条），别人 @ 你、点到"第 5 组"这类一概判不出，只会一句"与我无关"。
              这一段随每批正文一起发（不写在提示词里，所以改提示词不会把它改没），上限 2000 字，
              <strong className="font-medium">保存后下一轮生效</strong>。
            </span>
          </label>

          <div className="grid gap-6 sm:grid-cols-2 xl:grid-cols-3">
            {EXTRACT_FIELDS.map((item) => (
              <NumberField
                key={item.key}
                item={item}
                value={form[item.key]}
                restart={(meta?.restart_required ?? []).includes(item.key)}
                onChange={(next) => setForm((old) => ({ ...old, [item.key]: next }))}
              />
            ))}
          </div>

          <label className="block">
            <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-stone-700">
              提炼提示词
              <Badge>即时生效</Badge>
            </span>
            <Textarea
              className="mt-2 font-mono text-xs leading-5"
              rows={16}
              aria-label="提炼提示词"
              value={form.extract_prompt}
              onChange={(event) =>
                setForm((old) => ({ ...old, extract_prompt: event.target.value }))
              }
            />
            <span className="mt-1 block text-xs leading-5 text-stone-500">
              整段决定模型怎么判任务，这里给的是<strong className="font-medium">正在生效的那份</strong>
              原文：改成什么样下一轮就问什么。留空即回到内置那段。
              <strong className="font-medium">改坏最常见的结果</strong>
              是整批 JSON 读不懂——那批消息不会标成已处理，下一轮照旧重问，不静默丢东西，
              但界面上会一直报失败。三个数字与提示词都是
              <strong className="font-medium">保存后下一轮生效</strong>
              ：正在跑的那一轮继续用它起轮时那一份，不会跑一半换个问法。
            </span>
          </label>
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
