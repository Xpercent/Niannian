import { Bar, BarChart, CartesianGrid, XAxis } from "recharts";
import { Card, CardHeader, CardTitle, CardContent } from "./ui";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "./ui/chart";
import type { Stats } from "../lib/types";

const config = {
  count: { label: "待办数量", color: "#2c7150" },
} satisfies ChartConfig;

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
            <ChartContainer
              config={config}
              className="h-44 w-full"
              aria-label={`进行中 ${stats.pending} 项，已完成 ${stats.completed} 项，已忽略 ${stats.cancelled} 项`}
            >
              <BarChart
                data={data}
                accessibilityLayer={false}
                margin={{ top: 12, bottom: 0, left: 0, right: 0 }}
              >
                <CartesianGrid vertical={false} strokeDasharray="3 3" />
                <XAxis
                  dataKey="name"
                  tickLine={false}
                  axisLine={false}
                  tickMargin={10}
                  fontSize={10}
                />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Bar
                  dataKey="count"
                  fill="var(--color-count)"
                  radius={[4, 4, 0, 0]}
                  maxBarSize={28}
                  isAnimationActive={false}
                />
              </BarChart>
            </ChartContainer>
            <div className="mt-5 flex items-baseline justify-between">
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
