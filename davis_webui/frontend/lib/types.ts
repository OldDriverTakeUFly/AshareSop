export interface StockInfo {
  ts_code: string;
  name: string;
  industry: string;
  is_cyclical: boolean;
}

export interface DavisScore {
  ts_code: string;
  name: string;
  valuation_score: number;
  trend_score: number;
  prosperity_score: number;
  distress_score: number;
  final_score: number;
  rank: number;
}

export interface DistressDetail {
  ts_code: string;
  layer1_score: number;
  layer2_score: number;
  layer3_score: number;
  total_score: number;
  signals_detail: Record<string, Record<string, number>>;
}

export interface ProsperityDetail {
  ts_code: string;
  revenue_score: number;
  profit_score: number;
  slope_score: number;
  duration_score: number;
  composite_score: number;
  delta_g: number;
}

export type TaskStatus = "pending" | "running" | "completed" | "failed";

export interface ScreeningStartRequest {
  top_n?: number;
  dry_run?: boolean;
}

export interface TaskInfo {
  task_id: string;
  status: TaskStatus;
  progress: number;
  message: string;
  error: string | null;
}

export interface ScreeningResults {
  scores: DavisScore[];
  total_count: number;
}

export interface StockDetail {
  stock_info: StockInfo;
  davis_score: DavisScore;
  prosperity_detail: ProsperityDetail | null;
  distress_detail: DistressDetail | null;
  financial_summary: Record<string, number | string>;
}

export interface ReportData {
  ts_code: string;
  name: string;
  markdown_content: string;
}

export interface TrendData {
  ts_code: string;
  monthly_dates: string[];
  monthly_pe: (number | null)[];
  monthly_pb: number[];
  pe_slope: number;
  pb_slope: number;
  pe_acceleration: number;
  pb_acceleration: number;
  trend_score: number;
}

export interface DistressHeatmapStock {
  ts_code: string;
  name: string;
  layer1_signals: Record<string, number>;
  layer2_signals: Record<string, number>;
  layer3_signals: Record<string, number>;
  layer_scores: Record<string, number>;
  total_score: number;
}

export interface DistressHeatmapData {
  stocks: DistressHeatmapStock[];
}

export interface ChecklistGenerateRequest {
  task_id: string;
  top_n?: number;
}

export interface ChecklistSection {
  title: string;
  items: string[];
}

export interface ChecklistData {
  ts_code: string;
  name: string;
  rank: number;
  scores: Record<string, number>;
  prosperity_display: string;
  distress_display: string;
  sections: ChecklistSection[];
}

export interface ChecklistFillRequest {
  prosperity_adjustment: number;
  distress_adjustment: number;
  research_notes?: Record<string, string>;
}

export interface RescoreRequest {
  task_id: string;
}

export interface RescoreResult {
  ts_code: string;
  name: string;
  original_prosperity: number;
  adjusted_prosperity: number;
  original_distress: number;
  adjusted_distress: number;
  prosperity_adjustment: number;
  distress_adjustment: number;
}

export interface RescoreResponse {
  results: RescoreResult[];
}
