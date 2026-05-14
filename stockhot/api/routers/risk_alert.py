"""Risk-alert analysis API endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from stockhot.api.db import get_analysis_result, get_daily_data

router = APIRouter(prefix="/api/risk-alert", tags=["risk-alert"])


@router.get("/{date}")
async def risk_alert_analysis(date: str) -> dict:
    """Return full risk alert analysis for a given date."""
    daily = await get_daily_data(date)
    raw = daily.get("risk_alert_raw")

    if not raw:
        return {
            "date": date,
            "status": "no_data",
            "data": {
                "st_stocks": [],
                "suspended_stocks": [],
                "abnormal_volatility": [],
                "capital_flight": [],
                "high_position_risks": [],
                "summary": "",
            },
        }

    analysis = await get_analysis_result(date, "risk_alert")
    summary = analysis.get("summary", "") if analysis else ""

    return {
        "date": date,
        "status": "ok",
        "data": {
            "st_stocks": raw.get("st_stocks", []),
            "suspended_stocks": raw.get("suspended_stocks", []),
            "abnormal_volatility": raw.get("abnormal_volatility", []),
            "capital_flight": raw.get("capital_flight", []),
            "high_position_risks": raw.get("high_position_risks", []),
            "summary": summary,
        },
    }
