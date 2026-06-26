"""Database helper utilities for invest_sop module."""

from datetime import date, datetime, timedelta
from typing import Any

from stockhot.storage.database import get_connection

_ALLOWED_TABLES = frozenset(
    {
        "invest_overseas_market",
        "invest_domestic_events",
        "invest_supply_chain",
        "invest_futures_sentiment",
        "invest_morning_data",
        "invest_cycle_assessments",
        "invest_holdings",
        "invest_holdings_transactions",
        "invest_sector_rules",
        "invest_watchlist",
        "advisor_runs",
    }
)


def upsert_record(table: str, data_dict: dict[str, Any], unique_keys: list[str]) -> None:
    """Insert or update a record in the specified table.

    Uses INSERT ... ON CONFLICT DO UPDATE to handle upsert based on unique constraints.

    Args:
        table: Target table name.
        data_dict: Column name to value mapping.
        unique_keys: List of column names that form the unique constraint.
    """
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"Invalid table name: {table}")
    conn = get_connection()
    try:
        columns = ", ".join(data_dict.keys())
        placeholders = ", ".join("?" for _ in data_dict)
        update_clause = ", ".join(f"{k} = excluded.{k}" for k in data_dict.keys())
        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) ON CONFLICT DO UPDATE SET {update_clause}"
        conn.execute(sql, tuple(data_dict.values()))
        conn.commit()
    finally:
        conn.close()


def query_by_date(table: str, date: str, date_column: str = "date") -> list[dict[str, Any]]:
    """Query records from a table by date.

    Args:
        table: Table name to query.
        date: Date string to filter by ('YYYY-MM-DD').
        date_column: Name of the date column (default: 'date').

    Returns:
        List of row dicts.
    """
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"Invalid table name: {table}")
    conn = get_connection()
    try:
        cursor = conn.execute(
            f"SELECT * FROM {table} WHERE {date_column} = ?",
            (date,),
        )
        return [dict(row) for row in cursor]
    finally:
        conn.close()


def _coerce_date(value: str | date | datetime) -> str:
    """Normalize a date-like value to a 'YYYY-MM-DD' string."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()


def query_by_date_range(
    table: str,
    end_date: str | date | datetime,
    days_back: int = 3,
    date_column: str = "date",
) -> list[dict[str, Any]]:
    """Query records from a table within a trailing date window.

    Returns rows whose date falls in ``[end_date - days_back, end_date]`` inclusive,
    ordered ascending by date. Used to read the recent multi-day trend of a table
    (e.g. ``invest_overseas_market``) so news impact can be cross-checked against
    actual subsequent price action — see the news-recency framework.

    Args:
        table: Table name to query (must be in ``_ALLOWED_TABLES``).
        end_date: End of the window as ``'YYYY-MM-DD'`` str, ``date`` or ``datetime``.
        days_back: Number of calendar days before ``end_date`` to include. Rows whose
            date is older than ``end_date - days_back`` are excluded.
        date_column: Name of the date column (default: 'date').

    Returns:
        List of row dicts ordered by date ascending. Missing intermediate days are
        simply absent (the table only stores days that were collected).
    """
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"Invalid table name: {table}")
    end = datetime.strptime(_coerce_date(end_date), "%Y-%m-%d").date()
    start = end - timedelta(days=days_back)
    conn = get_connection()
    try:
        cursor = conn.execute(
            f"SELECT * FROM {table} WHERE {date_column} BETWEEN ? AND ? ORDER BY {date_column} ASC",
            (start.isoformat(), end.isoformat()),
        )
        return [dict(row) for row in cursor]
    finally:
        conn.close()
