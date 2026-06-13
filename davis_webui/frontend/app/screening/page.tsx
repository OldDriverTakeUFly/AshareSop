"use client";
import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  startScreening,
  getScreeningResults,
  loadHistoryTask,
} from "@/lib/api";
import { ScoreTable } from "@/components/ScoreTable";
import { TaskProgress } from "@/components/TaskProgress";

function ScreeningContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const loadedTaskId = searchParams.get("task");

  const [topN, setTopN] = useState(30);
  const [dryRun, setDryRun] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showResults, setShowResults] = useState(false);
  const [dismissHistory, setDismissHistory] = useState(false);

  const handleStart = async () => {
    setError(null);
    setShowResults(false);
    try {
      const res = await startScreening({ top_n: topN, dry_run: dryRun });
      setTaskId(res.task_id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const { data: results } = useQuery({
    queryKey: ["screening-results", taskId],
    queryFn: () => getScreeningResults(taskId!),
    enabled: showResults && !!taskId,
  });

  const isHistoryMode = !!loadedTaskId && !dismissHistory;

  const { data: loadResult } = useQuery({
    queryKey: ["history-load", loadedTaskId],
    queryFn: () => loadHistoryTask(loadedTaskId!),
    enabled: isHistoryMode,
  });

  const { data: historyResults } = useQuery({
    queryKey: ["screening-results-history", loadedTaskId],
    queryFn: () => getScreeningResults(loadedTaskId!),
    enabled: isHistoryMode && !!loadResult?.loaded,
  });

  if (isHistoryMode) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">戴维斯双击估值筛选</h1>
          <Link
            href="/history"
            className="text-sm text-blue-400 hover:text-blue-300"
          >
            ← 返回历史
          </Link>
        </div>

        {!historyResults && (
          <div className="text-zinc-500">加载历史任务中...</div>
        )}

        {historyResults && (
          <div className="space-y-4">
            <div className="flex justify-between items-center">
              <p className="text-zinc-400 text-sm">
                共 {historyResults.total_count} 只标的
              </p>
              <button
                onClick={() => setDismissHistory(true)}
                className="text-sm text-blue-400 hover:text-blue-300"
              >
                重新筛选
              </button>
            </div>
            {historyResults.scores.length > 0 ? (
              <ScoreTable
                scores={historyResults.scores}
                onRowClick={(tsCode) =>
                  router.push(`/stocks/${tsCode}?task=${loadedTaskId}`)
                }
              />
            ) : (
              <div className="text-center py-12 text-zinc-500">
                <p className="text-lg">暂无筛选结果</p>
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">戴维斯双击估值筛选</h1>

      {!taskId && (
        <div className="flex items-center gap-4 p-4 bg-zinc-900 rounded-lg">
          <label className="text-sm text-zinc-400">标的数量</label>
          <input
            type="number"
            value={topN}
            onChange={(e) => setTopN(Number(e.target.value))}
            min={1}
            max={100}
            className="bg-zinc-800 border border-zinc-700 rounded px-3 py-1 text-sm w-20"
          />
          <label className="flex items-center gap-2 text-sm text-zinc-400">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
            />
            Dry Run
          </label>
          <button
            onClick={handleStart}
            className="bg-blue-600 hover:bg-blue-500 px-4 py-1.5 rounded text-sm font-medium"
          >
            开始筛选
          </button>
          {error && <span className="text-red-400 text-sm">{error}</span>}
        </div>
      )}

      {taskId && !showResults && (
        <div className="space-y-4">
          <TaskProgress taskId={taskId} onComplete={() => setShowResults(true)} />
          <button
            onClick={() => {
              setTaskId(null);
              setShowResults(false);
            }}
            className="text-sm text-zinc-500 hover:text-zinc-300"
          >
            取消
          </button>
        </div>
      )}

      {showResults && results && (
        <div className="space-y-4">
          <div className="flex justify-between items-center">
            <p className="text-zinc-400 text-sm">共 {results.total_count} 只标的</p>
            <button
              onClick={() => {
                setTaskId(null);
                setShowResults(false);
              }}
              className="text-sm text-blue-400 hover:text-blue-300"
            >
              重新筛选
            </button>
          </div>
          {results.scores.length > 0 ? (
            <ScoreTable
              scores={results.scores}
              onRowClick={(tsCode) =>
                router.push(`/stocks/${tsCode}?task=${taskId}`)
              }
            />
          ) : (
            <div className="text-center py-12 text-zinc-500">
              <p className="text-lg">暂无筛选结果</p>
              <p className="text-sm mt-2">请尝试调整参数后重新运行</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ScreeningPage() {
  return (
    <Suspense
      fallback={<div className="text-zinc-500">加载中...</div>}
    >
      <ScreeningContent />
    </Suspense>
  );
}
