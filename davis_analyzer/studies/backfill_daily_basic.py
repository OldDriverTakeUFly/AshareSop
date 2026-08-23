#!/usr/bin/env python
"""Backfill market_data.db daily_basic (per-stock valuation) history by
whole-market per-trade-date snapshots.

Only fills trade_date < earliest cached date (2021+ depth already maintained
by the engine's 24h incremental refresh). Rate: ~2900 calls, resumable
(skips dates already present in bulk).

Usage: .venv/bin/python davis_analyzer/studies/backfill_daily_basic.py \
          [--start 20140601] [--end 20260821] [--resume-note]
"""
import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd
from stockhot.data_layer.market_db import MARKET_DB_PATH
from stockhot.tushare_config import get_pro_api

FIELDS = "ts_code,trade_date,pe_ttm,pb,ps,total_mv,turnover_rate,circ_mv,free_share"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20140601")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y%m%d"))
    args = ap.parse_args()

    pro = get_pro_api(timeout=60)
    conn = sqlite3.connect(str(MARKET_DB_PATH))

    cal = pro.trade_cal(exchange="SSE", start_date=args.start, end_date=args.end,
                        fields="cal_date,is_open")
    dates = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist())
    have = {r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM daily_basic WHERE trade_date < '20260601'")}
    todo = [d for d in dates if d not in have]
    print(f"{len(dates)} trade days in range, {len(have)} already cached, {len(todo)} to backfill", flush=True)

    t0, done, rows = time.time(), 0, 0
    for i, d in enumerate(todo):
        try:
            df = pro.daily_basic(trade_date=d, fields=FIELDS)
        except Exception as e:
            print(f"[{d}] API error: {e}, sleep 15s and retry once", flush=True)
            time.sleep(15)
            try:
                df = pro.daily_basic(trade_date=d, fields=FIELDS)
            except Exception as e2:
                print(f"[{d}] FAILED permanently: {e2}", flush=True)
                continue
        if df is None or df.empty:
            continue
        now = time.time()
        recs = [(r["ts_code"], r["trade_date"], r.get("pe_ttm"), r.get("pb"),
                 r.get("ps"), r.get("total_mv"), now, r.get("turnover_rate"),
                 r.get("circ_mv"), r.get("free_share"))
                for r in df.to_dict("records")]
        conn.executemany(
            """INSERT OR REPLACE INTO daily_basic
               (ts_code, trade_date, pe_ttm, pb, ps, total_mv, fetched_at,
                turnover_rate, circ_mv, free_share)
               VALUES (?,?,?,?,?,?,?,?,?,?)""", recs)
        conn.commit()
        done += 1
        rows += len(recs)
        if done % 100 == 0:
            rate = done / (time.time() - t0) * 60
            print(f"progress {done}/{len(todo)} days, {rows} rows, {rate:.0f} days/min", flush=True)

    print(f"DONE: {done} days, {rows} rows, {(time.time()-t0)/60:.1f} min", flush=True)
    print(conn.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM daily_basic").fetchone())
    conn.close()


if __name__ == "__main__":
    main()
