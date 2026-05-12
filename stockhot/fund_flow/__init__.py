"""Fund flow trends module for StockHot-CN.

Fetches market-wide, sector, and individual fund-flow data from AkShare,
analyses multi-day trends (direction, momentum, large-vs-retail divergence),
and persists results via the storage layer.
"""

from __future__ import annotations

import akshare as ak

from stockhot.core.logging import logger
from stockhot.core.rate_limiter import safe_akshare_call
from stockhot.core.utils import (
    from_akshare_date,
    safe_float,
    safe_text,
)
from stockhot.storage.database import save_analysis_result, save_daily_data



def fetch_market_fund_flow() -> list[dict]:
    """Fetch recent market-wide fund flow history via AkShare.

    Uses ``ak.stock_market_fund_flow()`` which takes no date parameter and
    returns a history table.  Fields extracted (AkShare Chinese column names):

    - 日期  → date
    - 主力净流入-净额 → main_net
    - 主力净流入-净流入占比 → main_pct
    - 超大单净流入-净额 → huge_net
    - 大单净流入-净额   → large_net
    - 中单净流入-净额   → medium_net
    - 小单净流入-净额   → small_net

    Returns a list of dicts with normalised field names.
    """
    df = safe_akshare_call(ak.stock_market_fund_flow)
    if df is None or df.empty:
        logger.warning("fetch_market_fund_flow: empty result")
        return []

    rows: list[dict] = []
    for _, row in df.iterrows():
        raw_date = safe_text(row.get("日期"))
        if not raw_date:
            continue
        rows.append(
            {
                "date": from_akshare_date(raw_date) if len(raw_date) == 8 else raw_date,
                "main_net": safe_float(row.get("主力净流入-净额")),
                "main_pct": safe_float(row.get("主力净流入-净流入占比")),
                "huge_net": safe_float(row.get("超大单净流入-净额")),
                "large_net": safe_float(row.get("大单净流入-净额")),
                "medium_net": safe_float(row.get("中单净流入-净额")),
                "small_net": safe_float(row.get("小单净流入-净额")),
            }
        )
    logger.info(f"fetch_market_fund_flow: {len(rows)} rows")
    return rows


def fetch_sector_fund_flow(
    indicator: str = "今日",
    sector_type: str = "行业",
) -> list[dict]:
    """Fetch sector-level fund flow ranking via AkShare.

    Uses ``ak.stock_sector_fund_flow_rank(indicator, sector_type)``.

    Fields extracted:
    - 名称       → name
    - 今日涨跌幅 → change_pct
    - 主力净流入-净额 → main_net
    - 主力净流入-净流入占比 → main_pct
    - 超大单净流入-净额 → huge_net
    - 大单净流入-净额   → large_net
    - 中单净流入-净额   → medium_net
    - 小单净流入-净额   → small_net
    """
    df = safe_akshare_call(
        ak.stock_sector_fund_flow_rank,
        indicator=indicator,
        sector_type=sector_type,
    )
    if df is None or df.empty:
        logger.warning("fetch_sector_fund_flow: empty result")
        return []

    rows: list[dict] = []
    for _, row in df.iterrows():
        name = safe_text(row.get("名称"))
        if not name:
            continue
        rows.append(
            {
                "name": name,
                "change_pct": safe_float(row.get("今日涨跌幅")),
                "main_net": safe_float(row.get("主力净流入-净额")),
                "main_pct": safe_float(row.get("主力净流入-净流入占比")),
                "huge_net": safe_float(row.get("超大单净流入-净额")),
                "large_net": safe_float(row.get("大单净流入-净额")),
                "medium_net": safe_float(row.get("中单净流入-净额")),
                "small_net": safe_float(row.get("小单净流入-净额")),
            }
        )
    logger.info(f"fetch_sector_fund_flow: {len(rows)} rows")
    return rows


def fetch_individual_fund_flow(
    stock: str,
    market: str = "sh",
) -> list[dict]:
    """Fetch individual stock fund flow history via AkShare.

    Uses ``ak.stock_individual_fund_flow(stock, market)``.

    Fields extracted:
    - 日期   → date
    - 收盘价 → close_price
    - 涨跌幅 → change_pct
    - 主力净流入-净额 → main_net
    - 超大单净流入-净额 → huge_net
    - 大单净流入-净额   → large_net
    - 中单净流入-净额   → medium_net
    - 小单净流入-净额   → small_net
    """
    df = safe_akshare_call(
        ak.stock_individual_fund_flow,
        stock=stock,
        market=market,
    )
    if df is None or df.empty:
        logger.warning("fetch_individual_fund_flow: empty result")
        return []

    rows: list[dict] = []
    for _, row in df.iterrows():
        raw_date = safe_text(row.get("日期"))
        if not raw_date:
            continue
        rows.append(
            {
                "date": from_akshare_date(raw_date) if len(raw_date) == 8 else raw_date,
                "close_price": safe_float(row.get("收盘价")),
                "change_pct": safe_float(row.get("涨跌幅")),
                "main_net": safe_float(row.get("主力净流入-净额")),
                "huge_net": safe_float(row.get("超大单净流入-净额")),
                "large_net": safe_float(row.get("大单净流入-净额")),
                "medium_net": safe_float(row.get("中单净流入-净额")),
                "small_net": safe_float(row.get("小单净流入-净额")),
            }
        )
    logger.info(f"fetch_individual_fund_flow({stock}): {len(rows)} rows")
    return rows


def analyze_fund_flow_trend(
    market_flow: list[dict],
    lookback: int = 5,
) -> dict:
    """Analyse multi-day fund flow trend from market-wide data.

    Looks at the most recent *lookback* rows of ``market_flow`` and computes:

    - **direction**: 持续流入 / 持续流出 / 震荡
      (all positive → inflow, all negative → outflow, else oscillation)
    - **momentum**: 加速 / 减速 / 稳定
      (increasing absolute magnitude → accelerating, decreasing → decelerating)
    - **large_vs_retail_divergence**: bool
      True when large orders (huge + large) and retail orders (medium + small)
      have opposite signs in the latest row.

    Returns a dict with keys: direction, momentum, large_vs_retail_divergence,
    lookback_rows, avg_main_net.
    """
    if not market_flow:
        return {
            "direction": "无数据",
            "momentum": "无数据",
            "large_vs_retail_divergence": False,
            "lookback_rows": 0,
            "avg_main_net": 0.0,
        }

    recent = market_flow[-lookback:]
    main_nets = [safe_float(r.get("main_net")) for r in recent]

    if all(v > 0 for v in main_nets):
        direction = "持续流入"
    elif all(v < 0 for v in main_nets):
        direction = "持续流出"
    else:
        direction = "震荡"

    abs_nets = [abs(v) for v in main_nets]
    if len(abs_nets) >= 2:
        diffs = [abs_nets[i + 1] - abs_nets[i] for i in range(len(abs_nets) - 1)]
        if all(d > 0 for d in diffs):
            momentum = "加速"
        elif all(d < 0 for d in diffs):
            momentum = "减速"
        else:
            momentum = "稳定"
    else:
        momentum = "稳定"

    latest = recent[-1]
    large_total = safe_float(latest.get("huge_net")) + safe_float(latest.get("large_net"))
    retail_total = safe_float(latest.get("medium_net")) + safe_float(latest.get("small_net"))
    divergence = (large_total * retail_total) < 0

    avg_main = sum(main_nets) / len(main_nets) if main_nets else 0.0

    return {
        "direction": direction,
        "momentum": momentum,
        "large_vs_retail_divergence": divergence,
        "lookback_rows": len(recent),
        "avg_main_net": round(avg_main, 4),
    }


def generate_summary(
    market_flow: list[dict],
    sector_flow: list[dict],
    trend: dict,
) -> str:
    """Generate a human-readable statistical summary string.

    Pure text — no AI/LLM involved.
    """
    if not market_flow:
        return "暂无市场资金流向数据。"

    latest = market_flow[-1]
    main_net = safe_float(latest.get("main_net"))
    direction_label = "净流入" if main_net >= 0 else "净流出"

    parts = [
        f"最近一日主力{direction_label}{abs(main_net):.2f}亿。",
        f"趋势判断：{trend.get('direction', '未知')}，{trend.get('momentum', '未知')}。",
    ]

    if trend.get("large_vs_retail_divergence"):
        parts.append("大单与小单方向背离。")

    if sector_flow:
        top = sector_flow[0]
        name = safe_text(top.get("name"))
        top_net = safe_float(top.get("main_net"))
        parts.append(f"行业资金流入首位：{name}，主力净流入{top_net:.2f}亿。")

    return "".join(parts)


def run_fund_flow_analysis(date: str) -> dict:
    """Main entry point for the fund flow trends module.

    1. Fetches market-wide and sector fund flow data.
    2. Analyses trends.
    3. Saves raw data and analysis result to DB.
    4. Returns ``{date, status, data}``.
    """
    logger.info(f"run_fund_flow_analysis: date={date}")

    market_flow = fetch_market_fund_flow()
    sector_flow = fetch_sector_fund_flow()

    if not market_flow and not sector_flow:
        logger.warning("run_fund_flow_analysis: no data available")
        return {"date": date, "status": "no_data", "data": {}}

    trend = analyze_fund_flow_trend(market_flow)
    summary = generate_summary(market_flow, sector_flow, trend)

    data = {
        "market_flow": market_flow,
        "sector_flow": sector_flow,
        "trend": trend,
        "summary": summary,
    }

    save_daily_data({"date": date, "fund_flow_market": market_flow, "fund_flow_sector": sector_flow})

    save_analysis_result(date, "fund_flow_trend", {"trend": trend, "summary": summary})

    logger.info(f"run_fund_flow_analysis: done — {trend['direction']} / {trend['momentum']}")
    return {"date": date, "status": "success", "data": data}
