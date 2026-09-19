/**
 * React Query hooks wrapping the StockHot-CN API client.
 * Each hook manages loading, error, and data states.
 */
"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchLimitUp,
  fetchDragonTiger,
  fetchFundFlow,
  fetchRiskAlert,
  fetchHealth,
  fetchDates,
  triggerDataRefresh,
  fetchInvestHoldings,
  createInvestHoldingSimple,
  adjustInvestHolding,
  fetchInvestHoldingTransactions,
  fetchInvestSectorRules,
  updateInvestHoldingPrice,
  updateInvestHoldingStoploss,
  removeInvestHolding,
  fetchInvestOverview,
  fetchInvestVixHistory,
  fetchInvestCommodityHistory,
  fetchInvestReports,
  fetchInvestReport,
} from "./api";
import type { ApiError } from "./api";
import type { InvestHoldingCreateSimple, InvestHoldingAdjust, InvestHoldingUpdatePrice, InvestHoldingUpdateStoploss } from "./types";

// ---------------------------------------------------------------------------
// Query key factory — centralised keys for cache invalidation
// ---------------------------------------------------------------------------

export const queryKeys = {
  limitUp: (date: string) => ["limit-up", date] as const,
  dragonTiger: (date: string) => ["dragon-tiger", date] as const,
  fundFlow: (date: string) => ["fund-flow", date] as const,
  riskAlert: (date: string) => ["risk-alert", date] as const,
  health: () => ["health"] as const,
  dates: () => ["dates"] as const,
  invest: {
    holdings: ["invest", "holdings"] as const,
    holdingTransactions: (id: number) => ["invest", "holdings", id, "transactions"] as const,
    sectorRules: ["invest", "sector-rules"] as const,
    overview: (date: string) => ["invest", "overview", date] as const,
    vixHistory: (startDate: string, endDate: string) => ["invest", "vix", startDate, endDate] as const,
    commodityHistory: (metricName: string, startDate: string, endDate: string) => ["invest", "commodity", metricName, startDate, endDate] as const,
    reports: ["invest", "reports"] as const,
    report: (date: string) => ["invest", "report", date] as const,
  },
} as const;

// ---------------------------------------------------------------------------
// Shared stale/refetch defaults (5 min stale, no auto-refetch)
// ---------------------------------------------------------------------------

const defaults = {
  staleTime: 5 * 60 * 1000,
  refetchOnWindowFocus: false,
  retry: 1,
} as const;

// ---------------------------------------------------------------------------
// Data hooks
// ---------------------------------------------------------------------------

/** Limit-up (涨停) analysis for a given date. */
export function useLimitUp(date: string) {
  return useQuery({
    queryKey: queryKeys.limitUp(date),
    queryFn: () => fetchLimitUp(date),
    enabled: !!date,
    ...defaults,
  });
}

/** Dragon-tiger (龙虎榜) data for a given date. */
export function useDragonTiger(date: string) {
  return useQuery({
    queryKey: queryKeys.dragonTiger(date),
    queryFn: () => fetchDragonTiger(date),
    enabled: !!date,
    ...defaults,
  });
}

/** Fund flow (资金流向) data for a given date. */
export function useFundFlow(date: string) {
  return useQuery({
    queryKey: queryKeys.fundFlow(date),
    queryFn: () => fetchFundFlow(date),
    enabled: !!date,
    ...defaults,
  });
}

/** Risk alert (风险提示) data for a given date. */
export function useRiskAlert(date: string) {
  return useQuery({
    queryKey: queryKeys.riskAlert(date),
    queryFn: () => fetchRiskAlert(date),
    enabled: !!date,
    ...defaults,
  });
}

/** Backend health status. */
export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health(),
    queryFn: fetchHealth,
    ...defaults,
  });
}

/** Available trading dates. */
export function useAvailableDates() {
  return useQuery({
    queryKey: queryKeys.dates(),
    queryFn: fetchDates,
    ...defaults,
  });
}

// ---------------------------------------------------------------------------
// Mutation hook
// ---------------------------------------------------------------------------

/** Trigger a data refresh and invalidate related caches on success. */
export function useTriggerRefresh() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (date: string) => triggerDataRefresh(date),
    onSuccess: (_data, date) => {
      // Invalidate all queries that might have changed after refresh
      void queryClient.invalidateQueries({ queryKey: queryKeys.limitUp(date) });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.dragonTiger(date),
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.fundFlow(date),
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.riskAlert(date),
      });
      void queryClient.invalidateQueries({ queryKey: queryKeys.dates() });
    },
  });
}

// Re-export ApiError for consumer convenience
export type { ApiError };

// ---------------------------------------------------------------------------
// Invest SOP hooks
// ---------------------------------------------------------------------------

/** Active invest holdings. */
export function useInvestHoldings() {
  return useQuery({
    queryKey: queryKeys.invest.holdings,
    queryFn: fetchInvestHoldings,
    ...defaults,
  });
}

/** Overview data for a date. */
export function useInvestOverview(date: string) {
  return useQuery({
    queryKey: queryKeys.invest.overview(date),
    queryFn: () => fetchInvestOverview(date),
    enabled: !!date,
    ...defaults,
  });
}

/** Historical VIX data. */
export function useInvestVixHistory(startDate: string, endDate: string) {
  return useQuery({
    queryKey: queryKeys.invest.vixHistory(startDate, endDate),
    queryFn: () => fetchInvestVixHistory(startDate, endDate),
    enabled: !!startDate && !!endDate,
    ...defaults,
  });
}

/** Historical commodity data. */
export function useInvestCommodityHistory(metricName: string, startDate: string, endDate: string) {
  return useQuery({
    queryKey: queryKeys.invest.commodityHistory(metricName, startDate, endDate),
    queryFn: () => fetchInvestCommodityHistory(metricName, startDate, endDate),
    enabled: !!metricName && !!startDate && !!endDate,
    ...defaults,
  });
}

/** Available report dates. */
export function useInvestReports() {
  return useQuery({
    queryKey: queryKeys.invest.reports,
    queryFn: fetchInvestReports,
    ...defaults,
  });
}

/** Report content for a date. */
export function useInvestReport(date: string) {
  return useQuery({
    queryKey: queryKeys.invest.report(date),
    queryFn: () => fetchInvestReport(date),
    enabled: !!date,
    ...defaults,
  });
}

/** Create a new holding with simplified input (mutation). */
export function useCreateHoldingSimple() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: InvestHoldingCreateSimple) => createInvestHoldingSimple(data),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.invest.holdings });
    },
  });
}

/** Adjust holding position — buy/sell (mutation). */
export function useAdjustHolding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: InvestHoldingAdjust }) =>
      adjustInvestHolding(id, data),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.invest.holdings });
    },
  });
}

/** Transaction history for a single holding. */
export function useHoldingTransactions(id: number) {
  return useQuery({
    queryKey: queryKeys.invest.holdingTransactions(id),
    queryFn: () => fetchInvestHoldingTransactions(id),
    enabled: !!id,
    ...defaults,
  });
}

/** Sector rules. */
export function useSectorRules() {
  return useQuery({
    queryKey: queryKeys.invest.sectorRules,
    queryFn: fetchInvestSectorRules,
    ...defaults,
  });
}

/** Update holding price (mutation). */
export function useUpdateHoldingPrice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: InvestHoldingUpdatePrice }) =>
      updateInvestHoldingPrice(id, data),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.invest.holdings });
    },
  });
}

/** Update holding stop-loss (mutation). */
export function useUpdateHoldingStoploss() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: InvestHoldingUpdateStoploss }) =>
      updateInvestHoldingStoploss(id, data),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.invest.holdings });
    },
  });
}

/** Remove holding (mutation). */
export function useRemoveHolding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => removeInvestHolding(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.invest.holdings });
    },
  });
}
