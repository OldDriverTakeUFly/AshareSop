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
} from "./api";
import type { ApiError } from "./api";

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
