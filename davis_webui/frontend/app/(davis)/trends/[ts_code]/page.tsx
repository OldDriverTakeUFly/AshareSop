"use client";
import { use, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { getTrendData } from "@/lib/api";
import { TrendChart } from "@/components/TrendChart";

function TrendsContent({
  params,
}: {
  params: Promise<{ ts_code: string }>;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { ts_code } = use(params);
  const taskId = searchParams.get("task") || "";

  const { data: trendData, error } = useQuery({
    queryKey: ["trend-data", taskId, ts_code],
    queryFn: () => getTrendData(taskId, ts_code),
    enabled: !!taskId,
  });

  if (!taskId) return <div className="text-zinc-500">缺少任务ID</div>;
  if (error)
    return (
      <div className="text-red-400">
        加载失败: {(error as Error).message}
      </div>
    );
  if (!trendData)
    return <div className="text-zinc-500">加载中...（获取估值历史数据）</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <button
          onClick={() => router.back()}
          className="text-sm text-zinc-400 hover:text-zinc-200"
        >
          ← 返回
        </button>
        <h1 className="text-2xl font-bold">PE/PB 趋势分析</h1>
      </div>

      <div className="bg-zinc-900 p-6 rounded-lg">
        <TrendChart data={trendData} />
      </div>

      <div className="bg-zinc-900 p-4 rounded-lg text-sm text-zinc-400 space-y-2">
        <p className="font-semibold text-zinc-300">趋势解读</p>
        <p>
          PE/PB 下行代表估值正在回归合理区间，对戴维斯双击策略而言是看多信号。
        </p>
        <p>斜率为负表示估值在下降（利好），加速度为负表示下降在加速。</p>
        <p>非周期股使用 PE 70% + PB 30% 加权，周期股仅使用 PB。</p>
      </div>
    </div>
  );
}

export default function TrendsPage({
  params,
}: {
  params: Promise<{ ts_code: string }>;
}) {
  return (
    <Suspense fallback={<div className="text-zinc-500">加载中...</div>}>
      <TrendsContent params={params} />
    </Suspense>
  );
}
