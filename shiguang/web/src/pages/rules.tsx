import { useEffect, useState } from "react";
import { Plus, Trash2, Save } from "lucide-react";
import { api, json, patchJson } from "../lib/utils";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Textarea,
  Badge,
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "../components/ui";
import { Empty, ErrorNotice, Field, Loading } from "../components/common";

type Rule = {
  id: number;
  name: string;
  description: string;
  pattern: string;
  action: "ignore" | "extract_task";
  enabled: number | boolean;
};
type Draft = Omit<Rule, "id" | "enabled"> & { enabled: boolean };
const blank: Draft = {
  name: "",
  description: "",
  pattern: "",
  action: "extract_task",
  enabled: true,
};

export default function RulesPage({
  notify,
}: {
  notify: (message: string, error?: boolean) => void;
}) {
  const [rules, setRules] = useState<Rule[]>([]);
  const [draft, setDraft] = useState<Draft>(blank);
  const [editing, setEditing] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<Rule | null>(null);
  const [busy, setBusy] = useState(false);
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setRules(await api<Rule[]>("/api/rules"));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const save = async () => {
    if (busy) return;
    if (!draft.name.trim() || !draft.pattern.trim())
      return notify("名称和匹配规则不能为空", true);
    setBusy(true);
    try {
      if (editing === null) {
        const saved = await api<Rule>("/api/rules", json(draft));
        setEditing(saved.id);
      } else await api(`/api/rules/${editing}`, patchJson(draft));
      setRules(await api<Rule[]>("/api/rules"));
      setDraft(blank);
      setEditing(null);
      notify("规则已保存");
    } catch (e) {
      notify(String(e), true);
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!deleting || busy) return;
    setBusy(true);
    try {
      await api(`/api/rules/${deleting.id}`, { method: "DELETE" });
      if (editing === deleting.id) {
        setEditing(null);
        setDraft(blank);
      }
      setDeleting(null);
      setRules(await api<Rule[]>("/api/rules"));
      notify("规则已删除");
    } catch (e) {
      notify(String(e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <fieldset disabled={busy} className="space-y-5 min-w-0">
      <div>
        <h1 className="text-xl font-semibold text-stone-900">处理规则</h1>
        <p className="mt-1 text-sm text-stone-500">
          用正则匹配真实微信消息，并决定是否跳过或提炼任务。
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>{editing === null ? "新增规则" : "编辑规则"}</CardTitle>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              setDraft(blank);
              setEditing(null);
            }}
          >
            <Plus className="size-4" />
            新建
          </Button>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <Field label="名称">
            <Input
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            />
          </Field>
          <Field label="动作">
            <select
              className="h-9 rounded-md border border-stone-200 px-3 text-sm"
              value={draft.action}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  action: e.target.value as Draft["action"],
                })
              }
            >
              <option value="extract_task">提炼任务</option>
              <option value="ignore">忽略消息</option>
            </select>
          </Field>
          <Field label="匹配正则" hint="使用 Python 正则语法">
            <Input
              value={draft.pattern}
              onChange={(e) => setDraft({ ...draft, pattern: e.target.value })}
            />
          </Field>
          <Field label="说明">
            <Textarea
              value={draft.description}
              onChange={(e) =>
                setDraft({ ...draft, description: e.target.value })
              }
            />
          </Field>
          <label className="flex items-center gap-2 text-sm text-stone-700">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) =>
                setDraft({ ...draft, enabled: e.target.checked })
              }
            />
            启用规则
          </label>
          <div>
            <Button onClick={() => void save()}>
              <Save className="size-4" />
              保存
            </Button>
          </div>
        </CardContent>
      </Card>
      {loading ? (
        <Loading />
      ) : error ? (
        <ErrorNotice message={error} onRetry={() => void load()} />
      ) : rules.length === 0 ? (
        <Empty title="尚无规则" description="创建第一条消息处理规则。" />
      ) : (
        <div className="space-y-3">
          {rules.map((rule) => (
            <Card key={rule.id}>
              <CardContent className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="font-medium text-stone-800">{rule.name}</h3>
                    <Badge
                      className={
                        rule.enabled
                          ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                          : "border-stone-200 text-stone-500"
                      }
                    >
                      {rule.enabled ? "启用" : "停用"}
                    </Badge>
                  </div>
                  <p className="mt-1 break-all font-mono text-xs text-stone-500">
                    {rule.pattern}
                  </p>
                  <p className="mt-1 text-sm text-stone-600">
                    {rule.description || "无说明"} ·{" "}
                    {rule.action === "ignore" ? "忽略消息" : "提炼任务"}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setEditing(rule.id);
                      setDraft({
                        name: rule.name,
                        description: rule.description,
                        pattern: rule.pattern,
                        action: rule.action,
                        enabled: Boolean(rule.enabled),
                      });
                    }}
                  >
                    编辑
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setDeleting(rule)}
                  >
                    <Trash2 className="size-4" />
                    删除
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      <Dialog
        open={Boolean(deleting)}
        onOpenChange={(open) => !open && setDeleting(null)}
      >
        <DialogContent className="p-6">
          <DialogTitle>删除规则？</DialogTitle>
          <DialogDescription className="mt-2">
            将删除“{deleting?.name}”，此操作无法撤销。
          </DialogDescription>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setDeleting(null)}>
              取消
            </Button>
            <Button
              disabled={busy}
              variant="destructive"
              onClick={() => void remove()}
            >
              确认删除
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </fieldset>
  );
}
