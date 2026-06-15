"""Data definitions for davis_analyzer — pure dataclasses, no logic."""

from dataclasses import dataclass, field


@dataclass
class StockInfo:
    ts_code: str
    name: str
    industry: str
    list_status: str
    is_cyclical: bool


@dataclass
class ValuationData:
    ts_code: str
    trade_date: str
    pe_ttm: float
    pb: float
    ps: float
    total_mv: float


@dataclass
class FinancialData:
    ts_code: str
    report_period: str
    revenue: float
    net_profit: float
    eps: float
    roe: float
    operating_cf: float
    total_debt: float
    total_assets: float
    yoy_revenue_growth: float = 0.0
    yoy_profit_growth: float = 0.0


@dataclass
class ProsperityScore:
    ts_code: str
    revenue_score: float
    profit_score: float
    slope_score: float
    duration_score: float
    composite_score: float
    delta_g: float
    relative_delta_g: float = 0.0


@dataclass
class DistressSignal:
    ts_code: str
    layer1_score: float
    layer2_score: float
    layer3_score: float
    total_score: float
    signals_detail: dict


@dataclass
class DavisDoubleScore:
    ts_code: str
    name: str
    valuation_score: float
    prosperity_score: float
    distress_score: float
    final_score: float
    rank: int
    trend_score: float = 0.0


@dataclass
class StockReport:
    ts_code: str
    name: str
    report_content: str


@dataclass
class PipelineResult:
    """Container for all pipeline intermediate data."""

    scores: list["DavisDoubleScore"]
    stock_infos: dict[str, "StockInfo"]
    valuation_data: dict[str, tuple]
    prosperity_scores: dict[str, "ProsperityScore"]
    distress_signals: dict[str, "DistressSignal"]
    financial_data: dict[str, list["FinancialData"]]
    trend_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class RescoredResult:
    ts_code: str
    name: str
    original_prosperity: float
    adjusted_prosperity: float
    original_distress: float
    adjusted_distress: float
    prosperity_adjustment: float
    distress_adjustment: float


@dataclass
class IndustryProsperityScore:
    industry: str
    stock_count: int
    avg_composite_score: float
    median_delta_g: float
    avg_revenue_score: float
    avg_profit_score: float
    avg_slope_score: float
    avg_duration_score: float
    stage: str
    ignition_count: int
    top_stock_codes: list[str]


@dataclass
class CatalystSignal:
    signal_type: str  # "roe_improving", "cashflow_positive", "debt_declining", "revenue_stabilizing"
    description: str
    strength: float  # 0-100


@dataclass
class InflectionAnalysis:
    ts_code: str
    stage: str  # "加速期" | "减速期" | "上升拐点" | "下降拐点"
    inflection_quarter: str | None  # e.g. "2024Q3" or None
    primary_driver: str  # e.g. "营收加速增长" or "营收企稳回升"
    catalysts: list[CatalystSignal]
    narrative: str


@dataclass
class ProsperityStockDetail:
    ts_code: str
    name: str
    industry: str
    prosperity_score: ProsperityScore
    stage: str
    is_ignition: bool
    risk_warnings: list[str]
    rank_in_industry: int
    ignition_reasons: list[str] = field(default_factory=list)
    inflection: InflectionAnalysis | None = None


@dataclass
class ProsperitySectorResult:
    industry_scores: list[IndustryProsperityScore]
    stock_details: dict[str, ProsperityStockDetail]
    stock_infos: dict[str, StockInfo]
    prosperity_scores: dict[str, ProsperityScore]
    financial_data: dict[str, list[FinancialData]]
    analysis_date: str
