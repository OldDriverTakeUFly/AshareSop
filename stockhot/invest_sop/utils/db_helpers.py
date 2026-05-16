"""Database helper utilities for invest_sop module."""

from datetime import datetime
from typing import Any

from stockhot.storage.database import get_connection


def upsert_record(table: str, data_dict: dict[str, Any], unique_keys: list[str]) -> None:
    """Insert or replace a record in the specified table.

    Uses INSERT OR REPLACE to handle upsert based on unique constraints.

    Args:
        table: Target table name.
        data_dict: Column name to value mapping.
        unique_keys: List of column names that form the unique constraint.
    """
    conn = get_connection()
    try:
        columns = ", ".join(data_dict.keys())
        placeholders = ", ".join("?" for _ in data_dict)
        sql = f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})"
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
    conn = get_connection()
    try:
        cursor = conn.execute(
            f"SELECT * FROM {table} WHERE {date_column} = ?",
            (date,),
        )
        return [dict(row) for row in cursor]
    finally:
        conn.close()
