"use client";
import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { getDistressHeatmap } from "@/lib/api";
import { DistressHeatmap } from "@/components/DistressHeatmap";

function DistressContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const taskId = searchParams.get("task") || "";

  const { data, error, isLoading } = useQuery({
    queryKey: ["distress-heatmap", taskId],
    queryFn: () => getDistressHeatmap(taskId),
    enabled: !!taskId,
  });

  if (!taskId) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold">困境反转热力图</h1>
        <div className="text-center py-12 text-zinc-500">
          <p className="text-lg">请先运行筛选</p>
          <Link
            href="/screening"
            className="text-blue-400 hover:text-blue-300 text-sm mt-2 inline-block"
          >
            前往筛选页面 →
          </Link>
        </div>
      </div>
    );
  }

  if (isLoading) {
    return <div className="text-zinc-500">加载中...</div>;
  }

  if (error) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold">困境反转热力图</h1>
        <div className="text-red-400">
          加载失败: {(error as Error).message}
        </div>
      </div>
    );
  }

  if (!data || data.stocks.length === 0) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold">困境反转热力图</h1>
        <div className="text-center py-12 text-zinc-500">
          <p className="text-lg">暂无困境信号数据</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">困境反转热力图</h1>

      <div className="bg-zinc-900 p-4 rounded-lg overflow-x-auto">
        <DistressHeatmap
          data={data}
          onRowClick={(tsCode) =>
            router.push(`/stocks/${tsCode}?task=${taskId}`)
          }
        />
      </div>

      <div className="flex items-center gap-4">
        <span className="text-sm text-zinc-400">0.0 低信号</span>
        <div
          className="flex-1 h-4 rounded"
          style={{
            background:
              "linear-gradient(to right, rgb(239,68,68), rgb(34,197,94))",
          }}
        />
        <span className="text-sm text-zinc-400">1.0 高信号</span>
      </div>

      <div className="space-y-3">
        <details className="bg-zinc-900 rounded-lg p-4">
          <summary className="cursor-pointer font-semibold text-zinc-200">
            困境确认
          </summary>
          <p className="text-zinc-400 text-sm mt-2">
            判断股票是否处于真正的困境状态。综合评估EPS下滑幅度、估值历史分位和财务健康指标。
          </p>
        </details>
        <details className="bg-zinc-900 rounded-lg p-4">
          <summary className="cursor-pointer font-semibold text-zinc-200">
            反转可能
          </summary>
          <p className="text-zinc-400 text-sm mt-2">
            判断资产负债表是否支撑困境反转。考察资产负债表改善、经营现金流回升和ROE趋势。
          </p>
        </details>
        <details className="bg-zinc-900 rounded-lg p-4">
          <summary className="cursor-pointer font-semibold text-zinc-200">
            反转激活
          </summary>
          <p className="text-zinc-400 text-sm mt-2">
            判断基本面是否出现拐点信号。关注营收拐点、利润拐点和delta G转正。
          </p>
        </details>
      </div>
    </div>
  );
}

export default function DistressPage() {
  return (
    <Suspense
      fallback={<div className="text-zinc-500">加载中...</div>}
    >
      <DistressContent />
    </Suspense>
  );
}
