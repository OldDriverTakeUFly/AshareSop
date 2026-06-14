"use client";
import { useState } from "react";
import type { DistressHeatmapData, DistressHeatmapStock } from "@/lib/types";

function cellColor(value: number): string {
  const r = Math.round(239 - value * 205);
  const g = Math.round(68 + value * 129);
  const b = Math.round(68 + value * 26);
  return `rgb(${r}, ${g}, ${b})`;
}

const COLUMNS = [
  { layer: "layer1_signals", key: "eps_decline", label: "困境确认/EPS下滑" },
  { layer: "layer1_signals", key: "pe_pb_percentile", label: "困境确认/估值分位" },
  { layer: "layer1_signals", key: "financial_health", label: "困境确认/财务健康" },
  { layer: "layer2_signals", key: "balance_sheet", label: "反转可能/资产负债表" },
  { layer: "layer2_signals", key: "operating_cf", label: "反转可能/经营现金流" },
  { layer: "layer2_signals", key: "roe_trend", label: "反转可能/ROE趋势" },
  { layer: "layer3_signals", key: "revenue_inflection", label: "反转激活/营收拐点" },
  { layer: "layer3_signals", key: "profit_inflection", label: "反转激活/利润拐点" },
  { layer: "layer3_signals", key: "delta_g_positive", label: "反转激活/delta G" },
];

const LAYER_COLS = [
  { key: "layer1", label: "困境确认" },
  { key: "layer2", label: "反转可能" },
  { key: "layer3", label: "反转激活" },
];

function getSortValue(stock: DistressHeatmapStock, key: string): number {
  if (key === "rank") return stock.rank;
  if (key === "total_score") return stock.total_score;
  if (key === "layer1") return stock.layer_scores["layer1"] ?? 0;
  if (key === "layer2") return stock.layer_scores["layer2"] ?? 0;
  if (key === "layer3") return stock.layer_scores["layer3"] ?? 0;
  for (const col of COLUMNS) {
    if (col.key === key) {
      const layer =
        stock[
          col.layer as
            | "layer1_signals"
            | "layer2_signals"
            | "layer3_signals"
        ];
      return layer[col.key] ?? 0;
    }
  }
  return 0;
}

export function DistressHeatmap({
  data,
  onRowClick,
}: {
  data: DistressHeatmapData;
  onRowClick?: (tsCode: string) => void;
}) {
  const [sortKey, setSortKey] = useState<string>("rank");
  const [sortAsc, setSortAsc] = useState(true);

  const handleSort = (key: string) => {
    if (key === sortKey) setSortAsc(!sortAsc);
    else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  const sorted = [...data.stocks].sort((a, b) => {
    const diff = getSortValue(a, sortKey) - getSortValue(b, sortKey);
    return sortAsc ? diff : -diff;
  });

  const sortArrow = (key: string) =>
    sortKey === key ? (sortAsc ? " ↑" : " ↓") : "";

  const sortableTh = (
    key: string,
    label: string,
    className?: string,
  ) => (
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
              股票
            </th>
            {sortableTh("rank", "排名")}
            {COLUMNS.map((col) => (
              <th
                key={col.key}
                onClick={() => handleSort(col.key)}
                className="p-2 text-zinc-400 border-b border-zinc-700 whitespace-nowrap cursor-pointer hover:text-zinc-200"
                title={col.label}
              >
                {col.label.split("/")[1]}
                {sortArrow(col.key)}
              </th>
            ))}
            {LAYER_COLS.map((col) =>
              sortableTh(col.key, col.label, "bg-zinc-950/50"),
            )}
            {sortableTh("total_score", "总分", "font-bold")}
          </tr>
        </thead>
        <tbody>
          {sorted.map((stock) => (
            <tr
              key={stock.ts_code}
              onClick={() => onRowClick?.(stock.ts_code)}
              className="hover:bg-zinc-800 cursor-pointer"
            >
              <td className="sticky left-0 bg-zinc-900 p-2 whitespace-nowrap border-b border-zinc-800">
                <span className="font-mono text-zinc-300">{stock.ts_code}</span>
                <span className="ml-2 text-zinc-400">{stock.name}</span>
              </td>
              <td className="p-2 border-b border-zinc-800 text-center text-zinc-300 font-mono">
                {stock.rank}
              </td>
              {COLUMNS.map((col) => {
                const layer =
                  stock[
                    col.layer as
                      | "layer1_signals"
                      | "layer2_signals"
                      | "layer3_signals"
                  ];
                const value = layer[col.key] ?? 0;
                return (
                  <td
                    key={col.key}
                    className="p-2 border-b border-zinc-800 text-center"
                    style={{ backgroundColor: cellColor(value) }}
                    title={`${col.label}: ${value.toFixed(2)}`}
                  >
                    {value.toFixed(2)}
                  </td>
                );
              })}
              {LAYER_COLS.map((col) => {
                const value = stock.layer_scores[col.key] ?? 0;
                return (
                  <td
                    key={col.key}
                    className="p-2 border-b border-zinc-800 text-center bg-zinc-950/50"
                    style={{ backgroundColor: cellColor(value / 100) }}
                    title={`${col.label}: ${value.toFixed(1)}`}
                  >
                    {value.toFixed(1)}
                  </td>
                );
              })}
              <td className="p-2 border-b border-zinc-800 text-center font-bold text-zinc-200">
                {stock.total_score.toFixed(1)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
