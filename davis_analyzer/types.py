"""Data definitions for davis_analyzer — pure dataclasses, no logic."""

from dataclasses import dataclass


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


@dataclass
class StockReport:
    ts_code: str
    name: str
    report_content: str
