"use client";
import { useQuery } from "@tanstack/react-query";
import { getTaskStatus } from "@/lib/api";

export function TaskProgress({
  taskId,
  onComplete,
}: {
  taskId: string;
  onComplete?: () => void;
}) {
  const { data: task, error } = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => getTaskStatus(taskId),
    refetchInterval: (query) => {
      if (
        query.state.data?.status === "completed" ||
        query.state.data?.status === "failed"
      ) {
        if (query.state.data?.status === "completed") onComplete?.();
        return false;
      }
      return 2000;
    },
  });

  if (error)
    return (
      <div className="text-red-400 p-4">错误: {(error as Error).message}</div>
    );
  if (!task) return <div className="p-4">加载中...</div>;

  const barColor =
    task.status === "completed"
      ? "bg-green-500"
      : task.status === "failed"
        ? "bg-red-500"
        : "bg-blue-500";

  return (
    <div className="space-y-2">
      <div className="flex justify-between text-sm">
        <span>{task.message}</span>
        <span className="text-zinc-400">{task.progress.toFixed(0)}%</span>
      </div>
      <div className="w-full bg-zinc-800 rounded-full h-3 overflow-hidden">
        <div
          className={`${barColor} h-full transition-all duration-500`}
          style={{ width: `${task.progress}%` }}
        />
      </div>
      {task.status === "failed" && task.error && (
        <div className="text-red-400 text-sm">{task.error}</div>
      )}
    </div>
  );
}
