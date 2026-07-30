"""Compute 6 pseudo-intraday features from daily_price OHLC.

Features (all from existing daily_price columns, zero new data):
  1. gap           = open / pre_close - 1
  2. amplitude     = (high - low) / pre_close
  3. close_position = (close - low) / (high - low)   [0-1, higher = closed strong]
  4. upper_shadow  = (high - max(open, close)) / (high - low)
  5. lower_shadow  = (min(open, close) - low) / (high - low)
  6. body_ratio    = (close - open) / (high - low)

Batch strategy: load all daily_price in one query, compute per-row, batch insert.
"""
import os, sys, time
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")

import pandas as pd
import numpy as np
from stockhot.data_layer.market_db import get_connection, init_db

init_db()

START_DATE = "20250101"
END_DATE = "20260721"


def main():
    print(f"Loading daily_price OHLC from {START_DATE} to {END_DATE}...", flush=True)
    with get_connection() as conn:
        df = pd.read_sql_query(
            "SELECT ts_code, trade_date, open, high, low, close, pre_close "
            "FROM daily_price "
            "WHERE trade_date >= ? AND trade_date <= ? "
            "AND open > 0 AND high > 0 AND low > 0 AND close > 0 "
            "AND pre_close > 0 "
            "ORDER BY ts_code, trade_date",
            conn, params=(START_DATE, END_DATE),
        )
    print(f"  Loaded {len(df):,} rows, {df['ts_code'].nunique()} stocks", flush=True)

    # Compute features
    print("Computing 6 intraday features...", flush=True)
    o, h, l, c, pc = df["open"], df["high"], df["low"], df["close"], df["pre_close"]
    hl = h - l
    hl_safe = hl.where(hl > 0, np.nan)  # avoid div by zero

    df["gap"] = (o / pc - 1).round(6)
    df["amplitude"] = (hl / pc).round(6)
    df["close_position"] = ((c - l) / hl_safe).clip(0, 1).round(4)
    df["upper_shadow"] = ((h - np.maximum(o, c)) / hl_safe).clip(0, 1).round(4)
    df["lower_shadow"] = ((np.minimum(o, c) - l) / hl_safe).clip(0, 1).round(4)
    df["body_ratio"] = ((c - o) / hl_safe).round(4)

    # Drop rows with NaN (high == low, no range)
    before = len(df)
    df = df.dropna(subset=["close_position"])
    dropped = before - len(df)
    if dropped > 0:
        print(f"  Dropped {dropped:,} rows with high==low (no range)", flush=True)

    print(f"  Computed features for {len(df):,} rows", flush=True)

    # Batch insert
    print("Inserting into intraday_feature table...", flush=True)
    t0 = time.time()
    records = []
    now = time.time()
    for _, r in df.iterrows():
        records.append((
            r["ts_code"], r["trade_date"],
            float(r["gap"]), float(r["amplitude"]),
            float(r["close_position"]), float(r["upper_shadow"]),
            float(r["lower_shadow"]), float(r["body_ratio"]),
            now,
        ))

    batch_size = 10000
    total = 0
    with get_connection() as conn:
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            conn.executemany(
                "INSERT OR REPLACE INTO intraday_feature "
                "(ts_code, trade_date, gap, amplitude, close_position, "
                "upper_shadow, lower_shadow, body_ratio, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            conn.commit()
            total += len(batch)
            if total % 100000 == 0 or total == len(records):
                elapsed = time.time() - t0
                print(f"  Inserted {total:,}/{len(records):,} ({elapsed:.0f}s)", flush=True)

    elapsed = time.time() - t0
    print(f"\nDone: {total:,} rows in {elapsed:.0f}s", flush=True)

    # Verify
    with get_connection() as c:
        row = c.execute(
            "SELECT COUNT(*), COUNT(DISTINCT ts_code), COUNT(DISTINCT trade_date), "
            "MIN(trade_date), MAX(trade_date) FROM intraday_feature"
        ).fetchone()
        print(f"\nintraday_feature table: {row[0]:,} rows, {row[1]} stocks, "
              f"{row[2]} dates ({row[3]} → {row[4]})")

        # Sample
        c.row_factory = __import__("sqlite3").Row
        rows = c.execute(
            "SELECT * FROM intraday_feature WHERE ts_code='300750.SZ' "
            "ORDER BY trade_date DESC LIMIT 5"
        ).fetchall()
        print(f"\nSample (300750.SZ):")
        for r in rows:
            print(f"  {r['trade_date']} gap={r['gap']:+.4f} amp={r['amplitude']:.4f} "
                  f"close_pos={r['close_position']:.2f} upper={r['upper_shadow']:.2f} "
                  f"lower={r['lower_shadow']:.2f} body={r['body_ratio']:+.2f}")


if __name__ == "__main__":
    main()
