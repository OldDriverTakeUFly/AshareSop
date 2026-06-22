"""Async SQLite reader for invest_* tables.

Uses aiosqlite for non-blocking database reads. All functions are read-only.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from stockhot.api.config import settings
from stockhot.invest_sop.config import INVEST_REPORTS_DIR

_ALLOWED_TABLES = frozenset(
    {
        "invest_overseas_market",
        "invest_supply_chain",
        "invest_futures_sentiment",
        "invest_morning_data",
        "invest_domestic_events",
    }
)

_REPORT_TYPE_MAP = {
    "pre_market": "盘前预研",
    "directive": "操作指令",
    "cycle_review": "周期评估",
}


async def get_connection(db_path: Path | str | None = None) -> aiosqlite.Connection:
    """Open an async SQLite connection with WAL mode."""
    path = str(db_path or settings.DB_PATH)
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def get_holdings(status: str = "active") -> list[dict]:
    """Return invest_holdings rows filtered by *status*."""
    db = await get_connection()
    try:
        cursor = await db.execute(
            "SELECT * FROM invest_holdings WHERE status=? ORDER BY id",
            (status,),
        )
        return [dict(row) async for row in cursor]
    finally:
        await db.close()


async def get_holding_by_id(id: int) -> dict | None:
    """Return a single invest_holdings row by primary key."""
    db = await get_connection()
    try:
        cursor = await db.execute(
            "SELECT * FROM invest_holdings WHERE id=?",
            (id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_overseas_by_date(date: str) -> dict | None:
    """Return invest_overseas_market row for *date*."""
    db = await get_connection()
    try:
        cursor = await db.execute(
            "SELECT * FROM invest_overseas_market WHERE date=?",
            (date,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_supply_chain_by_date(
    date: str,
    sector: str | None = None,
) -> list[dict]:
    """Return invest_supply_chain rows for *date*, optionally filtered by *sector*."""
    db = await get_connection()
    try:
        if sector is not None:
            cursor = await db.execute(
                "SELECT * FROM invest_supply_chain WHERE date=? AND sector=?",
                (date, sector),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM invest_supply_chain WHERE date=?",
                (date,),
            )
        return [dict(row) async for row in cursor]
    finally:
        await db.close()


async def get_futures_by_date(date: str) -> dict | None:
    """Return invest_futures_sentiment row for *date*."""
    db = await get_connection()
    try:
        cursor = await db.execute(
            "SELECT * FROM invest_futures_sentiment WHERE date=?",
            (date,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_morning_by_date(date: str) -> dict | None:
    """Return invest_morning_data row for *date*."""
    db = await get_connection()
    try:
        cursor = await db.execute(
            "SELECT * FROM invest_morning_data WHERE date=?",
            (date,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_events_by_date(date: str) -> list[dict]:
    """Return invest_domestic_events rows for *date*."""
    db = await get_connection()
    try:
        cursor = await db.execute(
            "SELECT * FROM invest_domestic_events WHERE date=?",
            (date,),
        )
        return [dict(row) async for row in cursor]
    finally:
        await db.close()


async def get_cycle_assessments() -> list[dict]:
    """Return all invest_cycle_assessments rows ordered by sector."""
    db = await get_connection()
    try:
        cursor = await db.execute(
            "SELECT * FROM invest_cycle_assessments ORDER BY sector",
        )
        return [dict(row) async for row in cursor]
    finally:
        await db.close()


async def get_supply_chain_history(
    metric_names: list[str],
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Return invest_supply_chain rows for given *metric_names* within a date range."""
    placeholders = ",".join(["?"] * len(metric_names))
    params = [*metric_names, start_date, end_date]
    db = await get_connection()
    try:
        cursor = await db.execute(
            f"SELECT * FROM invest_supply_chain "
            f"WHERE metric_name IN ({placeholders}) "
            f"AND date BETWEEN ? AND ? ORDER BY date",
            params,
        )
        return [dict(row) async for row in cursor]
    finally:
        await db.close()


async def get_overseas_history(
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Return invest_overseas_market rows within a date range."""
    db = await get_connection()
    try:
        cursor = await db.execute(
            "SELECT * FROM invest_overseas_market " "WHERE date BETWEEN ? AND ? ORDER BY date",
            (start_date, end_date),
        )
        return [dict(row) async for row in cursor]
    finally:
        await db.close()


async def get_available_dates(
    table_name: str,
    start_date: str,
    end_date: str,
) -> list[str]:
    """Return distinct dates from *table_name* within a date range.

    *table_name* is validated against a whitelist for SQL injection safety.
    """
    if table_name not in _ALLOWED_TABLES:
        raise ValueError(f"Table {table_name!r} is not allowed")
    db = await get_connection()
    try:
        cursor = await db.execute(
            f"SELECT DISTINCT date FROM {table_name} "
            "WHERE date BETWEEN ? AND ? ORDER BY date DESC",
            (start_date, end_date),
        )
        return [row["date"] async for row in cursor]
    finally:
        await db.close()


async def get_holding_transactions(holding_id: int) -> list[dict]:
    db = await get_connection()
    try:
        cursor = await db.execute(
            "SELECT * FROM invest_holdings_transactions WHERE holding_id=? ORDER BY date DESC, id DESC",
            (holding_id,),
        )
        return [dict(row) async for row in cursor]
    finally:
        await db.close()


async def get_sector_rules() -> list[dict]:
    db = await get_connection()
    try:
        cursor = await db.execute("SELECT * FROM invest_sector_rules ORDER BY sector")
        return [dict(row) async for row in cursor]
    finally:
        await db.close()


async def get_report_dates() -> list[dict]:
    """Scan INVEST_REPORTS_DIR for .md files and extract date + type info."""
    results: list[dict] = []
    reports_dir = INVEST_REPORTS_DIR
    if not reports_dir.exists():
        return results
    for path in sorted(reports_dir.glob("*.md")):
        stem = path.stem  # e.g. "2025-05-16_pre_market"
        parts = stem.split("_", 1)
        if len(parts) != 2:
            continue
        date_str, suffix = parts
        report_type = _REPORT_TYPE_MAP.get(suffix)
        if report_type is None:
            continue
        results.append(
            {
                "date": date_str,
                "type": report_type,
                "filename": path.name,
            }
        )
    return results
