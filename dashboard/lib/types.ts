/**
 * TypeScript interfaces matching Pydantic schemas in stockhot/api/schemas.py.
 * Field names are identical to the Python models for seamless JSON deserialization.
 */

// ---------------------------------------------------------------------------
// Limit Up (涨停)
// ---------------------------------------------------------------------------

export interface LimitUpStock {
  code: string;
  name: string;
  change_pct: number;
  seal_amount: number;
  max_board: number;
  consecutive_boards: number;
  sector: string;
  broken_count: number;
  first_seal_time: string;
  last_seal_time: string;
  turnover_rate: number;
}

export interface BrokenStock {
  code: string;
  name: string;
  change_pct: number;
  broken_count: number;
  sector: string;
}

export interface LimitDownStock {
  code: string;
  name: string;
  change_pct: number;
  sector: string;
}

export interface ConsecutiveBoard {
  board_count: number;
  stocks: Array<Record<string, string>>;
}

export interface SectorCorrelation {
  name: string;
  count: number;
  stocks: string[];
}

export interface SealStrength {
  code: string;
  name: string;
  seal_amount: number;
  broken_count: number;
  score: number;
}

export interface LimitUpAnalysis {
  consecutive_boards: ConsecutiveBoard[];
  sector_correlation: SectorCorrelation[];
  seal_strength_ranking: SealStrength[];
  summary: string;
}

export interface LimitUpResponse {
  date: string;
  status: string;
  limit_up_pool: LimitUpStock[];
  broken_pool: BrokenStock[];
  limit_down_pool: LimitDownStock[];
  analysis: LimitUpAnalysis | null;
}

// ---------------------------------------------------------------------------
// Dragon Tiger (龙虎榜)
// ---------------------------------------------------------------------------

export interface LhbDetail {
  code: string;
  name: string;
  reason: string;
  close_price: number;
  change_pct: number;
  net_buy_amount: number;
  buy_amount: number;
  sell_amount: number;
  list_date: string;
}

export interface Institutional {
  inst_code: string;
  inst_name: string;
  buy_amount: number;
  sell_amount: number;
  net_amount: number;
}

export interface Broker {
  broker_name: string;
  buy_amount: number;
  sell_amount: number;
  net_amount: number;
}

export interface HotMoney {
  broker: string;
  buy_targets: string[];
  sell_targets: string[];
  net_direction: string;
}

export interface DragonTigerResponse {
  date: string;
  status: string;
  detail: LhbDetail[];
  institutional: Institutional[];
  brokers: Broker[];
  hot_money: HotMoney[];
  summary: string;
}

// ---------------------------------------------------------------------------
// Fund Flow (资金流向)
// ---------------------------------------------------------------------------

export interface MarketFundFlow {
  date: string;
  main_net: number;
  main_pct: number;
  huge_net: number;
  large_net: number;
  medium_net: number;
  small_net: number;
}

export interface SectorFundFlow {
  name: string;
  change_pct: number;
  main_net: number;
  main_pct: number;
  huge_net: number;
  large_net: number;
  medium_net: number;
  small_net: number;
}

export interface FundFlowTrend {
  direction: string;
  momentum: string;
  large_vs_retail_divergence: boolean;
  lookback_rows: number;
  avg_main_net: number;
}

export interface FundFlowResponse {
  date: string;
  status: string;
  market_flow: MarketFundFlow[];
  sector_flow: SectorFundFlow[];
  trend: FundFlowTrend | null;
  summary: string;
}

// ---------------------------------------------------------------------------
// Risk Alert (风险提示)
// ---------------------------------------------------------------------------

export interface StStock {
  代码: string;
  名称: string;
  最新价: number;
  涨跌幅: number;
}

export interface RiskAlertData {
  st_stocks: StStock[];
  suspended_stocks: Record<string, unknown>[];
  abnormal_volatility: Record<string, unknown>[];
  capital_flight: Record<string, unknown>[];
  high_position_risks: Record<string, unknown>[];
  summary: string;
}

export interface RiskAlertResponse {
  date: string;
  status: string;
  data: RiskAlertData;
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

export interface AvailableDates {
  dates: string[];
}

export interface HealthStatus {
  status: string;
  db_path: string;
  latest_dates: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Invest SOP (投资SOP)
// ---------------------------------------------------------------------------

export interface InvestHolding {
  id: number;
  code: string;
  name: string | null;
  sector: string | null;
  entry_price: number | null;
  current_price: number | null;
  stop_loss_logic: number | null;
  stop_loss_technical: number | null;
  stop_loss_hard: number | null;
  target_price: number | null;
  position_pct: number | null;
  entry_date: string | null;
  status: string | null;
  notes: string | null;
  updated_at: string | null;
}

export interface InvestHoldingCreate {
  code: string;
  name: string;
  sector: string;
  entry_price: number;
  stop_loss_logic?: number;
  stop_loss_technical?: number;
  stop_loss_hard?: number;
  target_price?: number;
  position_pct?: number;
}

export interface InvestHoldingUpdatePrice {
  current_price: number;
}

export interface InvestHoldingUpdateStoploss {
  stop_loss_logic?: number;
  stop_loss_technical?: number;
  stop_loss_hard?: number;
}

export interface InvestOverseasData {
  date: string;
  sp500_pct: number | null;
  nasdaq_pct: number | null;
  dow_pct: number | null;
  us_10y: number | null;
  us_10y_change_bp: number | null;
  vix: number | null;
  us_vix: number | null;
  a50_pct: number | null;
  usd_cny: number | null;
}

export interface InvestSupplyChainRecord {
  id: number;
  date: string;
  sector: string;
  metric_name: string;
  value: number | null;
  unit: string | null;
  source: string | null;
}

export interface InvestFuturesData {
  date: string;
  if_pct: number | null;
  ic_pct: number | null;
  im_pct: number | null;
  if_basis: number | null;
  ic_basis: number | null;
  northbound_net: number | null;
  margin_balance: number | null;
  put_call_ratio: number | null;
}

export interface InvestCycleAssessment {
  id: number;
  sector: string;
  cycle_position: string | null;
  crowding_score: number | null;
  assessment_date: string | null;
  notes: string | null;
}

export interface InvestEventRecord {
  id: number;
  date: string;
  event_name: string;
  affected_sector: string | null;
  impact_direction: string | null;
  severity: string | null;
}

export interface InvestHistoryPoint {
  date: string;
  value: number;
}

export interface InvestVixHistoryPoint {
  date: string;
  vix: number | null;
  us_vix: number | null;
}

export interface InvestOverviewResponse {
  date: string;
  overseas: InvestOverseasData | null;
  futures: InvestFuturesData | null;
  events: InvestEventRecord[];
}

export interface InvestReportInfo {
  date: string;
  type: string;
  filename: string;
}

export interface InvestReportContent {
  type: string;
  content: string;
}

export interface InvestReportResponse {
  date: string;
  reports: InvestReportContent[];
}
