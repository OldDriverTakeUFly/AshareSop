"""Risk alert module for StockHot-CN.

Detects and aggregates risk signals from multiple dimensions:
ST stocks, suspended stocks, abnormal volatility (dragon-tiger board),
sustained capital outflows, and high-position limit-up risks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import akshare as ak

from stockhot.core.logging import logger
from stockhot.core.rate_limiter import safe_akshare_call
from stockhot.core.utils import safe_float, safe_text
from stockhot.storage.database import (
    get_daily_data,
    save_analysis_result,
    save_daily_data,
)

VOLATILITY_REASONS: list[str] = [
    "日涨幅偏离值达7%",
    "日跌幅偏离值达7%",
    "日换手率达到20%",
    "连续三个交易日涨幅偏离值累计达20%",
    "连续三个交易日跌幅偏离值累计达20%",
]

HIGH_POSITION_THRESHOLD: int = 3

ANALYSIS_TYPE: str = "risk_alert"


def fetch_st_stocks() -> list[dict]:
    """Fetch ST stock list via AkShare.

    Returns a list of dicts with keys: 代码, 名称, 最新价, 涨跌幅.
    """
    df = safe_akshare_call(ak.stock_zh_a_st_em)
    if df is None or df.empty:
        logger.info("ST stocks: 无数据")
        return []

    rows: list[dict] = []
    for _, row in df.iterrows():
        rows.append(
            {
                "代码": safe_text(row.get("代码")),
                "名称": safe_text(row.get("名称")),
                "最新价": safe_float(row.get("最新价")),
                "涨跌幅": safe_float(row.get("涨跌幅")),
            }
        )
    logger.info(f"ST stocks: 获取 {len(rows)} 只")
    return rows


def fetch_suspended_stocks() -> list[dict]:
    """Fetch suspended stock list via AkShare.

    Returns a list of dicts with keys: 代码, 名称.
    """
    df = safe_akshare_call(ak.stock_zh_a_stop_em)
    if df is None or df.empty:
        logger.info("Suspended stocks: 无数据")
        return []

    rows: list[dict] = []
    for _, row in df.iterrows():
        rows.append(
            {
                "代码": safe_text(row.get("代码")),
                "名称": safe_text(row.get("名称")),
            }
        )
    logger.info(f"Suspended stocks: 获取 {len(rows)} 只")
    return rows


def detect_abnormal_volatility(lhb_detail: list[dict]) -> list[dict]:
    """Filter dragon-tiger board entries whose 上榜原因 matches volatility keywords.

    A match is declared when *any* string in :data:`VOLATILITY_REASONS`
    is a substring of the entry's ``上榜原因`` field.
    """
    if not lhb_detail:
        return []

    flagged: list[dict] = []
    for entry in lhb_detail:
        reason = safe_text(entry.get("上榜原因"))
        if not reason:
            continue
        if any(keyword in reason for keyword in VOLATILITY_REASONS):
            flagged.append(entry)
    return flagged


def detect_capital_flight(sector_fund_flow: list[dict]) -> list[dict]:
    """Flag sectors with sustained net capital outflows (net_inflow < 0)."""
    if not sector_fund_flow:
        return []

    flagged: list[dict] = []
    for entry in sector_fund_flow:
        net_inflow = safe_float(entry.get("net_inflow"))
        if net_inflow < 0:
            flagged.append(entry)
    return flagged


def detect_high_position_risks(limit_up_pool: list[dict]) -> list[dict]:
    """Flag stocks with consecutive limit-up boards above the threshold.

    Threshold is :data:`HIGH_POSITION_THRESHOLD` (default 3).
    """
    if not limit_up_pool:
        return []

    flagged: list[dict] = []
    for entry in limit_up_pool:
        boards = safe_float(entry.get("consecutive_boards"), default=0.0)
        if boards > HIGH_POSITION_THRESHOLD:
            flagged.append(entry)
    return flagged


def generate_summary(
    st_stocks: list[dict],
    abnormal: list[dict],
    flight: list[dict],
    high_pos: list[dict],
) -> str:
    """Generate a pure statistical summary of all risk signals."""
    parts: list[str] = []

    parts.append(f"ST股票: {len(st_stocks)} 只")

    if abnormal:
        parts.append(f"异常波动: {len(abnormal)} 只")

    if flight:
        parts.append(f"资金出逃板块: {len(flight)} 个")

    if high_pos:
        parts.append(f"高位连板风险: {len(high_pos)} 只")

    total = len(st_stocks) + len(abnormal) + len(flight) + len(high_pos)
    if total == 0:
        return "风险提示: 当前未检出显著风险信号。"

    header = f"风险提示: 共检出 {total} 项风险信号。"
    return header + " " + "；".join(parts) + "。"


def run_risk_alert_analysis(date: str | None = None) -> dict:
    """Run the full risk alert pipeline for *date*.

    1. Fetches ST / suspended stocks via AkShare.
    2. Reads cross-module data (dragon-tiger, fund-flow, limit-up) from DB.
    3. Runs all detectors.
    4. Persists results and returns a summary dict.
    """
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    logger.info(f"Risk alert analysis: {target_date}")

    st_stocks = fetch_st_stocks()
    suspended_stocks = fetch_suspended_stocks()

    market_data = get_daily_data(target_date)
    lhb_detail: list[dict] = market_data.get("lhb_detail") or []
    sector_fund_flow: list[dict] = market_data.get("sector_fund_flow") or []
    limit_up_pool: list[dict] = market_data.get("limit_up_pool") or []

    abnormal = detect_abnormal_volatility(lhb_detail)
    flight = detect_capital_flight(sector_fund_flow)
    high_pos = detect_high_position_risks(limit_up_pool)

    summary = generate_summary(st_stocks, abnormal, flight, high_pos)

    data: dict[str, Any] = {
        "st_stocks": st_stocks,
        "suspended_stocks": suspended_stocks,
        "abnormal_volatility": abnormal,
        "capital_flight": flight,
        "high_position_risks": high_pos,
        "summary": summary,
    }

    save_daily_data({"date": target_date, "risk_alert_raw": data})
    save_analysis_result(target_date, ANALYSIS_TYPE, data)

    logger.info(f"Risk alert analysis done: {summary}")

    return {
        "date": target_date,
        "status": "success",
        "data": data,
    }
