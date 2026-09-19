"use client";

import { useMemo } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import type { SectorFundFlow } from "@/lib/types";

interface SectorBarChartProps {
  data: SectorFundFlow[];
  limit?: number;
}

export function SectorBarChart({ data, limit = 20 }: SectorBarChartProps) {
  const chartData = useMemo(() => {
    return [...data]
      .sort((a, b) => b.main_net - a.main_net)
      .slice(0, limit);
  }, [data, limit]);

  return (
    <ResponsiveContainer width="100%" height={Math.max(chartData.length * 28, 200)}>
      <BarChart
        data={chartData}
        layout="vertical"
        margin={{ top: 4, right: 16, left: 0, bottom: 4 }}
      >
        <CartesianGrid
          strokeDasharray="3 3"
          stroke="var(--color-border)"
          horizontal={false}
        />
        <XAxis
          type="number"
          tick={{ fontSize: 12, fill: "var(--color-muted-foreground)" }}
          tickFormatter={(v: number) => `${v.toFixed(0)}亿`}
        />
        <YAxis
          type="category"
          dataKey="name"
          width={80}
          tick={{ fontSize: 12, fill: "var(--color-muted-foreground)" }}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "var(--color-popover)",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-lg)",
            fontSize: 13,
          }}
          formatter={(value) => [`${Number(value ?? 0).toFixed(2)}亿`, "主力净流入"]}
        />
        <Bar dataKey="main_net" radius={[0, 4, 4, 0]} maxBarSize={20}>
          {chartData.map((entry, index) => (
            <Cell
              key={index}
              fill={entry.main_net >= 0 ? "#ef4444" : "#22c55e"}
              fillOpacity={0.85}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
