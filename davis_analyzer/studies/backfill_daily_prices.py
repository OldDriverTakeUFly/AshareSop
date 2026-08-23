#!/usr/bin/env python
"""Backfill market_data.db daily_price history (default from 2015) for
tournament/replay/backtest use.

Why: TushareClient.get_daily_prices only fetches FORWARD (incremental from
MAX(trade_date)); it never backfills history. This tool backfills per-stock
daily + adj_factor (full 12-column DAL schema) so old windows can be replayed.

Usage:
  .venv/bin/python davis_analyzer/studies/backfill_daily_prices.py \
      [--start 20150101] [--codes 688981.SH,688347.SH] \
      [--from-ledger] [--index 000001.SH,000300.SH,399006.SZ,000688.SH]

  --from-ledger  collect ts_codes ever appeared in tournament_ledger.participants
  --index        index codes fetched via pro.index_daily (adj_factor=1.0) into
                 daily_price so the benchmark adapter works on old windows too

Safe: INSERT OR REPLACE on (ts_code, trade_date); never deletes; re-runnable.
"""
import argparse
import json
import os
import re
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

CODE_RE = re.compile(r"\d{6}\.(?:SH|SZ|BJ)")


def codes_from_ledger(conn: sqlite3.Connection) -> list[str]:
    codes: set[str] = set()
    for (part,) in conn.execute("SELECT participants FROM tournament_ledger"):
        for m in CODE_RE.findall(str(part or "")):
            codes.add(m)
    # detail 字段里也可能埋代码
    for (detail,) in conn.execute("SELECT detail FROM tournament_ledger"):
        for m in CODE_RE.findall(str(detail or "")):
            codes.add(m)
    return sorted(codes)


def cached_range(conn: sqlite3.Connection, ts_code: str) -> tuple[str | None, int]:
    row = conn.execute(
        "SELECT MIN(trade_date), COUNT(*) FROM daily_price WHERE ts_code=?",
        (ts_code,),
    ).fetchone()
    return row[0], row[1] or 0


def insert_rows(conn: sqlite3.Connection, df: pd.DataFrame, adj: pd.DataFrame,
                ts_code: str) -> int:
    if df is None or df.empty:
        return 0
    adj_map = {}
    if adj is not None and not adj.empty:
        for r in adj.to_dict("records"):
            td, af = str(r.get("trade_date", "")), r.get("adj_factor")
            if td and af is not None:
                adj_map[td] = float(af)
    now = time.time()
    recs = []
    for r in df.to_dict("records"):
        td = str(r.get("trade_date", ""))
        recs.append((r.get("ts_code", ts_code), td, r.get("open"), r.get("high"),
                     r.get("low"), r.get("close"), r.get("pre_close"),
                     r.get("pct_chg"), r.get("vol"), r.get("amount"),
                     adj_map.get(td),
                     now))
    conn.executemany(
        """INSERT OR REPLACE INTO daily_price
           (ts_code, trade_date, open, high, low, close, pre_close, pct_chg,
            vol, amount, adj_factor, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", recs)
    return len(recs)


def fetch_stock_history(pro, ts_code: str, start: str, end: str):
    """Pull daily + adj_factor in <=5y segments (avoid long-range truncation)."""
    dframes, aframes = [], []
    seg_start = start
    while seg_start <= end:
        seg_end = min((pd.to_datetime(seg_start) + pd.Timedelta(days=5 * 365 - 1)).strftime("%Y%m%d"), end)
        d = pro.daily(ts_code=ts_code, start_date=seg_start, end_date=seg_end,
                      fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount")
        a = pro.adj_factor(ts_code=ts_code, start_date=seg_start, end_date=seg_end,
                           fields="ts_code,trade_date,adj_factor")
        if len(d):
            dframes.append(d)
        if len(a):
            aframes.append(a)
        seg_start = (pd.to_datetime(seg_end) + pd.Timedelta(days=1)).strftime("%Y%m%d")
    daily = pd.concat(dframes, ignore_index=True).drop_duplicates("trade_date") if dframes else pd.DataFrame()
    adj = pd.concat(aframes, ignore_index=True).drop_duplicates("trade_date") if aframes else pd.DataFrame()
    return daily, adj


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20150101")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y%m%d"))
    ap.add_argument("--codes", default="")
    ap.add_argument("--from-ledger", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="every stock in stock_basic (L/D/P); fetch only the gap "
                         "[start, cached_min-1] per stock, never re-pulling existing rows")
    ap.add_argument("--index", default="")
    args = ap.parse_args()

    conn = sqlite3.connect(str(MARKET_DB_PATH))
    pro = get_pro_api(timeout=60)
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if args.all:
        allb = pro.stock_basic(fields="ts_code,list_status")
        allb = allb[allb["list_status"].isin(["L", "D", "P"])]
        codes += [c for c in allb["ts_code"] if c not in codes]
        print(f"--all: 全市场 {len(codes)} 只(含退市)")
    if args.from_ledger:
        led = codes_from_ledger(conn)
        print(f"ledger 收集到 {len(led)} 只: {led[:10]}{'...' if len(led) > 10 else ''}")
        codes += [c for c in led if c not in codes]
    indexes = [c.strip() for c in args.index.split(",") if c.strip()]

    for code in codes:
        mn, cnt = cached_range(conn, code)
        if mn is not None and mn <= args.start:
            continue  # already covered
        # only fetch the gap [start, cached_min-1]; never re-pull existing rows
        gap_end = ((pd.to_datetime(mn) - pd.Timedelta(days=1)).strftime("%Y%m%d")
                   if mn else args.end)
        if gap_end < args.start:
            continue
        daily, adj = fetch_stock_history(pro, code, args.start, gap_end)
        n = insert_rows(conn, daily, adj, code)
        conn.commit()
        mn2, cnt2 = cached_range(conn, code)
        print(f"  {code}: 回补 {n} 行 [{args.start}..{gap_end}] → 缓存 {mn2}.. 共 {cnt2} 行", flush=True)

    for code in indexes:
        dframes = []
        seg = args.start
        while seg <= args.end:
            seg_end = min((pd.to_datetime(seg) + pd.Timedelta(days=5 * 365 - 1)).strftime("%Y%m%d"), args.end)
            d = pro.index_daily(ts_code=code, start_date=seg, end_date=seg_end,
                                fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount")
            if len(d):
                dframes.append(d)
            seg = (pd.to_datetime(seg_end) + pd.Timedelta(days=1)).strftime("%Y%m%d")
        idx_df = pd.concat(dframes, ignore_index=True).drop_duplicates("trade_date") if dframes else pd.DataFrame()
        one = pd.DataFrame({"trade_date": idx_df["trade_date"], "adj_factor": [1.0] * len(idx_df)})
        n = insert_rows(conn, idx_df, one, code)
        conn.commit()
        mn2, cnt2 = cached_range(conn, code)
        print(f"  {code}(指数): 回补 {n} 行 → 缓存 {mn2}.. 共 {cnt2} 行")

    conn.close()
    print("done.")


if __name__ == "__main__":
    main()
