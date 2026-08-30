#!/usr/bin/env python3
"""长川科技 (300604.SZ) 单股四维评分 + 5 因子 + 股东户数 + 相对估值 + 时效校验.

用法:
    cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python davis_analyzer/studies/changchuan_scoring.py

输出: .sisyphus/evidence/changchuan/changchuan_engine_20260830.json + stdout
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")

import os
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd

from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo
from davis_analyzer.valuation import (
    calculate_percentile,
    calculate_valuation_score,
    detect_cyclical,
    fetch_valuation_history,
)
from stockhot.tushare_config import get_pro_api

TS_CODE = "300604.SZ"
NAME = "长川科技"
OUT = Path(".sisyphus/evidence/changchuan/changchuan_engine_20260830.json")

pro = get_pro_api(timeout=60)

# ── 0. 代码核对 + 时效校验 ──
basic = pro.stock_basic(ts_code=TS_CODE, fields="ts_code,name,industry,list_date")
print("== stock_basic 核对 ==")
print(basic.to_string(index=False))

db1 = pro.daily_basic(ts_code=TS_CODE, limit=1)
inc1 = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
fc1 = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
fc1 = fc1[pd.to_numeric(fc1["ann_date"], errors="coerce") >= 20250101] if len(fc1) else fc1
print(f"最新交易日: {db1.iloc[0]['trade_date'] if len(db1) else 'none'}")
print(f"最新报告期: {inc1.iloc[0]['end_date']} ann={inc1.iloc[0]['ann_date']}" if len(inc1) else "income none")
if len(fc1):
    print("最新预告(2025+):")
    print(fc1.sort_values("ann_date", ascending=False).head(5).to_string(index=False))

# ── 1. 财务（12 季）──
client = TushareClient()
fin = fetch_financial_data(client, TS_CODE, periods=12)
print(f"\n== 财务 {len(fin)} 期, 最新 {fin[0].report_period} ==")
for fd in fin:
    yr = f"{fd.yoy_revenue_growth*100:.1f}%" if fd.yoy_revenue_growth is not None else "NA"
    yp = f"{fd.yoy_profit_growth*100:.1f}%" if fd.yoy_profit_growth is not None else "NA"
    print(f"{fd.report_period}: rev={fd.revenue/1e8:.2f}亿 np={fd.net_profit/1e8:.3f}亿 eps={fd.eps} roe={fd.roe} yoyR={yr} yoyP={yp} gm={getattr(fd,'grossprofit_margin',None)} rd={getattr(fd,'rd_exp',None)}")

# ── 2. 估值：全量 3 年 daily_basic 分段直连（≤500天/段）──
end_d = date(2026, 8, 30)
start_d = end_d - timedelta(days=1095)
frames = []
cur = start_d
while cur < end_d:
    seg_end = min(cur + timedelta(days=499), end_d)
    d = pro.daily_basic(ts_code=TS_CODE, start_date=cur.strftime("%Y%m%d"),
                        end_date=seg_end.strftime("%Y%m%d"),
                        fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,turnover_rate,dv_ttm")
    if len(d):
        frames.append(d)
    cur = seg_end + timedelta(days=1)
db = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True)
db = db.sort_values("trade_date").reset_index(drop=True)
print(f"\n== daily_basic 全量 {len(db)} 交易日, {db['trade_date'].iloc[0]} ~ {db['trade_date'].iloc[-1]} ==")
latest_row = db.iloc[-1]
print(f"最新: {latest_row['trade_date']} PE={latest_row['pe_ttm']} PB={latest_row['pb']} PS={latest_row['ps']} MV={float(latest_row['total_mv'])/1e4:.1f}亿")

pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
for nm, s in [("PE", pe), ("PB", pb), ("PS", ps), ("MV(亿)", mv / 1e4)]:
    cur_v = s.iloc[-1]
    pct = (s < cur_v).sum() / len(s) * 100
    qs = {p: s.quantile(p / 100) for p in [10, 25, 50, 75, 90, 95]}
    print(f"{nm}: cur={cur_v:.2f} pct={pct:.1f}% " + " ".join(f"p{p}={v:.2f}" for p, v in qs.items()))

pe_pct = (pe < pe.iloc[-1]).sum() / len(pe)
pb_pct = (pb < pb.iloc[-1]).sum() / len(pb)

# YTD 涨幅（手工复核）
dy = pro.daily(ts_code=TS_CODE, start_date="20251231", end_date="20260830",
               fields="ts_code,trade_date,close,pre_close")
dy = dy.sort_values("trade_date")
last_close = float(dy.iloc[-1]["close"])
prev_year_close = float(dy.iloc[0]["pre_close"])
print(f"\nYTD: {prev_year_close} -> {last_close} = {(last_close/prev_year_close-1)*100:.1f}%")

# 1/3/5年动量手工复核
for days, label in [(60, "60d"), (120, "120d"), (250, "250d")]:
    d0 = (end_d - timedelta(days=int(days * 1.6))).strftime("%Y%m%d")
    dd = pro.daily(ts_code=TS_CODE, start_date=d0, end_date=end_d.strftime("%Y%m%d"),
                   fields="trade_date,close").sort_values("trade_date")
    if len(dd) > days // 2:
        base = float(dd.iloc[max(0, len(dd) - days)]["close"])
        print(f"{label} return: {(last_close/base-1)*100:.1f}% (base {base} @ {dd.iloc[max(0, len(dd)-days)]['trade_date']})")

# ── 3. 四维评分 ──
# 估值历史（用全量手工构造供引擎）
from davis_analyzer.types import ValuationData
val_rows = [r for _, r in db.iterrows() if not pd.isna(r["pe_ttm"]) and not pd.isna(r["pb"])]
val_history = [
    ValuationData(ts_code=TS_CODE, trade_date=str(r["trade_date"]), pe_ttm=float(r["pe_ttm"]),
                  pb=float(r["pb"]), ps=float(r["ps"]) if not pd.isna(r["ps"]) else None,
                  total_mv=float(r["total_mv"]))
    for r in val_rows
]
val_history.sort(key=lambda v: v.trade_date, reverse=True)
industry = str(basic.iloc[0]["industry"]) if len(basic) else ""
is_cyc = detect_cyclical(industry)
stock_info = StockInfo(ts_code=TS_CODE, name=NAME, industry=industry, list_status="L", is_cyclical=is_cyc)
val_score, pe_pct_e, pb_pct_e = calculate_valuation_score(val_history, is_cyc)
print(f"\n估值分: {val_score:.2f} pe_pct={pe_pct_e*100:.1f}% pb_pct={pb_pct_e*100:.1f}% n={len(val_history)} 周期={is_cyc} 行业={industry}")

pscore = calculate_prosperity_score(fin)
print(f"景气度: composite={pscore.composite_score:.2f} rev={pscore.revenue_score:.2f} prof={pscore.profit_score:.2f} slope={pscore.slope_score:.2f} dur={pscore.duration_score:.2f} ΔG={pscore.delta_g}")

# 趋势
from davis_analyzer.trend import batch_trend, calculate_monthly_trend, calculate_trend_slope, calculate_trend_acceleration
dates = pd.to_datetime([v.trade_date for v in val_history], format="%Y%m%d")
daily_pe = pd.Series([v.pe_ttm for v in val_history], index=dates).sort_index()
daily_pb = pd.Series([v.pb for v in val_history], index=dates).sort_index()
trend_map = batch_trend({TS_CODE: (daily_pe, daily_pb)}, {TS_CODE: stock_info})
trend_score = trend_map.get(TS_CODE, 50.0)
mpe, mpb = calculate_monthly_trend(daily_pe, daily_pb)
print(f"趋势分: {trend_score:.2f} pe_slope={calculate_trend_slope(mpe):.4f} pb_slope={calculate_trend_slope(mpb):.4f} pe_accel={calculate_trend_acceleration(mpe):.4f} 月点数={len(mpe)}")

latest = fin[0]
distress = calculate_distress_score(
    eps_history=[f.eps for f in fin], pe_pct=pe_pct_e, pb_pct=pb_pct_e,
    debt_ratio=(latest.total_debt or 0) / (latest.total_assets or 1),
    operating_cf=latest.operating_cf or 0.0, total_debt=latest.total_debt or 0.0,
    total_assets=latest.total_assets or 0.0, roe_history=[f.roe for f in fin],
    revenue_history=[f.yoy_revenue_growth or 0.0 for f in fin],
    profit_history=[f.yoy_profit_growth or 0.0 for f in fin],
    delta_g=pscore.delta_g, ts_code=TS_CODE)
print(f"困境分: {distress.total_score:.2f} L1={distress.layer1_score:.2f} L2={distress.layer2_score:.2f} L3={distress.layer3_score:.2f}")

davis = calculate_davis_double_score(valuation_score=val_score, prosperity_score=pscore.composite_score,
                                     distress_score=distress.total_score, trend_score=trend_score,
                                     ts_code=TS_CODE, name=NAME)
print(f"戴维斯双击: final={davis.final_score:.2f}")

# ── 4. 5 因子 ──
print("\n== 5 因子 ==")
mom = analyze_momentum(client, TS_CODE)
if mom:
    print(f"动量: score={mom.momentum_score} abs={mom.absolute_momentum_score} rs={mom.rs_percentile} windows={mom.window_returns}")
div = analyze_dividend(client, TS_CODE)
print(f"股息: score={div.dividend_score} years={div.consecutive_years} yield={div.latest_yield_pct}")
fc = analyze_forecast(client, TS_CODE, pscore)
print(f"预告: {fc}")
rev = analyze_forecast_revision(client, TS_CODE)
print(f"预告修正: {rev}")
hc = analyze_holder_concentration(client, TS_CODE)
if hc:
    print(f"筹码: score={hc.concentration_score} trend={hc.trend} chg={hc.latest_chg_pct} counts={hc.holder_counts} periods={hc.periods}")
pq = analyze_profitability_quality(fin)
print(f"盈利质量: score={pq.quality_score} gm={pq.latest_gross_margin} gm_delta={pq.gross_margin_delta} rd={pq.latest_rd_intensity}")

# ── 5. 股东户数近 8 期 ──
h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num").dropna(subset=["holder_num"])
h = h.sort_values("end_date").tail(8)
print("\n== 股东户数 ==")
prev = None
for _, r in h.iterrows():
    num = int(r["holder_num"])
    chg = f"{(num-prev)/prev*100:+.1f}%" if prev else "基期"
    print(f"{r['end_date']}: {num:,} ({chg})")
    prev = num

# ── 6. 相对估值 ──
from stockhot.valuation import analyze_relative_valuation
rv = analyze_relative_valuation(pro, TS_CODE, NAME)
print(f"\n== 相对估值 ==\npe_ratio={rv.pe_ratio} pe_ratio_pct={rv.pe_ratio_pct} erp={rv.erp} quadrant={rv.quadrant} ({rv.quadrant_label}) verdict={rv.composite_verdict}")
print(f"index_pe={getattr(rv,'index_pe',None)} index_pe_pct={getattr(rv,'index_pe_pct',None)} risk_free={getattr(rv,'risk_free_rate',None)}")

# ── 7. 2026中报关键数（income 直查）──
inc26 = pro.income(ts_code=TS_CODE, start_date="20260401", end_date="20261231",
                   fields="ts_code,ann_date,end_date,revenue,n_income,n_income_attr_p")
inc26 = inc26.sort_values("end_date", ascending=False)
print("\n== 2026 income（累计口径）==")
print(inc26.to_string(index=False))
for _, r in inc26.iterrows():
    print(f"{r['end_date']}: rev={float(r['revenue'])/1e8:.2f}亿 归母={float(r['n_income_attr_p'])/1e8:.3f}亿")

# fina_indicator 毛利率/研发
fi = pro.fina_indicator(ts_code=TS_CODE, start_date="20240101", end_date="20261231",
                        fields="ts_code,end_date,grossprofit_margin,rd_exp,debt_to_assets,ocf_to_profit")
print("\n== fina_indicator ==")
print(fi.sort_values("end_date", ascending=False).head(10).to_string(index=False))

OUT.parent.mkdir(parents=True, exist_ok=True)
summary = {
    "ts_code": TS_CODE, "name": NAME,
    "valuation": {"score": val_score, "pe_pct": pe_pct_e, "pb_pct": pb_pct_e, "n_days": len(val_history),
                  "latest_pe": float(pe.iloc[-1]), "latest_pb": float(pb.iloc[-1]), "latest_ps": float(ps.iloc[-1]),
                  "latest_mv_yi": float(mv.iloc[-1]) / 1e4},
    "prosperity": {"composite": pscore.composite_score, "delta_g": pscore.delta_g},
    "distress": distress.total_score, "trend": trend_score, "davis_final": davis.final_score,
    "ytd_pct": (last_close / prev_year_close - 1) * 100,
}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print(f"\nsaved -> {OUT}")
