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
