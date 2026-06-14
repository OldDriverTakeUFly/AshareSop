"use client";
import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { getProsperitySectorResults } from "@/lib/api";
import { ProsperityHeatmap } from "@/components/ProsperityHeatmap";

function HeatmapContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const taskId = searchParams.get("task") || "";

  const { data, error, isLoading } = useQuery({
    queryKey: ["prosperity-heatmap", taskId],
    queryFn: () => getProsperitySectorResults(taskId),
    enabled: !!taskId,
  });

  if (!taskId) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold">景气赛道热力图</h1>
        <div className="text-center py-12 text-zinc-500">
          <Link
            href="/prosperity"
            className="text-blue-400 hover:text-blue-300 text-sm"
          >
            前景气分析页面 →
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
        <Link
          href={`/prosperity?task=${taskId}`}
          className="text-blue-400 hover:text-blue-300 text-sm"
        >
          ← 返回景气排名
        </Link>
        <h1 className="text-2xl font-bold">景气赛道热力图</h1>
        <div className="text-red-400">
          加载失败: {(error as Error).message}
        </div>
      </div>
    );
  }

  if (!data || data.industries.length === 0) {
    return (
      <div className="space-y-6">
        <Link
          href={`/prosperity?task=${taskId}`}
          className="text-blue-400 hover:text-blue-300 text-sm"
        >
          ← 返回景气排名
        </Link>
        <h1 className="text-2xl font-bold">景气赛道热力图</h1>
        <div className="text-center py-12 text-zinc-500">
          <p className="text-lg">暂无景气赛道数据</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Link
        href={`/prosperity?task=${taskId}`}
        className="text-blue-400 hover:text-blue-300 text-sm"
      >
        ← 返回景气排名
      </Link>
      <h1 className="text-2xl font-bold">景气赛道热力图</h1>

      <div className="bg-zinc-900 p-4 rounded-lg overflow-x-auto">
        <ProsperityHeatmap
          data={data}
          onRowClick={(industry) =>
            router.push(
              `/prosperity/${encodeURIComponent(industry)}?task=${taskId}`,
            )
          }
        />
      </div>

      <div className="flex items-center gap-4">
        <span className="text-sm text-zinc-400">0 低分</span>
        <div
          className="flex-1 h-4 rounded"
          style={{
            background:
              "linear-gradient(to right, rgb(239,68,68), rgb(34,197,94))",
          }}
        />
        <span className="text-sm text-zinc-400">100 高分</span>
      </div>
    </div>
  );
}

export default function HeatmapPage() {
  return (
    <Suspense fallback={<div className="text-zinc-500">加载中...</div>}>
      <HeatmapContent />
    </Suspense>
  );
}
