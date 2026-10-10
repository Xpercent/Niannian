import { Card, CardHeader, CardTitle, CardContent } from "./ui";
import type { Stats } from "../lib/types";

export function WorkspaceChart({ stats }: { stats?: Stats }) {
  const data = stats
    ? [
        { name: "进行中", count: stats.pending },
        { name: "已完成", count: stats.completed },
        { name: "已忽略", count: stats.cancelled },
      ]
    : [];
  const total = stats ? stats.pending + stats.completed + stats.cancelled : 0;
  const ratio = total ? Math.round((stats!.completed / total) * 100) : 0;
  return (
    <Card>
      <CardHeader>
        <CardTitle>工作概览</CardTitle>
        <span className="text-[10px] text-stone-400">当前全部待办</span>
      </CardHeader>
      <CardContent>
        {stats ? (
          <>
            <div className="flex items-baseline justify-between">
              <span className="text-xs text-stone-400">累计完成比例</span>
              <strong className="text-xl tabular-nums">{ratio}%</strong>
            </div>
            <progress
              value={ratio}
              max={100}
              aria-label="累计完成比例"
              className="mt-3 h-1 w-full accent-emerald-700"
            />
            <div className="mt-3 space-y-2 text-xs text-stone-500">
              {data.map((item) => (
                <p key={item.name} className="flex justify-between">
                  <span>{item.name}</span>
                  <span className="tabular-nums">{item.count}</span>
                </p>
              ))}
            </div>
          </>
        ) : (
          <p className="py-16 text-center text-xs text-stone-400">
            等待真实数据
          </p>
        )}
      </CardContent>
    </Card>
  );
}
