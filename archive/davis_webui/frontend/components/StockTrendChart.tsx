"use client";
import { useQuery } from "@tanstack/react-query";
import {
  ComposedChart,
  Line,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { getStockValuation } from "@/lib/api";

interface ChartDatum {
  date: string;
  pe: number;
  pb: number;
  revenueGrowth: number | null;
  profitGrowth: number | null;
}

function formatDate(raw: string): string {
  if (raw.length === 8) {
    return `${raw.slice(0, 4)}/${raw.slice(4, 6)}/${raw.slice(6, 8)}`;
  }
  return raw;
}

export function StockTrendChart({
  taskId,
  tsCode,
  stockName,
}: {
  taskId: string;
  tsCode: string;
  stockName: string;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["stock-valuation", taskId, tsCode],
    queryFn: () => getStockValuation(taskId, tsCode),
    enabled: !!taskId && !!tsCode,
  });

  if (isLoading) {
    return (
      <div className="text-zinc-400 text-sm py-8 text-center">
        正在获取估值数据...
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-red-400 text-sm py-8 text-center">
        {(error as Error).message}
      </div>
    );
  }

  if (!data || data.daily_dates.length === 0) {
    return (
      <div className="text-zinc-500 text-sm py-8 text-center">暂无估值数据</div>
    );
  }

  // Build quarter lookup by YYYYMM prefix for matching to daily dates
  const quarterMap = new Map<string, { revenue: number; profit: number }>();
  data.quarterly_periods.forEach((period, i) => {
    quarterMap.set(period.slice(0, 6), {
      revenue: data.quarterly_revenue_growth[i],
      profit: data.quarterly_profit_growth[i],
    });
  });

  // Assign growth values only to the first daily date matching each quarter
  const assignedQuarters = new Set<string>();

  const chartData: ChartDatum[] = data.daily_dates.map((date, i) => {
    const prefix = date.slice(0, 6);
    const qData = quarterMap.get(prefix);
    let revenueGrowth: number | null = null;
    let profitGrowth: number | null = null;
    if (qData && !assignedQuarters.has(prefix)) {
      revenueGrowth = qData.revenue;
      profitGrowth = qData.profit;
      assignedQuarters.add(prefix);
    }
    return {
      date: formatDate(date),
      pe: data.daily_pe[i],
      pb: data.daily_pb[i],
      revenueGrowth,
      profitGrowth,
    };
  });

  const tickInterval = Math.max(1, Math.floor(chartData.length / 8));

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-zinc-300">
        {stockName}（{tsCode}）估值与成长趋势
      </h3>
      <ResponsiveContainer width="100%" height={400}>
        <ComposedChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
          <XAxis
            dataKey="date"
            tick={{ fill: "#71717a", fontSize: 11 }}
            angle={-45}
            textAnchor="end"
            height={60}
            interval={tickInterval}
          />
          <YAxis
            yAxisId="valuation"
            orientation="left"
            tick={{ fill: "#71717a", fontSize: 11 }}
          />
          <YAxis
            yAxisId="growth"
            orientation="right"
            tick={{ fill: "#71717a", fontSize: 11 }}
            unit="%"
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#18181b",
              border: "1px solid #3f3f46",
              borderRadius: "4px",
            }}
          />
          <Legend />
          <Line
            yAxisId="valuation"
            type="monotone"
            dataKey="pe"
            name="PE(TTM)"
            stroke="#f59e0b"
            strokeWidth={2}
            dot={false}
            connectNulls
          />
          <Line
            yAxisId="valuation"
            type="monotone"
            dataKey="pb"
            name="PB"
            stroke="#a855f7"
            strokeWidth={2}
            dot={false}
            connectNulls
          />
          <Bar yAxisId="growth" dataKey="revenueGrowth" name="营收增速">
            {chartData.map((entry, index) => (
              <Cell
                key={`rev-${index}`}
                fill={
                  entry.revenueGrowth !== null && entry.revenueGrowth > 0
                    ? "#ef4444"
                    : "#22c55e"
                }
              />
            ))}
          </Bar>
          <Bar yAxisId="growth" dataKey="profitGrowth" name="利润增速">
            {chartData.map((entry, index) => (
              <Cell
                key={`profit-${index}`}
                fill={
                  entry.profitGrowth !== null && entry.profitGrowth > 0
                    ? "#dc2626"
                    : "#16a34a"
                }
              />
            ))}
          </Bar>
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
