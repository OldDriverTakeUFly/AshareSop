from __future__ import annotations

from fastapi import APIRouter

from stockhot.api.config import settings
from stockhot.api.db import get_analysis_result, get_daily_data
from stockhot.api.schemas import LimitUpAnalysis, LimitUpResponse

router = APIRouter(prefix="/api/limit-up", tags=["limit-up"])


@router.get("/{date}", response_model=LimitUpResponse)
async def get_limit_up(date: str):
    daily = await get_daily_data(date, settings.DB_PATH)
    analysis_raw = await get_analysis_result(date, "limit_up_analysis", settings.DB_PATH)

    limit_up_pool = daily.get("limit_up_pool", [])
    broken_pool = daily.get("broken_pool", [])
    limit_down_pool = daily.get("limit_down_pool", [])

    has_data = limit_up_pool or broken_pool or limit_down_pool or analysis_raw

    if not has_data:
        return LimitUpResponse(
            date=date,
            status="no_data",
            limit_up_pool=[],
            broken_pool=[],
            limit_down_pool=[],
        )

    analysis = LimitUpAnalysis(**analysis_raw) if analysis_raw else None

    return LimitUpResponse(
        date=date,
        status="ok",
        limit_up_pool=limit_up_pool,
        broken_pool=broken_pool,
        limit_down_pool=limit_down_pool,
        analysis=analysis,
    )


@router.get("/{date}/summary")
async def get_limit_up_summary(date: str):
    analysis_raw = await get_analysis_result(date, "limit_up_analysis", settings.DB_PATH)
    summary = analysis_raw.get("summary", "暂无数据") if analysis_raw else "暂无数据"
    return {"date": date, "summary": summary}
