"""Stock data models for StockHot-CN."""

from dataclasses import dataclass
from datetime import date


@dataclass
class Stock:
    code: str
    name: str
    price: float
    change_pct: float
    volume: int
    amount: float


@dataclass
class Sector:
    name: str
    change_pct: float
    volume: int
    turnover_rate: float


@dataclass
class FundFlow:
    code: str
    name: str
    net_inflow: float
    inflow_rate: float


@dataclass
class DailyData:
    trade_date: date
    gainers: list[Stock]
    losers: list[Stock]
    sectors: list[Sector]
    fund_flows: list[FundFlow]