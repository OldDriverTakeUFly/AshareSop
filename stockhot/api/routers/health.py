"""Health-check and date-list endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from stockhot.api.config import settings
from stockhot.api.db import get_available_dates, get_connection
from stockhot.api.schemas import AvailableDates, HealthStatus

router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthStatus)
async def health_check():
    """Return backend health including latest dates per data type."""
    db_exists = Path(settings.DB_PATH).exists()
    latest_dates: dict[str, str] = {}

    if db_exists:
        db = await get_connection(settings.DB_PATH)
        try:
            cursor = await db.execute(
                "SELECT data_type, MAX(trade_date) AS max_date "
                "FROM daily_data GROUP BY data_type"
            )
            async for row in cursor:
                latest_dates[row["data_type"]] = row["max_date"]
        finally:
            await db.close()

    return HealthStatus(
        status="ok",
        db_path=settings.DB_PATH,
        latest_dates=latest_dates,
    )


@router.get("/dates", response_model=AvailableDates)
async def list_dates():
    """Return all available trade dates in descending order."""
    dates = await get_available_dates(settings.DB_PATH)
    return AvailableDates(dates=dates)
