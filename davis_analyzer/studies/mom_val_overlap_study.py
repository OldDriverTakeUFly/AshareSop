#!/usr/bin/env python
"""Momentum x Valuation overlap study (event-style, no lookahead).

Question: does giving higher attention to stocks that are BOTH momentum
leaders AND cheap (low PB percentile) beat pure momentum / pure cheapness?

Design:
- Screening dates: ~monthly from 2025-11 to 2026-08-21 (10 dates).
- Factors at each date D (data <= D only):
    mom  = r60*0.5 + r20*0.2 + r120*0.3   (same as the momentum top20 screen)
    pb_pct = percentile of current PB within the stock's own past 250 trading days
- Portfolios (equal weight, forward 20 trading days):
    A) MOM20      : top 20 by mom
    B) MOM_VAL20  : mom top 100 -> keep pb_pct < 0.50 -> top 20 by mom
    C) VAL_MOM20  : pb_pct < 0.30 (cheapest decile-ish) -> top 20 by mom (control)
- Benchmark: CSI300 (000300.SH) same-horizon return.
Universe filter: non-ST, SH/SZ only, listed >= 250td before D, valid data.
"""
import os
import sys

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import numpy as np
import pandas as pd
from stockhot.tushare_config import get_pro_api

pro = get_pro_api(timeout=60)

START, END = "20140601", "20260821"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mom_val_panel.pkl")
if os.path.exists(CACHE):
    close, pb = pd.read_pickle(CACHE)
    print(f"panel loaded from cache: {close.shape}")
else:
    cal = pro.trade_cal(exchange="SSE", start_date=START, end_date=END,
                        fields="cal_date,is_open")
    dates = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist())
    frames = {}
    for d in dates:
        df = pro.daily_basic(trade_date=d, fields="ts_code,close,pb,pe_ttm")
        if len(df):
            frames[d] = df.set_index("ts_code")
    close = pd.DataFrame({d: f["close"] for d, f in frames.items()})
    pb = pd.DataFrame({d: f["pb"] for d, f in frames.items()})
    close.columns = pd.to_datetime(close.columns); close = close.sort_index(axis=1)
    pb.columns = pd.to_datetime(pb.columns); pb = pb.sort_index(axis=1)
    pd.to_pickle((close, pb), CACHE)
    print(f"panel pulled & cached: {close.shape}")

basic = pro.stock_basic(fields="ts_code,name,industry,list_date,list_status").set_index("ts_code")
basic = basic[basic["list_status"].isin(["L", "D", "P"])]  # 含退市,消幸存者偏差

idx = pro.index_daily(ts_code="000300.SH", start_date=START, end_date=END,
                      fields="trade_date,close").set_index("trade_date")["close"]
idx.index = pd.to_datetime(idx.index, format="%Y%m%d")

# screening dates: month-end trade days from 2024-07 to 2026-08
scr = []
for me in pd.date_range("2015-01-31", "2026-08-31", freq="ME"):
    dts = [c for c in close.columns if c <= me]
    if dts:
        scr.append(dts[-1])

rows = []
for D in scr:
    dcol = pd.to_datetime(D)
    dts = [c for c in close.columns if c <= dcol]
    if len(dts) < 260:
        continue
    fwd_col = None
    cols_after = [c for c in close.columns if c > dcol]
    if len(cols_after) < 20:
        continue
    fwd_col = cols_after[19]  # 20 trading days later
    d_idx = dts[-1]
    d120, d60, d20 = dts[-121], dts[-61], dts[-21]
    c_now, c120, c60, c20 = close[d_idx], close[d120], close[d60], close[d20]

    mom = (c_now / c60 - 1) * 50 + (c_now / c20 - 1) * 20 + (c_now / c120 - 1) * 30

    # PB percentile within own past 250 trading days (inclusive of today)
    win = pb[dts[-250:]]
    cur_pb = win.iloc[:, -1]
    pct = (win.lt(cur_pb, axis=0)).sum(axis=1) / win.notna().sum(axis=1)

    uni = pd.DataFrame({"mom": mom, "pb_pct": pct}).dropna()
    uni = uni[uni.index.isin(basic.index)]
    nm = basic.loc[uni.index, "name"]
    uni = uni[~nm.str.contains("ST|退", na=False)]
    uni = uni[uni.index.str.match(r"^(6|0|3)")]
    ld = pd.to_datetime(basic.loc[uni.index, "list_date"], format="%Y%m%d")
    uni = uni[ld < (d_idx - pd.Timedelta(days=400))]

    fwd = close[fwd_col] / c_now - 1

    def port_ret(sel):
        r = fwd.reindex(sel.index).dropna()
        return r.mean() * 100, len(r)

    mom100 = uni.sort_values("mom", ascending=False).head(100)
    A = mom100.head(20)
    B = mom100[mom100["pb_pct"] < 0.50].sort_values("mom", ascending=False).head(20)
    Cpool = uni[uni["pb_pct"] < 0.30].sort_values("mom", ascending=False).head(20)

    rA, nA = port_ret(A)
    rB, nB = port_ret(B)
    rC, nC = port_ret(Cpool)
    bm = (idx[fwd_col] / idx[d_idx] - 1) * 100

    rows.append({"date": D, "fwd_date": fwd_col.strftime("%Y%m%d"),
                 "MOM20": rA, "MOMxVAL": rB, "VALxMOM": rC, "CSI300": bm,
                 "nB": nB, "nC": nC})

res = pd.DataFrame(rows).set_index("date")
print("\n=== 前瞻20交易日收益(%) ===")
print(res.round(2).to_string())

print("\n=== 汇总(均值 / 中位 / 相对CSI300超额均值 / 胜率) ===")
for col in ["MOM20", "MOMxVAL", "VALxMOM", "CSI300"]:
    s = res[col]
    ex = (res[col] - res["CSI300"]).dropna()
    print(f"{col:9s} mean={s.mean():+6.2f}%  med={s.median():+6.2f}%  "
          f"excess={ex.mean():+6.2f}pp  win_vs_idx={(ex > 0).mean()*100:.0f}%  n={len(s)}")
print("\nB池平均标的数:", res['nB'].mean(), "| C池:", res['nC'].mean())
res.to_csv(os.path.join(os.path.dirname(__file__), "mom_val_overlap_results.csv"))
