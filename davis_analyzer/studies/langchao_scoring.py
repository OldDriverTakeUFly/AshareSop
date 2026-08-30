#!/usr/bin/env python3
"""浪潮信息 (000977.SZ) 深度研报取数脚本：四维评分 + 5 补充因子 + 股东户数 + 时效校验 + 相对估值 + 可比快照."""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import batch_trend
from davis_analyzer.types import StockInfo
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality
from stockhot.tushare_config import get_pro_api

TS_CODE = "000977.SZ"
NAME = "浪潮信息"
OUT = {}

pro = get_pro_api(timeout=60)
client = TushareClient()

# ── 0. 代码核对 ──
basic = pro.stock_basic(ts_code=TS_CODE)
print("STOCK BASIC:", basic[["ts_code", "name", "industry"]].to_dict("records"))

# ── 1. 时效校验 ──
db1 = pro.daily_basic(ts_code=TS_CODE, limit=1)
inc1 = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date", limit=1)
fc1 = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
if len(fc1):
    fc1 = fc1[pd.to_numeric(fc1["ann_date"]) >= 20260101]
OUT["freshness"] = {
    "latest_trade": db1.iloc[0]["trade_date"],
    "latest_period": inc1.iloc[0]["end_date"],
    "latest_ann": inc1.iloc[0]["ann_date"],
    "forecast": fc1.head(3).to_dict("records") if len(fc1) else [],
}
print("FRESHNESS:", OUT["freshness"])

# ── 2. 财务 ──
fin = fetch_financial_data(client, TS_CODE, periods=12)
print(f"FIN periods={len(fin)}")
fin_rows = []
for f in fin:
    fin_rows.append({
        "period": f.report_period,
        "rev_yi": round((f.revenue or 0) / 1e8, 2),
        "np_yi": round((f.net_profit or 0) / 1e8, 2),
        "eps": f.eps,
        "roe": f.roe,
        "ocf_yi": round((f.operating_cf or 0) / 1e8, 2),
        "debt_ratio": round((f.total_debt or 0) / (f.total_assets or 1), 4),
        "yoy_rev": f.yoy_revenue_growth,
        "yoy_np": f.yoy_profit_growth,
        "gross_margin": getattr(f, "grossprofit_margin", None),
        "rd_exp_yi": round((getattr(f, "rd_exp", 0) or 0) / 1e8, 2),
    })
OUT["financial"] = fin_rows

# ── 3. 估值：分段直连 daily_basic 3 年 ──
end = date.today().strftime("%Y%m%d")
start = (date.today() - timedelta(days=1150)).strftime("%Y%m%d")
dfs = []
cur = start
while cur < end:
    nxt = (pd.to_datetime(cur) + timedelta(days=400)).strftime("%Y%m%d")
    seg = pro.daily_basic(ts_code=TS_CODE, start_date=cur, end_date=min(nxt, end),
                          fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,dv_ttm,close,turnover_rate")
    dfs.append(seg)
    cur = (pd.to_datetime(nxt) + timedelta(days=1)).strftime("%Y%m%d")
daily = pd.concat(dfs).drop_duplicates("trade_date").reset_index(drop=True).sort_values("trade_date")
print(f"DAILY rows={len(daily)} last={daily['trade_date'].iloc[-1]}")
pe = pd.to_numeric(daily["pe_ttm"], errors="coerce").dropna()
pb = pd.to_numeric(daily["pb"], errors="coerce").dropna()
ps = pd.to_numeric(daily["ps"], errors="coerce").dropna()
mv = pd.to_numeric(daily["total_mv"], errors="coerce").dropna()

def pct_table(s):
    cur_v = s.iloc[-1]
    return {
        "current": round(float(cur_v), 2),
        "pct": round(float((s < cur_v).sum() / len(s) * 100), 1),
        **{f"q{p}": round(float(s.quantile(p / 100)), 2) for p in [10, 25, 50, 75, 90, 95]},
    }

OUT["valuation"] = {
    "days": len(daily), "pe": pct_table(pe), "pb": pct_table(pb), "ps": pct_table(ps),
    "mv_yi": round(float(mv.iloc[-1]) / 1e4, 1),
    "close": float(pd.to_numeric(daily['close'], errors='coerce').iloc[-1]),
    "dv_ttm": daily["dv_ttm"].iloc[-1],
}
print("VALUATION:", json.dumps(OUT["valuation"], ensure_ascii=False))

# YTD 涨幅
c = pd.to_numeric(daily["close"], errors="coerce")
ytd_base = c[daily["trade_date"] <= "20260105"].iloc[-1] if (daily["trade_date"] <= "20260105").any() else None
OUT["ytd_pct"] = round(float((c.iloc[-1] / ytd_base - 1) * 100), 1) if ytd_base else None
print("YTD:", OUT["ytd_pct"])

# ── 4. 景气度 + 阶段 ──
pscore = calculate_prosperity_score(fin)
stage = classify_stock_stage(pscore)
OUT["prosperity"] = {
    "composite": pscore.composite_score, "revenue": pscore.revenue_score,
    "profit": pscore.profit_score, "slope": pscore.slope_score,
    "duration": pscore.duration_score, "delta_g": pscore.delta_g, "stage": str(stage),
}
print("PROSPERITY:", OUT["prosperity"])

# ── 5. 趋势 ──
try:
    td = pd.to_datetime(daily["trade_date"], format="%Y%m%d")
    pe_full = pd.to_numeric(daily["pe_ttm"], errors="coerce")
    pb_full = pd.to_numeric(daily["pb"], errors="coerce")
    daily_pe = pd.Series(pe_full.values, index=td).dropna()
    daily_pb = pd.Series(pb_full.values, index=td).dropna()
    stock_info = StockInfo(ts_code=TS_CODE, name=NAME, industry="计算机设备", list_status="L", is_cyclical=False)
    trend_map = batch_trend({TS_CODE: (daily_pe, daily_pb)}, {TS_CODE: stock_info})
    OUT["trend_score"] = trend_map.get(TS_CODE, 50.0)
except Exception as e:
    print("trend err:", e)
    OUT["trend_score"] = 50.0
print("TREND:", OUT["trend_score"])

# ── 6. 困境 + Davis ──
pe_pct_v = OUT["valuation"]["pe"]["pct"] / 100
pb_pct_v = OUT["valuation"]["pb"]["pct"] / 100
latest = fin[0]
dscore = calculate_distress_score(
    eps_history=[f.eps for f in fin], pe_pct=pe_pct_v, pb_pct=pb_pct_v,
    debt_ratio=(latest.total_debt or 0) / (latest.total_assets or 1),
    operating_cf=latest.operating_cf or 0.0, total_debt=latest.total_debt or 0.0,
    total_assets=latest.total_assets or 0.0, roe_history=[f.roe for f in fin],
    revenue_history=[f.yoy_revenue_growth or 0.0 for f in fin],
    profit_history=[f.yoy_profit_growth or 0.0 for f in fin],
    delta_g=pscore.delta_g, ts_code=TS_CODE,
)
val_score = 100 - (pe_pct_v * 0.7 + pb_pct_v * 0.3) * 100
davis = calculate_davis_double_score(
    valuation_score=val_score, prosperity_score=pscore.composite_score, distress_score=dscore.total_score,
    trend_score=OUT["trend_score"], ts_code=TS_CODE, name=NAME,
)
OUT["distress"] = {"total": dscore.total_score, "l1": dscore.layer1_score, "l2": dscore.layer2_score, "l3": dscore.layer3_score}
OUT["davis"] = {"final": davis.final_score, "val": val_score}
print("DAVIS:", OUT["davis"], "DISTRESS:", OUT["distress"])

# ── 7. 5 因子 ──
mom = analyze_momentum(client, TS_CODE)
div = analyze_dividend(client, TS_CODE)
fc = analyze_forecast(client, TS_CODE, pscore)
rev = analyze_forecast_revision(client, TS_CODE)
hc = analyze_holder_concentration(client, TS_CODE)
pq = analyze_profitability_quality(fin)
OUT["factors"] = {
    "momentum": {"score": mom.momentum_score, "rs": mom.rs_percentile,
                 "windows": {k: round(v, 3) if v is not None else None for k, v in (mom.window_returns or {}).items()}} if mom else None,
    "dividend": {"score": div.dividend_score, "years": div.consecutive_years, "yield": div.latest_yield_pct},
    "forecast": {"score": fc.leading_score, "type": fc.type, "p_mid": fc.p_change_mid, "stale": fc.is_stale} if fc else None,
    "revision": {"dir": rev.revision_direction, "pp": rev.revision_pp} if rev else None,
    "holder": {"score": hc.concentration_score, "trend": hc.trend, "chg": hc.latest_chg_pct,
               "counts": list(hc.holder_counts), "periods": list(hc.periods)} if hc else None,
    "quality": {"score": pq.quality_score, "gm": pq.latest_gross_margin, "gm_delta": pq.gross_margin_delta, "rd": pq.latest_rd_intensity},
}
print("FACTORS:", json.dumps(OUT["factors"], ensure_ascii=False, default=str))

# ── 8. 手工复核动量（pro.daily，派息极低未复权误差可忽略） ──
d = pro.daily(ts_code=TS_CODE, start_date="20250101", end_date=end, fields="trade_date,close")
d = d.sort_values("trade_date").reset_index(drop=True)
close_s = pd.to_numeric(d["close"], errors="coerce")
def ret(days):
    if len(close_s) > days:
        return round(float(close_s.iloc[-1] / close_s.iloc[-days - 1] - 1) * 100, 1)
    return None
OUT["manual_returns"] = {"20d": ret(20), "60d": ret(60), "120d": ret(120), "250d": ret(250)}
print("MANUAL RETURNS:", OUT["manual_returns"])

# ── 9. 相对估值 ──
try:
    from stockhot.valuation import analyze_relative_valuation
    rv = analyze_relative_valuation(TS_CODE)
    OUT["relative_valuation"] = {
        "pe_ratio": getattr(rv, "pe_ratio", None),
        "pe_ratio_pct": getattr(rv, "pe_ratio_pct", None),
        "erp": getattr(rv, "erp", None),
        "risk_free": getattr(rv, "risk_free_rate", None),
        "stock_pe_pct": getattr(rv, "stock_pe_pct", None),
        "index_pe": getattr(rv, "index_pe", None),
        "index_pe_pct": getattr(rv, "index_pe_pct", None),
        "quadrant": getattr(rv, "quadrant", None),
    }
except Exception as e:
    print("relval err:", e)
    OUT["relative_valuation"] = {"error": str(e)}
print("RELVAL:", OUT["relative_valuation"])

# ── 10. 可比公司快照 ──
comps = {"601138.SH": "工业富联", "000938.SZ": "紫光股份", "000063.SZ": "中兴通讯"}
OUT["comps"] = {}
for code, cname in comps.items():
    try:
        row = pro.daily_basic(ts_code=code, limit=1)
        r = row.iloc[0]
        fin2 = fetch_financial_data(client, code, periods=4)
        f0 = fin2[0]
        OUT["comps"][cname] = {
            "date": r["trade_date"], "pe": r["pe_ttm"], "pb": r["pb"], "ps": r["ps"],
            "mv_yi": round(float(r["total_mv"]) / 1e4, 1),
            "period": f0.report_period,
            "rev_yi": round((f0.revenue or 0) / 1e8, 2),
            "np_yi": round((f0.net_profit or 0) / 1e8, 2),
            "yoy_rev": f0.yoy_revenue_growth, "yoy_np": f0.yoy_profit_growth,
            "gm": getattr(f0, "grossprofit_margin", None),
        }
    except Exception as e:
        OUT["comps"][cname] = {"error": str(e)}
print("COMPS:", json.dumps(OUT["comps"], ensure_ascii=False, default=str))

with open("/tmp/langchao_data.json", "w", encoding="utf-8") as f:
    json.dump(OUT, f, ensure_ascii=False, indent=2, default=str)
print("DONE -> /tmp/langchao_data.json")
