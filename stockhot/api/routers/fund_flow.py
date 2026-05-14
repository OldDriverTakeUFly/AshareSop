"""Fund-flow analysis API endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from stockhot.api.db import get_analysis_result, get_daily_data

router = APIRouter(prefix="/api/fund-flow", tags=["fund-flow"])


@router.get("/{date}")
async def fund_flow_analysis(date: str) -> dict:
    """Return full fund flow analysis for a given date."""
    daily = await get_daily_data(date)
    market_flow = daily.get("fund_flow_market", [])
    sector_flow = daily.get("fund_flow_sector", [])

    if not market_flow and not sector_flow:
        return {
            "date": date,
            "status": "no_data",
            "market_flow": [],
            "sector_flow": [],
            "summary": "",
        }

    trend_raw = await get_analysis_result(date, "fund_flow_trend")
    if trend_raw:
        # Production data wraps trend indicators inside a "trend" key
        # Handle both nested {"trend": {...}, "summary": "..."} and flat {...}
        trend = trend_raw.get("trend", trend_raw)
        summary = trend_raw.get("summary", "")
    else:
        trend = None
        summary = ""

    return {
        "date": date,
        "status": "ok",
        "market_flow": market_flow,
        "sector_flow": sector_flow,
        "trend": trend,
        "summary": summary,
    }


@router.get("/{date}/market")
async def fund_flow_market(date: str) -> dict:
    """Return market-wide fund flow time series for chart rendering."""
    daily = await get_daily_data(date)
    market_flow = daily.get("fund_flow_market", [])
    return {"date": date, "data": market_flow}


@router.get("/{date}/sectors")
async def fund_flow_sectors(date: str) -> dict:
    """Return sector fund flow ranking sorted by main_net descending."""
    daily = await get_daily_data(date)
    sector_flow = daily.get("fund_flow_sector", [])
    sorted_sectors = sorted(sector_flow, key=lambda x: x.get("main_net", 0), reverse=True)
    return {"date": date, "data": sorted_sectors}
