"use client";
import { useState } from "react";
import type { ProsperitySectorResults, IndustryScore } from "@/lib/types";

function cellColor(value: number): string {
  const r = Math.round(239 - value * 205);
  const g = Math.round(68 + value * 129);
  const b = Math.round(68 + value * 26);
  return `rgb(${r}, ${g}, ${b})`;
}

function deltaGColor(value: number): string {
  if (value > 0) {
    const intensity = Math.min(value / 50, 1);
    return `rgb(${Math.round(34 + (1 - intensity) * 100)}, ${Math.round(197)}, ${Math.round(94)})`;
  }
  if (value < 0) {
    const intensity = Math.min(Math.abs(value) / 50, 1);
    return `rgb(${Math.round(239)}, ${Math.round(68 + (1 - intensity) * 100)}, ${Math.round(68)})`;
  }
  return "rgb(63, 63, 70)";
}

const METRIC_COLUMNS = [
  { key: "avg_revenue_score" as const, label: "营收", max: 100 },
  { key: "avg_profit_score" as const, label: "盈利", max: 100 },
  { key: "avg_slope_score" as const, label: "斜率", max: 100 },
  { key: "avg_duration_score" as const, label: "持续", max: 100 },
  { key: "avg_composite_score" as const, label: "综合", max: 100 },
  { key: "ignition_count" as const, label: "点火", max: 50 },
];

type SortKey =
  | "stock_count"
  | "median_delta_g"
  | "avg_revenue_score"
  | "avg_profit_score"
  | "avg_slope_score"
  | "avg_duration_score"
  | "avg_composite_score"
  | "ignition_count";

function getMetricValue(row: IndustryScore, key: SortKey): number {
  switch (key) {
    case "stock_count":
      return row.stock_count;
    case "median_delta_g":
      return row.median_delta_g;
    case "avg_revenue_score":
      return row.avg_revenue_score;
    case "avg_profit_score":
      return row.avg_profit_score;
    case "avg_slope_score":
      return row.avg_slope_score;
    case "avg_duration_score":
      return row.avg_duration_score;
    case "avg_composite_score":
      return row.avg_composite_score;
    case "ignition_count":
      return row.ignition_count;
    default:
      return 0;
  }
}

export function ProsperityHeatmap({
  data,
  onRowClick,
}: {
  data: ProsperitySectorResults;
  onRowClick?: (industry: string) => void;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("avg_composite_score");
  const [sortAsc, setSortAsc] = useState(false);

  const handleSort = (key: SortKey) => {
    if (key === sortKey) setSortAsc(!sortAsc);
    else {
      setSortKey(key);
      setSortAsc(false);
    }
  };

  const sorted = [...data.industries].sort((a, b) => {
    const av = getMetricValue(a, sortKey);
    const bv = getMetricValue(b, sortKey);
    return sortAsc ? av - bv : bv - av;
  });

  const sortArrow = (key: SortKey) =>
    sortKey === key ? (sortAsc ? " ↑" : " ↓") : "";

  const sortableTh = (key: SortKey, label: string, className?: string) => (
    <th
      onClick={() => handleSort(key)}
      className={`p-2 text-zinc-400 border-b border-zinc-700 whitespace-nowrap cursor-pointer hover:text-zinc-200 ${
        className ?? ""
      }`}
    >
      {label}
      {sortArrow(key)}
    </th>
  );

  return (
    <div className="overflow-x-auto">
      <table className="text-xs border-collapse">
        <thead>
          <tr>
            <th className="sticky left-0 bg-zinc-900 p-2 text-left text-zinc-400 border-b border-zinc-700">
              行业
            </th>
            {sortableTh("stock_count", "股票数")}
            {sortableTh("median_delta_g", "ΔG中位数")}
            {METRIC_COLUMNS.map((col) =>
              sortableTh(
                col.key,
                col.label,
                col.key === "avg_composite_score" ? "font-bold" : undefined,
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr
              key={row.industry}
              onClick={() => onRowClick?.(row.industry)}
              className="hover:bg-zinc-800 cursor-pointer"
            >
              <td className="sticky left-0 bg-zinc-900 p-2 whitespace-nowrap border-b border-zinc-800">
                <span className="text-zinc-300">{row.industry}</span>
              </td>
              <td className="p-2 border-b border-zinc-800 text-center text-zinc-300 font-mono">
                {row.stock_count}
              </td>
              <td
                className="p-2 border-b border-zinc-800 text-center font-mono"
                style={{ backgroundColor: deltaGColor(row.median_delta_g) }}
                title={`ΔG中位数: ${row.median_delta_g.toFixed(2)}`}
              >
                {row.median_delta_g.toFixed(2)}
              </td>
              {METRIC_COLUMNS.map((col) => {
                const value = getMetricValue(row, col.key);
                const normalized = col.max > 0 ? value / col.max : value;
                return (
                  <td
                    key={col.key}
                    className={`p-2 border-b border-zinc-800 text-center font-mono ${
                      col.key === "avg_composite_score" ? "font-bold" : ""
                    }`}
                    style={{ backgroundColor: cellColor(normalized) }}
                    title={`${col.label}: ${value.toFixed(1)}`}
                  >
                    {col.key === "ignition_count" ? value : value.toFixed(1)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
