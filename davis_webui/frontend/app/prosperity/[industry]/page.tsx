"use client";
import { Suspense, useState } from "react";
import Link from "next/link";
import { use } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { getProsperityIndustryDetail } from "@/lib/api";
import type { ProsperityStock } from "@/lib/types";
import { ProsperityRadar } from "@/components/ProsperityRadar";

function stageBadge(stage: string) {
  if (stage === "加速期")
    return (
      <span className="bg-green-900/50 text-green-400 border border-green-700 px-2 py-0.5 rounded text-xs">
        {stage}
      </span>
    );
  if (stage === "减速期")
    return (
      <span className="bg-yellow-900/50 text-yellow-400 border border-yellow-700 px-2 py-0.5 rounded text-xs">
        {stage}
      </span>
    );
  if (stage === "拐点期")
    return (
      <span className="bg-red-900/50 text-red-400 border border-red-700 px-2 py-0.5 rounded text-xs">
        {stage}
      </span>
    );
  return (
    <span className="bg-zinc-800 text-zinc-400 px-2 py-0.5 rounded text-xs">
      {stage}
    </span>
  );
}

function IndustryDetailContent({
  params,
}: {
  params: Promise<{ industry: string }>;
}) {
  const searchParams = useSearchParams();
  const { industry } = use(params);
  const taskId = searchParams.get("task") || "";
  const [selectedStock, setSelectedStock] = useState<ProsperityStock | null>(
    null,
  );

  const { data: detail, error, isLoading } = useQuery({
    queryKey: ["prosperity-industry", taskId, industry],
    queryFn: () => getProsperityIndustryDetail(taskId, industry),
    enabled: !!taskId && !!industry,
  });

  if (!taskId) return <div className="text-zinc-500">缺少任务ID</div>;
  if (isLoading) return <div className="text-zinc-500">加载中...</div>;
  if (error)
    return (
      <div className="text-red-400">
        加载失败: {(error as Error).message}
      </div>
    );
  if (!detail || detail.stocks.length === 0)
    return <div className="text-zinc-500">暂无数据</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link
          href={`/prosperity?task=${taskId}`}
          className="text-sm text-zinc-400 hover:text-zinc-200"
        >
          ← 返回景气排名
        </Link>
        <h1 className="text-2xl font-bold">{industry}</h1>
        {stageBadge(detail.industry_score.stage)}
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div className="bg-zinc-900 p-4 rounded-lg">
          <p className="text-zinc-400 text-sm">平均景气评分</p>
          <p className="text-3xl font-bold font-mono">
            {detail.industry_score.avg_composite_score.toFixed(1)}
          </p>
        </div>
        <div className="bg-zinc-900 p-4 rounded-lg">
          <p className="text-zinc-400 text-sm">中位数 ΔG</p>
          <p
            className={`text-3xl font-bold font-mono ${
              detail.industry_score.median_delta_g > 0
                ? "text-green-400"
                : "text-red-400"
            }`}
          >
            {detail.industry_score.median_delta_g.toFixed(2)}
          </p>
        </div>
        <div className="bg-zinc-900 p-4 rounded-lg">
          <p className="text-zinc-400 text-sm">点火个股数</p>
          <p className="text-3xl font-bold font-mono text-orange-400">
            {detail.industry_score.ignition_count}
          </p>
        </div>
      </div>

      <div className="bg-zinc-900 rounded-lg overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-700 text-zinc-400">
              <th className="px-3 py-2 text-left">排名</th>
              <th className="px-3 py-2 text-left">代码/名称</th>
              <th className="px-3 py-2 text-right">景气评分</th>
              <th className="px-3 py-2 text-right">ΔG</th>
              <th className="px-3 py-2 text-center">阶段</th>
              <th className="px-3 py-2 text-center">点火</th>
              <th className="px-3 py-2 text-center">风险</th>
            </tr>
          </thead>
          <tbody>
            {detail.stocks.map((stock) => (
              <tr
                key={stock.ts_code}
                onClick={() => setSelectedStock(stock)}
                className={`border-b border-zinc-800 cursor-pointer hover:bg-zinc-800 ${
                  selectedStock?.ts_code === stock.ts_code ? "bg-zinc-800" : ""
                }`}
              >
                <td className="px-3 py-2 text-zinc-400 font-mono">
                  {stock.rank_in_industry}
                </td>
                <td className="px-3 py-2">
                  <span className="font-mono text-zinc-500">
                    {stock.ts_code}
                  </span>
                  <span className="ml-2 text-zinc-200">{stock.name}</span>
                </td>
                <td className="px-3 py-2 text-right font-mono font-bold">
                  <span
                    className={
                      stock.composite_score >= 70
                        ? "text-green-400"
                        : stock.composite_score >= 50
                          ? "text-yellow-400"
                          : "text-red-400"
                    }
                  >
                    {stock.composite_score.toFixed(1)}
                  </span>
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  <span
                    className={
                      stock.delta_g > 0
                        ? "text-green-400"
                        : "text-red-400"
                    }
                  >
                    {stock.delta_g.toFixed(2)}
                  </span>
                </td>
                <td className="px-3 py-2 text-center">
                  {stageBadge(stock.stage)}
                </td>
                <td className="px-3 py-2 text-center">
                  {stock.is_ignition ? "🔥" : ""}
                </td>
                <td className="px-3 py-2 text-center">
                  {stock.risk_warnings.length > 0 ? (
                    <span
                      title={stock.risk_warnings.join("、")}
                      className="text-red-400 cursor-help"
                    >
                      {stock.risk_warnings.length}
                    </span>
                  ) : (
                    <span className="text-zinc-600">0</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selectedStock && (
        <div className="bg-zinc-900 p-4 rounded-lg">
          <h3 className="text-lg font-semibold mb-2">
            {selectedStock.name} 景气雷达
          </h3>
          <ProsperityRadar
            scores={{
              revenue_score: selectedStock.revenue_score,
              profit_score: selectedStock.profit_score,
              slope_score: selectedStock.slope_score,
              duration_score: selectedStock.duration_score,
            }}
          />
        </div>
      )}
    </div>
  );
}

export default function IndustryDetailPage({
  params,
}: {
  params: Promise<{ industry: string }>;
}) {
  return (
    <Suspense
      fallback={<div className="text-zinc-500">加载中...</div>}
    >
      <IndustryDetailContent params={params} />
    </Suspense>
  );
}
