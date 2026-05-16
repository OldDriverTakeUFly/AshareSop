"use client";

import { useState, useEffect, useMemo } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { format, parse } from "date-fns";
import { CalendarIcon } from "lucide-react";

import { setCredentials } from "@/lib/api";
import { useLimitUp, useDragonTiger, useFundFlow, useRiskAlert, useAvailableDates } from "@/lib/hooks";
import type {
  LimitUpResponse,
  DragonTigerResponse,
  FundFlowResponse,
  RiskAlertResponse,
  SectorCorrelation,
} from "@/lib/types";

import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  CardDescription,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ErrorState } from "@/components/ErrorState";

setCredentials({ username: "stockhot", password: "stockhot" });

// ---------------------------------------------------------------------------
// QueryClient singleton
// ---------------------------------------------------------------------------

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5 * 60 * 1000, refetchOnWindowFocus: false },
  },
});

// ---------------------------------------------------------------------------
// Date helper
// ---------------------------------------------------------------------------

function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function yesterdayStr(): string {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// Delta indicator helpers
// ---------------------------------------------------------------------------

type DeltaDirection = "up" | "down" | "flat";

function computeDelta(a: number, b: number): { direction: DeltaDirection; delta: number } {
  const diff = b - a;
  if (Math.abs(diff) < 0.005) return { direction: "flat", delta: 0 };
  return { direction: diff > 0 ? "up" : "down", delta: Math.abs(diff) };
}

function DeltaIndicator({ a, b, invert = false, unit = "" }: { a: number; b: number; invert?: boolean; unit?: string }) {
  const { direction, delta } = computeDelta(a, b);
  // invert: true means lower is better (e.g. risk signals, broken count)
  const effectiveDir: DeltaDirection =
    direction === "flat" ? "flat" : invert ? (direction === "up" ? "down" : "up") : direction;

  if (effectiveDir === "flat") {
    return (
      <span className="inline-flex items-center gap-1 text-sm text-muted-foreground">
        <span className="text-xs">→</span> 持平
      </span>
    );
  }

  const color = effectiveDir === "up" ? "text-red-500" : "text-emerald-500";
  const arrow = effectiveDir === "up" ? "↑" : "↓";
  const sign = direction === "up" ? "+" : "-";

  return (
    <span className={`inline-flex items-center gap-1 text-sm font-medium ${color}`}>
      {arrow}{sign}{delta.toFixed(delta >= 1 ? 0 : 2)}{unit}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Inline DatePicker
// ---------------------------------------------------------------------------

function DatePicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const selected = useMemo(() => {
    const d = parse(value, "yyyy-MM-dd", new Date());
    return isNaN(d.getTime()) ? undefined : d;
  }, [value]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          className="w-full justify-start gap-2 font-mono"
        >
          <CalendarIcon className="size-4 shrink-0" />
          {value || "YYYY-MM-DD"}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="single"
          selected={selected}
          onSelect={(d) => {
            if (d) {
              onChange(format(d, "yyyy-MM-dd"));
              setOpen(false);
            }
          }}
        />
      </PopoverContent>
    </Popover>
  );
}

// ---------------------------------------------------------------------------
// Extractors — pull summary metrics from each module's response
// ---------------------------------------------------------------------------

interface LimitUpMetrics {
  poolCount: number;
  brokenCount: number;
  topSectors: SectorCorrelation[];
}

function extractLimitUpMetrics(data: LimitUpResponse | undefined): LimitUpMetrics {
  if (!data) return { poolCount: 0, brokenCount: 0, topSectors: [] };
  const topSectors = [...(data.analysis?.sector_correlation ?? [])]
    .sort((a, b) => b.count - a.count)
    .slice(0, 3);
  return {
    poolCount: data.limit_up_pool?.length ?? 0,
    brokenCount: data.broken_pool?.length ?? 0,
    topSectors,
  };
}

interface DragonTigerMetrics {
  detailCount: number;
  netBuyTotal: number;
}

function extractDragonTigerMetrics(data: DragonTigerResponse | undefined): DragonTigerMetrics {
  if (!data) return { detailCount: 0, netBuyTotal: 0 };
  const netBuyTotal = data.detail.reduce((sum, d) => sum + d.net_buy_amount, 0);
  return { detailCount: data.detail?.length ?? 0, netBuyTotal };
}

interface FundFlowMetrics {
  mainNet: number;
  direction: string;
  momentum: string;
}

function extractFundFlowMetrics(data: FundFlowResponse | undefined): FundFlowMetrics {
  if (!data || !data.trend) return { mainNet: 0, direction: "—", momentum: "—" };
  return {
    mainNet: data.trend.avg_main_net,
    direction: data.trend.direction,
    momentum: data.trend.momentum,
  };
}

interface RiskMetrics {
  totalSignals: number;
}

function extractRiskMetrics(data: RiskAlertResponse | undefined): RiskMetrics {
  if (!data?.data) return { totalSignals: 0 };
  const rd = data.data;
  return {
    totalSignals:
      rd.st_stocks.length +
      rd.abnormal_volatility.length +
      rd.capital_flight.length +
      rd.high_position_risks.length,
  };
}

// ---------------------------------------------------------------------------
// Inner page content
// ---------------------------------------------------------------------------

function CompareContent() {
  const { data: datesData } = useAvailableDates();
  const [dateA, setDateA] = useState(yesterdayStr);
  const [dateB, setDateB] = useState(todayStr);

  useEffect(() => {
    if (datesData?.dates?.length) {
      const d = datesData.dates;
      setDateB(d[0]);
      setDateA(d.length > 1 ? d[1] : d[0]);
    }
  }, [datesData?.dates]);

  const limitUpA = useLimitUp(dateA);
  const limitUpB = useLimitUp(dateB);
  const dragonA = useDragonTiger(dateA);
  const dragonB = useDragonTiger(dateB);
  const fundA = useFundFlow(dateA);
  const fundB = useFundFlow(dateB);
  const riskA = useRiskAlert(dateA);
  const riskB = useRiskAlert(dateB);

  const isLoading =
    limitUpA.isLoading || limitUpB.isLoading ||
    dragonA.isLoading || dragonB.isLoading ||
    fundA.isLoading || fundB.isLoading ||
    riskA.isLoading || riskB.isLoading;

  const hasError =
    limitUpA.isError || limitUpB.isError ||
    dragonA.isError || dragonB.isError ||
    fundA.isError || fundB.isError ||
    riskA.isError || riskB.isError;

  const refetchAll = () => {
    limitUpA.refetch();
    limitUpB.refetch();
    dragonA.refetch();
    dragonB.refetch();
    fundA.refetch();
    fundB.refetch();
    riskA.refetch();
    riskB.refetch();
  };

  const metricsA = useMemo(() => ({
    limitUp: extractLimitUpMetrics(limitUpA.data),
    dragon: extractDragonTigerMetrics(dragonA.data),
    fund: extractFundFlowMetrics(fundA.data),
    risk: extractRiskMetrics(riskA.data),
  }), [limitUpA.data, dragonA.data, fundA.data, riskA.data]);

  const metricsB = useMemo(() => ({
    limitUp: extractLimitUpMetrics(limitUpB.data),
    dragon: extractDragonTigerMetrics(dragonB.data),
    fund: extractFundFlowMetrics(fundB.data),
    risk: extractRiskMetrics(riskB.data),
  }), [limitUpB.data, dragonB.data, fundB.data, riskB.data]);

  // ---------------------------------------------------------------------------
  // Loading
  // ---------------------------------------------------------------------------

  if (isLoading) {
    return (
      <div className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
        <Skeleton className="h-8 w-48" />
        <div className="grid gap-4 sm:grid-cols-2">
          <Skeleton className="h-10 rounded-lg" />
          <Skeleton className="h-10 rounded-lg" />
        </div>
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-48 rounded-xl" />
        ))}
      </div>
    );
  }

  if (hasError) {
    return (
      <div className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">历史对比</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            选择两个日期，对比关键指标变化
          </p>
        </div>
        <ErrorState onRetry={refetchAll} />
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Comparison card component
  // ---------------------------------------------------------------------------

  function ComparisonRow({ label, valueA, valueB, invert, unit }: {
    label: string;
    valueA: number;
    valueB: number;
    invert?: boolean;
    unit?: string;
  }) {
    return (
      <div className="flex items-center justify-between gap-3 py-2 border-b border-border/50 last:border-b-0">
        <span className="text-sm text-muted-foreground shrink-0">{label}</span>
        <div className="flex items-center gap-2 text-sm">
          <span className="font-mono tabular-nums text-muted-foreground">{valueA.toFixed(valueA % 1 === 0 ? 0 : 2)}{unit}</span>
          <span className="text-muted-foreground/60">→</span>
          <span className="font-mono tabular-nums font-medium">{valueB.toFixed(valueB % 1 === 0 ? 0 : 2)}{unit}</span>
          <DeltaIndicator a={valueA} b={valueB} invert={invert} unit={unit} />
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">历史对比</h1>
        <div className="mt-1 flex items-center gap-3">
          <p className="text-sm text-muted-foreground">
            选择两个日期，对比关键指标变化
          </p>
          <span className="text-xs text-muted-foreground/60">
            数据更新于 {new Date().toLocaleString("zh-CN")}
          </span>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">日期 A（基准）</CardTitle>
          </CardHeader>
          <CardContent>
            <DatePicker value={dateA} onChange={setDateA} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">日期 B（对比）</CardTitle>
          </CardHeader>
          <CardContent>
            <DatePicker value={dateB} onChange={setDateB} />
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>涨停板</CardTitle>
            <CardDescription>涨停池、炸板数、热门板块</CardDescription>
          </CardHeader>
          <CardContent>
            <ComparisonRow
              label="涨停池"
              valueA={metricsA.limitUp.poolCount}
              valueB={metricsB.limitUp.poolCount}
              unit="只"
            />
            <ComparisonRow
              label="炸板数"
              valueA={metricsA.limitUp.brokenCount}
              valueB={metricsB.limitUp.brokenCount}
              invert
              unit="只"
            />
            <div className="pt-3 mt-1">
              <p className="text-xs text-muted-foreground mb-2">日期 A 热门板块</p>
              <div className="flex flex-wrap gap-1.5">
                {metricsA.limitUp.topSectors.length === 0 ? (
                  <span className="text-xs text-muted-foreground">暂无</span>
                ) : (
                  metricsA.limitUp.topSectors.map((s) => (
                    <Badge key={s.name} variant="secondary" className="text-xs">
                      {s.name} ({s.count})
                    </Badge>
                  ))
                )}
              </div>
              <p className="text-xs text-muted-foreground mb-2 mt-3">日期 B 热门板块</p>
              <div className="flex flex-wrap gap-1.5">
                {metricsB.limitUp.topSectors.length === 0 ? (
                  <span className="text-xs text-muted-foreground">暂无</span>
                ) : (
                  metricsB.limitUp.topSectors.map((s) => (
                    <Badge key={s.name} variant="secondary" className="text-xs">
                      {s.name} ({s.count})
                    </Badge>
                  ))
                )}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>龙虎榜</CardTitle>
            <CardDescription>上榜详情数、净买入总额</CardDescription>
          </CardHeader>
          <CardContent>
            <ComparisonRow
              label="上榜数"
              valueA={metricsA.dragon.detailCount}
              valueB={metricsB.dragon.detailCount}
              unit="只"
            />
            <ComparisonRow
              label="净买入额"
              valueA={metricsA.dragon.netBuyTotal}
              valueB={metricsB.dragon.netBuyTotal}
              unit="亿"
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>资金流向</CardTitle>
            <CardDescription>主力净流入、趋势方向、动量</CardDescription>
          </CardHeader>
          <CardContent>
            <ComparisonRow
              label="主力均净流入"
              valueA={metricsA.fund.mainNet}
              valueB={metricsB.fund.mainNet}
              unit="亿"
            />
            <div className="flex items-center justify-between gap-3 py-2 border-b border-border/50">
              <span className="text-sm text-muted-foreground shrink-0">趋势方向</span>
              <div className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground">{metricsA.fund.direction}</span>
                <span className="text-muted-foreground/60">→</span>
                <span className="font-medium">{metricsB.fund.direction}</span>
              </div>
            </div>
            <div className="flex items-center justify-between gap-3 py-2 border-b border-border/50 last:border-b-0">
              <span className="text-sm text-muted-foreground shrink-0">动量</span>
              <div className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground">{metricsA.fund.momentum}</span>
                <span className="text-muted-foreground/60">→</span>
                <span className="font-medium">{metricsB.fund.momentum}</span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>风险提示</CardTitle>
            <CardDescription>各维度风险信号总数</CardDescription>
          </CardHeader>
          <CardContent>
            <ComparisonRow
              label="风险信号总数"
              valueA={metricsA.risk.totalSignals}
              valueB={metricsB.risk.totalSignals}
              invert
              unit="项"
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page export with QueryClientProvider wrapper
// ---------------------------------------------------------------------------

export default function ComparePage() {
  return (
    <QueryClientProvider client={queryClient}>
      <CompareContent />
    </QueryClientProvider>
  );
}
