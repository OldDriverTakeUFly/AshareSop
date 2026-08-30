#!/usr/bin/env python3
"""拓荆科技 (688072.SH) 单股评分 + 补充因子 + 筹码/时效/相对估值 采集脚本.

用法:
    cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python davis_analyzer/studies/tuojing_scoring.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd
from loguru import logger

from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.tushare_client import TushareClient
from stockhot.tushare_config import get_pro_api

TS_CODE = "688072.SH"
NAME = "拓荆科技"

client = TushareClient()
pro = get_pro_api(timeout=60)

out: dict = {"ts_code": TS_CODE, "name": NAME}

# ── 0. 核对代码 ──
basic = pro.stock_basic(ts_code=TS_CODE, fields="ts_code,name,industry,list_date")
out["basic"] = basic.iloc[0].to_dict()
logger.info("stock_basic: {}", out["basic"])

# ── 1. 财务 ──
fin = fetch_financial_data(client, TS_CODE, periods=12)
out["financial"] = [
    {
        "period": f_.report_period,
        "revenue_yi": round((f_.revenue or 0) / 1e8, 2),
        "np_yi": round(float(f_.net_profit or 0) / 1e8, 2),
        "eps": f_.eps,
        "roe": f_.roe,
        "ocf_yi": round((f_.operating_cf or 0) / 1e8, 2),
        "debt_ratio": round((f_.total_debt or 0) / (f_.total_assets or 1), 4),
        "yoy_rev": f_.yoy_revenue_growth,
        "yoy_np": f_.yoy_profit_growth,
    }
    for f_ in fin
]

# ── 2. 估值 3 年（分段直连防缩水）──
end = date.today().strftime("%Y%m%d")
start = (date.today() - timedelta(days=1120)).strftime("%Y%m%d")
frames = []
cur = start
while cur < end:
    nxt = (pd.Timestamp(cur) + pd.Timedelta(days=400)).strftime("%Y%m%d")
    nxt = min(nxt, end)
    seg = pro.daily_basic(ts_code=TS_CODE, start_date=cur, end_date=nxt,
                          fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv")
    frames.append(seg)
    cur = nxt
db = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True)
db = db.sort_values("trade_date").reset_index(drop=True)
out["daily_basic_rows"] = len(db)
out["latest_trade"] = db["trade_date"].iloc[-1]
for col in ["pe_ttm", "pb", "ps", "total_mv"]:
    s = pd.to_numeric(db[col], errors="coerce").dropna()
    last = s.iloc[-1]
    out[f"latest_{col}"] = float(last)
    out[f"pct_{col}"] = round(float((s < last).sum() / len(s) * 100), 1)
    out[f"quantiles_{col}"] = {str(q): round(float(s.quantile(q / 100)), 2) for q in [10, 25, 50, 75, 90, 95]}
out["total_mv_yi"] = round(out["latest_total_mv"] / 1e4, 1)

# 年初至今涨幅
daily = pro.daily(ts_code=TS_CODE, start_date="20251231", end_date=end,
                  fields="trade_date,close,pre_close")
daily = daily.sort_values("trade_date").reset_index(drop=True)
if len(daily):
    base = daily["pre_close"].iloc[0]
    out["ytd_pct"] = round(float((daily["close"].iloc[-1] / base - 1) * 100), 1)
    out["latest_close"] = float(daily["close"].iloc[-1])
# 60/120/250d 收益（手工复核口径）
px = pro.daily(ts_code=TS_CODE, start_date=(date.today() - timedelta(days=420)).strftime("%Y%m%d"),
               end_date=end, fields="trade_date,close").sort_values("trade_date").reset_index(drop=True)
closes = px["close"].tolist()
for w, label in [(60, "r60"), (120, "r120"), (250, "r250")]:
    if len(closes) > w:
        out[label] = round(float((closes[-1] / closes[-1 - w] - 1) * 100), 1)

# ── 3. 景气度 ──
pscore = calculate_prosperity_score(fin)
out["prosperity"] = {
    "composite": pscore.composite_score, "delta_g": pscore.delta_g,
    "revenue": pscore.revenue_score, "profit": pscore.profit_score,
    "slope": pscore.slope_score, "duration": pscore.duration_score,
}

# ── 4. 补充因子 ──
mom = analyze_momentum(client, TS_CODE)
out["momentum"] = {
    "score": mom.momentum_score if mom else None,
    "abs_score": mom.absolute_momentum_score if mom else None,
    "rs": mom.rs_percentile if mom else None,
    "window_returns": mom.window_returns if mom else None,
}
div = analyze_dividend(client, TS_CODE)
out["dividend"] = {"score": div.dividend_score, "years": div.consecutive_years, "yield": div.latest_yield_pct}
fc = analyze_forecast(client, TS_CODE, pscore)
out["forecast"] = {"leading": fc.leading_score, "type": fc.type, "p_mid": fc.p_change_mid, "stale": fc.is_stale} if fc else None
rev = analyze_forecast_revision(client, TS_CODE)
out["forecast_revision"] = {"dir": rev.revision_direction, "pp": rev.revision_pp} if rev else None
hc = analyze_holder_concentration(client, TS_CODE)
out["holder_conc"] = {"score": hc.concentration_score, "trend": hc.trend, "chg": hc.latest_chg_pct} if hc else None
pq = analyze_profitability_quality(fin)
out["profitability"] = {
    "score": pq.quality_score, "gm": pq.latest_gross_margin,
    "gm_delta": pq.gross_margin_delta, "rd": pq.latest_rd_intensity,
}

# ── 5. 股东户数 ──
h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num")
h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(8)
out["holder_num"] = [
    {"end_date": r["end_date"], "num": int(r["holder_num"])} for _, r in h.iterrows()
]

# ── 6. 时效校验 ──
inc = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
out["freshness_income"] = inc.iloc[0].to_dict() if len(inc) else {}
fcq = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
if len(fcq):
    fcq = fcq[pd.to_numeric(fcq["ann_date"]) >= 20250101].sort_values("ann_date")
    out["freshness_forecast"] = fcq.iloc[-1].to_dict() if len(fcq) else "none_since_2025"

# ── 7. 相对估值 ──
try:
    from stockhot.valuation import analyze_relative_valuation
    rv = analyze_relative_valuation(TS_CODE)
    out["relative_valuation"] = {
        "pe_ratio_pct": rv.pe_ratio_pct, "erp": rv.erp, "quadrant": rv.quadrant,
        "stock_pe_pct": rv.stock_pe_pct, "index_pe": rv.index_pe,
        "index_pe_pct": rv.index_pe_pct, "risk_free_rate": rv.risk_free_rate,
        "signals": [str(s) for s in (rv.signals or [])],
    }
except Exception as e:
    out["relative_valuation"] = f"error: {e}"

# ── 8. 年度财务（income 年度口径交叉）──
try:
    fy = pro.fina_indicator(ts_code=TS_CODE, period="20251231",
                            fields="ts_code,end_date,grossprofit_margin,rd_exp_to_revenue,roe,netprofit_margin")
    out["fina_2025"] = fy.iloc[0].to_dict() if len(fy) else {}
except Exception as e:
    out["fina_2025"] = f"error: {e}"

opath = Path(".sisyphus/evidence/tuojing/tuojing-data.json")
opath.parent.mkdir(parents=True, exist_ok=True)
opath.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
