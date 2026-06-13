"use client";
import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  startScreening,
  getScreeningResults,
  loadHistoryTask,
  getTaskStatus,
  removeStockFromResults,
} from "@/lib/api";
import { ScoreTable } from "@/components/ScoreTable";
import { TaskProgress } from "@/components/TaskProgress";

const STORAGE_KEY = "davis_screening_task_id";

function ScreeningContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const loadedTaskId = searchParams.get("task");
  const queryClient = useQueryClient();

  const [topN, setTopN] = useState(30);
  const [dryRun, setDryRun] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showResults, setShowResults] = useState(false);
  const [dismissHistory, setDismissHistory] = useState(false);
  const [restoredFromStorage, setRestoredFromStorage] = useState(false);

  useEffect(() => {
    if (loadedTaskId) return;
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
    } else if (
      restoredStatus.status === "failed" ||
      restoredStatus.status === "pending"
    ) {
      if (restoredStatus.status === "failed") {
        localStorage.removeItem(STORAGE_KEY);
        setTaskId(null);
        setRestoredFromStorage(false);
      }
    } else if (restoredStatus.status === "running") {
      setShowResults(false);
    }
  }, [restoredFromStorage, restoredStatus]);

  const handleStart = async () => {
    setError(null);
    setShowResults(false);
    try {
      const res = await startScreening({ top_n: topN, dry_run: dryRun });
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
    queryKey: ["screening-results", taskId],
    queryFn: () => getScreeningResults(taskId!),
    enabled: showResults && !!taskId,
  });

  const handleDeleteStock = async (tsCode: string) => {
    if (!taskId) return;
    try {
      await removeStockFromResults(taskId, tsCode);
      queryClient.invalidateQueries({
        queryKey: ["screening-results", taskId],
      });
    } catch (e) {
      setError((e as Error).message);
    }
  };

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

  const handleDeleteStockHistory = async (tsCode: string) => {
    if (!loadedTaskId) return;
    try {
      await removeStockFromResults(loadedTaskId, tsCode);
      queryClient.invalidateQueries({
        queryKey: ["screening-results-history", loadedTaskId],
      });
    } catch (e) {
      setError((e as Error).message);
    }
  };

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

        {error && (
          <div className="text-red-400 text-sm bg-red-950/50 border border-red-800 rounded px-3 py-2">
            {error}
          </div>
        )}

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
                onDelete={handleDeleteStockHistory}
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

  const isRunning =
    taskId &&
    !showResults &&
    (restoredFromStorage
      ? restoredStatus?.status === "running" ||
        restoredStatus?.status === "pending"
      : true);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">戴维斯双击估值筛选</h1>

      {error && (
        <div className="text-red-400 text-sm bg-red-950/50 border border-red-800 rounded px-3 py-2">
          {error}
        </div>
      )}

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
            <p className="text-zinc-400 text-sm">共 {results.total_count} 只标的</p>
            <button
              onClick={handleReset}
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
              onDelete={handleDeleteStock}
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
