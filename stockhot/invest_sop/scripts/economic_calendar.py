"""Economic calendar collector — fetches US macro event surprises.

Pulls high-importance US economic data releases (nonfarm payrolls, CPI,
unemployment, FOMC rate decisions, etc.) from AKShare's Baidu Finance
calendar, storing the actual-vs-expected surprise for the international
overlay's 5th signal (event surprise, weight 20%).

Table: invest_economic_calendar (in stockhot.db, alongside overseas data)
  date       TEXT  -- YYYY-MM-DD (event release date)
  time       TEXT  -- HH:MM (release time, Beijing)
  event      TEXT  -- event name (e.g. "美国7月非农就业人口变动季调后(万)")
  actual     REAL  -- actual value (公布)
  expected   REAL  -- consensus forecast (预期)
  previous   REAL  -- previous period value (前值)
  importance INT   -- 1-3 stars (only ≥2 stored)
  surprise   REAL  -- actual - expected (the signal for overlay)

Usage (called by overseas_market_data.py or standalone):
    PYTHONPATH=. .venv/bin/python stockhot/invest_sop/scripts/economic_calendar.py
    PYTHONPATH=. .venv/bin/python stockhot/invest_sop/scripts/economic_calendar.py --date 2026-08-07
"""

from __future__ import annotations

import argparse
import os
import re
import traceback
from datetime import datetime, timedelta

import pandas as pd

from stockhot.invest_sop.utils.db_helpers import upsert_record
from stockhot.storage.database import get_connection

TABLE = "invest_economic_calendar"

# ── Schema ─────────────────────────────────────────────────────────────


def _ensure_table() -> None:
    """Create invest_economic_calendar if not exists (idempotent)."""
    conn = get_connection()
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            date TEXT NOT NULL,
            time TEXT,
            event TEXT NOT NULL,
            actual REAL,
            expected REAL,
            previous REAL,
            importance INTEGER DEFAULT 0,
            surprise REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (date, event)
        )
        """
    )
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_date ON {TABLE}(date)")
    conn.commit()
    conn.close()


# ── Key event detection ────────────────────────────────────────────────

# Events that materially move risk appetite. The overlay's event signal
# only fires for these — generic data releases (drilling counts, ETF flows)
# are noise at the risk-appetite level.
_KEY_EVENT_PATTERNS = {
    "nonfarm": [
        r"非农就业人口变动",
        r"私营企业非农",
        r"ADP就业",
    ],
    "unemployment": [
        r"失业率",
    ],
    "cpi": [
        r"CPI\b",
        r"核心CPI",
        r"PCE.*物价",
    ],
    "rate_decision": [
        r"利率决议",
        r"联邦基金利率",
        r"FOMC",
    ],
    "gdp": [
        r"GDP",
    ],
    "pmi": [
        r"PMI.*制造业",
        r"ISM制造业",
    ],
}

# Keyword → event-type classification (for overlay scoring).
_EVENT_TYPE_RE = {
    etype: re.compile("|".join(pats), re.IGNORECASE)
    for etype, pats in _KEY_EVENT_PATTERNS.items()
}


def _classify_event(event_name: str) -> str | None:
    """Map a raw event name to a scoring category (nonfarm/cpi/etc)."""
    for etype, pat in _EVENT_TYPE_RE.items():
        if pat.search(event_name):
            return etype
    return None


# ── Collection ─────────────────────────────────────────────────────────


def collect_economic_calendar(target_date: str) -> int:
    """Fetch US macro events for target_date, store key ones to DB.

    Args:
        target_date: YYYY-MM-DD or YYYYMMDD.

    Returns:
        Number of key events stored (0 if none or error).
    """
    import akshare as ak

    # Normalize date format
    d = target_date.replace("-", "")
    dash_date = f"{d[:4]}-{d[4:6]}-{d[6:8]}"

    # Strip proxy for AKShare (same pattern as overseas_market_data)
    removed = {}
    for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
        if key in os.environ:
            removed[key] = os.environ.pop(key)

    try:
        df = ak.news_economic_baidu(date=d)
    except Exception as e:
        print(f"  [WARN] economic_calendar AKShare failed: {e}")
        traceback.print_exc()
        return 0
    finally:
        os.environ.update(removed)

    if df is None or len(df) == 0:
        print(f"  [WARN] economic_calendar: no data for {dash_date}")
        return 0

    # Filter: US events only + importance ≥ 2 + has expected value
    us = df[df["地区"].str.contains("美国", na=False)]
    high = us[(us["重要性"] >= 2) & (us["预期"].notna())]

    stored = 0
    for _, row in high.iterrows():
        event_name = str(row["事件"])

        # Only store key events (nonfarm/cpi/FOMC/etc), skip noise
        if _classify_event(event_name) is None:
            continue

        actual = _safe_float(row["公布"])
        expected = _safe_float(row["预期"])
        previous = _safe_float(row["前值"])
        surprise = round(actual - expected, 2) if (actual is not None and expected is not None) else None

        record = {
            "date": dash_date,
            "time": str(row.get("时间", "")),
            "event": event_name,
            "actual": actual,
            "expected": expected,
            "previous": previous,
            "importance": int(row["重要性"]),
            "surprise": surprise,
        }
        upsert_record(TABLE, record, unique_keys=["date", "event"])
        stored += 1
        surp_str = f"{surprise:+.1f}" if surprise is not None else "?"
        print(f"  [OK] {record['time']} {event_name[:30]} 实际{actual} 预期{expected} 偏差{surp_str}")

    return stored


def _safe_float(val) -> float | None:
    """Convert AKShare cell to float, return None on failure."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(str(val).replace(",", "").replace("%", "").replace("万亿", ""))
    except (ValueError, TypeError):
        return None


# ── Backfill ───────────────────────────────────────────────────────────


def backfill_calendar(start_date: str, end_date: str) -> int:
    """Backfill economic calendar for a date range (e.g. last 30 days).

    Useful for populating the overlay's event signal history.
    """
    start = datetime.strptime(start_date.replace("-", ""), "%Y%m%d")
    end = datetime.strptime(end_date.replace("-", ""), "%Y%m%d")
    total = 0
    cur = start
    while cur <= end:
        d = cur.strftime("%Y%m%d")
        n = collect_economic_calendar(d)
        total += n
        if n:
            print(f"  [{d}] {n} key events")
        cur += timedelta(days=1)
    return total


# ── Main ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Collect economic calendar")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--start", help="Backfill start date (overrides --date)")
    parser.add_argument("--end", help="Backfill end date")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    _ensure_table()

    if args.start:
        end = args.end or datetime.now().strftime("%Y%m%d")
        print(f"[economic_calendar] Backfill {args.start} → {end}")
        n = backfill_calendar(args.start, end)
        print(f"[economic_calendar] Backfill done: {n} key events stored")
    else:
        print(f"[economic_calendar] date={args.date}")
        n = collect_economic_calendar(args.date)
        print(f"[economic_calendar] Done: {n} key events stored")


if __name__ == "__main__":
    main()
