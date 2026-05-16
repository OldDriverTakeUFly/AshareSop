"use client";

import { type ReactNode, useState, useEffect, useMemo } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRiskAlert, useHealth } from "@/lib/hooks";
import { setCredentials } from "@/lib/api";
import {
  RiskTable,
  type RiskColumnDef,
  type RiskLevel,
} from "@/components/tables/RiskTable";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  CardDescription,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { EmptyState } from "@/components/EmptyState";
import { cn } from "@/lib/utils";
import { CalendarIcon } from "lucide-react";
import { Calendar } from "@/components/ui/calendar";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { format, parse } from "date-fns";
import { Button } from "@/components/ui/button";

setCredentials({ username: "stockhot", password: "stockhot" });

const queryClient = new QueryClient();

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type Row = Record<string, unknown>;

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
        <Button variant="outline" className="gap-2 font-mono">
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
// ST-Stock columns
// ---------------------------------------------------------------------------

const stColumns: RiskColumnDef<Row>[] = [
  { key: "代码", label: "代码", mono: true },
  { key: "名称", label: "名称" },
  { key: "最新价", label: "最新价", numeric: true, sortable: true },
  {
    key: "涨跌幅",
    label: "涨跌幅",
    numeric: true,
    sortable: true,
    render: (val: unknown) => {
      const n = typeof val === "number" ? val : 0;
      return (
        <span
          className={cn(
            n < 0
              ? "text-green-600 dark:text-green-400"
              : n > 0
                ? "text-red-600 dark:text-red-400"
                : ""
          )}
        >
          {n.toFixed(2)}%
        </span>
      );
    },
  },
];

// ---------------------------------------------------------------------------
// Generic column builder
// ---------------------------------------------------------------------------

function buildGenericColumns(items: Row[]): RiskColumnDef<Row>[] {
  if (items.length === 0) return [];
  return Object.keys(items[0]).map((key) => ({
    key,
    label: key,
    render: (val: unknown): ReactNode => {
      if (typeof val === "number")
        return (
          <span className="font-mono tabular-nums">{val.toFixed(2)}</span>
        );
      return val != null ? String(val) : "\u2014";
    },
  }));
}

// ---------------------------------------------------------------------------
// Severity badge
// ---------------------------------------------------------------------------

function SeverityBadge({ level }: { level: RiskLevel }) {
  return (
    <span
      className={cn(
        "inline-block rounded px-1.5 py-0.5 text-xs font-medium",
        level === "high" &&
          "bg-red-500/20 text-red-700 dark:text-red-400",
        level === "medium" &&
          "bg-orange-500/20 text-orange-700 dark:text-orange-400",
        level === "info" && "bg-muted text-muted-foreground"
      )}
    >
      {level === "high" ? "高" : level === "medium" ? "中" : "低"}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Risk section card
// ---------------------------------------------------------------------------

const borderMap: Record<RiskLevel, string> = {
  high: "border-l-red-500",
  medium: "border-l-orange-500",
  info: "border-l-muted-foreground/30",
};

function RiskSection({
  title,
  description,
  severity,
  children,
}: {
  title: string;
  description: string;
  severity: RiskLevel;
  children: ReactNode;
}) {
  return (
    <Card className={cn("border-l-4", borderMap[severity])}>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CardTitle>{title}</CardTitle>
          <SeverityBadge level={severity} />
        </div>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Inner content — separated so useRiskAlert stays under QueryClientProvider
// ---------------------------------------------------------------------------

function RiskAlertContent() {
  const { data: health } = useHealth();
  const defaultDate = health?.latest_dates?.risk_alert_raw ?? "";
  const [selectedDate, setSelectedDate] = useState("");
  useEffect(() => {
    if (!selectedDate && defaultDate) setSelectedDate(defaultDate);
  }, [selectedDate, defaultDate]);
  const date = selectedDate;
  const { data, isLoading, isError, error, refetch } = useRiskAlert(date);

  if (isLoading) {
    return (
      <div className="space-y-6 p-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-14 w-full max-w-md" />
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="space-y-2">
            <Skeleton className="h-6 w-36" />
            <Skeleton className="h-40 w-full" />
          </div>
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div className="space-y-6 p-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">风险提示</h1>
          <div className="mt-1 flex items-center gap-3">
            <DatePicker value={date} onChange={setSelectedDate} />
          </div>
        </div>
        <ErrorState onRetry={() => refetch()} error={error ?? undefined} />
      </div>
    );
  }

  if (!data?.data) {
    return (
      <div className="space-y-6 p-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">风险提示</h1>
          <div className="mt-1 flex items-center gap-3">
            <DatePicker value={date} onChange={setSelectedDate} />
            <span className="text-xs text-muted-foreground/60">
              数据更新于 {new Date().toLocaleString("zh-CN")}
            </span>
          </div>
        </div>
        <EmptyState
          message="暂无风险数据"
          description="非交易日或数据未更新"
        />
      </div>
    );
  }

  const rd = data.data;
  const total =
    rd.st_stocks.length +
    rd.abnormal_volatility.length +
    rd.capital_flight.length +
    rd.high_position_risks.length;

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">风险提示</h1>
        <div className="mt-1 flex items-center gap-3">
          <DatePicker value={date} onChange={setSelectedDate} />
          <span className="text-xs text-muted-foreground/60">
            数据更新于 {new Date().toLocaleString("zh-CN")}
          </span>
        </div>
      </div>

      <Card>
        <CardContent className="py-3">
          <p className="text-base">
            共检出{" "}
            <span className="text-xl font-bold text-destructive">
              {total}
            </span>{" "}
            项风险信号
          </p>
        </CardContent>
      </Card>

      {rd.st_stocks.length > 0 && (
        <RiskSection
          title="ST 股票"
          description="特别处理股票，信息性提示"
          severity="info"
        >
          <RiskTable
            data={rd.st_stocks as unknown as Row[]}
            columns={stColumns}
            getRiskLevel={() => "info"}
          />
        </RiskSection>
      )}

      {rd.abnormal_volatility.length > 0 && (
        <RiskSection
          title="异常波动"
          description="价格或成交量异常波动的股票"
          severity="high"
        >
          <RiskTable
            data={rd.abnormal_volatility}
            columns={buildGenericColumns(rd.abnormal_volatility)}
            getRiskLevel={() => "high"}
          />
        </RiskSection>
      )}

      {rd.capital_flight.length > 0 && (
        <RiskSection
          title="资金出逃"
          description="主力资金大幅流出的板块或个股"
          severity="medium"
        >
          <RiskTable
            data={rd.capital_flight}
            columns={buildGenericColumns(rd.capital_flight)}
            getRiskLevel={() => "medium"}
          />
        </RiskSection>
      )}

      {rd.high_position_risks.length > 0 && (
        <RiskSection
          title="高位风险"
          description="连续涨停后的高位风险警示"
          severity="high"
        >
          <RiskTable
            data={rd.high_position_risks}
            columns={buildGenericColumns(rd.high_position_risks)}
            getRiskLevel={() => "high"}
          />
        </RiskSection>
      )}

      {total === 0 && (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            暂无风险数据
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page export
// ---------------------------------------------------------------------------

export default function RiskAlertPage() {
  return (
    <QueryClientProvider client={queryClient}>
      <RiskAlertContent />
    </QueryClientProvider>
  );
}
