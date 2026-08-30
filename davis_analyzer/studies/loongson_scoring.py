#!/usr/bin/env python3
"""龙芯中科 (688047.SH) 数据采集脚本：四维评分 + 5 补充因子 + 时效 + 股东户数 + 相对估值.

亏损标的：PE 失效，估值锚 PB/PS。输出 JSON 到 stdout + /tmp/loongson.json
"""
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

from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.valuation import (
    calculate_percentile,
    calculate_valuation_score,
    detect_cyclical,
    fetch_valuation_history,
)
from stockhot.tushare_config import get_pro_api

TS_CODE = "688047.SH"
NAME = "龙芯中科"

client = TushareClient()
pro = get_pro_api(timeout=30)
out: dict = {"ts_code": TS_CODE, "name": NAME}

# ── 0. 身份核对 ──
basic = pro.stock_basic(ts_code=TS_CODE, fields="ts_code,name,industry,listing_date")
out["basic"] = basic.iloc[0].to_dict()

# ── 1. 财务 12 期 ──
fin = fetch_financial_data(client, TS_CODE, periods=12)
out["financial"] = [
    {
        "period": f.report_period,
        "revenue_yi": round((f.revenue or 0) / 1e8, 3),
        "net_profit_yi": round((f.net_profit or 0) / 1e8, 3),
        "eps": f.eps,
        "roe": f.roe,
        "op_cf_yi": round((f.operating_cf or 0) / 1e8, 3),
        "total_assets_yi": round((f.total_assets or 0) / 1e8, 2),
        "debt_ratio": round((f.total_debt or 0) / (f.total_assets or 1), 4),
        "yoy_rev": f.yoy_revenue_growth,
        "yoy_prof": f.yoy_profit_growth,
    }
    for f in fin
]

# ── 2. 估值历史（PE 失效校验 + PB/PS 分位，分段拉全 3 年）──
end = date.today().strftime("%Y%m%d")
start = (date.today() - timedelta(days=1150)).strftime("%Y%m%d")
frames = []
cur = start
while cur < end:
    nxt = (pd.to_datetime(cur, format="%Y%m%d") + timedelta(days=490)).strftime("%Y%m%d")
    frames.append(
        pro.daily_basic(ts_code=TS_CODE, start_date=cur, end_date=min(nxt, end),
                        fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,close,turnover_rate")
    )
    cur = nxt
db = pd.concat(frames).drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)
pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
mv = pd.to_numeric(db["total_mv"], errors="coerce")
close = pd.to_numeric(db["close"], errors="coerce")
out["valuation"] = {
    "trade_days": len(db),
    "latest_date": db["trade_date"].iloc[-1],
    "latest_close": float(close.iloc[-1]),
    "latest_mv_yi": round(float(mv.iloc[-1]) / 1e4, 1),
    "pe_valid_points": len(pe),
    "pe_latest": float(pe.iloc[-1]) if len(pe) else None,
    "pb_latest": round(float(pb.iloc[-1]), 2),
    "pb_pct": round(float((pb < pb.iloc[-1]).sum() / len(pb) * 100), 1),
    "ps_latest": round(float(ps.iloc[-1]), 2),
    "ps_pct": round(float((ps < ps.iloc[-1]).sum() / len(ps) * 100), 1),
    "pb_quantiles": {q: round(float(pb.quantile(q / 100)), 2) for q in [10, 25, 50, 75, 90, 95]},
    "ps_quantiles": {q: round(float(ps.quantile(q / 100)), 2) for q in [10, 25, 50, 75, 90, 95]},
}
# YTD 涨幅
ytd_base = db[db["trade_date"] <= "20251231"]
if len(ytd_base):
    out["valuation"]["ytd_pct"] = round(
        (float(close.iloc[-1]) / float(close[ytd_base.index[-1]]) - 1) * 100, 1)

# ── 3. 景气度 ──
pscore = calculate_prosperity_score(fin)
out["prosperity"] = {
    "composite": pscore.composite_score, "revenue": pscore.revenue_score,
    "profit": pscore.profit_score, "slope": pscore.slope_score,
    "duration": pscore.duration_score, "delta_g": pscore.delta_g,
}

# ── 4. 困境 + 戴维斯（估值分用 PB 口径）──
latest = fin[0]
val_history = fetch_valuation_history(client, TS_CODE)
pe_pct = 0.5
pb_pct_engine = out["valuation"]["pb_pct"] / 100
vscore = 50.0
if val_history:
    from davis_analyzer.types import StockInfo
    si = StockInfo(ts_code=TS_CODE, name=NAME, industry="半导体设计",
                   list_status="L", is_cyclical=False)
    try:
        vscore, _, _ = calculate_valuation_score(val_history, si.is_cyclical)
    except Exception as e:
        print("valuation score fail:", e)
# 亏损股：手工构造百分位喂 distress（PE 分位失效，用 PB 分位替代双填）
distress = calculate_distress_score(
    eps_history=[f.eps for f in fin], pe_pct=pb_pct_engine, pb_pct=pb_pct_engine,
    debt_ratio=(latest.total_debt or 0) / (latest.total_assets or 1),
    operating_cf=latest.operating_cf or 0.0, total_debt=latest.total_debt or 0.0,
    total_assets=latest.total_assets or 0.0, roe_history=[f.roe for f in fin],
    revenue_history=[f.yoy_revenue_growth or 0.0 for f in fin],
    profit_history=[f.yoy_profit_growth or 0.0 for f in fin],
    delta_g=pscore.delta_g, ts_code=TS_CODE)
davis = calculate_davis_double_score(
    valuation_score=vscore, prosperity_score=pscore.composite_score,
    distress_score=distress.total_score, trend_score=50.0,
    ts_code=TS_CODE, name=NAME)
out["distress"] = {"total": distress.total_score, "L1": distress.layer1_score,
                   "L2": distress.layer2_score, "L3": distress.layer3_score}
out["davis_double"] = {"final": davis.final_score}

# ── 5. 五补充因子 ──
out["momentum"] = None
try:
    m = analyze_momentum(client, TS_CODE)
    if m:
        out["momentum"] = {"score": m.momentum_score, "abs": m.absolute_momentum_score,
                           "rs_pct": m.rs_percentile,
                           "window_returns": m.window_returns}
except Exception as e:
    out["momentum"] = {"error": str(e)}
div = analyze_dividend(client, TS_CODE)
out["dividend"] = {"score": div.dividend_score, "years": div.consecutive_years,
                   "yield_pct": div.latest_yield_pct}
try:
    fc = analyze_forecast(client, TS_CODE, pscore)
    out["forecast"] = {"score": fc.leading_score, "type": fc.type,
                       "p_change_mid": fc.p_change_mid, "stale": fc.is_stale} if fc else None
except Exception as e:
    out["forecast"] = {"error": str(e)}
try:
    rev = analyze_forecast_revision(client, TS_CODE)
    out["forecast_revision"] = {"dir": rev.revision_direction, "pp": rev.revision_pp} if rev else None
except Exception as e:
    out["forecast_revision"] = {"error": str(e)}
try:
    hc = analyze_holder_concentration(client, TS_CODE)
    out["holder_conc"] = {"score": hc.concentration_score, "trend": hc.trend,
                          "latest_chg_pct": hc.latest_chg_pct} if hc else None
except Exception as e:
    out["holder_conc"] = {"error": str(e)}
pq = analyze_profitability_quality(fin)
out["profit_quality"] = {"score": pq.quality_score, "gm": pq.latest_gross_margin,
                         "gm_delta": pq.gross_margin_delta, "rd": pq.latest_rd_intensity}

# ── 6. 时效校验 ──
inc = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date", limit=1)
out["freshness"] = {
    "latest_trade": out["valuation"]["latest_date"],
    "income_period": inc.iloc[0]["end_date"], "income_ann": inc.iloc[0]["ann_date"],
}
fcf = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
fcf = fcf[pd.to_numeric(fcf["ann_date"]) >= 20250101]
out["freshness"]["forecast"] = fcf.iloc[0].to_dict() if len(fcf) else None

# ── 7. 股东户数 ──
h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num")
h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(8)
rows, prev = [], None
for _, r in h.iterrows():
    num = int(r["holder_num"])
    rows.append({"end_date": r["end_date"], "holder_num": num,
                 "chg_pct": round((num - prev) / prev * 100, 1) if prev else None})
    prev = num
out["holder_number"] = rows

# ── 8. 相对估值（市场锚定，PE 法预计失效）──
try:
    from stockhot.valuation import analyze_relative_valuation
    rv = analyze_relative_valuation(TS_CODE)
    if rv:
        out["relative_valuation"] = {
            "pe_ratio": rv.pe_ratio, "pe_ratio_pct": rv.pe_ratio_pct,
            "index_pe": rv.index_pe, "index_pe_pct": rv.index_pe_pct,
            "erp_pct": round(rv.erp * 100, 2) if rv.erp else None,
            "rf_pct": round(rv.risk_free_rate * 100, 2) if rv.risk_free_rate else None,
            "quadrant": rv.quadrant, "signals": rv.signals,
        }
except Exception as e:
    out["relative_valuation"] = {"error": str(e)}

# ── 9. 同业锚定：海光 688041 PS/PB ──
peers = {}
for code, nm in [("688041.SH", "海光信息"), ("603986.SH", "兆易创新")]:
    try:
        d = pro.daily_basic(ts_code=code, limit=1)
        peers[nm] = {"close": float(d.iloc[0]["close"]),
                     "pe": d.iloc[0]["pe_ttm"], "pb": float(d.iloc[0]["pb"]),
                     "ps": float(d.iloc[0]["ps_ttm"] if pd.notna(d.iloc[0].get("ps_ttm")) else d.iloc[0]["ps"]),
                     "mv_yi": round(float(d.iloc[0]["total_mv"]) / 1e4, 1)}
    except Exception as e:
        peers[nm] = {"error": str(e)}
out["peers"] = peers

with open("/tmp/loongson.json", "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=2, default=str)
print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
