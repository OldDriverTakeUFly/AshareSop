"""Migrate legacy single-table ``api_cache`` rows into the structured cache tables.

Usage::

    python -m davis_analyzer.migrate_cache            # perform migration
    python -m davis_analyzer.migrate_cache --dry-run  # report counts only

Each legacy row stores a JSON serialisation of a pandas DataFrame under
``response``. The script parses that JSON, routes it to the matching structured
table (``stock_basic_cache`` / ``daily_basic_cache`` / ``financial_cache``) and
inserts the rows, preserving the original ``fetched_at`` timestamp.

Endpoints mapped:
  * ``stock_basic``                  → stock_basic_cache
  * ``daily_basic``                  → daily_basic_cache
  * ``income``/``balancesheet``/
    ``cashflow``/``fina_indicator``  → financial_cache (one JSON payload per
                                        ts_code + end_date + endpoint)
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
from loguru import logger

from davis_analyzer.tushare_client import _CACHE_DB, _init_cache_db

_FINANCIAL_ENDPOINTS = {"income", "balancesheet", "cashflow", "fina_indicator"}

_DAILY_BASIC_FIELDS = ["ts_code", "trade_date", "pe_ttm", "pb", "ps", "total_mv"]
_STOCK_BASIC_FIELDS = ["ts_code", "name", "industry", "list_status"]


def _parse_response(raw: str) -> pd.DataFrame:
    """Decode a legacy ``response`` blob into a DataFrame (may be empty)."""
    try:
        return pd.read_json(io.StringIO(raw), orient="records")
    except (ValueError, TypeError):
        return pd.DataFrame()


def _migrate_stock_basic(
    conn: sqlite3.Connection, df: pd.DataFrame, fetched_at: float, dry_run: bool
) -> int:
    if df.empty:
        return 0
    records = []
    for r in df.to_dict("records"):
        records.append(
            (
                r.get("ts_code"),
                r.get("name"),
                r.get("industry"),
                r.get("list_status", "L"),
                fetched_at,
            )
        )
    if not dry_run:
        conn.executemany(
            """
            INSERT OR REPLACE INTO stock_basic_cache
                (ts_code, name, industry, list_status, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            records,
        )
    return len(records)


def _migrate_daily_basic(
    conn: sqlite3.Connection, df: pd.DataFrame, fetched_at: float, dry_run: bool
) -> int:
    if df.empty:
        return 0
    records = []
    for r in df.to_dict("records"):
        records.append(
            (
                r.get("ts_code"),
                str(r.get("trade_date", "")),
                r.get("pe_ttm"),
                r.get("pb"),
                r.get("ps"),
                r.get("total_mv"),
                fetched_at,
            )
        )
    if not dry_run:
        conn.executemany(
            """
            INSERT OR REPLACE INTO daily_basic_cache
                (ts_code, trade_date, pe_ttm, pb, ps, total_mv, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )
    return len(records)


def _migrate_financial(
    conn: sqlite3.Connection,
    endpoint: str,
    df: pd.DataFrame,
    fetched_at: float,
    dry_run: bool,
) -> int:
    if df.empty or "end_date" not in df.columns:
        return 0
    from davis_analyzer.tushare_client import _dedupe_financial_rows

    deduped = _dedupe_financial_rows(df, endpoint)
    records = []
    for r in deduped.to_dict("records"):
        records.append(
            (
                r.get("ts_code"),
                str(r.get("end_date", "")),
                endpoint,
                json.dumps(r, ensure_ascii=False),
                fetched_at,
            )
        )
    if not dry_run:
        conn.executemany(
            """
            INSERT OR REPLACE INTO financial_cache
                (ts_code, end_date, endpoint, payload, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            records,
        )
    return len(records)


def migrate(db_path: Path = _CACHE_DB, dry_run: bool = False) -> Counter:
    """Run the migration. Returns a ``Counter`` of rows handled per table."""
    _init_cache_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        legacy = conn.execute("SELECT endpoint, response, fetched_at FROM api_cache").fetchall()

    total_legacy = len(legacy)
    counts: Counter = Counter()

    with sqlite3.connect(str(db_path)) as conn:
        for endpoint, response, fetched_at in legacy:
            df = _parse_response(response)
            if endpoint == "stock_basic":
                n = _migrate_stock_basic(conn, df, fetched_at, dry_run)
                counts["stock_basic_cache"] += n
            elif endpoint == "daily_basic":
                n = _migrate_daily_basic(conn, df, fetched_at, dry_run)
                counts["daily_basic_cache"] += n
            elif endpoint in _FINANCIAL_ENDPOINTS:
                n = _migrate_financial(conn, endpoint, df, fetched_at, dry_run)
                counts["financial_cache"] += n
            else:
                counts["skipped_unknown"] += 1
        if not dry_run:
            conn.commit()

    mode = "DRY-RUN" if dry_run else "MIGRATED"
    logger.info("{}: {} legacy rows from api_cache", mode, total_legacy)
    for table, n in sorted(counts.items()):
        logger.info("  → {}: {} rows", table, n)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate legacy api_cache into structured cache tables."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report migration statistics without writing to the DB.",
    )
    args = parser.parse_args(argv)

    if not _CACHE_DB.exists():
        logger.error("Cache DB not found: {}", _CACHE_DB)
        return 1

    migrate(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
