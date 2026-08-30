#!/usr/bin/env python3
"""海光信息 (688041.SH) 研报取数脚本：四维评分 + 5 补充因子 + 股东户数 + 相对估值 + 时效校验."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")

import os
from dotenv import load_dotenv
load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.valuation import fetch_valuation_history, calculate_valuation_score, detect_cyclical
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.trend import batch_trend
from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.types import StockInfo
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality

TS_CODE = "688041.SH"
NAME = "海光信息"

client = TushareClient()

# ── 0. 时效校验 ──
from stockhot.tushare_config import get_pro_api
pro = get_pro_api(timeout=30)
db1 = pro.daily_basic(ts_code=TS_CODE, limit=1)
print("== 时效校验 ==")
print("daily_basic 最新交易日:", db1.iloc[0]["trade_date"], "| 收盘", db1.iloc[0]["close"],
      "| PE", db1.iloc[0]["pe_ttm"], "| PB", db1.iloc[0]["pb"], "| PS", db1.iloc[0]["ps"],
      "| 总市值(亿)", float(db1.iloc[0]["total_mv"])/1e4)
inc1 = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date", limit=1)
print("income 最新报告期:", inc1.iloc[0]["end_date"], "披露日:", inc1.iloc[0]["ann_date"])
fc1 = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
fc1 = fc1[pd.to_numeric(fc1["ann_date"]) >= 20250101].sort_values("ann_date")
print("近两年业绩预告:")
print(fc1.to_string(index=False))

# ── 1. 财务 ──
fin = fetch_financial_data(client, TS_CODE, periods=12)
print("\n== 财务（12期）==  最新:", fin[0].report_period, "ts_code核对:", fin[0].ts_code)
for f in fin:
    print(f"{f.report_period} rev={f.revenue/1e8:.2f}亿 np={f.net_profit/1e8 if f.net_profit else 0:.2f}亿 "
          f"eps={f.eps} roe={f.roe} yoy_rev={f.yoy_revenue_growth} yoy_np={f.yoy_profit_growth} "
          f"gm={getattr(f,'grossprofit_margin',None)} rd={getattr(f,'rd_exp',None)}")

# ── 2. 估值 3年分位（直连分段拉取防缩水）──
end = date.today().strftime("%Y%m%d")
start = (date.today() - timedelta(days=1150)).strftime("%Y%m%d")
frames = []
cur = start
while cur < end:
    nxt = (pd.to_datetime(cur, format="%Y%m%d") + timedelta(days=400)).strftime("%Y%m%d")
    d = pro.daily_basic(ts_code=TS_CODE, start_date=cur, end_date=min(nxt, end),
                        fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,close,turnover_rate")
    if len(d):
        frames.append(d)
    cur = nxt
db = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True).sort_values("trade_date").reset_index(drop=True)
print(f"\n== 估值：{len(db)} 个交易日, 末行 {db['trade_date'].iloc[-1]} ==")
for col in ["pe_ttm", "pb", "ps"]:
    s = pd.to_numeric(db[col], errors="coerce").dropna()
    latest = s.iloc[-1]
    pct = (s < latest).sum() / len(s) * 100
    q = {p: s.quantile(p/100) for p in [10, 25, 50, 75, 90, 95]}
    print(f"{col}: 当前={latest:.2f} 分位={pct:.1f}% | " +
          " ".join(f"P{p}={v:.2f}" for p, v in q.items()))
mv = pd.to_numeric(db["total_mv"], errors="coerce")
print(f"总市值最新: {mv.iloc[-1]/1e4:.1f}亿")
# YTD
db["trade_date"] = db["trade_date"].astype(str)
close = pd.to_numeric(db["close"], errors="coerce")
y2026 = db[db["trade_date"] >= "20260101"]
print("2026 年初收盘:", pd.to_numeric(y2026["close"], errors="coerce").iloc[0] if len(y2026) else "N/A", "最新:", close.iloc[-1])

# ── 3. 景气度 ──
pscore = calculate_prosperity_score(fin)
stage = classify_stock_stage(pscore)
print(f"\n== 景气度 == composite={pscore.composite_score} delta_g={pscore.delta_g} 阶段={stage}")
print(f"  revenue_score={pscore.revenue_score} profit_score={pscore.profit_score} slope={pscore.slope_score} duration={pscore.duration_score}")

# ── 4. 估值评分(引擎) + 趋势 ──
val_history = fetch_valuation_history(client, TS_CODE)
print(f"\n== fetch_valuation_history 点数: {len(val_history)} ==")
stock_df = client.get_stock_list()
row = stock_df[stock_df["ts_code"] == TS_CODE]
industry = str(row.iloc[0]["industry"]) if not row.empty else "半导体"
info = StockInfo(ts_code=TS_CODE, name=NAME, industry=industry, list_status="L", is_cyclical=detect_cyclical(industry))
print("行业:", industry, "周期股:", info.is_cyclical, "名称核对:", row.iloc[0]["name"] if not row.empty else "?")
if val_history and len(val_history) > 100:
    val_score, pe_pct, pb_pct = calculate_valuation_score(val_history, info.is_cyclical)
    dates = pd.to_datetime([v.trade_date for v in val_history], format="%Y%m%d")
    daily_pe = pd.Series([v.pe_ttm for v in val_history], index=dates)
    daily_pb = pd.Series([v.pb for v in val_history], index=dates)
    trend_map = batch_trend({TS_CODE: (daily_pe, daily_pb)}, {TS_CODE: info})
    trend_score = trend_map.get(TS_CODE, 50.0)
    print(f"估值分(引擎)={val_score:.2f} pe_pct={pe_pct*100:.1f}% pb_pct={pb_pct*100:.1f}% 趋势分={trend_score:.2f}")
else:
    val_score, pe_pct, pb_pct, trend_score = 50.0, 0.5, 0.5, 50.0
    print("val_history 不足，用直连分位替代")

# ── 5. 困境 + 戴维斯 ──
latest_fd = fin[0]
eps_history = [f.eps for f in fin]
roe_history = [f.roe for f in fin]
rev_g = [f.yoy_revenue_growth or 0.0 for f in fin]
prof_g = [f.yoy_profit_growth or 0.0 for f in fin]
distress = calculate_distress_score(
    eps_history=eps_history, pe_pct=pe_pct, pb_pct=pb_pct,
    debt_ratio=(latest_fd.total_debt or 0)/(latest_fd.total_assets or 1),
    operating_cf=latest_fd.operating_cf or 0.0,
    total_debt=latest_fd.total_debt or 0.0, total_assets=latest_fd.total_assets or 0.0,
    roe_history=roe_history, revenue_history=rev_g, profit_history=prof_g,
    delta_g=pscore.delta_g, ts_code=TS_CODE)
davis = calculate_davis_double_score(valuation_score=val_score, prosperity_score=pscore.composite_score,
                                     distress_score=distress.total_score, trend_score=trend_score,
                                     ts_code=TS_CODE, name=NAME)
print(f"\n== 困境分 total={distress.total_score:.2f} L1={distress.layer1_score} L2={distress.layer2_score} L3={distress.layer3_score}")
print(f"== 戴维斯 final={davis.final_score:.2f} rank={davis.rank}")

# ── 6. 5 补充因子 ──
print("\n== 补充因子 ==")
mom = analyze_momentum(client, TS_CODE)
if mom:
    print(f"动量: score={mom.momentum_score} abs={mom.absolute_momentum_score} rs_pct={mom.rs_percentile} windows={mom.window_returns}")
div = analyze_dividend(client, TS_CODE)
print(f"红利: score={div.dividend_score} 连续{div.consecutive_years}年 yield={div.latest_yield_pct}%")
fc = analyze_forecast(client, TS_CODE, pscore)
print(f"预告: {fc}")
hc = analyze_holder_concentration(client, TS_CODE)
if hc:
    print(f"筹码: score={hc.concentration_score} trend={hc.trend} 最新环比={hc.latest_chg_pct}")
pq = analyze_profitability_quality(fin)
print(f"盈利质量: score={pq.quality_score} 毛利率={pq.latest_gross_margin} Δ={pq.gross_margin_delta} 研发强度={pq.latest_rd_intensity}")

# ── 7. 股东户数 ──
h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num")
h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(10)
print("\n== 股东户数 ==")
prev = None
for _, r in h.iterrows():
    chg = f"{(int(r['holder_num'])-prev)/prev*100:+.1f}%" if prev else "基期"
    print(f"{r['end_date']}: {int(r['holder_num']):,} ({chg})")
    prev = int(r["holder_num"])

# ── 8. 相对估值 ──
print("\n== 相对估值 ==")
try:
    from stockhot.valuation import analyze_relative_valuation
    rv = analyze_relative_valuation(TS_CODE)
    print(json.dumps({k: getattr(rv, k) for k in dir(rv) if not k.startswith("_") and not callable(getattr(rv, k))},
                     ensure_ascii=False, default=str, indent=1))
except Exception as e:
    print("相对估值失败:", e)
