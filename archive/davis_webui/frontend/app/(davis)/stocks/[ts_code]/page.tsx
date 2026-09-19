"use client";
import { use, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { getStockDetail, getReport } from "@/lib/api";
import { ScoreRadar } from "@/components/ScoreRadar";
import { ReportViewer } from "@/components/ReportViewer";

function StockDetailContent({
  params,
}: {
  params: Promise<{ ts_code: string }>;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { ts_code } = use(params);
  const taskId = searchParams.get("task") || "";

  const { data: detail, error: detailError } = useQuery({
    queryKey: ["stock-detail", taskId, ts_code],
    queryFn: () => getStockDetail(taskId, ts_code),
    enabled: !!taskId,
  });

  const { data: report } = useQuery({
    queryKey: ["report", taskId, ts_code],
    queryFn: () => getReport(taskId, ts_code),
    enabled: !!taskId,
  });

  if (!taskId) return <div className="text-zinc-500">缺少任务ID</div>;
  if (detailError)
    return (
      <div className="text-red-400">
        加载失败: {(detailError as Error).message}
      </div>
    );
  if (!detail) return <div className="text-zinc-500">加载中...</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <button
          onClick={() => router.push(`/screening`)}
          className="text-sm text-zinc-400 hover:text-zinc-200"
        >
          ← 返回
        </button>
        <h1 className="text-2xl font-bold">{detail.stock_info.name}</h1>
        <span className="text-zinc-500 font-mono">
          {detail.stock_info.ts_code}
        </span>
        <span className="text-zinc-400 text-sm">
          {detail.stock_info.industry}
        </span>
        {detail.stock_info.is_cyclical && (
          <span className="text-orange-400 text-xs">周期股</span>
        )}
        <span className="ml-auto bg-blue-600 px-3 py-1 rounded text-sm font-bold">
          {detail.davis_score.final_score.toFixed(1)}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-6">
        <div className="bg-zinc-900 p-4 rounded-lg">
          <h2 className="text-lg font-semibold mb-4">评分雷达</h2>
          <ScoreRadar
            scores={{
              valuation: detail.davis_score.valuation_score,
              trend: detail.davis_score.trend_score,
              prosperity: detail.davis_score.prosperity_score,
              distress: detail.davis_score.distress_score,
            }}
          />
        </div>
        <div className="bg-zinc-900 p-4 rounded-lg space-y-3">
          <h2 className="text-lg font-semibold">评分明细</h2>
          {[
            { label: "估值分位", value: detail.davis_score.valuation_score },
            { label: "趋势评分", value: detail.davis_score.trend_score },
            { label: "景气度", value: detail.davis_score.prosperity_score },
            { label: "困境反转", value: detail.davis_score.distress_score },
          ].map((s) => (
            <div key={s.label} className="flex justify-between items-center">
              <span className="text-zinc-400 text-sm">{s.label}</span>
              <span
                className={`font-mono font-bold ${
                  s.value >= 70
                    ? "text-green-400"
                    : s.value >= 50
                      ? "text-yellow-400"
                      : "text-red-400"
                }`}
              >
                {s.value.toFixed(1)}
              </span>
            </div>
          ))}
          {detail.prosperity_detail && (
            <>
              <div className="border-t border-zinc-700 pt-2 mt-2">
                <p className="text-zinc-500 text-xs mb-1">景气度明细</p>
              </div>
              {[
                {
                  label: "营收增长",
                  value: detail.prosperity_detail.revenue_score,
                },
                {
                  label: "盈利增长",
                  value: detail.prosperity_detail.profit_score,
                },
                {
                  label: "趋势斜率",
                  value: detail.prosperity_detail.slope_score,
                },
                {
                  label: "持续时间",
                  value: detail.prosperity_detail.duration_score,
                },
                { label: "delta G", value: detail.prosperity_detail.delta_g },
                {
                  label: "相对 ΔG",
                  value: detail.prosperity_detail.relative_delta_g ?? "—",
                },
              ].map((s) => (
                <div key={s.label} className="flex justify-between text-sm">
                  <span className="text-zinc-400">{s.label}</span>
                  <span className="font-mono text-zinc-300">
                    {typeof s.value === "number"
                      ? s.value.toFixed(2)
                      : s.value}
                  </span>
                </div>
              ))}
            </>
          )}
          <button
            onClick={() =>
              router.push(`/trends/${ts_code}?task=${taskId}`)
            }
            className="text-blue-400 text-sm hover:text-blue-300 mt-2"
          >
            查看趋势图 →
          </button>
        </div>
      </div>

      {report && (
        <div className="bg-zinc-900 p-6 rounded-lg">
          <h2 className="text-lg font-semibold mb-4">深度研报</h2>
          <ReportViewer markdown={report.markdown_content} />
        </div>
      )}
    </div>
  );
}

export default function StockDetailPage({
  params,
}: {
  params: Promise<{ ts_code: string }>;
}) {
  return (
    <Suspense fallback={<div className="text-zinc-500">加载中...</div>}>
      <StockDetailContent params={params} />
    </Suspense>
  );
}
