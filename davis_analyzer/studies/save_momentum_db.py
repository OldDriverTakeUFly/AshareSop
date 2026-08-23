#!/usr/bin/env python
"""Persist momentum cross-sections to SQLite for future backtest/query.

Writes to storage/database/research_factors.db (dedicated research DB, not
the shared tushare cache market_data.db):

  momentum_snapshots   — per screening date: top-100 momentum names with
                         industry / r20 / r60 / r120 / mom / pb_pct(250d)
  momentum_study_runs  — one row per study execution (params + summary stats)

Reuses the local panel cache (mom_val_panel.pkl) built by
sector_transfer_study.py; refresh that first to include new trade dates.
"""
import os
import sys

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import sqlite3
import pandas as pd
from stockhot.tushare_config import get_pro_api

STUDIES = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(STUDIES))
DB = os.path.join(ROOT, "storage", "database", "research_factors.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)

close, pb = pd.read_pickle(os.path.join(STUDIES, "mom_val_panel.pkl"))
pro = get_pro_api(timeout=60)
basic = pro.stock_basic(fields="ts_code,name,industry,list_date,list_status").set_index("ts_code")
basic = basic[basic["list_status"] == "L"]

SCR = []
for me in pd.date_range("2024-07-31", "2026-08-31", freq="ME"):
    dts_all = [c for c in close.columns if c <= me]
    if dts_all:
        SCR.append(dts_all[-1].strftime("%Y%m%d"))

con = sqlite3.connect(DB)
con.execute("""CREATE TABLE IF NOT EXISTS momentum_snapshots (
    trade_date TEXT NOT NULL, rank INTEGER NOT NULL, ts_code TEXT NOT NULL,
    name TEXT, industry TEXT, r20 REAL, r60 REAL, r120 REAL, mom REAL,
    pb_pct_250d REAL, is_value_pool INTEGER,
    PRIMARY KEY (trade_date, ts_code))""")
con.execute("""CREATE TABLE IF NOT EXISTS momentum_study_runs (
    run_date TEXT NOT NULL, study TEXT NOT NULL, params TEXT, summary TEXT,
    PRIMARY KEY (run_date, study))""")

rows = []
for m_end in SCR:
    dts_all = [c for c in close.columns if c.strftime("%Y%m%d") <= m_end]
    if not dts_all or len(dts_all) < 260:
        continue
    d_idx = dts_all[-1]
    d120, d60, d20 = dts_all[-121], dts_all[-61], dts_all[-21]
    c_now = close[d_idx]
    r20 = (c_now / close[d20] - 1) * 100
    r60 = (c_now / close[d60] - 1) * 100
    r120 = (c_now / close[d120] - 1) * 100
    mom = r60 * 0.5 + r20 * 0.2 + r120 * 0.3

    win = pb[dts_all[-250:]]
    cur_pb = win.iloc[:, -1]
    pct = (win.lt(cur_pb, axis=0)).sum(axis=1) / win.notna().sum(axis=1)

    uni = pd.DataFrame({"r20": r20, "r60": r60, "r120": r120, "mom": mom,
                        "pb_pct": pct}).dropna()
    uni = uni[uni.index.isin(basic.index)]
    nm = basic.loc[uni.index, "name"]
    uni = uni[~nm.str.contains("ST|退", na=False)]
    uni = uni[uni.index.str.match(r"^(6|0|3)")]
    ld = pd.to_datetime(basic.loc[uni.index, "list_date"], format="%Y%m%d")
    uni = uni[ld < (d_idx - pd.Timedelta(days=400))]

    top = uni.sort_values("mom", ascending=False).head(100).reset_index()
    top.columns = ["ts_code", "r20", "r60", "r120", "mom", "pb_pct"]
    for i, r in top.iterrows():
        rows.append((d_idx.strftime("%Y%m%d"), i + 1, r["ts_code"],
                     basic.loc[r["ts_code"], "name"],
                     basic.loc[r["ts_code"], "industry"],
                     round(r["r20"], 2), round(r["r60"], 2), round(r["r120"], 2),
                     round(r["mom"], 2), round(r["pb_pct"], 4),
                     int(r["pb_pct"] < 0.30)))

con.executemany("INSERT OR REPLACE INTO momentum_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)

for study, csvf in [("mom_val_overlap", "mom_val_overlap_results.csv"),
                    ("sector_transfer", "sector_transfer_results.csv")]:
    p = os.path.join(STUDIES, csvf)
    if os.path.exists(p):
        summary = pd.read_csv(p).describe().round(2).to_csv()
        con.execute("INSERT OR REPLACE INTO momentum_study_runs VALUES (?,?,?,?)",
                    ("2026-08-23", study,
                     "mom=60d*0.5+20d*0.2+120d*0.3; value pool pb_pct250<0.30; fwd 20td; monthly 2025-11~2026-08",
                     summary))

con.commit()
n = pd.read_sql("SELECT trade_date, COUNT(*) n FROM momentum_snapshots GROUP BY trade_date", con)
print(n.to_string(index=False))
print("\nDB saved ->", DB)
print(pd.read_sql("SELECT name FROM sqlite_master WHERE type='table'", con).to_string(index=False))
con.close()
