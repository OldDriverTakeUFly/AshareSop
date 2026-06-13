import type {
  ChecklistData,
  ChecklistFillRequest,
  ChecklistGenerateRequest,
  DistressHeatmapData,
  HistoryEntry,
  ReportData,
  RescoreRequest,
  RescoreResult,
  ScreeningResults,
  ScreeningStartRequest,
  StockDetail,
  TaskInfo,
  TrendData,
} from "./types";

const API_BASE = "/api";

async function fetchJson<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      message = body.detail ?? body.message ?? message;
    } catch {
      message = res.statusText || message;
    }
    throw new Error(message);
  }

  return res.json() as Promise<T>;
}

export function startScreening(
  req: ScreeningStartRequest,
): Promise<{ task_id: string }> {
  return fetchJson("/screening/start", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function getTaskStatus(taskId: string): Promise<TaskInfo> {
  return fetchJson(`/screening/${taskId}/status`);
}

export function getScreeningResults(taskId: string): Promise<ScreeningResults> {
  return fetchJson(`/screening/${taskId}/results`);
}

export function getStockDetail(
  taskId: string,
  tsCode: string,
): Promise<StockDetail> {
  return fetchJson(`/stocks/${taskId}/${tsCode}`);
}

export function getReport(
  taskId: string,
  tsCode: string,
): Promise<ReportData> {
  return fetchJson(`/reports/${taskId}/${tsCode}`);
}

export function getTrendData(
  taskId: string,
  tsCode: string,
): Promise<TrendData> {
  return fetchJson(`/trends/${taskId}/${tsCode}`);
}

export function getDistressHeatmap(taskId: string): Promise<DistressHeatmapData> {
  return fetchJson(`/distress/${taskId}`);
}

export function generateChecklists(
  req: ChecklistGenerateRequest,
): Promise<{ checklists: ChecklistData[] }> {
  return fetchJson("/checklists/generate", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function fillChecklist(
  tsCode: string,
  req: ChecklistFillRequest,
): Promise<{ success: boolean }> {
  return fetchJson(`/checklists/${tsCode}/fill`, {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function rescore(
  req: RescoreRequest,
): Promise<{ results: RescoreResult[] }> {
  return fetchJson("/checklists/rescore", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function getHistory(): Promise<{ history: HistoryEntry[] }> {
  return fetchJson("/history");
}

export function loadHistoryTask(
  taskId: string,
): Promise<{ task_id: string; loaded: boolean }> {
  return fetchJson(`/history/${taskId}`);
}
