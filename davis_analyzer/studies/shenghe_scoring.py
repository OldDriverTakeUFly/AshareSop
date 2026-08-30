#!/usr/bin/env python3
"""盛合晶微 (688820.SH) 数据采集脚本.

涵盖: 四维评分(估值/趋势/景气/困境) + 5 补充因子引擎 + 股东户数 + 相对估值 + 时效校验。
注意: 2026-04-21 上市, 历史估值仅 ~4 个月, 分位口径 = 上市以来。
用法:
    cd /home/leo/Projects/CodeAgentDashboard && PYTHONPATH=. .venv/bin/python davis_analyzer/studies/shenghe_scoring.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")

import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

TS_CODE = "688820.SH"
NAME = "盛合晶微"
OUT = Path("/tmp/shenghe_data.json")

result: dict = {}

# ═══ 1. 基础信息核对 + 时效校验 ═══
from stockhot.tushare_config import get_pro_api

pro = get_pro_api(timeout=60)

basic = pro.stock_basic(ts_code=TS_CODE)
print("== stock_basic ==")
print(basic.to_string())

db1 = pro.daily_basic(ts_code=TS_CODE, limit=5)
print("\n== daily_basic 最新 ==")
print(db1[["trade_date", "close", "pe_ttm", "pb", "ps", "total_mv", "turnover_rate"]].to_string())

fc = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
print("\n== forecast ==")
print(fc.to_string() if len(fc) else "无")

# ═══ 2. davis_analyzer 四维评分 ═══
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.valuation import fetch_valuation_history, calculate_percentile, detect_cyclical
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.trend import batch_trend
from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.types import StockInfo

client = TushareClient()

fin = fetch_financial_data(client, TS_CODE, periods=12)
print(f"\n== 财务 {len(fin)} 期 ==")
for f in fin:
    print(f"{f.report_period} rev={f.revenue} np={f.net_profit} roe={f.roe} "
          f"yoy_rev={f.yoy_revenue_growth} yoy_np={f.yoy_profit_growth} gm={getattr(f,'grossprofit_margin',None)} rd={getattr(f,'rd_exp',None)} cf={f.operating_cf}")

val_history = fetch_valuation_history(client, TS_CODE)
print(f"\n== 估值历史 {len(val_history)} 点 ==")
if val_history:
    pe_s = [v.pe_ttm for v in val_history if v.pe_ttm]
    pb_s = [v.pb for v in val_history if v.pb]
    ps_s = [v.ps for v in val_history if v.ps]
    latest_v = val_history[0]
    print(f"latest {latest_v.trade_date} pe={latest_v.pe_ttm} pb={latest_v.pb} ps={latest_v.ps} mv={latest_v.total_mv/1e4:.0f}亿")
    if pe_s:
        print(f"PE 分位(上市以来): {calculate_percentile(latest_v.pe_ttm, pe_s)*100:.1f}%")
    if pb_s:
        print(f"PB 分位: {calculate_percentile(latest_v.pb, pb_s)*100:.1f}%")
    if ps_s:
        print(f"PS 分位: {calculate_percentile(latest_v.ps, ps_s)*100:.1f}%")
    for p in [10, 25, 50, 75, 90]:
        print(f"  PB {p}%分位={pd.Series(pb_s).quantile(p/100):.2f}  PS {p}%分位={pd.Series(ps_s).quantile(p/100):.2f}")

pscore = calculate_prosperity_score(fin)
print(f"\n== 景气度 == composite={pscore.composite_score:.2f} rev={pscore.revenue_score:.2f} "
      f"profit={pscore.profit_score:.2f} slope={pscore.slope_score:.2f} dur={pscore.duration_score:.2f} delta_g={pscore.delta_g:.2f}")

# 估值分(上市以来口径, 手动算)
pe_pct = calculate_percentile(latest_v.pe_ttm, pe_s) if val_history and pe_s else 0.5
pb_pct = calculate_percentile(latest_v.pb, pb_s) if val_history and pb_s else 0.5
# 非周期半导体: PE70%+PB30%
val_score = max(0.0, 100.0 - (pe_pct*0.7 + pb_pct*0.3)*100)
print(f"估值分(手动, 上市以来口径): {val_score:.1f}  pe_pct={pe_pct:.3f} pb_pct={pb_pct:.3f}")

# 趋势
trend_score = 50.0
if val_history and len(val_history) >= 3:
    try:
        dates = pd.to_datetime([v.trade_date for v in val_history], format="%Y%m%d")
        daily_pe = pd.Series([v.pe_ttm for v in val_history], index=dates)
        daily_pb = pd.Series([v.pb for v in val_history], index=dates)
        si = StockInfo(ts_code=TS_CODE, name=NAME, industry="半导体", list_status="L", is_cyclical=False)
        trend_map = batch_trend({TS_CODE: (daily_pe, daily_pb)}, {TS_CODE: si})
        trend_score = trend_map.get(TS_CODE, 50.0)
    except Exception as e:
        print("trend err:", e)
print(f"趋势分: {trend_score:.2f}")

latest = fin[0]
dscore = calculate_distress_score(
    eps_history=[f.eps for f in fin], pe_pct=pe_pct, pb_pct=pb_pct,
    debt_ratio=(latest.total_debt or 0)/(latest.total_assets or 1),
    operating_cf=latest.operating_cf or 0.0, total_debt=latest.total_debt or 0.0,
    total_assets=latest.total_assets or 0.0, roe_history=[f.roe for f in fin],
    revenue_history=[f.yoy_revenue_growth or 0.0 for f in fin],
    profit_history=[f.yoy_profit_growth or 0.0 for f in fin],
    delta_g=pscore.delta_g, ts_code=TS_CODE)
print(f"困境分: total={dscore.total_score:.2f} L1={dscore.layer1_score:.2f} L2={dscore.layer2_score:.2f} L3={dscore.layer3_score:.2f}")

davis = calculate_davis_double_score(valuation_score=val_score, prosperity_score=pscore.composite_score,
                                     distress_score=dscore.total_score, trend_score=trend_score,
                                     ts_code=TS_CODE, name=NAME)
print(f"戴维斯双击 final={davis.final_score:.2f}")

result["davis"] = {"final": davis.final_score, "val": val_score, "prosp": pscore.composite_score,
                   "distress": dscore.total_score, "trend": trend_score, "rank": davis.rank}

# ═══ 3. 五个补充因子引擎 ═══
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality

print("\n== 补充因子 ==")
try:
    mom = analyze_momentum(client, TS_CODE)
    if mom:
        print(f"momentum: score={mom.momentum_score} abs={mom.absolute_momentum_score} rs={mom.rs_percentile} returns={mom.window_returns}")
        result["momentum"] = {"score": mom.momentum_score, "abs": mom.absolute_momentum_score,
                              "rs": mom.rs_percentile, "returns": mom.window_returns}
except Exception as e:
    print("momentum err:", e)

div = analyze_dividend(client, TS_CODE)
print(f"dividend: score={div.dividend_score} years={div.consecutive_years} yield={div.latest_yield_pct}")
result["dividend"] = {"score": div.dividend_score, "years": div.consecutive_years, "yield": div.latest_yield_pct}

try:
    fcs = analyze_forecast(client, TS_CODE, pscore)
    print(f"forecast: {fcs}")
    result["forecast"] = str(fcs)
except Exception as e:
    print("forecast err:", e)

try:
    hc = analyze_holder_concentration(client, TS_CODE)
    if hc:
        print(f"holder_conc: score={hc.concentration_score} trend={hc.trend} latest_chg={hc.latest_chg_pct} counts={hc.holder_counts}")
        result["holder_conc"] = {"score": hc.concentration_score, "trend": hc.trend, "chg": hc.latest_chg_pct}
except Exception as e:
    print("hc err:", e)

try:
    pq = analyze_profitability_quality(fin)
    print(f"profitability_quality: score={pq.quality_score} gm={pq.latest_gross_margin} gm_delta={pq.gross_margin_delta} rd={pq.latest_rd_intensity}")
    result["pq"] = {"score": pq.quality_score, "gm": pq.latest_gross_margin, "gm_delta": pq.gross_margin_delta, "rd": pq.latest_rd_intensity}
except Exception as e:
    print("pq err:", e)

# ═══ 4. 股东户数 ═══
print("\n== 股东户数 ==")
try:
    h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num")
    h = h.dropna(subset=["holder_num"]).sort_values("end_date")
    print(h.to_string())
    result["holder_num"] = h.to_dict("records")
except Exception as e:
    print("holder err:", e)

# ═══ 5. 相对估值 ═══
print("\n== 相对估值 ==")
try:
    from stockhot.valuation import analyze_relative_valuation
    rv = analyze_relative_valuation(pro, TS_CODE, NAME)
    print(f"pe_ratio={rv.pe_ratio} pe_ratio_pct={rv.pe_ratio_pct} erp={rv.erp} quadrant={rv.quadrant}({rv.quadrant_label})")
    print(f"index_pe={getattr(rv,'index_pe',None)} index_pe_pct={getattr(rv,'index_pe_pct',None)} rf={getattr(rv,'risk_free_rate',None)}")
    print("verdict:", rv.composite_verdict)
    print("signals:", rv.signals)
    result["rv"] = {"pe_ratio": rv.pe_ratio, "pe_ratio_pct": rv.pe_ratio_pct, "erp": rv.erp,
                    "quadrant": rv.quadrant, "label": rv.quadrant_label, "verdict": str(rv.composite_verdict),
                    "index_pe": getattr(rv, "index_pe", None), "index_pe_pct": getattr(rv, "index_pe_pct", None)}
except Exception as e:
    print("rv err:", e)

# ═══ 6. 日价复核动量(手工) ═══
print("\n== 手工价格动量 ==")
try:
    end = date.today().strftime("%Y%m%d")
    px = pro.daily(ts_code=TS_CODE, start_date="20260401", end_date=end).sort_values("trade_date")
    closes = px.set_index("trade_date")["close"]
    print(f"上市以来交易日 {len(closes)}, 首 {closes.index[0]} close={closes.iloc[0]}, 末 {closes.index[-1]} close={closes.iloc[-1]}")
    for w, lbl in [(20, "20d"), (60, "60d"), (120, "120d")]:
        if len(closes) > w:
            print(f"{lbl}: {(closes.iloc[-1]/closes.iloc[-1-w]-1)*100:.1f}%")
    hi, lo = closes.max(), closes.min()
    print(f"区间高 {hi} 低 {lo}, 现价距高点 {(closes.iloc[-1]/hi-1)*100:.1f}%")
    result["price"] = {"last": float(closes.iloc[-1]), "last_date": closes.index[-1], "hi": float(hi), "lo": float(lo)}
except Exception as e:
    print("px err:", e)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\nsaved -> {OUT}")
