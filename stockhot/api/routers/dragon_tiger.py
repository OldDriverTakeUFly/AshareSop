from __future__ import annotations

from fastapi import APIRouter

from stockhot.api.config import settings
from stockhot.api.db import get_analysis_result, get_daily_data
from stockhot.api.schemas import DragonTigerResponse

router = APIRouter(prefix="/api/dragon-tiger", tags=["dragon-tiger"])


@router.get("/{date}", response_model=DragonTigerResponse)
async def get_dragon_tiger(date: str):
    daily = await get_daily_data(date, settings.DB_PATH)
    analysis_raw = await get_analysis_result(date, "dragon_tiger", settings.DB_PATH)

    detail = daily.get("dragon_tiger_detail", [])

    has_data = detail or analysis_raw

    if not has_data:
        return DragonTigerResponse(
            date=date,
            status="no_data",
            detail=[],
            institutional=[],
            brokers=[],
            hot_money=[],
            summary="",
        )

    institutional = analysis_raw.get("institutional", []) if analysis_raw else []
    brokers = analysis_raw.get("brokers", []) if analysis_raw else []
    hot_money = analysis_raw.get("hot_money", []) if analysis_raw else []
    summary = analysis_raw.get("summary", "") if analysis_raw else ""

    return DragonTigerResponse(
        date=date,
        status="ok",
        detail=detail,
        institutional=institutional,
        brokers=brokers,
        hot_money=hot_money,
        summary=summary,
    )


@router.get("/{date}/summary")
async def get_dragon_tiger_summary(date: str):
    analysis_raw = await get_analysis_result(date, "dragon_tiger", settings.DB_PATH)
    summary = analysis_raw.get("summary", "暂无数据") if analysis_raw else "暂无数据"
    return {"date": date, "summary": summary}
