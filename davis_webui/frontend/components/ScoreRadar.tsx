"use client";
import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  Radar,
  ResponsiveContainer,
} from "recharts";

export function ScoreRadar({
  scores,
}: {
  scores: {
    valuation: number;
    trend: number;
    prosperity: number;
    distress: number;
  };
}) {
  const data = [
    { subject: "估值", value: scores.valuation },
    { subject: "趋势", value: scores.trend },
    { subject: "景气度", value: scores.prosperity },
    { subject: "困境", value: scores.distress },
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
