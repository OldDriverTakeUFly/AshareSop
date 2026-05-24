"""Database helper utilities for invest_sop module."""

from datetime import datetime
from typing import Any

from stockhot.storage.database import get_connection

_ALLOWED_TABLES = frozenset({
    "invest_overseas_market",
    "invest_domestic_events",
    "invest_supply_chain",
    "invest_futures_sentiment",
    "invest_morning_data",
    "invest_cycle_assessments",
    "invest_holdings",
    "invest_holdings_transactions",
    "invest_sector_rules",
})


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
