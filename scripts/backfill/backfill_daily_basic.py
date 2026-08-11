"""Backfill daily_basic history by trade_date (whole-market per call).

Usage:
    python -m davis_analyzer.scripts.backfill_daily_basic
    python davis_analyzer/scripts/backfill_daily_basic.py 20210101 20260630
"""
import os
import sys
from dotenv import load_dotenv

# Load .env from repo root (two levels up).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_REPO_ROOT, ".env"), override=True)
os.environ.setdefault("PROJECT_ROOT", _REPO_ROOT)

# Silence loguru INFO from safe_tushare_call etc.
import loguru
loguru.logger.remove()
loguru.logger.add(sys.stderr, level="INFO")
# Keep davis_analyzer.tushare_client logs visible.
loguru.logger.add(
    lambda msg: print(msg, end=""),
    level="INFO",
    filter=lambda record: "tushare_client" in record["name"],
)


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "20210101"
    end = sys.argv[2] if len(sys.argv) > 2 else "20260630"

    from davis_analyzer.tushare_client import TushareClient

    client = TushareClient()
    print(f"=== daily_basic backfill: {start} → {end} ===")

    # Pre-flight: report current state.
    import sqlite3
    from davis_analyzer.tushare_client import _CACHE_DB
    with sqlite3.connect(str(_CACHE_DB)) as conn:
        row = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date) "
            "FROM daily_basic"
        ).fetchone()
        print(f"  Before: rows={row[0]:,}  days={row[1]}  range={row[2]}~{row[3]}")

    result = client.backfill_daily_basic_by_date(start, end)
    print(f"\n  Result: {result}")

    # Post-flight.
    with sqlite3.connect(str(_CACHE_DB)) as conn:
        row = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date) "
            "FROM daily_basic"
        ).fetchone()
        print(f"  After:  rows={row[0]:,}  days={row[1]}  range={row[2]}~{row[3]}")


if __name__ == "__main__":
    main()
