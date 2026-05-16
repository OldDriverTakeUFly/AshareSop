"use client";

import { useEffect } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { setCredentials } from "@/lib/api";
import {
  useInvestVixHistory,
  useInvestCommodityHistory,
} from "@/lib/hooks";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5 * 60 * 1000, refetchOnWindowFocus: false },
  },
});

function dateRange() {
  const today = new Date().toISOString().slice(0, 10);
  const threeMonthsAgo = new Date(Date.now() - 90 * 24 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 10);
  return { start: threeMonthsAgo, end: today };
}

function ChartsContent() {
  useEffect(() => {
    setCredentials({ username: "stockhot", password: "stockhot" });
  }, []);

  const { start, end } = dateRange();

  const { data: vixData, isLoading: vixLoading } = useInvestVixHistory(start, end);
  const { data: lcData, isLoading: lcLoading } = useInvestCommodityHistory(
    "碳酸锂期货收盘价",
    start,
    end,
  );
  const { data: pvsData, isLoading: pvsLoading } = useInvestCommodityHistory(
    "多晶硅期货收盘价",
    start,
    end,
  );

  const isLoading = vixLoading || lcLoading || pvsLoading;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold tracking-tight">历史走势</h1>
        <span className="text-sm text-muted-foreground">
          {start} ~ {end}
        </span>
      </div>

      {isLoading && (
        <div className="space-y-6">
          {Array.from({ length: 3 }, (_, i) => (
            <div key={i} className="animate-pulse rounded-lg border p-4">
              <div className="mb-2 h-4 w-24 rounded bg-muted" />
              <div className="h-[300px] w-full rounded bg-muted" />
            </div>
          ))}
        </div>
      )}

      {!isLoading && (
        <div className="space-y-6">
          {/* VIX Chart */}
          <div className="rounded-lg border p-4">
            <h3 className="mb-2 text-sm font-medium">VIX 走势</h3>
            {vixData && vixData.length > 0 ? (
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={vixData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} />
                  <Tooltip />
                  <Legend />
                  <Line
                    type="monotone"
                    dataKey="vix"
                    stroke="#8884d8"
                    name="QVIX"
                    dot={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="us_vix"
                    stroke="#82ca9d"
                    name="US VIX"
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-[300px] items-center justify-center text-muted-foreground">
                暂无数据
              </div>
            )}
          </div>

          {/* 碳酸锂 Chart */}
          <div className="rounded-lg border p-4">
            <h3 className="mb-2 text-sm font-medium">碳酸锂期货收盘价</h3>
            {lcData && lcData.length > 0 ? (
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={lcData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} />
                  <Tooltip />
                  <Line
                    type="monotone"
                    dataKey="value"
                    stroke="#ff7300"
                    name="碳酸锂"
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-[300px] items-center justify-center text-muted-foreground">
                暂无数据
              </div>
            )}
          </div>

          {/* 多晶硅 Chart */}
          <div className="rounded-lg border p-4">
            <h3 className="mb-2 text-sm font-medium">多晶硅期货收盘价</h3>
            {pvsData && pvsData.length > 0 ? (
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={pvsData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} />
                  <Tooltip />
                  <Line
                    type="monotone"
                    dataKey="value"
                    stroke="#387908"
                    name="多晶硅"
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-[300px] items-center justify-center text-muted-foreground">
                暂无数据
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function InvestSopChartsPage() {
  return (
    <QueryClientProvider client={queryClient}>
      <ChartsContent />
    </QueryClientProvider>
  );
}
