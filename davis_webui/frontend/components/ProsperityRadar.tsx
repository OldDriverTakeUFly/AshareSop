"use client";
import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  Radar,
  ResponsiveContainer,
} from "recharts";

export function ProsperityRadar({
  scores,
}: {
  scores: {
    revenue_score: number;
    profit_score: number;
    slope_score: number;
    duration_score: number;
  };
}) {
  const data = [
    { subject: "营收增长", value: scores.revenue_score },
    { subject: "盈利增长", value: scores.profit_score },
    { subject: "趋势斜率", value: scores.slope_score },
    { subject: "持续时间", value: scores.duration_score },
  ];
  return (
    <ResponsiveContainer width="100%" height={300}>
      <RadarChart data={data}>
        <PolarGrid stroke="#3f3f46" />
        <PolarAngleAxis
          dataKey="subject"
          tick={{ fill: "#a1a1aa", fontSize: 13 }}
        />
        <Radar
          dataKey="value"
          stroke="#3b82f6"
          fill="#3b82f6"
          fillOpacity={0.3}
        />
      </RadarChart>
    </ResponsiveContainer>
  );
}
