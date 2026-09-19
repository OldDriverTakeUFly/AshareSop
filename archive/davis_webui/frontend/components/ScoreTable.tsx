"use client";
import { useState } from "react";
import type { DavisScore } from "@/lib/types";

type SortKey = keyof Pick<
  DavisScore,
  | "rank"
  | "final_score"
  | "valuation_score"
  | "trend_score"
  | "prosperity_score"
  | "distress_score"
>;

function scoreColor(score: number): string {
  if (score >= 70) return "text-green-400";
  if (score >= 50) return "text-yellow-400";
  return "text-red-400";
}

export function ScoreTable({
  scores,
  onRowClick,
  onDelete,
}: {
  scores: DavisScore[];
  onRowClick?: (tsCode: string) => void;
  onDelete?: (tsCode: string) => void;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [sortAsc, setSortAsc] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);

  const sorted = [...scores].sort((a, b) => {
    const diff = a[sortKey] - b[sortKey];
    return sortAsc ? diff : -diff;
  });

  const handleSort = (key: SortKey) => {
    if (key === sortKey) setSortAsc(!sortAsc);
    else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  const headers: { key: SortKey; label: string }[] = [
    { key: "rank", label: "排名" },
    { key: "final_score", label: "综合评分" },
    { key: "valuation_score", label: "估值" },
    { key: "trend_score", label: "趋势" },
    { key: "prosperity_score", label: "景气度" },
    { key: "distress_score", label: "困境" },
  ];

  const handleDelete = (e: React.MouseEvent, tsCode: string) => {
    e.stopPropagation();
    setDeleting(tsCode);
    onDelete?.(tsCode);
    setTimeout(() => setDeleting(null), 1000);
  };

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-zinc-700">
          <th className="text-left p-2 text-zinc-400">代码</th>
          <th className="text-left p-2 text-zinc-400">名称</th>
          {headers.map((h) => (
            <th
              key={h.key}
              onClick={() => handleSort(h.key)}
              className="text-right p-2 text-zinc-400 cursor-pointer hover:text-zinc-200"
            >
              {h.label} {sortKey === h.key ? (sortAsc ? "↑" : "↓") : ""}
            </th>
          ))}
          {onDelete && <th className="p-2" />}
        </tr>
      </thead>
      <tbody>
        {sorted.map((s) => (
          <tr
            key={s.ts_code}
            onClick={() => onRowClick?.(s.ts_code)}
            className="border-b border-zinc-800 hover:bg-zinc-800 cursor-pointer"
          >
            <td className="p-2 font-mono">{s.ts_code}</td>
            <td className="p-2">{s.name}</td>
            {headers.map((h) => (
              <td
                key={h.key}
                className={`p-2 text-right ${h.key !== "rank" ? scoreColor(s[h.key]) : ""}`}
              >
                {typeof s[h.key] === "number"
                  ? s[h.key].toFixed(1)
                  : s[h.key]}
              </td>
            ))}
            {onDelete && (
              <td className="p-2 text-right">
                <button
                  onClick={(e) => handleDelete(e, s.ts_code)}
                  disabled={deleting === s.ts_code}
                  className="text-zinc-600 hover:text-red-400 disabled:opacity-30 text-xs px-1"
                  title="删除"
                >
                  {deleting === s.ts_code ? "..." : "✕"}
                </button>
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
