"use client";

import { useMemo, useEffect, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { setCredentials } from "@/lib/api";
import { useLimitUp } from "@/lib/hooks";
import type {
  LimitUpStock,
  SectorCorrelation,
  SealStrength,
} from "@/lib/types";

import {
  Card,
  CardHeader,
  CardContent,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ConsecutiveBoardChart } from "@/components/charts/ConsecutiveBoardChart";
import { StockTable, type ColumnDef } from "@/components/tables/StockTable";
import { ErrorState } from "@/components/ErrorState";
import { EmptyState } from "@/components/EmptyState";

// ---------------------------------------------------------------------------
// QueryClient singleton for this page
// ---------------------------------------------------------------------------

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5 * 60 * 1000, refetchOnWindowFocus: false },
  },
});

// ---------------------------------------------------------------------------
// Today's date helper (YYYY-MM-DD)
// ---------------------------------------------------------------------------

function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function formatAmount(val: number): string {
  return `${val.toFixed(2)}亿`;
}

function changeColor(val: number): string {
  if (val > 0) return "text-red-500";
  if (val < 0) return "text-emerald-500";
  return "text-muted-foreground";
}

function formatChange(val: number): ReactNode {
  const sign = val > 0 ? "+" : "";
  return <span className={changeColor(val)}>{sign}{val.toFixed(2)}%</span>;
}

// ---------------------------------------------------------------------------
// Column definitions for the limit-up pool table
// ---------------------------------------------------------------------------

const limitUpColumns: ColumnDef<LimitUpStock>[] = [
  {
    key: "code",
    label: "代码",
    mono: true,
  },
  {
    key: "name",
    label: "名称",
  },
  {
    key: "change_pct",
    label: "涨跌幅",
    render: (val) => formatChange(val as number),
    sortable: true,
    numeric: true,
  },
  {
    key: "seal_amount",
    label: "封单额",
    render: (val) => (
      <span className="text-red-500">{formatAmount(val as number)}</span>
    ),
    sortable: true,
    numeric: true,
  },
  {
    key: "consecutive_boards",
    label: "连板数",
    sortable: true,
    numeric: true,
  },
  {
    key: "sector",
    label: "所属板块",
  },
  {
    key: "broken_count",
    label: "开板次数",
    numeric: true,
  },
];

// ---------------------------------------------------------------------------
// Inner page (needs QueryClient context)
// ---------------------------------------------------------------------------

function LimitUpContent() {
  const date = todayStr();

  useEffect(() => {
    setCredentials({ username: "stockhot", password: "stockhot" });
  }, []);

  const { data, isLoading, error, refetch } = useLimitUp(date);

  const limitUpCount = data?.limit_up_pool?.length ?? 0;
  const brokenCount = data?.broken_pool?.length ?? 0;
  const limitDownCount = data?.limit_down_pool?.length ?? 0;

  const topSectors = useMemo<SectorCorrelation[]>(() => {
    if (!data?.analysis?.sector_correlation) return [];
    return [...data.analysis.sector_correlation].sort(
      (a, b) => b.count - a.count
    );
  }, [data?.analysis?.sector_correlation]);

  const topSealStrength = useMemo<SealStrength[]>(() => {
    if (!data?.analysis?.seal_strength_ranking) return [];
    return data.analysis.seal_strength_ranking.slice(0, 10);
  }, [data?.analysis?.seal_strength_ranking]);

  if (isLoading) {
    return (
      <div className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-4 w-32" />
        <div className="grid gap-4 sm:grid-cols-3">
          <Skeleton className="h-28 rounded-xl" />
          <Skeleton className="h-28 rounded-xl" />
          <Skeleton className="h-28 rounded-xl" />
        </div>
        <Skeleton className="h-[300px] rounded-xl" />
        <Skeleton className="h-64 rounded-xl" />
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-48 rounded-xl" />
          <Skeleton className="h-48 rounded-xl" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <ErrorState onRetry={() => refetch()} error={error} />
      </div>
    );
  }

  if (!data || (data.limit_up_pool.length === 0 && data.broken_pool.length === 0 && data.limit_down_pool.length === 0)) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <h1 className="text-2xl font-bold tracking-tight">涨停板分析</h1>
        <p className="mt-1 text-sm text-muted-foreground">{date}</p>
        <EmptyState
          className="mt-12"
          message="暂无数据"
          description="当前日期没有涨停数据，请尝试切换日期"
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">涨停板分析</h1>
        <div className="mt-1 flex items-center gap-3">
          <p className="text-sm text-muted-foreground">{date}</p>
          {data && (
            <span className="text-xs text-muted-foreground/60">
              数据更新于 {new Date().toLocaleString("zh-CN")}
            </span>
          )}
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Card className="border-red-200 dark:border-red-900/40">
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">
              涨停
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-bold text-red-500">{limitUpCount}</p>
            <p className="mt-1 text-xs text-muted-foreground">只</p>
          </CardContent>
        </Card>

        <Card className="border-amber-200 dark:border-amber-900/40">
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">
              炸板
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-bold text-amber-500">{brokenCount}</p>
            <p className="mt-1 text-xs text-muted-foreground">只</p>
          </CardContent>
        </Card>

        <Card className="border-emerald-200 dark:border-emerald-900/40">
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">
              跌停
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-bold text-emerald-500">{limitDownCount}</p>
            <p className="mt-1 text-xs text-muted-foreground">只</p>
          </CardContent>
        </Card>
      </div>

      {data.analysis?.consecutive_boards &&
        data.analysis.consecutive_boards.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>连板分布</CardTitle>
            </CardHeader>
            <CardContent>
              <ConsecutiveBoardChart data={data.analysis.consecutive_boards} />
            </CardContent>
          </Card>
        )}

      <Card>
        <CardHeader>
          <CardTitle>涨停池</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <StockTable
            data={data.limit_up_pool as unknown as Record<string, unknown>[]}
            columns={limitUpColumns as unknown as ColumnDef<Record<string, unknown>>[]}
            emptyMessage="暂无涨停个股"
          />
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>板块涨停分布</CardTitle>
          </CardHeader>
          <CardContent>
            {topSectors.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                暂无数据
              </p>
            ) : (
              <ul className="space-y-3">
                {topSectors.map((sector) => (
                  <li
                    key={sector.name}
                    className="flex items-center justify-between gap-2"
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <Badge variant="secondary">{sector.count}</Badge>
                      <span className="truncate text-sm font-medium">
                        {sector.name}
                      </span>
                    </div>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {sector.stocks.slice(0, 3).join("、")}
                      {sector.stocks.length > 3 && "…"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>封单强度排行 TOP10</CardTitle>
          </CardHeader>
          <CardContent>
            {topSealStrength.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                暂无数据
              </p>
            ) : (
              <ul className="space-y-3">
                {topSealStrength.map((item, idx) => (
                  <li
                    key={item.code}
                    className="flex items-center justify-between gap-2"
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span
                        className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                          idx < 3
                            ? "bg-red-500 text-white"
                            : "bg-muted text-muted-foreground"
                        }`}
                      >
                        {idx + 1}
                      </span>
                      <span className="font-mono text-xs text-muted-foreground">
                        {item.code}
                      </span>
                      <span className="truncate text-sm font-medium">
                        {item.name}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 shrink-0">
                      <span className="text-sm text-red-500">
                        {formatAmount(item.seal_amount)}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        开板{item.broken_count}次
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page export with QueryClientProvider wrapper
// ---------------------------------------------------------------------------

export default function LimitUpPage() {
  return (
    <QueryClientProvider client={queryClient}>
      <LimitUpContent />
    </QueryClientProvider>
  );
}
