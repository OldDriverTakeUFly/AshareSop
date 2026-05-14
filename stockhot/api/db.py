"""Async database read adapter for StockHot-CN API.

Uses aiosqlite for non-blocking database reads. All functions are read-only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

from stockhot.core.config import DB_PATH


async def get_connection(db_path: Path | str | None = None) -> aiosqlite.Connection:
    """Open an async SQLite connection with WAL mode.

    Parameters
    ----------
    db_path:
        Override the default DB_PATH. Useful for testing.
    """
    path = str(db_path or DB_PATH)
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def get_daily_data(
    date: str,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Get all daily data for a given date.

    Returns a dict with key ``date`` plus one key per data_type found.
    """
    db = await get_connection(db_path)
    try:
        cursor = await db.execute(
            "SELECT data_type, data_json FROM daily_data WHERE trade_date = ?",
            (date,),
        )
        result: dict[str, Any] = {"date": date}
        async for row in cursor:
            result[row["data_type"]] = json.loads(row["data_json"])
        return result
    finally:
        await db.close()


async def get_analysis_result(
    date: str,
    analysis_type: str,
    db_path: Path | str | None = None,
) -> dict[str, Any] | None:
    """Get a specific analysis result.

    Returns ``None`` when no matching row exists.
    """
    db = await get_connection(db_path)
    try:
        cursor = await db.execute(
            "SELECT result_json FROM analysis_results "
            "WHERE trade_date = ? AND analysis_type = ?",
            (date, analysis_type),
        )
        row = await cursor.fetchone()
        return json.loads(row["result_json"]) if row else None
    finally:
        await db.close()


async def get_available_dates(
    db_path: Path | str | None = None,
) -> list[str]:
    db = await get_connection(db_path)
    try:
        cursor = await db.execute(
            "SELECT DISTINCT trade_date FROM daily_data ORDER BY trade_date DESC"
        )
        return [row["trade_date"] async for row in cursor]
    finally:
        await db.close()


async def get_latest_date(
    db_path: Path | str | None = None,
) -> str | None:
    dates = await get_available_dates(db_path)
    return dates[0] if dates else None
