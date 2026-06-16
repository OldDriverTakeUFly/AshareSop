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
  relative_delta_g: number;
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
  rank: number;
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

export interface HistoryEntry {
  task_id: string;
  created_at: string;
  top_n: number;
  total_count: number;
}

export interface IndustryScore {
  industry: string;
  stock_count: number;
  avg_composite_score: number;
  median_delta_g: number;
  avg_revenue_score: number;
  avg_profit_score: number;
  avg_slope_score: number;
  avg_duration_score: number;
  stage: string;
  ignition_count: number;
  top_stock_codes: string[];
}

export interface CatalystSignal {
  signal_type: string;
  description: string;
  strength: number;
}

export interface InflectionAnalysis {
  ts_code: string;
  stage: string;
  inflection_quarter: string | null;
  primary_driver: string;
  catalysts: CatalystSignal[];
  narrative: string;
  inflection_axis?: string | null;
}

export interface StockValuation {
  ts_code: string;
  daily_dates: string[];
  daily_pe: number[];
  daily_pb: number[];
  quarterly_periods: string[];
  quarterly_revenue_growth: number[];
  quarterly_profit_growth: number[];
}

export interface ProsperityStock {
  ts_code: string;
  name: string;
  industry: string;
  revenue_score: number;
  profit_score: number;
  slope_score: number;
  duration_score: number;
  composite_score: number;
  delta_g: number;
  relative_delta_g: number;
  dupont_driver: string | null;
  stage: string;
  is_ignition: boolean;
  ignition_reasons: string[];
  risk_warnings: string[];
  rank_in_industry: number;
  inflection: InflectionAnalysis | null;
}

export interface ProsperitySectorResults {
  industries: IndustryScore[];
  total_industries: number;
  analysis_date: string;
}

export interface ProsperityIndustryDetail {
  industry: string;
  stocks: ProsperityStock[];
  industry_score: IndustryScore;
}
