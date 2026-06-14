"use client";

export function StageBadge({ stage }: { stage: string }) {
  if (stage === "加速期") {
    return (
      <span className="bg-green-900/50 text-green-400 border border-green-700 px-2 py-0.5 rounded text-xs">
        加速期
      </span>
    );
  }
  if (stage === "减速期") {
    return (
      <span className="bg-yellow-900/50 text-yellow-400 border border-yellow-700 px-2 py-0.5 rounded text-xs">
        减速期
      </span>
    );
  }
  if (stage === "上升拐点") {
    return (
      <span className="bg-blue-900/50 text-blue-400 border border-blue-700 px-2 py-0.5 rounded text-xs">
        上升拐点
      </span>
    );
  }
  if (stage === "下降拐点") {
    return (
      <span className="bg-red-900/50 text-red-400 border border-red-700 px-2 py-0.5 rounded text-xs">
        下降拐点
      </span>
    );
  }
  if (stage === "拐点期") {
    return (
      <span className="bg-red-900/50 text-red-400 border border-red-700 px-2 py-0.5 rounded text-xs">
        拐点期
      </span>
    );
  }
  return (
    <span className="bg-zinc-800 text-zinc-400 px-2 py-0.5 rounded text-xs">
      {stage}
    </span>
  );
}
