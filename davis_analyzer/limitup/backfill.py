"""Phase 0: backfill limit_pool history from Tushare limit_list_d.

Writes the same 12 columns as stockhot's migrate_panels (dash dates,
suffix-less codes) plus float market value into module-owned
limit_pool_ext. Idempotent per day.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

import pandas as pd
from loguru import logger

from davis_analyzer.limitup import db

FetchFn = Callable[[str, str], "pd.DataFrame | None"]

POOL_KIND_BY_TYPE = {"U": "limit_up", "Z": "broken", "D": "limit_down"}

_INSERT_POOL = (
    "INSERT OR REPLACE INTO limit_pool "
    "(trade_date, ts_code, pool_kind, name, sector, change_pct, "
    "seal_amount, consecutive_boards, broken_count, "
    "first_seal_time, last_seal_time, turnover_rate) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
)


def ensure_ext_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS limit_pool_ext ("
        "trade_date TEXT NOT NULL, ts_code TEXT NOT NULL, pool_kind TEXT NOT NULL, "
        "float_mv REAL, PRIMARY KEY (trade_date, ts_code, pool_kind))"
    )
    conn.commit()


def _safe(v: object, default: object = None) -> object:
    try:
        f = float(v)  # type: ignore[arg-type]
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return default


def _pick(rec: pd.Series, *names: str) -> object:
    """Return the first column value that exists and is not NaN.

    Real limit_list_d payloads use `float_mv`/`turnover_ratio` while the
    Tushare doc names them `float_market_value`/`turnover_rate`; accept
    both, real name first.
    """
    for name in names:
        v = rec.get(name)
        if v is not None and not pd.isna(v):
            return v
    return None


def write_pool_day(
    conn: sqlite3.Connection, trade_date: str, df: pd.DataFrame,
    limit_type: str, pool_kind: str,
) -> int:
    """Persist one pool-type/day of raw limit_list_d rows; returns rows written."""
    dash = db.to_dash_date(trade_date)
    rows = 0
    for _, rec in df.iterrows():
        conn.execute(
            _INSERT_POOL,
            (
                dash, db.strip_code_suffix(str(rec.get("ts_code", ""))),
                pool_kind, rec.get("name"),
                rec.get("industry"),
                _safe(rec.get("pct_chg")), _safe(rec.get("fd_amount")),
                int(_safe(rec.get("limit_times"), 0) or 0),
                int(_safe(rec.get("open_times"), 0) or 0),
                db.normalize_seal_time(rec.get("first_time")),
                db.normalize_seal_time(rec.get("last_time")),
                _safe(_pick(rec, "turnover_ratio", "turnover_rate")),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO limit_pool_ext "
            "(trade_date, ts_code, pool_kind, float_mv) VALUES (?,?,?,?)",
            (dash, db.strip_code_suffix(str(rec.get("ts_code", ""))),
             pool_kind, _safe(_pick(rec, "float_mv", "float_market_value"))),
        )
        rows += 1
    conn.commit()
    return rows


def day_has_ext(conn: sqlite3.Connection, dash_date: str) -> bool:
    """A day counts as covered only when its ext rows carry float market value."""
    return bool(
        conn.execute(
            "SELECT 1 FROM limit_pool_ext WHERE trade_date=? AND float_mv IS NOT NULL "
            "LIMIT 1",
            (dash_date,),
        ).fetchone()
    )


def backfill(
    conn: sqlite3.Connection, start: str, end: str, fetch_fn: FetchFn
) -> dict:
    ensure_ext_table(conn)
    days_done = rows_written = days_skipped = 0
    for d in db.trading_dates(conn, start, end):
        dash = db.to_dash_date(d)
        if day_has_ext(conn, dash):
            days_skipped += 1
            continue
        got_any = False
        for limit_type, pool_kind in POOL_KIND_BY_TYPE.items():
            df = fetch_fn(d, limit_type)
            if df is None or df.empty:
                continue
            got_any = True
            rows_written += write_pool_day(conn, d, df, limit_type, pool_kind)
        if got_any:
            days_done += 1
        else:
            logger.warning("limit_list_d no data for {}", d)
    logger.info(
        "backfill done: days_done={} rows={} skipped={}",
        days_done, rows_written, days_skipped,
    )
    return {"days_done": days_done, "rows_written": rows_written,
            "days_skipped": days_skipped}


def probe_earliest(
    conn: sqlite3.Connection, fetch_fn: FetchFn, upper: str = "20200101"
) -> str | None:
    """Binary-search the earliest trading date limit_list_d covers.

    The probe window runs from `upper` (we never probe further back)
    to the latest cached trading date. Probing only calendar dates
    from daily_price guarantees every probe date is a real trading
    day (avoiding holiday false negatives).
    """
    cal = db.trading_dates(conn, db.normalize_date(upper), "99991231")
    if not cal or _empty(fetch_fn, cal[-1]):
        return None
    lo, hi = 0, len(cal) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if _empty(fetch_fn, cal[mid]):
            lo = mid + 1
        else:
            hi = mid
    return cal[lo] if not _empty(fetch_fn, cal[lo]) else None


def _empty(fetch_fn: FetchFn, d: str) -> bool:
    df = fetch_fn(d, "U")
    return df is None or df.empty
