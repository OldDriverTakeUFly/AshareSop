"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { getHistory, loadHistoryTask } from "@/lib/api";

export default function HistoryPage() {
  const router = useRouter();
  const [loadingTask, setLoadingTask] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["history"],
    queryFn: () => getHistory(),
  });

  const handleView = async (taskId: string) => {
    setError(null);
    setLoadingTask(taskId);
    try {
      await loadHistoryTask(taskId);
      router.push(`/screening?task=${taskId}`);
    } catch (e) {
      setError((e as Error).message);
      setLoadingTask(null);
    }
  };

  const formatDate = (iso: string) => {
    const d = new Date(iso);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">历史记录</h1>

      {error && (
        <div className="text-red-400 text-sm">{error}</div>
      )}

      {isLoading && <div className="text-zinc-500">加载中...</div>}

      {data && data.history.length === 0 && (
        <div className="text-center py-12 text-zinc-500">
          <p className="text-lg">暂无历史记录</p>
          <Link
            href="/screening"
            className="text-blue-400 hover:text-blue-300 text-sm mt-2 inline-block"
          >
            前往筛选页面 →
          </Link>
        </div>
      )}

      {data && data.history.length > 0 && (
        <div className="bg-zinc-900 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-700">
                <th className="text-left p-3 text-zinc-400">时间</th>
                <th className="text-right p-3 text-zinc-400">标的数</th>
                <th className="text-right p-3 text-zinc-400">股票数</th>
                <th className="text-right p-3 text-zinc-400">操作</th>
              </tr>
            </thead>
            <tbody>
              {data.history.map((entry) => (
                <tr
                  key={entry.task_id}
                  className="border-b border-zinc-800 hover:bg-zinc-800"
                >
                  <td className="p-3 text-zinc-300">
                    {formatDate(entry.created_at)}
                  </td>
                  <td className="p-3 text-right text-zinc-400">
                    {entry.top_n}
                  </td>
                  <td className="p-3 text-right text-zinc-400">
                    {entry.total_count}
                  </td>
                  <td className="p-3 text-right">
                    <button
                      onClick={() => handleView(entry.task_id)}
                      disabled={loadingTask === entry.task_id}
                      className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-3 py-1 rounded text-xs font-medium"
                    >
                      {loadingTask === entry.task_id ? "加载中..." : "查看"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
