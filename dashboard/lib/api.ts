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
