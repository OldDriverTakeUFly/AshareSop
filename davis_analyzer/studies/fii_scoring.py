#!/usr/bin/env python3
"""工业富联 (601138.SH) 研报取数脚本：四维评分 + 5 补充因子 + 股东户数 + 相对估值 + 时效校验."""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd
from loguru import logger

from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score, calculate_delta_g
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import batch_trend
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo
from davis_analyzer.valuation import (
    calculate_percentile,
    calculate_valuation_score,
    detect_cyclical,
)

TS_CODE = "601138.SH"
NAME = "工业富联"

client = TushareClient()
pro = client._get_client() if hasattr(client, "_get_client") else None

# verify identity
sl = client.get_stock_list()
row = sl[sl["ts_code"] == TS_CODE]
logger.info("identity: {}", row.iloc[0][["name", "industry"]].to_dict() if not row.empty else "NOT FOUND")
industry = str(row.iloc[0]["industry"]) if not row.empty else ""
info = StockInfo(ts_code=TS_CODE, name=NAME, industry=industry, list_status="L",
                 is_cyclical=detect_cyclical(industry))

# ── 1. freshness ──
from stockhot.tushare_config import get_pro_api

pro = get_pro_api(timeout=60)
db1 = pro.daily_basic(ts_code=TS_CODE, limit=3)
inc1 = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date", limit=3)
fc_raw = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
fresh = {
    "latest_trade": db1.iloc[0]["trade_date"],
    "latest_close": float(db1.iloc[0]["close"]),
    "latest_pe_ttm": float(db1.iloc[0]["pe_ttm"]) if pd.notna(db1.iloc[0]["pe_ttm"]) else None,
    "latest_pb": float(db1.iloc[0]["pb"]),
    "latest_total_mv_yi": float(db1.iloc[0]["total_mv"]) / 1e4,
    "income_latest_period": inc1.iloc[0]["end_date"],
    "income_latest_ann": inc1.iloc[0]["ann_date"],
}
print("FRESHNESS:", json.dumps(fresh, ensure_ascii=False))
if len(fc_raw):
    fc_raw = fc_raw[pd.to_numeric(fc_raw["ann_date"]) >= 20260101]
    for _, r in fc_raw.iterrows():
        print("FORECAST:", r.to_dict())

# ── 2. financials ──
fin = fetch_financial_data(client, TS_CODE, periods=12)
print("FIN PERIODS:", [(f.report_period, round((f.revenue or 0) / 1e8, 2), round(float(f.net_profit or 0) / 1e8, 2),
                        None if f.yoy_revenue_growth is None else round(f.yoy_revenue_growth * 100, 2),
                        None if f.yoy_profit_growth is None else round(f.yoy_profit_growth * 100, 2),
                        f.roe, None if f.operating_cf is None else round(f.operating_cf / 1e8, 2)) for f in fin])

# ── 3. valuation 3y via segmented pro.daily_basic (avoid cache truncation) ──
frames = []
end = date(2026, 8, 30)
start = end - timedelta(days=1120)
cur = start
while cur < end:
    nxt = min(cur + timedelta(days=450), end)
    seg = pro.daily_basic(ts_code=TS_CODE, start_date=cur.strftime("%Y%m%d"),
                          end_date=nxt.strftime("%Y%m%d"),
                          fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,close")
    frames.append(seg)
    cur = nxt + timedelta(days=1)
db = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True).sort_values("trade_date")
print("VALUATION rows:", len(db), "first:", db["trade_date"].iloc[0], "last:", db["trade_date"].iloc[-1])
for col in ["pe_ttm", "pb", "ps"]:
    s = pd.to_numeric(db[col], errors="coerce").dropna()
    cur_v = s.iloc[-1]
    pct = (s < cur_v).sum() / len(s) * 100
    qs = {p: round(float(s.quantile(p / 100)), 2) for p in [10, 25, 50, 75, 90, 95]}
    print(f"{col}: cur={cur_v:.2f} pct={pct:.1f}% quantiles={qs}")
mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
print(f"total_mv: {mv.iloc[-1]/1e4:.1f}亿  min={mv.min()/1e4:.0f} max={mv.max()/1e4:.0f}")

# manual ValuationData list
from davis_analyzer.types import ValuationData
val_list = []
for _, r in db.iterrows():
    if pd.notna(r["pe_ttm"]) and pd.notna(r["pb"]):
        val_list.append(ValuationData(ts_code=TS_CODE, trade_date=str(r["trade_date"]),
                                      pe_ttm=float(r["pe_ttm"]), pb=float(r["pb"]),
                                      ps=float(r["ps"]) if pd.notna(r["ps"]) else None,
                                      total_mv=float(r["total_mv"]) if pd.notna(r["total_mv"]) else None))
val_list.sort(key=lambda v: v.trade_date, reverse=True)
pe_pct = calculate_percentile(val_list[0].pe_ttm, [v.pe_ttm for v in val_list])
pb_pct = calculate_percentile(val_list[0].pb, [v.pb for v in val_list])
val_score, pe_pct2, pb_pct2 = calculate_valuation_score(val_list, info.is_cyclical)
print(f"VAL SCORE={val_score:.2f} pe_pct={pe_pct2*100:.1f}% pb_pct={pb_pct2*100:.1f}% cyclical={info.is_cyclical}")

# ── 4. trend ──
dates = pd.to_datetime([v.trade_date for v in val_list], format="%Y%m%d")
daily_pe = pd.Series([v.pe_ttm for v in val_list], index=dates).sort_index()
daily_pb = pd.Series([v.pb for v in val_list], index=dates).sort_index()
trend_score = batch_trend({TS_CODE: (daily_pe, daily_pb)}, {TS_CODE: info}).get(TS_CODE, 50.0)
print(f"TREND SCORE={trend_score:.2f}")

# ── 5. prosperity ──
pscore = calculate_prosperity_score(fin)
print(f"PROSPERITY composite={pscore.composite_score:.2f} rev={pscore.revenue_score:.2f} "
      f"profit={pscore.profit_score:.2f} slope={pscore.slope_score:.2f} dur={pscore.duration_score:.2f} "
      f"delta_g={pscore.delta_g:.2f}")

# ── 6. distress ──
latest = fin[0]
eps_h = [f.eps for f in fin]
roe_h = [f.roe for f in fin]
rev_g = [f.yoy_revenue_growth or 0.0 for f in fin]
prof_g = [f.yoy_profit_growth or 0.0 for f in fin]
distress = calculate_distress_score(
    eps_history=eps_h, pe_pct=pe_pct2, pb_pct=pb_pct2,
    debt_ratio=(latest.total_debt or 0) / (latest.total_assets or 1),
    operating_cf=latest.operating_cf or 0.0, total_debt=latest.total_debt or 0.0,
    total_assets=latest.total_assets or 0.0, roe_history=roe_h,
    revenue_history=rev_g, profit_history=prof_g, delta_g=pscore.delta_g, ts_code=TS_CODE)
print(f"DISTRESS total={distress.total_score:.2f} L1={distress.layer1_score:.2f} "
      f"L2={distress.layer2_score:.2f} L3={distress.layer3_score:.2f}")

# ── 7. davis ──
davis = calculate_davis_double_score(valuation_score=val_score,
                                     prosperity_score=pscore.composite_score,
                                     distress_score=distress.total_score,
                                     trend_score=trend_score, ts_code=TS_CODE, name=NAME)
print(f"DAVIS final={davis.final_score:.2f} rank={davis.rank}")

# ── 8. five factors ──
mom = analyze_momentum(client, TS_CODE)
if mom:
    print(f"MOMENTUM score={mom.momentum_score} abs={mom.absolute_momentum_score} rs={mom.rs_percentile} windows={mom.window_returns}")
div = analyze_dividend(client, TS_CODE)
print(f"DIVIDEND score={div.dividend_score} years={div.consecutive_years} yield={div.latest_yield_pct}")
fc = analyze_forecast(client, TS_CODE, pscore)
print(f"FORECAST_SIGNAL: {fc}")
rev_fc = analyze_forecast_revision(client, TS_CODE)
print(f"FORECAST_REVISION: {rev_fc}")
hc = analyze_holder_concentration(client, TS_CODE)
if hc:
    print(f"HOLDER_CONC score={hc.concentration_score} trend={hc.trend} latest_chg={hc.latest_chg_pct} counts={hc.holder_counts}")
pq = analyze_profitability_quality(fin)
print(f"PROFIT_QUALITY score={pq.quality_score} gm={pq.latest_gross_margin} gm_delta={pq.gross_margin_delta} rd={pq.latest_rd_intensity}")

# ── 9. holder number trend ──
h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num")
h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(10)
print("HOLDER_NUMS:", [(r["end_date"], int(r["holder_num"])) for _, r in h.iterrows()])

# ── 10. YTD price ──
d2026 = pro.daily(ts_code=TS_CODE, start_date="20251230", end_date="20260830",
                  fields="ts_code,trade_date,close,pre_close")
d2026 = d2026.sort_values("trade_date")
if len(d2026):
    base = float(d2026.iloc[0]["pre_close"])
    lastc = float(d2026.iloc[-1]["close"])
    print(f"YTD: base_prev_close={base} latest={lastc} ytd={(lastc/base-1)*100:.1f}% last_date={d2026.iloc[-1]['trade_date']}")
    # 250d range
    s = pd.to_numeric(d2026["close"], errors="coerce")
    print(f"2026 range: low={s.min()} high={s.max()}")

# ── 11. relative valuation ──
from stockhot.valuation import analyze_relative_valuation
rv = analyze_relative_valuation(TS_CODE)
print("RELATIVE_VAL:", rv)
