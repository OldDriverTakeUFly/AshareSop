#!/usr/bin/env python3
"""工业富联 (601138.SH) 研报取数脚本：四维评分 + 5因子 + 股东户数 + 相对估值 + 时效校验."""
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
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import batch_trend
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo
from davis_analyzer.valuation import calculate_valuation_score, detect_cyclical, fetch_valuation_history
from stockhot.tushare_config import get_pro_api

TS_CODE = "601138.SH"
NAME = "工业富联"
OUT = {}

client = TushareClient()
pro = get_pro_api(timeout=30)

# 时效校验
db1 = pro.daily_basic(ts_code=TS_CODE, limit=1)
inc1 = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
fc1 = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
fc1 = fc1[pd.to_numeric(fc1["ann_date"]) >= 20260101] if len(fc1) else fc1
OUT["freshness"] = {
    "latest_trade": db1.iloc[0]["trade_date"] if len(db1) else None,
    "latest_period": inc1.iloc[0]["end_date"] if len(inc1) else None,
    "latest_ann": inc1.iloc[0]["ann_date"] if len(inc1) else None,
    "forecast": fc1.iloc[0].to_dict() if len(fc1) else None,
}

# 财务 12 期
fin = fetch_financial_data(client, TS_CODE, periods=12)
OUT["fin"] = [
    dict(period=f.report_period, rev=f.revenue / 1e8 if f.revenue else None,
         np=f.net_profit / 1e8 if isinstance(f.net_profit, (int, float)) else None,
         eps=f.eps, roe=f.roe,
         yoy_rev=f.yoy_revenue_growth, yoy_np=f.yoy_profit_growth,
         gm=f.grossprofit_margin, rd=f.rd_exp / 1e8 if isinstance(f.rd_exp, (int, float)) else None)
    for f in fin
]

# 估值：client.get_daily_basic 校验行数，不足则 pro 分段
end = date.today().strftime("%Y%m%d")
start = (date.today() - timedelta(days=1150)).strftime("%Y%m%d")
db = client.get_daily_basic(TS_CODE, start, end)
if len(db) < 700:
    frames = []
    d0 = date.today() - timedelta(days=1150)
    while d0 < date.today():
        d1 = d0 + timedelta(days=400)
        seg = pro.daily_basic(ts_code=TS_CODE, start_date=d0.strftime("%Y%m%d"),
                              end_date=min(d1, date.today()).strftime("%Y%m%d"))
        frames.append(seg)
        d0 = d1 + timedelta(days=1)
    db = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True)
db = db.sort_values("trade_date").reset_index(drop=True)
pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
OUT["valuation"] = {
    "n_days": len(db), "last_trade": db["trade_date"].iloc[-1],
    "pe_now": pe.iloc[-1], "pb_now": pb.iloc[-1], "ps_now": ps.iloc[-1], "mv_yi": mv.iloc[-1] / 1e4,
    "pe_pct": (pe < pe.iloc[-1]).sum() / len(pe) * 100,
    "pb_pct": (pb < pb.iloc[-1]).sum() / len(pb) * 100,
    "ps_pct": (ps < ps.iloc[-1]).sum() / len(ps) * 100,
    "pe_quantiles": {str(q): pe.quantile(q / 100) for q in [10, 25, 50, 75, 90, 95]},
    "pb_quantiles": {str(q): pb.quantile(q / 100) for q in [10, 25, 50, 75, 90, 95]},
}

# 行业
stock_df = client.get_stock_list()
row = stock_df[stock_df["ts_code"] == TS_CODE]
industry = str(row.iloc[0].get("industry", "")) if not row.empty else ""
OUT["industry"] = industry
info = StockInfo(ts_code=TS_CODE, name=NAME, industry=industry, list_status="L",
                 is_cyclical=detect_cyclical(industry))

# 估值分（引擎）
vh = fetch_valuation_history(client, TS_CODE)
if len(vh) >= 500:
    val_score, pe_pct_e, pb_pct_e = calculate_valuation_score(vh, info.is_cyclical)
else:
    val_score, pe_pct_e, pb_pct_e = 50.0, OUT["valuation"]["pe_pct"] / 100, OUT["valuation"]["pb_pct"] / 100
OUT["val_score_engine"] = val_score

# 景气度
pscore = calculate_prosperity_score(fin)
OUT["prosperity"] = {k: getattr(pscore, k) for k in
                     ["composite_score", "delta_g", "revenue_score", "profit_score", "slope_score", "duration_score"]}
OUT["stage"] = classify_stock_stage(pscore)

# 趋势
trend_score = 50.0
if vh and len(vh) >= 3:
    dates = pd.to_datetime([v.trade_date for v in vh], format="%Y%m%d")
    tm = batch_trend({TS_CODE: (pd.Series([v.pe_ttm for v in vh], index=dates),
                                pd.Series([v.pb for v in vh], index=dates))}, {TS_CODE: info})
    trend_score = tm.get(TS_CODE, 50.0)
OUT["trend_score"] = trend_score

# 困境
latest = fin[0]
eps_h = [f.eps for f in fin]
roe_h = [f.roe for f in fin]
rev_g = [f.yoy_revenue_growth or 0.0 for f in fin]
np_g = [f.yoy_profit_growth or 0.0 for f in fin]
dr = (latest.total_debt or 0) / (latest.total_assets or 1)
ds = calculate_distress_score(eps_history=eps_h, pe_pct=pe_pct_e, pb_pct=pb_pct_e,
                              debt_ratio=dr, operating_cf=latest.operating_cf or 0,
                              total_debt=latest.total_debt or 0, total_assets=latest.total_assets or 0,
                              roe_history=roe_h, revenue_history=rev_g, profit_history=np_g,
                              delta_g=pscore.delta_g, ts_code=TS_CODE)
OUT["distress"] = {"total": ds.total_score, "l1": ds.layer1_score, "l2": ds.layer2_score, "l3": ds.layer3_score}

# 戴维斯综合
davis = calculate_davis_double_score(valuation_score=val_score,
                                     prosperity_score=pscore.composite_score,
                                     distress_score=ds.total_score, trend_score=trend_score,
                                     ts_code=TS_CODE, name=NAME)
OUT["davis"] = {"final": davis.final_score, "rank": davis.rank}

# 5 因子
OUT["momentum"] = None
try:
    mom = analyze_momentum(client, TS_CODE)
    if mom:
        OUT["momentum"] = {"score": mom.momentum_score, "abs": mom.absolute_momentum_score,
                           "rs": mom.rs_percentile, "windows": mom.window_returns}
except Exception as e:
    OUT["momentum_err"] = str(e)
div = analyze_dividend(client, TS_CODE)
OUT["dividend"] = {"score": div.dividend_score, "years": div.consecutive_years,
                   "yield": div.latest_yield_pct}
try:
    fc = analyze_forecast(client, TS_CODE, pscore)
    OUT["forecast"] = {"score": fc.leading_score, "pchg_mid": fc.p_change_mid, "type": fc.type,
                       "stale": fc.is_stale} if fc else None
except Exception as e:
    OUT["forecast_err"] = str(e)
try:
    rev = analyze_forecast_revision(client, TS_CODE)
    OUT["forecast_revision"] = {"dir": rev.revision_direction, "pp": rev.revision_pp} if rev else None
except Exception as e:
    OUT["forecast_revision_err"] = str(e)
hc = analyze_holder_concentration(client, TS_CODE)
OUT["holder_conc"] = {"score": hc.concentration_score, "trend": hc.trend,
                      "chg": hc.latest_chg_pct} if hc else None
pq = analyze_profitability_quality(fin)
OUT["profitability"] = {"score": pq.quality_score, "gm": pq.latest_gross_margin,
                        "gm_delta": pq.gross_margin_delta, "rd": pq.latest_rd_intensity}

# 股东户数
h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num")
h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(8)
OUT["holder_num"] = h[["end_date", "ann_date", "holder_num"]].to_dict("records")

# 动量手工复核（pro.daily 复权）
px = pro.daily(ts_code=TS_CODE, start_date="20240101", end_date=end)[["trade_date", "close"]].sort_values("trade_date")
px["close"] = pd.to_numeric(px["close"])
last = px["close"].iloc[-1]
wr = {}
for w in [20, 60, 120, 250]:
    if len(px) > w:
        wr[f"{w}d"] = (last / px["close"].iloc[-1 - w] - 1) * 100
OUT["manual_returns"] = wr
OUT["px_last"] = float(last)
OUT["px_last_date"] = px["trade_date"].iloc[-1]

# 相对估值
try:
    from stockhot.valuation import analyze_relative_valuation
    rv = analyze_relative_valuation(TS_CODE)
    OUT["relative_valuation"] = {
        "pe_ratio": getattr(rv, "pe_ratio", None), "pe_ratio_pct": getattr(rv, "pe_ratio_pct", None),
        "erp": getattr(rv, "erp", None), "risk_free": getattr(rv, "risk_free_rate", None),
        "index_pe": getattr(rv, "index_pe", None), "index_pe_pct": getattr(rv, "index_pe_pct", None),
        "quadrant": getattr(rv, "pe_band_quadrant", None),
        "signals": [str(s) for s in getattr(rv, "signals", [])],
    }
except Exception as e:
    OUT["relative_valuation_err"] = str(e)

with open("/tmp/fii_601138.json", "w") as f:
    json.dump(OUT, f, ensure_ascii=False, indent=2, default=str)
print(json.dumps(OUT, ensure_ascii=False, indent=2, default=str))
