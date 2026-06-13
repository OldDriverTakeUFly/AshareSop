"use client";
import type { DistressHeatmapData } from "@/lib/types";

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

export function DistressHeatmap({
  data,
  onRowClick,
}: {
  data: DistressHeatmapData;
  onRowClick?: (tsCode: string) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="text-xs border-collapse">
        <thead>
          <tr>
            <th className="sticky left-0 bg-zinc-900 p-2 text-left text-zinc-400 border-b border-zinc-700">
              股票
            </th>
            {COLUMNS.map((col) => (
              <th
                key={col.key}
                className="p-2 text-zinc-400 border-b border-zinc-700 whitespace-nowrap"
                title={col.label}
              >
                {col.label.split("/")[1]}
              </th>
            ))}
            <th className="p-2 text-zinc-400 border-b border-zinc-700">总分</th>
          </tr>
        </thead>
        <tbody>
          {data.stocks.map((stock) => (
            <tr
              key={stock.ts_code}
              onClick={() => onRowClick?.(stock.ts_code)}
              className="hover:bg-zinc-800 cursor-pointer"
            >
              <td className="sticky left-0 bg-zinc-900 p-2 whitespace-nowrap border-b border-zinc-800">
                <span className="font-mono text-zinc-300">{stock.ts_code}</span>
                <span className="ml-2 text-zinc-400">{stock.name}</span>
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
