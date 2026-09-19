"use client";

import { useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setCredentials } from "@/lib/api";
import { useFundFlow, useAvailableDates } from "@/lib/hooks";
import { FundFlowAreaChart } from "@/components/charts/FundFlowAreaChart";
import { SectorBarChart } from "@/components/charts/SectorBarChart";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { EmptyState } from "@/components/EmptyState";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5 * 60 * 1000, refetchOnWindowFocus: false },
  },
});

function directionColor(direction: string): string {
  if (direction === "持续流入") return "bg-red-500/15 text-red-600 border-red-500/25 dark:text-red-400";
  if (direction === "持续流出") return "bg-emerald-500/15 text-emerald-600 border-emerald-500/25 dark:text-emerald-400";
  return "bg-muted text-muted-foreground border-border";
}

function momentumColor(momentum: string): string {
  if (momentum === "增强") return "bg-red-500/15 text-red-600 border-red-500/25 dark:text-red-400";
  if (momentum === "减弱") return "bg-emerald-500/15 text-emerald-600 border-emerald-500/25 dark:text-emerald-400";
  return "bg-muted text-muted-foreground border-border";
}

function FundFlowContent() {
  const [date, setDate] = useState("");

  useEffect(() => {
    setCredentials({ username: "stockhot", password: "stockhot" });
  }, []);

  const { data: datesData } = useAvailableDates();
  const effectiveDate = date || datesData?.dates?.[0] || "";

  useEffect(() => {
    if (!date && datesData?.dates?.[0]) {
      setDate(datesData.dates[0]);
    }
  }, [date, datesData]);

  const { data, isLoading, isError, error, refetch } = useFundFlow(effectiveDate);

  const trend = data?.trend ?? null;
  const marketFlow = data?.market_flow ?? [];
  const sectorFlow = data?.sector_flow ?? [];

  if (isError) {
    return (
      <div className="mx-auto max-w-6xl space-y-6 p-6">
        <h1 className="text-2xl font-bold tracking-tight">资金流向分析</h1>
        <ErrorState onRetry={() => refetch()} error={error ?? undefined} />
      </div>
    );
  }

  const isEmpty = !isLoading && !trend && marketFlow.length === 0 && sectorFlow.length === 0;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold tracking-tight">资金流向分析</h1>
        {data?.date && (
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">{data.date}</span>
            <span className="text-xs text-muted-foreground/60">
              数据更新于 {new Date().toLocaleString("zh-CN")}
            </span>
          </div>
        )}
      </div>

      {isEmpty && (
        <EmptyState
          message="暂无数据"
          description="非交易日或数据未更新"
        />
      )}

      {!isEmpty && (
      <>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-muted-foreground text-xs font-medium uppercase tracking-wider">
              主力净流入
            </CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <Skeleton className="h-8 w-28" />
            ) : trend ? (
              <p
                className={`text-2xl font-bold tabular-nums ${
                  trend.avg_main_net >= 0
                    ? "text-red-600 dark:text-red-400"
                    : "text-emerald-600 dark:text-emerald-400"
                }`}
              >
                {trend.avg_main_net >= 0 ? "+" : ""}
                {trend.avg_main_net.toFixed(2)} 亿
              </p>
            ) : (
              <p className="text-muted-foreground">暂无数据</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-muted-foreground text-xs font-medium uppercase tracking-wider">
              趋势方向
            </CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <Skeleton className="h-6 w-24" />
            ) : trend ? (
              <span
                className={`inline-flex items-center rounded-full border px-3 py-1 text-sm font-medium ${directionColor(trend.direction)}`}
              >
                {trend.direction}
              </span>
            ) : (
              <p className="text-muted-foreground">暂无数据</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-muted-foreground text-xs font-medium uppercase tracking-wider">
              动量
            </CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <Skeleton className="h-6 w-24" />
            ) : trend ? (
              <span
                className={`inline-flex items-center rounded-full border px-3 py-1 text-sm font-medium ${momentumColor(trend.momentum)}`}
              >
                {trend.momentum}
              </span>
            ) : (
              <p className="text-muted-foreground">暂无数据</p>
            )}
          </CardContent>
        </Card>
      </div>

      {trend && (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs font-medium text-muted-foreground">
            趋势指标
          </span>
          <Badge className={directionColor(trend.direction)}>
            {trend.direction}
          </Badge>
          <Badge className={momentumColor(trend.momentum)}>
            {trend.momentum}
          </Badge>
          {trend.large_vs_retail_divergence && (
            <Badge className="border-amber-500/30 bg-amber-500/15 text-amber-600 dark:text-amber-400">
              ⚠ 主力与散户背离
            </Badge>
          )}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>大盘资金流向趋势</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-[360px] w-full" />
          ) : marketFlow.length > 0 ? (
            <FundFlowAreaChart data={marketFlow} />
          ) : (
            <div className="flex h-[360px] items-center justify-center text-muted-foreground">
              暂无数据
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>板块资金流向排名</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-[400px] w-full" />
          ) : sectorFlow.length > 0 ? (
            <SectorBarChart data={sectorFlow} />
          ) : (
            <EmptyState
              message="暂无板块排名数据"
              description="板块资金流向数据尚未采集，请等待数据更新"
            />
          )}
        </CardContent>
      </Card>
      </>
      )}
    </div>
  );
}

export default function FundFlowPage() {
  return (
    <QueryClientProvider client={queryClient}>
      <FundFlowContent />
    </QueryClientProvider>
  );
}
