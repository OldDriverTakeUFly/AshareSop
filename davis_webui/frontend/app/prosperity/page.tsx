"use client";
import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  startProsperitySector,
  getProsperitySectorResults,
  getTaskStatus,
} from "@/lib/api";
import { TaskProgress } from "@/components/TaskProgress";
import { StageBadge } from "@/components/StageBadge";

const STORAGE_KEY = "prosperity_task_id";

function cellColor(value: number): string {
  const r = Math.round(239 - value * 205);
  const g = Math.round(68 + value * 129);
  const b = Math.round(68 + value * 26);
  return `rgb(${r}, ${g}, ${b})`;
}

function ProsperityContent() {
  const searchParams = useSearchParams();
  const loadedTaskId = searchParams.get("task");

  const [topN, setTopN] = useState(10);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showResults, setShowResults] = useState(false);
  const [restoredFromStorage, setRestoredFromStorage] = useState(false);

  useEffect(() => {
    if (loadedTaskId) {
      setTaskId(loadedTaskId);
      setRestoredFromStorage(true);
      return;
    }
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) return;
    setTaskId(stored);
    setRestoredFromStorage(true);
  }, [loadedTaskId]);

  const { data: restoredStatus } = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => getTaskStatus(taskId!),
    enabled: !!taskId && restoredFromStorage,
    refetchInterval: (query) => {
      if (
        query.state.data?.status === "completed" ||
        query.state.data?.status === "failed"
      ) {
        return false;
      }
      return 2000;
    },
  });

  useEffect(() => {
    if (!restoredFromStorage || !restoredStatus) return;
    if (restoredStatus.status === "completed") {
      setShowResults(true);
      setRestoredFromStorage(false);
    } else if (restoredStatus.status === "failed") {
      localStorage.removeItem(STORAGE_KEY);
      setTaskId(null);
      setRestoredFromStorage(false);
    } else if (restoredStatus.status === "running") {
      setShowResults(false);
    }
  }, [restoredFromStorage, restoredStatus]);

  const handleStart = async () => {
    setError(null);
    setShowResults(false);
    try {
      const res = await startProsperitySector({
        top_n_per_industry: topN,
      });
      setTaskId(res.task_id);
      setRestoredFromStorage(true);
      localStorage.setItem(STORAGE_KEY, res.task_id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const handleReset = () => {
    setTaskId(null);
    setShowResults(false);
    setRestoredFromStorage(false);
    localStorage.removeItem(STORAGE_KEY);
  };

  const { data: results } = useQuery({
    queryKey: ["prosperity-sector-results", taskId],
    queryFn: () => getProsperitySectorResults(taskId!),
    enabled: showResults && !!taskId,
  });

  const [sortKey, setSortKey] = useState<string>("avg_composite_score");
  const [sortAsc, setSortAsc] = useState(false);

  const handleSort = (key: string) => {
    if (key === sortKey) setSortAsc(!sortAsc);
    else {
      setSortKey(key);
      setSortAsc(false);
    }
  };

  const sorted = [...(results?.industries ?? [])].sort((a, b) => {
    const av = (a as unknown as Record<string, unknown>)[sortKey] as number;
    const bv = (b as unknown as Record<string, unknown>)[sortKey] as number;
    return sortAsc ? av - bv : bv - av;
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

  const isRunning =
    taskId &&
    !showResults &&
    (restoredFromStorage
      ? restoredStatus?.status === "running" ||
        restoredStatus?.status === "pending"
      : true);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">行业景气度排名</h1>

      {error && (
        <div className="text-red-400 text-sm bg-red-950/50 border border-red-800 rounded px-3 py-2">
          {error}
        </div>
      )}

      {!taskId && (
        <div className="flex items-center gap-4 p-4 bg-zinc-900 rounded-lg">
          <label className="text-sm text-zinc-400">每行业标的数量</label>
          <input
            type="number"
            value={topN}
            onChange={(e) => setTopN(Number(e.target.value))}
            min={1}
            max={100}
            className="bg-zinc-800 border border-zinc-700 rounded px-3 py-1 text-sm w-20"
          />
          <button
            onClick={handleStart}
            className="bg-blue-600 hover:bg-blue-500 px-4 py-1.5 rounded text-sm font-medium"
          >
            开始分析
          </button>
        </div>
      )}

      {isRunning && (
        <div className="space-y-4">
          <TaskProgress
            taskId={taskId!}
            onComplete={() => {
              setShowResults(true);
              setRestoredFromStorage(false);
            }}
          />
          <button
            onClick={handleReset}
            className="text-sm text-zinc-500 hover:text-zinc-300"
          >
            取消
          </button>
        </div>
      )}

      {showResults && results && (
        <div className="space-y-4">
          <div className="flex justify-between items-center">
            <p className="text-zinc-400 text-sm">
              共 {results.total_industries} 个行业 · 分析日期{" "}
              {results.analysis_date}
            </p>
            <button
              onClick={handleReset}
              className="text-sm text-blue-400 hover:text-blue-300"
            >
              重新分析
            </button>
          </div>

          {results.industries.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="text-sm border-collapse">
                <thead>
                  <tr>
                    <th className="sticky left-0 bg-zinc-900 p-2 text-left text-zinc-400 border-b border-zinc-700">
                      行业
                    </th>
                    {sortableTh("stock_count", "股票数", "text-center")}
                    {sortableTh(
                      "avg_composite_score",
                      "景气评分",
                      "text-center",
                    )}
                    {sortableTh(
                      "median_delta_g",
                      "ΔG中位数",
                      "text-center",
                    )}
                    <th className="p-2 text-zinc-400 border-b border-zinc-700 whitespace-nowrap text-center">
                      阶段
                    </th>
                    {sortableTh("ignition_count", "点火数", "text-center")}
                    {sortableTh("avg_revenue_score", "营收", "text-center")}
                    {sortableTh("avg_profit_score", "盈利", "text-center")}
                    {sortableTh("avg_slope_score", "斜率", "text-center")}
                    {sortableTh(
                      "avg_duration_score",
                      "持续",
                      "text-center",
                    )}
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((row) => (
                    <tr key={row.industry} className="hover:bg-zinc-800">
                      <td className="sticky left-0 bg-zinc-900 p-2 border-b border-zinc-800 whitespace-nowrap">
                        <Link
                          href={`/prosperity/${encodeURIComponent(row.industry)}?task=${taskId}`}
                          className="text-blue-400 hover:text-blue-300"
                        >
                          {row.industry}
                        </Link>
                      </td>
                      <td className="p-2 border-b border-zinc-800 text-center text-zinc-300 font-mono">
                        {row.stock_count}
                      </td>
                      <td
                        className="p-2 border-b border-zinc-800 text-center font-mono font-bold"
                        style={{
                          backgroundColor: cellColor(
                            row.avg_composite_score / 100,
                          ),
                        }}
                      >
                        {row.avg_composite_score.toFixed(1)}
                      </td>
                      <td
                        className={`p-2 border-b border-zinc-800 text-center font-mono ${
                          row.median_delta_g > 0
                            ? "text-green-400"
                            : row.median_delta_g < 0
                              ? "text-red-400"
                              : "text-zinc-400"
                        }`}
                      >
                        {row.median_delta_g.toFixed(2)}
                      </td>
                      <td className="p-2 border-b border-zinc-800 text-center">
                        <StageBadge stage={row.stage} />
                      </td>
                      <td className="p-2 border-b border-zinc-800 text-center font-mono">
                        {row.ignition_count > 0
                          ? `🔥 ${row.ignition_count}`
                          : row.ignition_count}
                      </td>
                      <td
                        className="p-2 border-b border-zinc-800 text-center font-mono"
                        style={{
                          backgroundColor: cellColor(
                            row.avg_revenue_score / 100,
                          ),
                        }}
                      >
                        {row.avg_revenue_score.toFixed(1)}
                      </td>
                      <td
                        className="p-2 border-b border-zinc-800 text-center font-mono"
                        style={{
                          backgroundColor: cellColor(
                            row.avg_profit_score / 100,
                          ),
                        }}
                      >
                        {row.avg_profit_score.toFixed(1)}
                      </td>
                      <td
                        className="p-2 border-b border-zinc-800 text-center font-mono"
                        style={{
                          backgroundColor: cellColor(
                            row.avg_slope_score / 100,
                          ),
                        }}
                      >
                        {row.avg_slope_score.toFixed(1)}
                      </td>
                      <td
                        className="p-2 border-b border-zinc-800 text-center font-mono"
                        style={{
                          backgroundColor: cellColor(
                            row.avg_duration_score / 100,
                          ),
                        }}
                      >
                        {row.avg_duration_score.toFixed(1)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-center py-12 text-zinc-500">
              <p className="text-lg">暂无行业数据</p>
            </div>
          )}

          <Link
            href={`/prosperity/heatmap?task=${taskId}`}
            className="text-blue-400 hover:text-blue-300 text-sm"
          >
            📊 行业热力图 →
          </Link>
        </div>
      )}
    </div>
  );
}

export default function ProsperityPage() {
  return (
    <Suspense
      fallback={<div className="text-zinc-500">加载中...</div>}
    >
      <ProsperityContent />
    </Suspense>
  );
}
