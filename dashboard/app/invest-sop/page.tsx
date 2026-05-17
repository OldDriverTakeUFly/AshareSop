"use client";

import { useInvestOverview, useInvestHoldings } from "@/lib/hooks";

interface CardItem {
  title: string;
  value: string;
  accent: string;
}

function buildCards(
  overseas: Record<string, number | null> | null,
  holdingsCount: number,
): CardItem[] {
  if (!overseas) {
    return [{ title: "持仓数", value: `${holdingsCount}`, accent: "text-foreground" }];
  }

  const pctColor = (v: number | null) =>
    v == null
      ? "text-muted-foreground"
      : v >= 0
        ? "text-red-600 dark:text-red-400"
        : "text-emerald-600 dark:text-emerald-400";

  const fmtPct = (v: number | null) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`);
  const fmtVal = (v: number | null, suffix = "") =>
    v == null ? "—" : `${v.toFixed(4)}${suffix}`;

  return [
    { title: "S&P 500", value: fmtPct(overseas.sp500_pct), accent: pctColor(overseas.sp500_pct) },
    { title: "纳斯达克", value: fmtPct(overseas.nasdaq_pct), accent: pctColor(overseas.nasdaq_pct) },
    { title: "道琼斯", value: fmtPct(overseas.dow_pct), accent: pctColor(overseas.dow_pct) },
    {
      title: "美债10Y",
      value: overseas.us_10y == null ? "—" : `${overseas.us_10y.toFixed(2)}%`,
      accent: overseas.us_10y_change_bp != null && overseas.us_10y_change_bp >= 0
        ? "text-red-600 dark:text-red-400"
        : "text-emerald-600 dark:text-emerald-400",
    },
    {
      title: "QVIX",
      value: overseas.vix == null ? "—" : String(overseas.vix.toFixed(2)),
      accent: "text-foreground",
    },
    {
      title: "US VIX",
      value: overseas.us_vix == null ? "—" : String(overseas.us_vix.toFixed(2)),
      accent: "text-foreground",
    },
    { title: "A50", value: fmtPct(overseas.a50_pct), accent: pctColor(overseas.a50_pct) },
    {
      title: "USD/CNY",
      value: fmtVal(overseas.usd_cny),
      accent: "text-foreground",
    },
    { title: "持仓数", value: `${holdingsCount}`, accent: "text-foreground" },
  ];
}

function InvestSopContent() {
  const today = new Date().toISOString().slice(0, 10);

  const { data: overview, isLoading: overviewLoading } = useInvestOverview(today);
  const { data: holdings, isLoading: holdingsLoading } = useInvestHoldings();

  const isLoading = overviewLoading || holdingsLoading;
  const overseas = overview?.overseas ?? null;
  const holdingsCount = holdings?.length ?? 0;

  const cards = buildCards(
    overseas as Record<string, number | null> | null,
    holdingsCount,
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold tracking-tight">投资 SOP 概览</h1>
        {overview?.date && (
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">{overview.date}</span>
            <span className="text-xs text-muted-foreground/60">
              数据更新于 {new Date().toLocaleString("zh-CN")}
            </span>
          </div>
        )}
      </div>

      {isLoading && (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: 9 }, (_, i) => (
            <div key={i} className="animate-pulse rounded-lg border p-4">
              <div className="mb-2 h-3 w-16 rounded bg-muted" />
              <div className="h-7 w-20 rounded bg-muted" />
            </div>
          ))}
        </div>
      )}

      {!isLoading && !overseas && holdingsCount === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <p className="text-lg text-muted-foreground">暂无数据</p>
          <p className="mt-1 text-sm text-muted-foreground/70">非交易日或数据未更新</p>
        </div>
      )}

      {!isLoading && (overseas || holdingsCount > 0) && (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4">
          {cards.map((card) => (
            <div key={card.title} className="rounded-lg border p-4">
              <p className="text-xs font-medium text-muted-foreground">{card.title}</p>
              <p className={`mt-1 text-2xl font-bold tabular-nums ${card.accent}`}>
                {card.value}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function InvestSopPage() {
  return <InvestSopContent />;
}
