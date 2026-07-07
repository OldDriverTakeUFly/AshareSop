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
    yoy_revenue_growth: float | None = None
    yoy_profit_growth: float | None = None
    # Profitability-quality fields (added for margin-trend engine).
    # gross_profit is the gross profit (元); grossprofit_margin is the Tushare
    # percentage. rd_exp is R&D expense (元). All default None because older
    # cached fina_indicator payloads predate this fetch and lack the columns.
    gross_profit: float | None = None
    grossprofit_margin: float | None = None
    rd_exp: float | None = None


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
    # Supplementary factor signals (always-on from Step 7.6). All default to
    # empty so old persisted task files (pre-dating these fields) deserialize
    # cleanly — persistence.py reconstructs them with **kwargs and falls back
    # to {} when the key is absent.
    momentum_signals: dict[str, "MomentumSignal"] = field(default_factory=dict)
    dividend_signals: dict[str, "DividendSignal"] = field(default_factory=dict)
    forecast_signals: dict[str, "ForecastSignal"] = field(default_factory=dict)


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
    signal_type: str  # catalysts: "roe_improving", "cashflow_positive", "debt_declining", "revenue_stabilizing"
    # risks: "roe_declining", "cashflow_negative", "debt_rising", "growth_weakening"
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
    inflection_axis: str | None = None  # "revenue" | "profit" | "both" | None


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
    dupont_driver: str | None = None


@dataclass
class ProsperitySectorResult:
    industry_scores: list[IndustryProsperityScore]
    stock_details: dict[str, ProsperityStockDetail]
    stock_infos: dict[str, StockInfo]
    prosperity_scores: dict[str, ProsperityScore]
    financial_data: dict[str, list[FinancialData]]
    analysis_date: str


@dataclass
class ForecastSignal:
    """业绩预告 (earnings pre-announcement) leading-indicator result.

    Wraps a single most-relevant forecast row and a derived 0–100
    leading-indicator score. ``p_change_mid`` is the midpoint of the
    announced YoY net-profit change range.
    """

    ts_code: str
    ann_date: str  # YYYYMMDD disclosure date of the pre-announcement
    end_date: str  # report period the forecast covers, e.g. "20251231"
    type: str  # 预增/预减/续亏/扭亏/首亏/略增/略减 …
    p_change_min: float | None  # lower bound of YoY net-profit change (%)
    p_change_max: float | None  # upper bound of YoY net-profit change (%)
    p_change_mid: float | None  # midpoint, None when either bound missing
    leading_score: float  # 0–100 forward-looking prosperity score
    is_stale: bool  # True when ann_date older than FORECAST_STALE_DAYS


@dataclass
class HolderConcentration:
    """筹码集中度 (chip concentration) from holder-count trend.

    ``holder_counts`` and ``periods`` are chronologically ordered (oldest →
    newest). A declining holder count signals 筹码集中 / 主力收集 (bullish).
    """

    ts_code: str
    holder_counts: list[int]  # chronological, oldest first
    periods: list[str]  # matching report periods (end_date)
    concentration_score: float  # 0–100, higher = more concentrated
    trend: str  # "集中(动能增强)" | "分散(动能减弱)" | "数据不足"
    latest_chg_pct: float | None  # QoQ % change of the most recent period


@dataclass
class MomentumSignal:
    """Price-momentum + relative-strength (RS) result.

    Absolute momentum is the multi-window blended adjusted return; RS is the
    stock's return percentile within its industry over the longest window
    (how strong vs peers, not vs its own past). CANSLIM's "M" and "R" legs.
    """

    ts_code: str
    window_returns: dict[int, float]  # window_days → annualised return %
    absolute_momentum_score: float  # 0–100, blended multi-window
    rs_percentile: float | None  # 0–100 within industry (None if no peers)
    momentum_score: float  # 0–100 blend of absolute + RS
    data_sufficient: bool

    def __post_init__(self) -> None:
        # JSON round-trips coerce int keys to strings; coerce back so engine
        # consumers always get dict[int, float] regardless of input source.
        if self.window_returns and not isinstance(
            next(iter(self.window_returns)), int
        ):
            self.window_returns = {int(k): float(v) for k, v in self.window_returns.items()}


@dataclass
class DividendSignal:
    """红利 (dividend) factor result from payout history.

    Combines consecutive-year payout continuity with current indicated yield
    (cash_div / price). Supports the 红利型 domain in multi-factor-screening.
    """

    ts_code: str
    consecutive_years: int  # consecutive executed-payout years
    latest_yield_pct: float | None  # cash_div / price × 100, annual
    dividend_score: float  # 0–100 blend
    payout_years: list[str]  # report periods (end_date) of executed payouts
    data_sufficient: bool


@dataclass
class ForecastRevision:
    """一致预期修正 (analyst/management forecast revision) result.

    Detects the direction and magnitude of revisions to the same report
    period's earnings pre-announcement — an upward revision is a strong
    leading alpha signal (SUE-style). Built from two or more forecast rows
    covering the same end_date.
    """

    ts_code: str
    end_date: str  # report period the revisions target
    initial_mid: float | None  # first announced midpoint (%)
    revised_mid: float | None  # latest announced midpoint (%)
    revision_pp: float | None  # revised - initial, percentage points
    revision_direction: str  # "上调" | "下调" | "无修正" | "数据不足"
    revision_score: float  # 0–100, 50 = no revision
