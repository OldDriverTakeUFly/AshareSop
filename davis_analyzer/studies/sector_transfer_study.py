#!/usr/bin/env python
"""Sector-momentum transfer study: does a hot sector (from momentum leaders)
give an edge to value-pool stocks in the SAME sector (板块补涨/板块效应)?

Question (user's refined hypothesis): take sector distribution of momentum
leaders; find value-pool stocks in those hot sectors; do they outperform
(a) other value-pool stocks, (b) the plain value-pool momentum pick?

Design (same window/horizon as mom_val_overlap_study):
- Screening dates: month-end from 2025-11 to 2026-08-21; forward 20td.
- value pool V = pb_pct(250d) < 0.30, non-ST, listed>=400d (our "便宜池" proxy).
- Hot sectors: sectors contributing >=5 names to the mom top-100 leaders.
- Portfolios:
    VAL_MOM20   : V top20 by mom                (baseline, = prior study C)
    VAL_HOT_MOM : V ∩ hot-sector, top20 by mom  (the refined hypothesis)
    VAL_NOT_HOT : V ∩ not-hot-sector, top20 by mom (control isolating sector heat)
- Also cross-sectional within V: mean fwd return of hot-sector value stocks
  vs non-hot value stocks (equal weight all, not top20).

Panel cached to studies/mom_val_panel.pkl for reuse by future screens.
Also dumps latest momentum top25 to docs/回测记录/筛选快照/.
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

STUDIES = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(STUDIES, "mom_val_panel.pkl")
START, END = "20240901", "20260821"

if os.path.exists(CACHE):
    close, pb = pd.read_pickle(CACHE)
    print(f"panel loaded from cache: {close.shape}")
else:
    pro = get_pro_api(timeout=60)
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

pro = get_pro_api(timeout=60)
basic = pro.stock_basic(fields="ts_code,name,industry,list_date,list_status").set_index("ts_code")
basic = basic[basic["list_status"] == "L"]
idx = pro.index_daily(ts_code="000300.SH", start_date=START, end_date=END,
                      fields="trade_date,close").set_index("trade_date")["close"]
idx.index = pd.to_datetime(idx.index, format="%Y%m%d")

scr = []
for m_end in ["20251128", "20251231", "20260130", "20260227", "20260331",
              "20260430", "20260529", "20260630", "20260731", "20260821"]:
    dts = [c for c in close.columns if c.strftime("%Y%m%d") <= m_end]
    if dts:
        scr.append(dts[-1])

rows, xsec_rows = [], []
latest_dump = None
for d_idx in scr:
    dts = [c for c in close.columns if c <= d_idx]
    if len(dts) < 260:
        continue
    cols_after = [c for c in close.columns if c > d_idx]
    have_fwd = len(cols_after) >= 20
    fwd_col = cols_after[19] if have_fwd else None
    d120, d60, d20 = dts[-121], dts[-61], dts[-21]
    c_now = close[d_idx]
    mom = (c_now / close[d60] - 1) * 50 + (c_now / close[d20] - 1) * 20 \
        + (c_now / close[d120] - 1) * 30

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
    uni["industry"] = basic.loc[uni.index, "industry"]

    if d_idx == scr[-1]:
        latest_dump = uni.sort_values("mom", ascending=False).head(25)

    if not have_fwd:
        continue
    fwd = (close[fwd_col] / c_now - 1) * 100

    # hot sectors from momentum leaders (top100)
    leaders = uni.sort_values("mom", ascending=False).head(100)
    sect_cnt = leaders["industry"].value_counts()
    hot_sectors = set(sect_cnt[sect_cnt >= 5].index)

    V = uni[uni["pb_pct"] < 0.30].sort_values("mom", ascending=False)
    V_hot = V[V["industry"].isin(hot_sectors)]
    V_not = V[~V["industry"].isin(hot_sectors)]

    def pr(sel):
        r = fwd.reindex(sel.index).dropna()
        return (r.mean() if len(r) else np.nan), len(r)

    r_base, n_base = pr(V.head(20))
    r_hot, n_hot = pr(V_hot.sort_values("mom", ascending=False).head(20))
    r_not, n_not = pr(V_not.sort_values("mom", ascending=False).head(20))
    bm = (idx[fwd_col] / idx[d_idx] - 1) * 100

    # cross-sectional within whole value pool (all stocks, not top20)
    r_hot_all = fwd.reindex(V_hot.index).dropna()
    r_not_all = fwd.reindex(V_not.index).dropna()

    rows.append({"date": d_idx.strftime("%Y%m%d"),
                 "hot_sectors": ";".join(sorted(hot_sectors)),
                 "VAL_MOM20": r_base, "VAL_HOT_MOM": r_hot, "VAL_NOT_HOT": r_not,
                 "CSI300": bm, "n_hot": n_hot, "n_not": n_not,
                 "pool_hot": len(r_hot_all), "pool_not": len(r_not_all),
                 "xsec_hot": r_hot_all.mean() if len(r_hot_all) else np.nan,
                 "xsec_not": r_not_all.mean() if len(r_not_all) else np.nan})
    if d_idx == scr[-1]:
        latest_dump = uni.sort_values("mom", ascending=False).head(25)

res = pd.DataFrame(rows).set_index("date")
print("\n=== 组合前瞻20交易日收益(%) ===")
print(res[["VAL_MOM20", "VAL_HOT_MOM", "VAL_NOT_HOT", "CSI300", "n_hot", "pool_hot"]].round(2).to_string())
print("\n=== 价值池内截面: 热门板块 vs 非热门板块 全体等权(%) ===")
print(res[["xsec_hot", "xsec_not", "pool_hot", "pool_not"]].round(2).to_string())
print("\n=== 汇总 ===")
for col in ["VAL_MOM20", "VAL_HOT_MOM", "VAL_NOT_HOT", "CSI300", "xsec_hot", "xsec_not"]:
    s = res[col].dropna()
    ex = (res[col] - res["CSI300"]).dropna()
    print(f"{col:12s} mean={s.mean():+6.2f}%  med={s.median():+6.2f}%  "
          f"excess={ex.mean():+6.2f}pp  win_vs_idx={(ex > 0).mean()*100:.0f}%  n={len(s)}")

res.to_csv(os.path.join(STUDIES, "sector_transfer_results.csv"))

# dump latest momentum top25 as dated snapshot for future reference
if latest_dump is not None:
    out = os.path.join(STUDIES, "../../docs/回测记录/筛选快照",
                       f"全A股动量Top25_{d_idx.strftime('%Y-%m-%d')}.md")
    lines = ["# 全A股动量Top25 快照", "",
             f"> 生成日期: 2026-08-23 | 快照交易日: {d_idx.strftime('%Y-%m-%d')}",
             "> 动量分 = 60日×50% + 20日×20% + 120日×30%(Tushare 全市场快照,剔ST/北交所/上市<400天)",
             "> 用途: 板块分布/情绪兑现检查的存档基线,配套 sector_transfer_results.csv", "",
             "| # | 代码 | 名称 | 行业 | 20日% | 60日% | 120日% |", "|---|------|------|------|-------|-------|--------|"]
    for i, (code, r) in enumerate(latest_dump.iterrows(), 1):
        r20 = (c_now[code] / close[d20][code] - 1) * 100
        r60 = (c_now[code] / close[d60][code] - 1) * 100
        r120 = (c_now[code] / close[d120][code] - 1) * 100
        lines.append(f"| {i} | {code} | {basic.loc[code,'name']} | {r['industry']} | "
                     f"{r20:+.1f} | {r60:+.1f} | {r120:+.1f} |")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nmomentum top25 snapshot saved -> {out}")
