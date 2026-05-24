"""Pydantic schemas for StockHot-CN API response models.

Field names match what the analysis modules actually output to the database.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Limit Up (涨停)
# ---------------------------------------------------------------------------


class LimitUpStock(BaseModel):
    code: str
    name: str
    change_pct: float
    seal_amount: float
    max_board: float
    consecutive_boards: float
    sector: str
    broken_count: float
    first_seal_time: str
    last_seal_time: str
    turnover_rate: float


class BrokenStock(BaseModel):
    code: str
    name: str
    change_pct: float
    broken_count: float
    sector: str


class LimitDownStock(BaseModel):
    code: str
    name: str
    change_pct: float
    sector: str


class ConsecutiveBoard(BaseModel):
    board_count: int
    stocks: list[dict[str, str]]


class SectorCorrelation(BaseModel):
    name: str
    count: int
    stocks: list[str]


class SealStrength(BaseModel):
    code: str
    name: str
    seal_amount: float
    broken_count: float
    score: float


class LimitUpAnalysis(BaseModel):
    consecutive_boards: list[ConsecutiveBoard]
    sector_correlation: list[SectorCorrelation]
    seal_strength_ranking: list[SealStrength]
    summary: str


class LimitUpResponse(BaseModel):
    date: str
    status: str
    limit_up_pool: list[LimitUpStock]
    broken_pool: list[BrokenStock]
    limit_down_pool: list[LimitDownStock]
    analysis: Optional[LimitUpAnalysis] = None


# ---------------------------------------------------------------------------
# Dragon Tiger (龙虎榜)
# ---------------------------------------------------------------------------


class LhbDetail(BaseModel):
    code: str
    name: str
    reason: str
    close_price: float
    change_pct: float
    net_buy_amount: float
    buy_amount: float
    sell_amount: float
    list_date: str


class Institutional(BaseModel):
    inst_code: str
    inst_name: str
    buy_amount: float
    sell_amount: float
    net_amount: float


class Broker(BaseModel):
    broker_name: str
    buy_amount: float
    sell_amount: float
    net_amount: float


class HotMoney(BaseModel):
    broker: str
    buy_targets: list[str]
    sell_targets: list[str]
    net_direction: str


class DragonTigerResponse(BaseModel):
    date: str
    status: str
    detail: list[LhbDetail]
    institutional: list[Institutional]
    brokers: list[Broker]
    hot_money: list[HotMoney]
    summary: str


# ---------------------------------------------------------------------------
# Fund Flow (资金流向)
# ---------------------------------------------------------------------------


class MarketFundFlow(BaseModel):
    date: str
    main_net: float
    main_pct: float
    huge_net: float
    large_net: float
    medium_net: float
    small_net: float


class SectorFundFlow(BaseModel):
    name: str
    change_pct: float
    main_net: float
    main_pct: float
    huge_net: float
    large_net: float
    medium_net: float
    small_net: float


class FundFlowTrend(BaseModel):
    direction: str
    momentum: str
    large_vs_retail_divergence: bool
    lookback_rows: int
    avg_main_net: float


class FundFlowResponse(BaseModel):
    date: str
    status: str
    market_flow: list[MarketFundFlow]
    sector_flow: list[SectorFundFlow]
    trend: Optional[FundFlowTrend] = None
    summary: str


# ---------------------------------------------------------------------------
# Risk Alert (风险提示)
# ---------------------------------------------------------------------------


class StStock(BaseModel):
    代码: str
    名称: str
    最新价: float
    涨跌幅: float


class RiskAlertData(BaseModel):
    st_stocks: list[StStock]
    suspended_stocks: list[dict]
    abnormal_volatility: list[dict]
    capital_flight: list[dict]
    high_position_risks: list[dict]
    summary: str


class RiskAlertResponse(BaseModel):
    date: str
    status: str
    data: RiskAlertData


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


class AvailableDates(BaseModel):
    dates: list[str]


class HealthStatus(BaseModel):
    status: str
    db_path: str
    latest_dates: dict[str, str]


# ---------------------------------------------------------------------------
# Invest SOP (投资SOP)
# ---------------------------------------------------------------------------


class Holding(BaseModel):
    id: int
    code: str
    name: Optional[str] = None
    sector: Optional[str] = None
    entry_price: Optional[float] = None
    current_price: Optional[float] = None
    stop_loss_logic: Optional[float] = None
    stop_loss_technical: Optional[float] = None
    stop_loss_hard: Optional[float] = None
    target_price: Optional[float] = None
    position_pct: Optional[float] = None
    entry_date: Optional[str] = None
    status: Optional[str] = None
    quantity: int = 0
    avg_cost: Optional[float] = None
    notes: Optional[str] = None
    updated_at: Optional[str] = None


class HoldingCreateRequest(BaseModel):
    code: str
    name: str
    sector: str
    entry_price: float
    stop_loss_logic: Optional[float] = None
    stop_loss_technical: Optional[float] = None
    stop_loss_hard: Optional[float] = None
    target_price: Optional[float] = None
    position_pct: Optional[float] = None


class HoldingUpdatePriceRequest(BaseModel):
    current_price: float


class HoldingUpdateStoplossRequest(BaseModel):
    stop_loss_logic: Optional[float] = None
    stop_loss_technical: Optional[float] = None
    stop_loss_hard: Optional[float] = None


class HoldingCreateSimple(BaseModel):
    code: str
    quantity: int
    entry_price: Optional[float] = None  # If not provided, batch job fills it


class HoldingAdjustRequest(BaseModel):
    type: str  # "buy" or "sell"
    quantity: int
    price: float
    notes: Optional[str] = None


class HoldingTransaction(BaseModel):
    id: int
    holding_id: int
    type: str
    quantity: int
    price: float
    date: str
    notes: Optional[str] = None
    created_at: Optional[str] = None


class SectorRule(BaseModel):
    sector: str
    stop_loss_pct: float
    target_pct: float
    updated_at: Optional[str] = None


class SectorRuleUpdate(BaseModel):
    stop_loss_pct: Optional[float] = None
    target_pct: Optional[float] = None


class OverseasMarketData(BaseModel):
    date: str
    sp500_pct: Optional[float] = None
    nasdaq_pct: Optional[float] = None
    dow_pct: Optional[float] = None
    us_10y: Optional[float] = None
    us_10y_change_bp: Optional[float] = None
    vix: Optional[float] = None
    us_vix: Optional[float] = None
    a50_pct: Optional[float] = None
    usd_cny: Optional[float] = None


class SupplyChainRecord(BaseModel):
    id: int
    date: str
    sector: str
    metric_name: str
    value: Optional[float] = None
    unit: Optional[str] = None
    source: Optional[str] = None


class FuturesData(BaseModel):
    date: str
    if_pct: Optional[float] = None
    ic_pct: Optional[float] = None
    im_pct: Optional[float] = None
    if_basis: Optional[float] = None
    ic_basis: Optional[float] = None
    northbound_net: Optional[float] = None
    margin_balance: Optional[float] = None
    put_call_ratio: Optional[float] = None


class EventRecord(BaseModel):
    id: int
    date: str
    event_name: str
    affected_sector: Optional[str] = None
    impact_direction: Optional[str] = None
    severity: Optional[str] = None


class CycleAssessment(BaseModel):
    id: int
    sector: str
    cycle_position: Optional[str] = None
    crowding_score: Optional[int] = None
    assessment_date: Optional[str] = None
    notes: Optional[str] = None


class HistoryPoint(BaseModel):
    date: str
    value: float


class ReportInfo(BaseModel):
    date: str
    type: str
    filename: str
