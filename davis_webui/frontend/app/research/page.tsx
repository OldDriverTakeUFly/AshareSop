"use client";
import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  generateChecklists,
  fillChecklist,
  rescore,
} from "@/lib/api";
import type {
  ChecklistData,
  ChecklistFillRequest,
  RescoreResult,
} from "@/lib/types";

function ResearchPageInner() {
  const params = useSearchParams();
  const taskId = params.get("task");

  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [topN, setTopN] = useState(3);
  const [checklists, setChecklists] = useState<ChecklistData[]>([]);
  const [fillData, setFillData] = useState<
    Record<string, ChecklistFillRequest>
  >({});
  const [rescoreResults, setRescoreResults] = useState<RescoreResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState(0);

  if (!taskId) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold">深度调研</h1>
        <p className="text-zinc-400">
          请先运行筛选，{" "}
          <Link href="/screening" className="text-blue-400 hover:text-blue-300">
            前往筛选页面 →
          </Link>
        </p>
      </div>
    );
  }

  const handleGenerate = async () => {
    setError(null);
    setLoading(true);
    try {
      const res = await generateChecklists({ task_id: taskId, top_n: topN });
      setChecklists(res.checklists);
      setActiveTab(0);
      setStep(2);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveAndRescore = async () => {
    setError(null);
    setLoading(true);
    try {
      for (const cl of checklists) {
        const fd = fillData[cl.ts_code] ?? {
          prosperity_adjustment: 0,
          distress_adjustment: 0,
        };
        await fillChecklist(cl.ts_code, fd);
      }
      const res = await rescore({ task_id: taskId });
      setRescoreResults(res.results);
      setStep(3);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const getFill = (tsCode: string): ChecklistFillRequest => {
    return (
      fillData[tsCode] ?? {
        prosperity_adjustment: 0,
        distress_adjustment: 0,
        research_notes: {},
      }
    );
  };

  const updateFill = (
    tsCode: string,
    field: "prosperity_adjustment" | "distress_adjustment",
    value: number,
  ) => {
    setFillData((prev) => ({
      ...prev,
      [tsCode]: { ...getFill(tsCode), [field]: value },
    }));
  };

  const isValidAdjustment = (v: number) => v >= -20 && v <= 20;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">深度调研</h1>

      {error && (
        <div className="text-red-400 text-sm bg-red-950/50 border border-red-800 rounded px-3 py-2">
          {error}
        </div>
      )}

      {step === 1 && (
        <div className="space-y-4">
          <p className="text-zinc-400">
            生成调研清单后，可对每只股票进行定性分析并调整景气度和困境分数
          </p>
          <div className="flex items-center gap-4 p-4 bg-zinc-900 rounded-lg">
            <label className="text-sm text-zinc-400">调研数量</label>
            <input
              type="number"
              value={topN}
              onChange={(e) => setTopN(Number(e.target.value))}
              min={1}
              max={30}
              className="bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm w-20"
            />
            <button
              onClick={handleGenerate}
              disabled={loading}
              className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-4 py-1.5 rounded text-sm font-medium"
            >
              {loading ? "生成中..." : "生成调研清单"}
            </button>
          </div>
        </div>
      )}

      {step === 2 && checklists.length > 0 && (
        <div className="space-y-4">
          <div className="flex border-b border-zinc-700">
            {checklists.map((cl, i) => (
              <button
                key={cl.ts_code}
                onClick={() => setActiveTab(i)}
                className={`px-4 py-2 text-sm whitespace-nowrap ${
                  activeTab === i
                    ? "border-b-2 border-blue-500 text-zinc-100"
                    : "text-zinc-500 hover:text-zinc-300"
                }`}
              >
                {cl.name} ({cl.ts_code})
              </button>
            ))}
          </div>

          {(() => {
            const cl = checklists[activeTab];
            const fill = getFill(cl.ts_code);
            const prosperityError = !isValidAdjustment(
              fill.prosperity_adjustment,
            );
            const distressError = !isValidAdjustment(
              fill.distress_adjustment,
            );

            return (
              <div className="space-y-4">
                <div className="flex items-center gap-3">
                  <h2 className="text-lg font-bold">{cl.name}</h2>
                  <span className="font-mono text-sm text-zinc-400">
                    {cl.ts_code}
                  </span>
                  <span className="bg-zinc-700 px-2 py-0.5 rounded text-xs font-medium">
                    #{cl.rank}
                  </span>
                </div>

                <div className="flex gap-4 text-sm">
                  <span className="text-zinc-400">
                    综合:{" "}
                    <span className="text-zinc-200">
                      {(cl.scores.final ?? 0).toFixed(1)}
                    </span>
                  </span>
                  <span className="text-zinc-400">
                    景气度:{" "}
                    <span className="text-zinc-200">
                      {(cl.scores.prosperity ?? 0).toFixed(1)}
                    </span>
                  </span>
                  <span className="text-zinc-400">
                    困境:{" "}
                    <span className="text-zinc-200">
                      {(cl.scores.distress ?? 0).toFixed(1)}
                    </span>
                  </span>
                </div>

                <div className="space-y-3">
                  {cl.sections.map((section) => (
                    <div key={section.title}>
                      <label className="text-sm font-medium text-zinc-300">
                        {section.title}
                      </label>
                      <div className="mt-1 space-y-1">
                        {section.items.map((item) => (
                          <p
                            key={item}
                            className="text-xs text-zinc-500 pl-2"
                          >
                            • {item}
                          </p>
                        ))}
                      </div>
                      <textarea
                        className="mt-2 w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-zinc-300 resize-y"
                        rows={2}
                        placeholder="在此输入研究笔记..."
                      />
                    </div>
                  ))}
                </div>

                <div className="flex gap-6 pt-2">
                  <div className="space-y-1">
                    <label className="text-sm text-zinc-400">
                      景气度调整幅度
                    </label>
                    <input
                      type="number"
                      value={fill.prosperity_adjustment}
                      onChange={(e) =>
                        updateFill(
                          cl.ts_code,
                          "prosperity_adjustment",
                          Number(e.target.value),
                        )
                      }
                      className="bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm w-24"
                    />
                    {prosperityError && (
                      <p className="text-red-400 text-xs">范围: -20 到 +20</p>
                    )}
                  </div>
                  <div className="space-y-1">
                    <label className="text-sm text-zinc-400">
                      困境反转调整幅度
                    </label>
                    <input
                      type="number"
                      value={fill.distress_adjustment}
                      onChange={(e) =>
                        updateFill(
                          cl.ts_code,
                          "distress_adjustment",
                          Number(e.target.value),
                        )
                      }
                      className="bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm w-24"
                    />
                    {distressError && (
                      <p className="text-red-400 text-xs">范围: -20 到 +20</p>
                    )}
                  </div>
                </div>
              </div>
            );
          })()}

          <div className="flex items-center gap-4 pt-2">
            <button
              onClick={handleSaveAndRescore}
              disabled={loading}
              className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-4 py-1.5 rounded text-sm font-medium"
            >
              {loading ? "保存并重评中..." : "全部保存并重评"}
            </button>
            <button
              onClick={() => {
                setStep(1);
                setChecklists([]);
                setFillData({});
              }}
              className="text-sm text-zinc-500 hover:text-zinc-300"
            >
              返回
            </button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="space-y-4">
          <h2 className="text-lg font-bold">重评结果</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-700">
                <th className="text-left p-2 text-zinc-400">代码</th>
                <th className="text-left p-2 text-zinc-400">名称</th>
                <th className="text-right p-2 text-zinc-400">原景气度</th>
                <th className="text-right p-2 text-zinc-400">新景气度</th>
                <th className="text-right p-2 text-zinc-400">原困境</th>
                <th className="text-right p-2 text-zinc-400">新困境</th>
                <th className="text-right p-2 text-zinc-400">景气调整</th>
                <th className="text-right p-2 text-zinc-400">困境调整</th>
              </tr>
            </thead>
            <tbody>
              {rescoreResults.map((r) => (
                <tr
                  key={r.ts_code}
                  className="border-b border-zinc-800"
                >
                  <td className="p-2 font-mono">{r.ts_code}</td>
                  <td className="p-2">{r.name}</td>
                  <td className="p-2 text-right text-zinc-300">
                    {r.original_prosperity.toFixed(1)}
                  </td>
                  <td
                    className={`p-2 text-right font-medium ${
                      r.adjusted_prosperity > r.original_prosperity
                        ? "text-green-400"
                        : r.adjusted_prosperity < r.original_prosperity
                          ? "text-red-400"
                          : "text-zinc-300"
                    }`}
                  >
                    {r.adjusted_prosperity.toFixed(1)}
                  </td>
                  <td className="p-2 text-right text-zinc-300">
                    {r.original_distress.toFixed(1)}
                  </td>
                  <td
                    className={`p-2 text-right font-medium ${
                      r.adjusted_distress > r.original_distress
                        ? "text-green-400"
                        : r.adjusted_distress < r.original_distress
                          ? "text-red-400"
                          : "text-zinc-300"
                    }`}
                  >
                    {r.adjusted_distress.toFixed(1)}
                  </td>
                  <td className="p-2 text-right text-zinc-400">
                    {r.prosperity_adjustment > 0 ? "+" : ""}
                    {r.prosperity_adjustment.toFixed(1)}
                  </td>
                  <td className="p-2 text-right text-zinc-400">
                    {r.distress_adjustment > 0 ? "+" : ""}
                    {r.distress_adjustment.toFixed(1)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Link
            href="/screening"
            className="inline-block text-sm text-blue-400 hover:text-blue-300"
          >
            ← 返回筛选
          </Link>
        </div>
      )}
    </div>
  );
}

export default function ResearchPage() {
  return (
    <Suspense
      fallback={<div className="text-zinc-400 p-4">加载中...</div>}
    >
      <ResearchPageInner />
    </Suspense>
  );
}
