#!/usr/bin/env python3
"""中兴通讯 (000063.SZ) 深度研报取数脚本：四维评分 + 5 补充因子 + 股东户数 + 时效 + 相对估值."""
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
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import batch_trend
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo, ValuationData
from davis_analyzer.valuation import calculate_valuation_score, detect_cyclical

TS_CODE = "000063.SZ"
NAME = "中兴通讯"
OUT = "/tmp/zte_report_data.json"

client = TushareClient()
pro = client._get_pro_api() if hasattr(client, "_get_pro_api") else None
if pro is None:
    from stockhot.tushare_config import get_pro_api
    pro = get_pro_api(timeout=60)

res: dict = {"ts_code": TS_CODE, "name": NAME}

# ── 0. 核对代码与公司名 ──
basic = pro.stock_basic(ts_code=TS_CODE, fields="ts_code,name,industry,area,list_date")
res["stock_basic"] = basic.iloc[0].to_dict()
print("STOCK BASIC:", res["stock_basic"])

# ── 1. 财务 12 期 ──
fin = fetch_financial_data(client, TS_CODE, periods=12)
assert fin and fin[0].ts_code == TS_CODE, f"代码核对失败: {fin[0].ts_code if fin else 'empty'}"
res["fin"] = [
    {
        "period": f.report_period,
        "rev_yi": round((f.revenue or 0) / 1e8, 2),
        "np_yi": round(float(f.net_profit or 0) / 1e8, 2),
        "eps": f.eps,
        "roe": f.roe,
        "ocf_yi": round((f.operating_cf or 0) / 1e8, 2),
        "total_debt_yi": round((f.total_debt or 0) / 1e8, 2),
        "total_assets_yi": round((f.total_assets or 0) / 1e8, 2),
        "yoy_rev": f.yoy_revenue_growth,
        "yoy_np": f.yoy_profit_growth,
        "gm": getattr(f, "grossprofit_margin", None),
        "rd": getattr(f, "rd_exp", None),
    }
    for f in fin
]
print("FIN periods:", len(fin), "latest:", fin[0].report_period)

# ── 2. daily_basic 分段直连（3 年）──
end = date.today().strftime("%Y%m%d")
start = (date.today() - timedelta(days=1150)).strftime("%Y%m%d")
frames = []
cur = start
while cur < end:
    nxt = (pd.to_datetime(cur, format="%Y%m%d") + timedelta(days=480)).strftime("%Y%m%d")
    seg_end = min(nxt, end)
    df = pro.daily_basic(ts_code=TS_CODE, start_date=cur, end_date=seg_end,
                         fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,close,turnover_rate,dv_ttm")
    frames.append(df)
    cur = (pd.to_datetime(seg_end, format="%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
db = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True)
db = db.sort_values("trade_date").reset_index(drop=True)
print("DAILY_BASIC rows:", len(db), "latest:", db["trade_date"].iloc[-1])
assert len(db) >= 700, f"daily_basic 行数不足: {len(db)}"

pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
latest_row = db.iloc[-1]
res["valuation_snapshot"] = {
    "trade_date": latest_row["trade_date"],
    "close": float(latest_row["close"]),
    "pe_ttm": float(latest_row["pe_ttm"]),
    "pb": float(latest_row["pb"]),
    "ps": float(latest_row["ps"]),
    "total_mv_yi": round(float(latest_row["total_mv"]) / 1e4, 1),
    "dv_ttm": float(latest_row["dv_ttm"]) if pd.notna(latest_row["dv_ttm"]) else None,
}
res["percentiles"] = {
    "pe_pct": round((pe < pe.iloc[-1]).sum() / len(pe) * 100, 1),
    "pb_pct": round((pb < pb.iloc[-1]).sum() / len(pb) * 100, 1),
    "ps_pct": round((ps < ps.iloc[-1]).sum() / len(ps) * 100, 1),
    "n": len(db),
}
for k, s in [("pe", pe), ("pb", pb), ("ps", ps)]:
    res[f"{k}_quantiles"] = {str(p): round(float(s.quantile(p / 100)), 2) for p in [10, 25, 50, 75, 90, 95]}
# 年初至今涨跌幅
db["close_n"] = pd.to_numeric(db["close"], errors="coerce")
ytd_rows = db[db["trade_date"] >= "20260101"]
if len(ytd_rows):
    base = db[db["trade_date"] < "20260101"]["close_n"].iloc[-1]
    res["ytd_pct"] = round((ytd_rows["close_n"].iloc[-1] / base - 1) * 100, 1)
print("VALUATION:", res["valuation_snapshot"], res["percentiles"], "YTD:", res.get("ytd_pct"))

# ── 3. 估值分（构造 ValuationData，降序）──
vlist = []
for _, r in db.iterrows():
    if pd.isna(r["pb"]):
        continue
    vlist.append(ValuationData(ts_code=TS_CODE, trade_date=str(r["trade_date"]),
                               pe_ttm=(None if pd.isna(r["pe_ttm"]) else float(r["pe_ttm"])),
                               pb=float(r["pb"]), ps=float(r["ps"]), total_mv=float(r["total_mv"])))
vlist.sort(key=lambda v: v.trade_date, reverse=True)
industry = res["stock_basic"].get("industry", "") or "通信设备"
is_cyc = detect_cyclical(industry)
info = StockInfo(ts_code=TS_CODE, name=NAME, industry=industry, list_status="L", is_cyclical=is_cyc)
val_score, pe_pct_u, pb_pct_u = calculate_valuation_score(vlist, is_cyc)
print("VAL SCORE:", val_score, "pe_pct:", pe_pct_u, "pb_pct:", pb_pct_u, "cyc:", is_cyc)

# ── 4. 景气度 ──
pscore = calculate_prosperity_score(fin)
stage = classify_stock_stage(pscore)
res["prosperity"] = {
    "composite": pscore.composite_score, "delta_g": pscore.delta_g,
    "revenue": pscore.revenue_score, "profit": pscore.profit_score,
    "slope": pscore.slope_score, "duration": pscore.duration_score, "stage": stage,
}
print("PROSPERITY:", res["prosperity"])

# ── 5. 趋势 ──
dates = pd.to_datetime(db["trade_date"], format="%Y%m%d")
daily_pe = pd.Series(pd.to_numeric(db["pe_ttm"], errors="coerce").values, index=dates)
daily_pb = pd.Series(pd.to_numeric(db["pb"], errors="coerce").values, index=dates)
trend_map = batch_trend({TS_CODE: (daily_pe, daily_pb)}, {TS_CODE: info})
trend_score = trend_map.get(TS_CODE, 50.0)
print("TREND:", trend_score)

# ── 6. 困境 ──
latest = fin[0]
eps_hist = [f.eps for f in fin]
roe_hist = [f.roe for f in fin]
rev_g = [f.yoy_revenue_growth or 0.0 for f in fin]
np_g = [f.yoy_profit_growth or 0.0 for f in fin]
total_debt = latest.total_debt or 0.0
total_assets = latest.total_assets or 0.0
ocf = latest.operating_cf or 0.0
debt_ratio = total_debt / total_assets if total_assets > 0 else 0.0
dscore = calculate_distress_score(
    eps_history=eps_hist, pe_pct=pe_pct_u, pb_pct=pb_pct_u, debt_ratio=debt_ratio,
    operating_cf=ocf, total_debt=total_debt, total_assets=total_assets,
    roe_history=roe_hist, revenue_history=rev_g, profit_history=np_g,
    delta_g=pscore.delta_g, ts_code=TS_CODE)
print("DISTRESS:", dscore.total_score, "L1:", dscore.layer1_score, "L2:", dscore.layer2_score, "L3:", dscore.layer3_score)

# ── 7. 戴维斯 ──
davis = calculate_davis_double_score(
    valuation_score=val_score, prosperity_score=pscore.composite_score,
    distress_score=dscore.total_score, trend_score=trend_score,
    ts_code=TS_CODE, name=NAME)
res["davis"] = {"final": davis.final_score, "val": val_score, "trend": trend_score,
                "prosperity": pscore.composite_score, "distress": dscore.total_score,
                "distress_L": [dscore.layer1_score, dscore.layer2_score, dscore.layer3_score]}
print("DAVIS:", res["davis"])

# ── 8. 5 补充因子 ──
try:
    mom = analyze_momentum(client, TS_CODE)
    res["momentum"] = {"score": mom.momentum_score, "abs": mom.absolute_momentum_score,
                       "rs": mom.rs_percentile, "windows": mom.window_returns}
    # 手工复核
    px = pro.daily(ts_code=TS_CODE, start_date=(date.today() - timedelta(days=400)).strftime("%Y%m%d"), end_date=end,
                   fields="trade_date,close")
    px = px.sort_values("trade_date").reset_index(drop=True)
    closes = px["close"].astype(float).tolist()
    for w in [20, 60, 120, 250]:
        if len(closes) > w:
            res.setdefault("manual_returns", {})[f"{w}d"] = round((closes[-1] / closes[-1 - w] - 1) * 100, 1)
    print("MOMENTUM:", res["momentum"], "manual:", res.get("manual_returns"))
except Exception as e:
    print("momentum fail:", e)

div = analyze_dividend(client, TS_CODE)
res["dividend"] = {"score": div.dividend_score, "years": div.consecutive_years, "yield": div.latest_yield_pct}
print("DIVIDEND:", res["dividend"])

try:
    fc = analyze_forecast(client, TS_CODE, pscore)
    res["forecast"] = {"leading": fc.leading_score, "type": fc.type, "p_mid": fc.p_change_mid, "stale": fc.is_stale}
    print("FORECAST:", res["forecast"])
except Exception as e:
    print("forecast fail:", e)
try:
    rev = analyze_forecast_revision(client, TS_CODE)
    res["forecast_rev"] = {"dir": rev.revision_direction, "pp": rev.revision_pp, "score": rev.revision_score}
    print("FORECAST REV:", res["forecast_rev"])
except Exception as e:
    print("forecast rev fail:", e)

try:
    hc = analyze_holder_concentration(client, TS_CODE)
    res["holder_conc"] = {"score": hc.concentration_score, "trend": hc.trend, "chg": hc.latest_chg_pct,
                          "counts": hc.holder_counts, "periods": hc.periods}
    print("HOLDER CONC:", res["holder_conc"])
except Exception as e:
    print("holder conc fail:", e)

try:
    pq = analyze_profitability_quality(fin)
    res["profitability"] = {"score": pq.quality_score, "gm": pq.latest_gross_margin,
                            "gm_delta": pq.gross_margin_delta, "rd": pq.latest_rd_intensity}
    print("PROFITABILITY:", res["profitability"])
except Exception as e:
    print("profitability fail:", e)

# ── 9. 股东户数（8 期）──
try:
    h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num")
    h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(8)
    rows, prev = [], None
    for _, r in h.iterrows():
        num = int(r["holder_num"])
        chg = round((num - prev) / prev * 100, 1) if prev else None
        rows.append({"end_date": r["end_date"], "num": num, "chg": chg})
        prev = num
    res["holder_number"] = rows
    print("HOLDER NUMBER:", rows)
except Exception as e:
    print("holder number fail:", e)

# ── 10. 时效校验 ──
try:
    inc = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,f_ann_date", limit=3)
    res["income_freshness"] = inc.to_dict("records")
    fc_raw = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
    if len(fc_raw):
        fc_raw = fc_raw[pd.to_numeric(fc_raw["ann_date"]) >= 20250101]
        res["forecast_raw"] = fc_raw.to_dict("records")
    print("FRESHNESS:", res["income_freshness"], res.get("forecast_raw"))
except Exception as e:
    print("freshness fail:", e)

# ── 11. 相对估值 ──
try:
    from stockhot.valuation import analyze_relative_valuation
    rv = analyze_relative_valuation(client, TS_CODE)
    res["relative_val"] = {
        "pe_ratio": rv.pe_ratio, "pe_ratio_pct": rv.pe_ratio_pct,
        "stock_pe": rv.stock_pe, "stock_pe_pct": rv.stock_pe_pct,
        "index_pe": rv.index_pe, "index_pe_pct": rv.index_pe_pct,
        "erp": rv.erp, "risk_free": rv.risk_free_rate, "quadrant": rv.quadrant,
        "signals": getattr(rv, "signals", None),
    }
    print("RELATIVE VAL:", res["relative_val"])
except Exception as e:
    print("relative val fail:", e)

# ── 12. 分红历史（股息三重校验）──
try:
    dvd = pro.dividend(ts_code=TS_CODE, fields="ts_code,end_date,ann_date,div_proc,cash_div_taxable,stk_div,base_share")
    if len(dvd):
        dvd = dvd.sort_values("end_date")
        res["dividend_hist"] = dvd.tail(10).to_dict("records")
    print("DIV HIST tail:", res.get("dividend_hist"))
except Exception as e:
    print("dividend hist fail:", e)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2, default=str)
print("SAVED", OUT)
