"use client";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import type { TrendData } from "@/lib/types";

export function TrendChart({ data }: { data: TrendData }) {
  const chartData = data.monthly_dates.map((date, i) => ({
    date,
    PE: data.monthly_pe[i] ?? null,
    PB: data.monthly_pb[i] ?? null,
  }));

  return (
    <div className="space-y-4">
      <div className="flex gap-6 text-sm">
        <div>
          <span className="text-zinc-400">PE斜率:</span>{" "}
          <span className="text-blue-400 font-mono">
            {data.pe_slope.toFixed(4)}
          </span>
        </div>
        <div>
          <span className="text-zinc-400">PB斜率:</span>{" "}
          <span className="text-orange-400 font-mono">
            {data.pb_slope.toFixed(4)}
          </span>
        </div>
        <div>
          <span className="text-zinc-400">趋势评分:</span>
          <span
            className={`font-mono font-bold ml-1 ${data.trend_score >= 60 ? "text-green-400" : data.trend_score >= 40 ? "text-yellow-400" : "text-red-400"}`}
          >
            {data.trend_score.toFixed(1)}
          </span>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={400}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
          <XAxis
            dataKey="date"
            tick={{ fill: "#71717a", fontSize: 11 }}
            angle={-45}
            textAnchor="end"
            height={60}
          />
          <YAxis tick={{ fill: "#71717a", fontSize: 11 }} />
          <Tooltip
            contentStyle={{
              backgroundColor: "#18181b",
              border: "1px solid #3f3f46",
              borderRadius: "4px",
            }}
          />
          <Legend />
          <Line
            type="monotone"
            dataKey="PE"
            stroke="#3b82f6"
            strokeWidth={2}
            dot={false}
            connectNulls
          />
          <Line
            type="monotone"
            dataKey="PB"
            stroke="#f97316"
            strokeWidth={2}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
