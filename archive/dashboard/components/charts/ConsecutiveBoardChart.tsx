"use client";

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
import type { ConsecutiveBoard } from "@/lib/types";

interface ConsecutiveBoardChartProps {
  data: ConsecutiveBoard[];
}

// Gradient from light to dark blue
const BAR_COLORS = [
  "#93c5fd", // 1-board: lightest
  "#60a5fa", // 2-boards
  "#3b82f6", // 3-boards
  "#2563eb", // 4-boards
  "#1d4ed8", // 5-boards
  "#1e40af", // 6-boards
  "#1e3a8a", // 7+ boards: darkest
];

export function ConsecutiveBoardChart({ data }: ConsecutiveBoardChartProps) {
  const chartData = data.map((item) => ({
    board_count: item.board_count,
    stock_count: item.stocks.length,
  }));

  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis
          dataKey="board_count"
          label={{ value: "连板数", position: "insideBottom", offset: -2, fontSize: 12 }}
          tick={{ fontSize: 12, fill: "var(--color-muted-foreground)" }}
        />
        <YAxis
          allowDecimals={false}
          tick={{ fontSize: 12, fill: "var(--color-muted-foreground)" }}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "var(--color-popover)",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-lg)",
            fontSize: 13,
          }}
          formatter={(value) => [`${Number(value ?? 0)}只`, "个股数量"]}
          labelFormatter={(label) => `${label}连板`}
        />
        <Bar dataKey="stock_count" radius={[4, 4, 0, 0]} maxBarSize={48}>
          {chartData.map((_, index) => (
            <Cell
              key={index}
              fill={BAR_COLORS[Math.min(index, BAR_COLORS.length - 1)]}
              fillOpacity={0.9}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
