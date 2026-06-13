"use client";
import { useQuery } from "@tanstack/react-query";
import { getHistory } from "@/lib/api";

export function TaskSelector({
  value,
  onChange,
}: {
  value: string;
  onChange: (taskId: string) => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["history"],
    queryFn: () => getHistory(),
  });

  if (isLoading) {
    return <div className="text-zinc-500 text-sm">加载历史记录...</div>;
  }

  const history = data?.history ?? [];

  if (history.length === 0) {
    return (
      <div className="text-zinc-500 text-sm">
        暂无历史筛选记录，请先在筛选页面运行一次筛选。
      </div>
    );
  }

  const formatDate = (iso: string) => {
    const d = new Date(iso);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };

  return (
    <div className="flex items-center gap-3">
      <label className="text-sm text-zinc-400">选择筛选记录</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-zinc-200 min-w-[280px]"
      >
        <option value="">— 请选择 —</option>
        {history.map((entry) => (
          <option key={entry.task_id} value={entry.task_id}>
            {formatDate(entry.created_at)}（{entry.total_count} 只）
          </option>
        ))}
      </select>
    </div>
  );
}
