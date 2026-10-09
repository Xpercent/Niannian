import { useEffect, useState } from "react";
import { Save } from "lucide-react";
import { api, putJson } from "../lib/utils";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Textarea,
} from "../components/ui";
import { ErrorNotice, Loading } from "../components/common";

export default function ProfilePage({
  notify,
}: {
  notify: (message: string, error?: boolean) => void;
}) {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api<{ markdown: string }>("/api/profile");
      setText(result.markdown);
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
    setSaving(true);
    try {
      await api("/api/profile", putJson({ markdown: text }));
      const result = await api<{ markdown: string }>("/api/profile");
      setText(result.markdown);
      notify("画像已保存");
    } catch (e) {
      notify(String(e), true);
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-stone-900">个人画像</h1>
        <p className="mt-1 text-sm text-stone-500">
          用 Markdown 描述你的身份、偏好和工作背景，帮助任务提炼保持上下文。
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>PROFILE.md</CardTitle>
          <Button
            onClick={() => void save()}
            disabled={loading || saving || Boolean(error)}
          >
            <Save className="size-4" />
            {saving ? "保存中" : "保存"}
          </Button>
        </CardHeader>
        <CardContent>
          {loading ? (
            <Loading />
          ) : error ? (
            <ErrorNotice message={error} onRetry={() => void load()} />
          ) : (
            <Textarea
              disabled={saving}
              aria-label="个人画像 Markdown"
              maxLength={30000}
              className="min-h-[28rem] font-mono"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
