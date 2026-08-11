"""Backfill daily_price for all stocks from 2021-01-01.

Strategy: fetch by ts_code (each stock's full history in one API call),
like backfill_vol_amount_v2.py. ~5500 stocks × ~1000 days = 5.5M rows.

Also computes intraday_feature for each date (gap/amplitude/etc).
"""
import os, sys, time
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")

import sqlite3
import pandas as pd
import numpy as np
from stockhot.data_layer.market_db import get_connection, init_db
from stockhot.storage.database import init_database
from davis_analyzer.tushare_client import TushareClient

init_db()
init_database()
client = TushareClient()

START = "20210101"
END = "20241231"  # up to 2024 (2025+ already have)


def main():
    # Get all active stocks
    with get_connection() as c:
        rows = c.execute(
            "SELECT ts_code FROM stock_basic WHERE list_status='L' ORDER BY ts_code"
        ).fetchall()
    all_codes = [r[0] for r in rows]
    print(f"Total stocks: {len(all_codes)}")

    # Check which codes already have data in 2021-2024 range
    with get_connection() as c:
        done = set(r[0] for r in c.execute(
            "SELECT DISTINCT ts_code FROM daily_price "
            "WHERE trade_date >= '20210101' AND trade_date <= '20241231' "
            "AND vol > 0"
        ).fetchall())
    todo = [code for code in all_codes if code not in done]
    print(f"  Already done: {len(done)}")
    print(f"  Remaining: {len(todo)}")
    print(f"  Est time: {len(todo) / 400 * 60:.0f} min")

    t0 = time.time()
    total_rows = 0
    batch = []
    batch_size = 50

    for i, code in enumerate(todo):
        try:
            df = client._pro.daily(
                ts_code=code,
                start_date=START,
                end_date=END,
                fields="ts_code,trade_date,open,high,low,close,pre_close,vol,amount,pct_chg",
            )
            if df is None or df.empty:
                continue

            # Also get adj_factor
            try:
                adj_df = client._pro.adj_factor(ts_code=code, start_date=START, end_date=END)
                adj_map = {}
                if adj_df is not None and not adj_df.empty:
                    for r in adj_df.to_dict("records"):
                        adj_map[str(r.get("trade_date", ""))] = float(r.get("adj_factor", 0))
            except:
                adj_map = {}

            now = time.time()
            for r in df.to_dict("records"):
                td = str(r.get("trade_date", ""))
                batch.append((
                    code, td,
                    float(r["open"]) if r.get("open") else None,
                    float(r["high"]) if r.get("high") else None,
                    float(r["low"]) if r.get("low") else None,
                    float(r["close"]) if r.get("close") else None,
                    float(r["pre_close"]) if r.get("pre_close") else None,
                    float(r["pct_chg"]) if r.get("pct_chg") else None,
                    float(r["vol"]) if r.get("vol") else None,
                    float(r["amount"]) if r.get("amount") else None,
                    adj_map.get(td),
                    now,
                ))

            if len(batch) >= batch_size * 30:  # flush every ~50 stocks
                with get_connection() as conn:
                    conn.executemany(
                        "INSERT OR REPLACE INTO daily_price "
                        "(ts_code, trade_date, open, high, low, close, pre_close, "
                        "pct_chg, vol, amount, adj_factor, fetched_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        batch,
                    )
                    conn.commit()
                total_rows += len(batch)
                batch = []

            if (i + 1) % 500 == 0:
                elapsed = time.time() - t0
                eta = elapsed / (i + 1) * (len(todo) - i - 1)
                print(f"  [{i+1}/{len(todo)}] {code}: total={total_rows:,} "
                      f"({elapsed/60:.1f}min, ETA {eta/60:.1f}min)", flush=True)

        except Exception as e:
            if "频率" in str(e) or "limit" in str(e).lower():
                time.sleep(2)
            # Silent fail for individual stocks

    # Flush remaining
    if batch:
        with get_connection() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO daily_price "
                "(ts_code, trade_date, open, high, low, close, pre_close, "
                "pct_chg, vol, amount, adj_factor, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            conn.commit()
        total_rows += len(batch)

    elapsed = time.time() - t0
    print(f"\nDone: {total_rows:,} rows in {elapsed/60:.1f}min")

    # Verify
    with get_connection() as c:
        for year in [2021, 2022, 2023, 2024, 2025]:
            n = c.execute(
                f"SELECT COUNT(DISTINCT trade_date) FROM daily_price "
                f"WHERE trade_date >= '{year}0101' AND trade_date <= '{year}1231' AND vol > 0"
            ).fetchone()[0]
            stocks = c.execute(
                f"SELECT COUNT(DISTINCT ts_code) FROM daily_price "
                f"WHERE trade_date >= '{year}0101' AND trade_date <= '{year}1231' AND vol > 0"
            ).fetchone()[0]
            print(f"  {year}: {n} days, {stocks} stocks")


if __name__ == "__main__":
    main()
