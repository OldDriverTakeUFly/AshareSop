"""TypedDict models for StockHot-CN data structures.

Documents the dict shapes produced by data-clients and consumed
by analysis / storage / report modules.  These are for documentation
only — no runtime validation is intended.
"""

from typing import TypedDict


class StockData(TypedDict, total=False):
    """Individual stock data from market APIs.

    Minimal fields (eastmoney): code, name, price, change_pct, volume, amount.
    Extended fields (akshare_sina): turnover_rate, total_market_value,
    circulating_market_value.
    """

    code: str
    name: str
    price: float
    change_pct: float
    volume: float
    amount: float
    turnover_rate: float | None
    total_market_value: float | None
    circulating_market_value: float | None


class SectorData(TypedDict, total=False):
    """Sector / industry board data.

    Minimal fields (eastmoney): name, change_pct, volume, turnover_rate.
    Extended fields (akshare_sina / ths): company_count, amount,
    rising_count, falling_count, flat_count, leader_stock.
    """

    name: str
    change_pct: float
    volume: float
    amount: float
    turnover_rate: float
    company_count: int
    rising_count: int
    falling_count: int
    flat_count: int
    leader_stock: str


class FundFlowData(TypedDict, total=False):
    """Fund flow data for sectors / stocks.

    Minimal fields (eastmoney): code, name, net_inflow, inflow_rate.
    Extended fields (ths / akshare_sina normalization): change_pct,
    board_change_pct, inflow, outflow, leader_stock, leader_change_pct,
    category, source.
    """

    code: str
    name: str
    net_inflow: float
    inflow_rate: float
    change_pct: float
    board_change_pct: float | None
    inflow: float
    outflow: float
    leader_stock: str
    leader_change_pct: float | None
    category: str  # "industry" or "concept"
    source: str  # "ths", "eastmoney", etc.


class CatalystData(TypedDict, total=False):
    """A single catalyst event in an evidence pack."""

    date: str
    source: str
    tier: str
    title: str
    summary: str


class TargetData(TypedDict, total=False):
    """A representative A-share company target in an evidence pack."""

    name: str
    code: str
    reason: str
    source: str
    tier: str


class EvidencePack(TypedDict, total=False):
    """Curated public evidence pack for a theme.

    See ``stockhot.research_report.evidence`` for concrete instances.
    """

    theme: str
    aliases: list[str]
    headline: str
    source_tiers: dict[str, list[str]]
    catalysts: list[CatalystData]
    industry_context: list[str]
    segments: list[str]
    milestones: list[str]
    targets: list[TargetData]
