"use client";

import { useMemo, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useDragonTiger } from "@/lib/hooks";
import { setCredentials } from "@/lib/api";
import { StockTable, type ColumnDef } from "@/components/tables/StockTable";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { EmptyState } from "@/components/EmptyState";
import type {
  LhbDetail,
  Institutional,
  Broker,
  HotMoney,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format number as 亿 with 2 decimal places. */
function fmtYi(value: number): string {
  return `${(value / 1e8).toFixed(2)} 亿`;
}

/** Format number with comma separation. */
function fmtNum(value: number): string {
  if (Math.abs(value) >= 1e8) return fmtYi(value);
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(2)} 万`;
  return value.toFixed(2);
}

/** Chinese market color: red = positive, green = negative. */
function marketColor(value: number): string {
  if (value > 0) return "text-red-500";
  if (value < 0) return "text-green-500";
  return "text-muted-foreground";
}

// ---------------------------------------------------------------------------
// Column definitions
// ---------------------------------------------------------------------------

const detailColumns: ColumnDef<LhbDetail>[] = [
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
    key: "reason",
    label: "上榜原因",
  },
  {
    key: "change_pct",
    label: "涨跌幅",
    numeric: true,
    sortable: true,
    render: (val) => {
      const v = val as number;
      return <span className={marketColor(v)}>{v > 0 ? "+" : ""}{v.toFixed(2)}%</span>;
    },
  },
  {
    key: "net_buy_amount",
    label: "净买额",
    numeric: true,
    sortable: true,
    render: (val) => {
      const v = val as number;
      return <span className={marketColor(v)}>{fmtNum(v)}</span>;
    },
  },
  {
    key: "buy_amount",
    label: "买入额",
    numeric: true,
    render: (val) => fmtNum(val as number),
  },
  {
    key: "sell_amount",
    label: "卖出额",
    numeric: true,
    render: (val) => fmtNum(val as number),
  },
];

const instColumns: ColumnDef<Institutional>[] = [
  {
    key: "inst_code",
    label: "机构代码",
    mono: true,
  },
  {
    key: "inst_name",
    label: "机构名称",
  },
  {
    key: "buy_amount",
    label: "买入额",
    numeric: true,
    render: (val) => fmtNum(val as number),
  },
  {
    key: "sell_amount",
    label: "卖出额",
    numeric: true,
    render: (val) => fmtNum(val as number),
  },
  {
    key: "net_amount",
    label: "净额",
    numeric: true,
    sortable: true,
    render: (val) => {
      const v = val as number;
      return <span className={marketColor(v)}>{fmtNum(v)}</span>;
    },
  },
];

const brokerColumns: ColumnDef<Broker>[] = [
  {
    key: "broker_name",
    label: "营业部",
  },
  {
    key: "buy_amount",
    label: "买入额",
    numeric: true,
    render: (val) => fmtNum(val as number),
  },
  {
    key: "sell_amount",
    label: "卖出额",
    numeric: true,
    render: (val) => fmtNum(val as number),
  },
  {
    key: "net_amount",
    label: "净额",
    numeric: true,
    sortable: true,
    render: (val) => {
      const v = val as number;
      return <span className={marketColor(v)}>{fmtNum(v)}</span>;
    },
  },
];

// ---------------------------------------------------------------------------
// Inner page (needs QueryClient context)
// ---------------------------------------------------------------------------

function DragonTigerContent({ date }: { date: string }) {
  const { data, isLoading, isError, error, refetch } = useDragonTiger(date);

  const totalNetBuy = useMemo(() => {
    if (!data?.detail) return 0;
    return data.detail.reduce((sum, d) => sum + d.net_buy_amount, 0);
  }, [data?.detail]);

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-48" />
        <div className="grid grid-cols-2 gap-4">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
        <Skeleton className="h-96" />
      </div>
    );
  }

  if (isError) {
    return (
      <ErrorState
        onRetry={() => refetch()}
        error={error ?? undefined}
      />
    );
  }

  if (!data || data.status === "no_data") {
    return (
      <EmptyState
        message="暂无数据"
        description="非交易日或数据未更新"
      />
    );
  }

  const hotMoneyData: HotMoney[] = data.hot_money ?? [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight">龙虎榜</h1>
        <div className="mt-1 flex items-center gap-3">
          <p className="text-sm text-muted-foreground">{data.date}</p>
          <span className="text-xs text-muted-foreground/60">
            数据更新于 {new Date().toLocaleString("zh-CN")}
          </span>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 gap-4">
        <Card size="sm">
          <CardHeader>
            <CardTitle className="text-muted-foreground text-xs font-normal">
              上榜个股
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold tabular-nums">
              {data.detail?.length ?? 0}
              <span className="text-sm font-normal text-muted-foreground ml-1">
                只
              </span>
            </div>
          </CardContent>
        </Card>
        <Card size="sm">
          <CardHeader>
            <CardTitle className="text-muted-foreground text-xs font-normal">
              合计净买额
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div
              className={`text-2xl font-bold tabular-nums ${marketColor(totalNetBuy)}`}
            >
              {fmtYi(totalNetBuy)}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Tabs */}
      <Tabs defaultValue="detail">
        <TabsList>
          <TabsTrigger value="detail">详情</TabsTrigger>
          <TabsTrigger value="institutional">机构</TabsTrigger>
          <TabsTrigger value="brokers">营业部</TabsTrigger>
          <TabsTrigger value="hot-money">游资</TabsTrigger>
        </TabsList>

        <TabsContent value="detail" className="mt-4">
          <StockTable
            data={(data.detail ?? []) as unknown as Record<string, unknown>[]}
            columns={
              detailColumns as unknown as ColumnDef<Record<string, unknown>>[]
            }
          />
        </TabsContent>

        <TabsContent value="institutional" className="mt-4">
          <StockTable
            data={
              (data.institutional ??
                []) as unknown as Record<string, unknown>[]
            }
            columns={
              instColumns as unknown as ColumnDef<Record<string, unknown>>[]
            }
          />
        </TabsContent>

        <TabsContent value="brokers" className="mt-4">
          <StockTable
            data={(data.brokers ?? []) as unknown as Record<string, unknown>[]}
            columns={
              brokerColumns as unknown as ColumnDef<Record<string, unknown>>[]
            }
          />
        </TabsContent>

        <TabsContent value="hot-money" className="mt-4">
          {hotMoneyData.length === 0 ? (
            <div className="flex items-center justify-center h-32 text-muted-foreground">
              暂无数据
            </div>
          ) : (
            <div className="space-y-2">
              {hotMoneyData.map((hm, idx) => (
                <div
                  key={idx}
                  className="flex items-center gap-4 rounded-lg border px-4 py-3"
                >
                  <div className="min-w-[120px] font-medium truncate">
                    {hm.broker}
                  </div>
                  <div className="flex-1 grid grid-cols-2 gap-4 text-sm">
                    <div>
                      <span className="text-muted-foreground">买入: </span>
                      <span className="text-red-500">
                        {(hm.buy_targets ?? []).join("、") || "—"}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">卖出: </span>
                      <span className="text-green-500">
                        {(hm.sell_targets ?? []).join("、") || "—"}
                      </span>
                    </div>
                  </div>
                  <Badge
                    variant={
                      hm.net_direction === "买入" ? "destructive" : "secondary"
                    }
                  >
                    {hm.net_direction}
                  </Badge>
                </div>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page wrapper — provides QueryClient + sets credentials
// ---------------------------------------------------------------------------

const queryClient = new QueryClient();

export default function DragonTigerPage() {
  const [client] = useState(() => {
    setCredentials({ username: "stockhot", password: "stockhot" });
    return queryClient;
  });

  const today = new Date().toISOString().slice(0, 10);

  return (
    <QueryClientProvider client={client}>
      <div className="p-6 max-w-7xl mx-auto">
        <DragonTigerContent date={today} />
      </div>
    </QueryClientProvider>
  );
}
