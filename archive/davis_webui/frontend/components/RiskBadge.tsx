"use client";

const SEVERITY_MAP: Record<string, "yellow" | "red"> = {
  "增速放缓": "yellow",
  "趋势下行": "yellow",
  "增速不足": "red",
  "景气持续性存疑": "red",
};

export function RiskBadge({ warnings }: { warnings: string[] }) {
  if (!warnings || warnings.length === 0) {
    return <span className="text-green-400 text-xs">✓ 无风险</span>;
  }
  const visible = warnings.slice(0, 3);
  const overflow = warnings.length - 3;
  return (
    <div className="flex flex-wrap gap-1">
      {visible.map((w) => {
        const severity = SEVERITY_MAP[w] ?? "red";
        const cls =
          severity === "yellow"
            ? "bg-yellow-900/50 text-yellow-400 border border-yellow-700"
            : "bg-red-900/50 text-red-400 border border-red-700";
        return (
          <span
            key={w}
            className={`${cls} px-1.5 py-0.5 rounded text-xs whitespace-nowrap`}
          >
            {w}
          </span>
        );
      })}
      {overflow > 0 && (
        <span className="text-zinc-500 text-xs">+{overflow}</span>
      )}
    </div>
  );
}
