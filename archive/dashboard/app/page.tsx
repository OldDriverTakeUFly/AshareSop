"use client";

import Link from "next/link";
import { useHealth, useLimitUp, useDragonTiger, useFundFlow, useRiskAlert } from "@/lib/hooks";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

function formatAmount(value: number): string {
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(2)}万`;
  return value.toFixed(2);
}

export default function HomePage() {
  const { data: health, isLoading: healthLoading } = useHealth();

  const latestDate =
    health?.latest_dates?.limit_up_pool ??
    health?.latest_dates?.dragon_tiger_detail ??
    "";

  const limitUpQuery = useLimitUp(latestDate);
  const dragonTigerQuery = useDragonTiger(latestDate);
  const fundFlowQuery = useFundFlow(latestDate);
  const riskAlertQuery = useRiskAlert(latestDate);

  const isDataLoading =
    healthLoading ||
    (!latestDate && healthLoading) ||
    (latestDate && (
      limitUpQuery.isLoading ||
      dragonTigerQuery.isLoading ||
      fundFlowQuery.isLoading ||
      riskAlertQuery.isLoading
    ));

  const hasDataError =
    (latestDate && (
      limitUpQuery.isError ||
      dragonTigerQuery.isError ||
      fundFlowQuery.isError ||
      riskAlertQuery.isError
    ));

  const limitUpCount = limitUpQuery.data?.limit_up_pool?.length;
  const netBuy = dragonTigerQuery.data?.detail?.reduce(
    (sum, d) => sum + d.net_buy_amount,
    0
  );
  const mainNet = fundFlowQuery.data?.market_flow?.[0]?.main_net;
  const riskCount =
    (riskAlertQuery.data?.data?.st_stocks?.length ?? 0) +
    (riskAlertQuery.data?.data?.suspended_stocks?.length ?? 0) +
    (riskAlertQuery.data?.data?.abnormal_volatility?.length ?? 0) +
    (riskAlertQuery.data?.data?.capital_flight?.length ?? 0) +
    (riskAlertQuery.data?.data?.high_position_risks?.length ?? 0);

  const cards = [
    {
      title: "涨停",
      value: limitUpCount != null ? `${limitUpCount} 只` : "—",
      href: "/limit-up",
      accent: "text-destructive",
    },
    {
      title: "龙虎榜净买",
      value: netBuy != null ? formatAmount(netBuy) : "—",
      href: "/dragon-tiger",
      accent: netBuy != null && netBuy >= 0 ? "text-green-600" : "text-destructive",
    },
    {
      title: "资金流向",
      value: mainNet != null ? formatAmount(mainNet) : "—",
      href: "/fund-flow",
      accent: mainNet != null && mainNet >= 0 ? "text-green-600" : "text-destructive",
    },
    {
      title: "风险信号",
      value: riskCount > 0 ? `${riskCount} 项` : "—",
      href: "/risk-alert",
      accent: "text-amber-600",
    },
  ];

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight">市场概览</h1>
        <div className="mt-1 flex items-center gap-3">
          {latestDate && (
            <p className="text-sm text-muted-foreground">{latestDate}</p>
          )}
          {latestDate && !isDataLoading && (
            <span className="text-xs text-muted-foreground/60">
              数据更新于 {new Date().toLocaleString("zh-CN")}
            </span>
          )}
        </div>
      </div>

      {isDataLoading && (
        <div className="grid grid-cols-2 gap-4">
          {Array.from({ length: 4 }, (_, i) => (
            <Card key={i}>
              <CardHeader>
                <Skeleton className="h-4 w-16" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-8 w-20" />
              </CardContent>
              <CardFooter>
                <Skeleton className="h-3 w-16" />
              </CardFooter>
            </Card>
          ))}
        </div>
      )}

      {!isDataLoading && hasDataError && (
        <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-6 text-center">
          <p className="text-lg font-medium text-destructive">数据加载失败</p>
          <p className="mt-1 text-sm text-muted-foreground">请稍后刷新页面重试</p>
        </div>
      )}

      {!isDataLoading && !hasDataError && !latestDate && (
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <p className="text-lg text-muted-foreground">暂无数据</p>
          <p className="mt-1 text-sm text-muted-foreground/70">非交易日或数据未更新</p>
        </div>
      )}

      {!isDataLoading && !hasDataError && latestDate && (
        <div className="grid grid-cols-2 gap-4">
          {cards.map((card) => (
            <Link key={card.href} href={card.href} className="group">
              <Card className="transition-shadow group-hover:shadow-md h-full">
                <CardHeader>
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    {card.title}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <span className={`text-2xl font-bold ${card.accent}`}>
                    {card.value}
                  </span>
                </CardContent>
                <CardFooter>
                  <span className="text-xs text-muted-foreground group-hover:text-foreground transition-colors">
                    查看详情 →
                  </span>
                </CardFooter>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
