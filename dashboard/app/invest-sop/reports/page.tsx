"use client";

import { useEffect, useState, useMemo } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setCredentials } from "@/lib/api";
import { useInvestReports, useInvestReport } from "@/lib/hooks";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { EmptyState } from "@/components/EmptyState";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5 * 60 * 1000, refetchOnWindowFocus: false },
  },
});

const REPORT_TYPES = ["盘前预研", "操作指令", "周期评估"] as const;

// ---------------------------------------------------------------------------
// Inner content
// ---------------------------------------------------------------------------

function ReportsContent() {
  useEffect(() => {
    setCredentials({ username: "stockhot", password: "stockhot" });
  }, []);

  const [selectedDate, setSelectedDate] = useState<string>("");
  const [selectedType, setSelectedType] = useState<string>("盘前预研");

  const {
    data: reports,
    isLoading: reportsLoading,
    isError: reportsError,
    error: reportsErr,
    refetch: refetchReports,
  } = useInvestReports();

  const {
    data: reportData,
    isLoading: reportLoading,
    isError: reportContentError,
    error: reportErr,
    refetch: refetchReport,
  } = useInvestReport(selectedDate);

  const uniqueDates = useMemo(() => {
    if (!reports) return [];
    const dateSet = new Set(reports.map((r) => r.date));
    return Array.from(dateSet).sort((a, b) => b.localeCompare(a));
  }, [reports]);

  useEffect(() => {
    if (uniqueDates.length > 0 && !selectedDate) {
      setSelectedDate(uniqueDates[0]);
    }
  }, [uniqueDates, selectedDate]);

  const reportContent = useMemo(() => {
    if (!reportData?.reports) return null;
    return reportData.reports.find((r) => r.type === selectedType)?.content ?? null;
  }, [reportData, selectedType]);

  if (reportsLoading) {
    return (
      <div className="mx-auto max-w-5xl space-y-6 p-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-12 w-full max-w-md" />
        <Skeleton className="h-80 w-full" />
      </div>
    );
  }

  if (reportsError) {
    return (
      <div className="mx-auto max-w-5xl space-y-6 p-6">
        <h1 className="text-2xl font-bold tracking-tight">研报查阅</h1>
        <ErrorState onRetry={() => refetchReports()} error={reportsErr ?? undefined} />
      </div>
    );
  }

  if (uniqueDates.length === 0) {
    return (
      <div className="mx-auto max-w-5xl space-y-6 p-6">
        <h1 className="text-2xl font-bold tracking-tight">研报查阅</h1>
        <EmptyState message="暂无报告" description="报告生成后将在此显示" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-2xl font-bold tracking-tight">研报查阅</h1>
        <Select value={selectedDate} onValueChange={(v) => { if (v) setSelectedDate(v); }}>
          <SelectTrigger className="w-48">
            <SelectValue placeholder="选择日期" />
          </SelectTrigger>
          <SelectContent>
            {uniqueDates.map((date) => (
              <SelectItem key={date} value={date}>
                {date}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <Tabs
        value={selectedType}
        onValueChange={setSelectedType}
      >
        <TabsList>
          {REPORT_TYPES.map((type) => (
            <TabsTrigger key={type} value={type}>
              {type}
            </TabsTrigger>
          ))}
        </TabsList>

        {REPORT_TYPES.map((type) => (
          <TabsContent key={type} value={type}>
            {reportLoading ? (
              <div className="space-y-3 pt-4">
                <Skeleton className="h-6 w-1/3" />
                <Skeleton className="h-60 w-full" />
              </div>
            ) : reportContentError ? (
              <div className="pt-4">
                <ErrorState
                  onRetry={() => refetchReport()}
                  error={reportErr ?? undefined}
                />
              </div>
            ) : reportContent == null ? (
              <div className="pt-4">
                <EmptyState
                  message={`该日期暂无${type}报告`}
                  description={`${selectedDate} 没有生成${type}类型的报告`}
                />
              </div>
            ) : (
              <pre className="mt-4 whitespace-pre-wrap text-sm leading-relaxed font-mono bg-muted/50 rounded-lg p-6 border">
                {reportContent}
              </pre>
            )}
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page export
// ---------------------------------------------------------------------------

export default function ReportsPage() {
  return (
    <QueryClientProvider client={queryClient}>
      <ReportsContent />
    </QueryClientProvider>
  );
}
