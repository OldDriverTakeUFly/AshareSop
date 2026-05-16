/**
 * Typed API client for StockHot-CN backend.
 * All requests go through the Next.js proxy (relative URLs).
 * Supports HTTP Basic Auth via credentials parameter.
 */

import type {
  LimitUpResponse,
  DragonTigerResponse,
  FundFlowResponse,
  RiskAlertResponse,
  HealthStatus,
  AvailableDates,
  InvestHolding,
  InvestHoldingCreate,
  InvestHoldingUpdatePrice,
  InvestHoldingUpdateStoploss,
  InvestOverviewResponse,
  InvestSupplyChainRecord,
  InvestHistoryPoint,
  InvestVixHistoryPoint,
  InvestReportInfo,
  InvestReportResponse,
} from "./types";

/** Credentials for HTTP Basic Auth. */
export interface ApiCredentials {
  username: string;
  password: string;
}

let _credentials: ApiCredentials | null = null;

/** Set credentials for subsequent API calls. */
export function setCredentials(credentials: ApiCredentials): void {
  _credentials = credentials;
}

/** Clear stored credentials. */
export function clearCredentials(): void {
  _credentials = null;
}

/** Build an Authorization header if credentials are set. */
function authHeader(): HeadersInit {
  const headers: Record<string, string> = {};
  if (_credentials) {
    const encoded = btoa(
      `${_credentials.username}:${_credentials.password}`
    );
    headers["Authorization"] = `Basic ${encoded}`;
  }
  return headers;
}

/** Generic fetch wrapper with auth and error handling. */
async function apiFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...options,
    headers: {
      ...authHeader(),
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, res.statusText, body);
  }

  return res.json() as Promise<T>;
}

/** Custom error with HTTP status detail. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    public body: string
  ) {
    super(`API error ${status}: ${statusText}`);
    this.name = "ApiError";
  }
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

/** Fetch limit-up analysis for a given date. */
export function fetchLimitUp(date: string): Promise<LimitUpResponse> {
  return apiFetch<LimitUpResponse>(`/api/limit-up/${date}`);
}

/** Fetch dragon-tiger (龙虎榜) data for a given date. */
export function fetchDragonTiger(date: string): Promise<DragonTigerResponse> {
  return apiFetch<DragonTigerResponse>(`/api/dragon-tiger/${date}`);
}

/** Fetch fund flow data for a given date. */
export function fetchFundFlow(date: string): Promise<FundFlowResponse> {
  return apiFetch<FundFlowResponse>(`/api/fund-flow/${date}`);
}

/** Fetch risk alert data for a given date. */
export function fetchRiskAlert(date: string): Promise<RiskAlertResponse> {
  return apiFetch<RiskAlertResponse>(`/api/risk-alert/${date}`);
}

/** Fetch backend health status. */
export function fetchHealth(): Promise<HealthStatus> {
  return apiFetch<HealthStatus>("/api/health");
}

/** Fetch available trading dates. */
export function fetchDates(): Promise<AvailableDates> {
  return apiFetch<AvailableDates>("/api/dates");
}

/** Trigger a data refresh (re-scrape) for a given date. */
export function triggerDataRefresh(date: string): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/api/trigger/${date}`, {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Invest SOP API
// ---------------------------------------------------------------------------

/** Fetch active invest holdings. */
export function fetchInvestHoldings(): Promise<InvestHolding[]> {
  return apiFetch<InvestHolding[]>("/api/invest-sop/holdings");
}

/** Create a new invest holding. */
export function createInvestHolding(data: InvestHoldingCreate): Promise<InvestHolding> {
  return apiFetch<InvestHolding>("/api/invest-sop/holdings", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

/** Update holding current price. */
export function updateInvestHoldingPrice(id: number, data: InvestHoldingUpdatePrice): Promise<InvestHolding> {
  return apiFetch<InvestHolding>(`/api/invest-sop/holdings/${id}/price`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

/** Update holding stop-loss levels. */
export function updateInvestHoldingStoploss(id: number, data: InvestHoldingUpdateStoploss): Promise<InvestHolding> {
  return apiFetch<InvestHolding>(`/api/invest-sop/holdings/${id}/stoploss`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

/** Soft-delete a holding. */
export function removeInvestHolding(id: number): Promise<{ status: string; id: number }> {
  return apiFetch<{ status: string; id: number }>(`/api/invest-sop/holdings/${id}`, {
    method: "DELETE",
  });
}

/** Fetch overview data for a date. */
export function fetchInvestOverview(date: string): Promise<InvestOverviewResponse> {
  return apiFetch<InvestOverviewResponse>(`/api/invest-sop/overview/${date}`);
}

/** Fetch supply chain data for a date. */
export function fetchInvestSupplyChain(date: string): Promise<InvestSupplyChainRecord[]> {
  return apiFetch<InvestSupplyChainRecord[]>(`/api/invest-sop/supply-chain/${date}`);
}

/** Fetch historical commodity data. */
export function fetchInvestCommodityHistory(metricName: string, startDate: string, endDate: string): Promise<InvestHistoryPoint[]> {
  return apiFetch<InvestHistoryPoint[]>(`/api/invest-sop/history/commodity?metric_name=${encodeURIComponent(metricName)}&start_date=${startDate}&end_date=${endDate}`);
}

/** Fetch historical VIX data. */
export function fetchInvestVixHistory(startDate: string, endDate: string): Promise<InvestVixHistoryPoint[]> {
  return apiFetch<InvestVixHistoryPoint[]>(`/api/invest-sop/history/vix?start_date=${startDate}&end_date=${endDate}`);
}

/** List available report dates. */
export function fetchInvestReports(): Promise<InvestReportInfo[]> {
  return apiFetch<InvestReportInfo[]>("/api/invest-sop/reports");
}

/** Fetch report content for a date. */
export function fetchInvestReport(date: string): Promise<InvestReportResponse> {
  return apiFetch<InvestReportResponse>(`/api/invest-sop/reports/${date}`);
}
