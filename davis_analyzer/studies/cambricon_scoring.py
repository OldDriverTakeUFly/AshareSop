#!/usr/bin/env python3
"""寒武纪 (688256.SH) 深度研报取数脚本：四维评分 + 5 因子 + 股东户数 + 时效 + 相对估值."""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd
from stockhot.tushare_config import get_pro_api

from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.valuation import detect_cyclical

TS_CODE = "688256.SH"
NAME = "寒武纪"

pro = get_pro_api(timeout=60)
client = TushareClient()

# ── 0. 代码核对 + 时效校验 ──
basic = pro.stock_basic(ts_code=TS_CODE, fields="ts_code,name,industry,listing_date")
print("[0]", basic.to_dict("records"))

db1 = pro.daily_basic(ts_code=TS_CODE, limit=3)
print("[freshness] daily_basic latest:", db1[["trade_date", "close", "pe_ttm", "pb", "ps", "total_mv"]].to_dict("records"))

inc = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date", limit=3)
print("[freshness] income latest:", inc.to_dict("records"))

fc = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
fc = fc[pd.to_numeric(fc["ann_date"]) >= 20250101]
print("[freshness] forecast:", fc.to_dict("records") if len(fc) else "无近两年预告")

# ── 1. 财务 ──
fin = fetch_financial_data(client, TS_CODE, periods=12)
print(f"\n[1] 财务 {len(fin)} 期, 最新 {fin[0].report_period}, ts_code核对={fin[0].ts_code}")
for f in fin:
    rev = (f.revenue / 1e8) if f.revenue is not None else 0.0
    np_ = f.net_profit
    try:
        np_ = float(np_) / 1e8
    except Exception:
        pass
    print(f"  {f.report_period}: 营收={rev:.2f}亿 净利={np_} EPS={f.eps} ROE={f.roe} "
          f"营收yoy={f.yoy_revenue_growth} 净利yoy={f.yoy_profit_growth} 毛利率={getattr(f,'grossprofit_margin',None)} "
          f"研发={getattr(f,'rd_exp',None)} OCF={f.operating_cf}")

# ── 2. 估值：分段直连拉全 3 年 ──
end = date.today().strftime("%Y%m%d")
start = (date.today() - timedelta(days=1150)).strftime("%Y%m%d")
frames = []
cur = start
while cur < end:
    nxt = (pd.Timestamp(cur) + pd.Timedelta(days=400)).strftime("%Y%m%d")
    if nxt > end:
        nxt = end
    frames.append(pro.daily_basic(ts_code=TS_CODE, start_date=cur, end_date=nxt,
                                  fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv,turnover_rate"))
    cur = (pd.Timestamp(nxt) + pd.Timedelta(days=1)).strftime("%Y%m%d")
db = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True).sort_values("trade_date")
print(f"\n[2] daily_basic {len(db)} 行, {db['trade_date'].iloc[0]} → {db['trade_date'].iloc[-1]}")
last = db.iloc[-1]
print(f"  最新: {last['trade_date']} close={last['close']} PE={last['pe_ttm']} PB={last['pb']} PS={last['ps']} 市值={last['total_mv']/1e4:.0f}亿")

for col in ["pe_ttm", "pb", "ps"]:
    s = pd.to_numeric(db[col], errors="coerce").dropna()
    if len(s) == 0:
        print(f"  {col}: 全空")
        continue
    cur_v = s.iloc[-1]
    pct = (s < cur_v).sum() / len(s) * 100
    qs = {p: s.quantile(p / 100) for p in [10, 25, 50, 75, 90, 95]}
    qstr = " ".join(f"{p}%={v:.2f}" for p, v in qs.items())
    print(f"  {col}: 当前={cur_v:.2f} 分位={pct:.1f}% (n={len(s)}, 首日有效={s.index.min()}) | {qstr}")

# YTD
db["td"] = db["trade_date"].astype(str)
y2026 = db[db["td"].str.startswith("2026")]
y2025 = db[db["td"].str.startswith("2025")]
if len(y2026):
    first26 = float(y2026["close"].iloc[0]); last26 = float(y2026["close"].iloc[-1])
    prev_close = float(db[db["td"] < y2026["td"].iloc[0]]["close"].iloc[-1])
    print(f"  YTD2026: {prev_close:.0f} → {last26:.0f} ({(last26/prev_close-1)*100:.1f}%)")
    print(f"  2025 区间: 低={float(y2025['close'].min()):.0f} 高={float(y2025['close'].max()):.0f}; 2026 高={float(y2026['close'].max()):.0f} 低={float(y2026['close'].min()):.0f}")

# ── 3. 景气度 ──
pscore = calculate_prosperity_score(fin)
stage = classify_stock_stage(pscore)
print(f"\n[3] 景气度 composite={pscore.composite_score:.1f} ΔG={pscore.delta_g} 阶段={stage}")
print(f"  营收分={pscore.revenue_score} 利润分={pscore.profit_score} 斜率={pscore.slope_score} 持续={pscore.duration_score}")

# ── 4. 五因子 ──
print("\n[4] 五因子引擎")
mom = analyze_momentum(client, TS_CODE)
if mom:
    print(f"  momentum: score={mom.momentum_score} abs={mom.absolute_momentum_score} rs={mom.rs_percentile}")
    print(f"  window_returns={mom.window_returns}")
# 手工复核动量（防缓存缺口）
px = pro.daily(ts_code=TS_CODE, start_date=(date.today() - timedelta(days=400)).strftime("%Y%m%d"), end_date=end,
               fields="ts_code,trade_date,close")
px = px.sort_values("trade_date").reset_index(drop=True)
closes = px["close"].astype(float)
for w, lbl in [(60, "60d"), (120, "120d"), (250, "250d")]:
    if len(closes) > w:
        print(f"  manual {lbl}: {(closes.iloc[-1]/closes.iloc[-w-1]-1)*100:.1f}%")

div = analyze_dividend(client, TS_CODE)
print(f"  dividend: score={div.dividend_score} years={div.consecutive_years} yield={div.latest_yield_pct}")

fcs = analyze_forecast(client, TS_CODE, pscore)
print(f"  forecast: {fcs}")
rev_f = analyze_forecast_revision(client, TS_CODE)
print(f"  forecast_revision: {rev_f}")

hc = analyze_holder_concentration(client, TS_CODE)
if hc:
    print(f"  holder_conc: score={hc.concentration_score} trend={hc.trend} latest_chg={hc.latest_chg_pct}")
    print(f"  holder_counts={hc.holder_counts} periods={hc.periods}")

pq = analyze_profitability_quality(fin)
print(f"  profitability: score={pq.quality_score} gm={pq.latest_gross_margin} gm_delta={pq.gross_margin_delta} rd={pq.latest_rd_intensity}")

# ── 5. 股东户数（直连，防 NaN）──
h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num")
h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(10)
print("\n[5] 股东户数:")
for _, r in h.iterrows():
    print(f"  {r['end_date']} (ann {r['ann_date']}): {int(r['holder_num']):,}")

# ── 6. 相对估值 ──
print("\n[6] 相对估值")
try:
    from stockhot.valuation import analyze_relative_valuation
    rv = analyze_relative_valuation(pro, TS_CODE)
    print(f"  pe_ratio={rv.pe_ratio} pe_ratio_pct={rv.pe_ratio_pct} erp={rv.erp} quadrant={rv.quadrant}")
    print(f"  index_pe={rv.index_pe} index_pe_pct={rv.index_pe_pct} rf={rv.risk_free_rate} signals={rv.signals}")
except Exception as e:
    print("  relative valuation error:", e)

print("\nDONE")
