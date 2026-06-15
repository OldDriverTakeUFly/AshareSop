"use client";
import { StageBadge } from "@/components/StageBadge";
import type { InflectionAnalysis } from "@/lib/types";

const SIGNAL_LABELS: Record<string, string> = {
  roe_improving: "ROE改善",
  cashflow_positive: "现金流回正",
  debt_declining: "负债下降",
  revenue_stabilizing: "营收企稳",
  revenue_declining: "营收下滑",
  profit_deteriorating: "盈利恶化",
  debt_rising: "负债攀升",
  cashflow_deteriorating: "现金流恶化",
};

const STAGE_ACCENT: Record<string, string> = {
  加速期: "border-green-800",
  减速期: "border-yellow-800",
  上升拐点: "border-blue-800",
  下降拐点: "border-red-800",
  拐点期: "border-red-800",
};

function getAccentClass(stage: string): string {
  return STAGE_ACCENT[stage] ?? "border-zinc-700";
}

export function InflectionAnalysisDisplay({
  inflection,
}: {
  inflection: InflectionAnalysis;
}) {
  const accentBorder = getAccentClass(inflection.stage);

  return (
    <div
      className={`bg-zinc-900 p-4 rounded-lg border-l-2 ${accentBorder} space-y-4`}
    >
      <div className="flex items-center gap-3">
        <h3 className="text-lg font-semibold">景气拐点分析</h3>
        <StageBadge stage={inflection.stage} />
      </div>

      <div>
        <p className="text-zinc-400 text-sm">拐点季度</p>
        <p className="text-2xl font-bold font-mono">
          {inflection.inflection_quarter ?? "未检测到明确拐点"}
        </p>
      </div>

      {inflection.primary_driver && (
        <div className="bg-zinc-800 p-3 rounded">
          <p className="text-zinc-400 text-sm">主要驱动因素</p>
          <p className="text-zinc-200 font-medium">
            {inflection.primary_driver}
          </p>
        </div>
      )}

      {inflection.catalysts.length > 0 && (
        <div>
          <p className="text-zinc-400 text-sm mb-2">
            {inflection.stage === "上升拐点" || inflection.stage === "加速期"
              ? "催化信号"
              : "风险信号"}
          </p>
          <div className="grid grid-cols-2 gap-2">
            {inflection.catalysts.map((catalyst) => {
              const label =
                SIGNAL_LABELS[catalyst.signal_type] ?? catalyst.signal_type;
              return (
                <div
                  key={catalyst.signal_type}
                  className="bg-zinc-800 p-3 rounded"
                >
                  <p className="text-zinc-200 font-semibold text-sm">
                    {label}
                  </p>
                  <p className="text-zinc-400 text-sm mt-0.5">
                    {catalyst.description}
                  </p>
                  <div className="w-full bg-zinc-700 rounded-full h-1.5 mt-1">
                    <div
                      className="bg-blue-500 h-1.5 rounded-full"
                      style={{ width: `${catalyst.strength}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {inflection.narrative && (
        <div className="bg-zinc-800/50 p-3 rounded">
          <p className="text-zinc-300 italic text-sm leading-relaxed">
            {inflection.narrative}
          </p>
        </div>
      )}
    </div>
  );
}
