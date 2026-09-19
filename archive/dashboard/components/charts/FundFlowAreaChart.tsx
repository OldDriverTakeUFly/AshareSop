"use client";

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import type { MarketFundFlow } from "@/lib/types";

interface FundFlowAreaChartProps {
  data: MarketFundFlow[];
}

export function FundFlowAreaChart({ data }: FundFlowAreaChartProps) {
  return (
    <ResponsiveContainer width="100%" height={360}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis
          dataKey="date"
          tick={{ fontSize: 12, fill: "var(--color-muted-foreground)" }}
          tickFormatter={(v: string) => v.slice(5)}
        />
        <YAxis
          tick={{ fontSize: 12, fill: "var(--color-muted-foreground)" }}
          tickFormatter={(v: number) => `${v.toFixed(0)}亿`}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "var(--color-popover)",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-lg)",
            fontSize: 13,
          }}
          formatter={(value, name) => [
            `${Number(value ?? 0).toFixed(2)}亿`,
            String(name),
          ]}
        />
        <Legend wrapperStyle={{ fontSize: 13 }} />
        <Area
          type="monotone"
          dataKey="main_net"
          name="主力净流入"
          stroke="#3b82f6"
          fill="#3b82f6"
          fillOpacity={0.25}
          strokeWidth={2}
        />
        <Area
          type="monotone"
          dataKey="huge_net"
          name="超大单"
          stroke="#ef4444"
          fill="#ef4444"
          fillOpacity={0.15}
          strokeWidth={1.5}
        />
        <Area
          type="monotone"
          dataKey="large_net"
          name="大单"
          stroke="#f97316"
          fill="#f97316"
          fillOpacity={0.15}
          strokeWidth={1.5}
        />
        <Area
          type="monotone"
          dataKey="small_net"
          name="散户"
          stroke="#94a3b8"
          fill="#94a3b8"
          fillOpacity={0.1}
          strokeWidth={1}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
