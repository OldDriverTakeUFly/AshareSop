"""Backfill moneyflow (个股资金流) by trade_date, stored to SQLite.

Usage:
    python davis_analyzer/scripts/backfill_moneyflow.py [start] [end]
"""
import os
import sys
import time
import sqlite3
from dotenv import load_dotenv

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_REPO_ROOT, ".env"), override=True)
os.environ.setdefault("PROJECT_ROOT", _REPO_ROOT)

import loguru
loguru.logger.remove()
loguru.logger.add(sys.stderr, level="WARNING")

import pandas as pd
from davis_analyzer.tushare_client import TushareClient, _CACHE_DB
from stockhot.data_layer.market_db import init_db


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "20210101"
    end = sys.argv[2] if len(sys.argv) > 2 else "20260731"

    client = TushareClient()
    print(f"=== moneyflow backfill: {start} -> {end} ===")
    init_db()

    with sqlite3.connect(str(_CACHE_DB)) as conn:
        all_dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM daily_price WHERE trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
            (start, end),
        ).fetchall()]
        resume = conn.execute(
            "SELECT MAX(trade_date) FROM moneyflow WHERE trade_date >= ? AND trade_date <= ?",
            (start, end),
        ).fetchone()[0]
        if resume:
            fetch_dates = [d for d in all_dates if d > resume]
            print(f"  Resume after {resume}, {len(fetch_dates)} remaining of {len(all_dates)}")
        else:
            fetch_dates = list(all_dates)
            print(f"  {len(fetch_dates)} trade dates")

    fetched = 0
    rows = 0
    skipped = 0
    empty = []
    t0 = time.time()

    for i, d in enumerate(fetch_dates):
        try:
            df = client._call("moneyflow", client._pro.moneyflow, {"trade_date": d})
        except Exception as e:
            print(f"  {d}: ERROR {e}", file=sys.stderr)
            skipped += 1
            continue

        if df is None or df.empty:
            empty.append(d)
            skipped += 1
            continue

        now = time.time()
        records = []
        for r in df.to_dict("records"):
            records.append((
                d,
                r.get("ts_code", ""),
                r.get("buy_sm_amount"),
                r.get("sell_sm_amount"),
                r.get("buy_md_amount"),
                r.get("sell_md_amount"),
                r.get("buy_lg_amount"),
                r.get("sell_lg_amount"),
                r.get("buy_elg_amount"),
                r.get("sell_elg_amount"),
                r.get("net_mf_amount"),
                now,
            ))
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO moneyflow
                (trade_date, ts_code, buy_sm_amount, sell_sm_amount,
                 buy_md_amount, sell_md_amount, buy_lg_amount, sell_lg_amount,
                 buy_elg_amount, sell_elg_amount, net_mf_amount, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                records,
            )
            conn.commit()

        fetched += 1
        rows += len(df)

        if (i + 1) % 100 == 0 or (i + 1) == len(fetch_dates):
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (len(fetch_dates) - i - 1) / rate if rate > 0 else 0
            print(f"  [{i+1}/{len(fetch_dates)}] {d} rows={rows:,} "
                  f"({rate:.1f}/s ETA {eta:.0f}s)", flush=True)

    elapsed = time.time() - t0
    print(f"\n  Done: fetched={fetched} rows={rows:,} skipped={skipped} "
          f"empty={len(empty)} time={elapsed:.0f}s")

    with sqlite3.connect(str(_CACHE_DB)) as conn:
        r = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date) FROM moneyflow"
        ).fetchone()
        print(f"  DB total: rows={r[0]:,} days={r[1]} range={r[2]}~{r[3]}")


if __name__ == "__main__":
    main()
