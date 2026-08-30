#!/usr/bin/env python3
"""概伦电子 (688206.SH) 研报取数脚本：四维评分 + 5补充因子 + 股东户数 + 时效 + 相对估值."""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

import pandas as pd
from loguru import logger

from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import batch_trend, calculate_monthly_trend
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo
from davis_analyzer.valuation import detect_cyclical, fetch_valuation_history

TS_CODE = "688206.SH"
STOCK_NAME = "概伦电子"
OUT = Path(".sisyphus/evidence/gailun/gailun-data.json")


def main() -> None:
    out: dict = {"ts_code": TS_CODE, "name": STOCK_NAME}
    client = TushareClient()
    pro = client._get_pro() if hasattr(client, "_get_pro") else None
    if pro is None:
        from stockhot.tushare_config import get_pro_api
        pro = get_pro_api(timeout=30)

    # 0. 代码核对
    basic = pro.stock_basic(ts_code=TS_CODE)
    out["stock_basic"] = basic[["ts_code", "name", "industry", "list_date"]].to_dict("records")

    # 1. 财务
    fin = fetch_financial_data(client, TS_CODE, periods=12)
    out["financial"] = [
        {
            "report_period": f.report_period,
            "revenue_yi": round((f.revenue or 0) / 1e8, 3),
            "net_profit_yi": round((f.net_profit or 0) / 1e8, 3),
            "eps": f.eps,
            "roe": f.roe,
            "yoy_rev": f.yoy_revenue_growth,
            "yoy_prof": f.yoy_profit_growth,
            "operating_cf_yi": round((f.operating_cf or 0) / 1e8, 3),
            "total_assets_yi": round((f.total_assets or 0) / 1e8, 2),
            "debt_ratio": round((f.total_debt or 0) / (f.total_assets or 1), 4),
            "gross_margin": getattr(f, "grossprofit_margin", None),
            "rd_exp_yi": round((getattr(f, "rd_exp", 0) or 0) / 1e8, 3),
        }
        for f in fin
    ]

    eps_hist = [f.eps for f in fin]
    roe_hist = [f.roe for f in fin]
    rev_g = [f.yoy_revenue_growth or 0.0 for f in fin]
    prof_g = [f.yoy_profit_growth or 0.0 for f in fin]
    latest = fin[0]

    # 2. 估值历史（3年 daily_basic 手工分位，微利股 PE 失真仍报告）
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
    db = client.get_daily_basic(TS_CODE, start, end).sort_values("trade_date")
    pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
    pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
    ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
    out["valuation_snapshot"] = {
        "latest_trade_date": str(db.iloc[-1]["trade_date"]),
        "close": None,
        "pe_ttm": float(db.iloc[-1]["pe_ttm"]) if pd.notna(db.iloc[-1]["pe_ttm"]) else None,
        "pb": float(db.iloc[-1]["pb"]),
        "ps": float(db.iloc[-1]["ps"]),
        "total_mv_yi": round(float(db.iloc[-1]["total_mv"]) / 1e4, 1),
        "pe_pct": round((pe < pe.iloc[-1]).sum() / len(pe) * 100, 1),
        "pb_pct": round((pb < pb.iloc[-1]).sum() / len(pb) * 100, 1),
        "ps_pct": round((ps < ps.iloc[-1]).sum() / len(ps) * 100, 1),
        "pe_quantiles": {str(q): round(pe.quantile(q / 100), 1) for q in [10, 25, 50, 75, 90, 95]},
        "pb_quantiles": {str(q): round(pb.quantile(q / 100), 2) for q in [10, 25, 50, 75, 90, 95]},
        "ps_quantiles": {str(q): round(ps.quantile(q / 100), 2) for q in [10, 25, 50, 75, 90, 95]},
    }
    # YTD 涨幅
    d = pro.daily(ts_code=TS_CODE, start_date="20260101", end_date=end,
                  fields="trade_date,close,pre_close,pct_chg")
    d = d.sort_values("trade_date")
    if len(d):
        base = float(d.iloc[0]["pre_close"])
        last = float(d.iloc[-1]["close"])
        out["valuation_snapshot"]["close"] = last
        out["valuation_snapshot"]["ytd_pct"] = round((last / base - 1) * 100, 1)

    pe_pct = out["valuation_snapshot"]["pe_pct"] / 100 if pe.notna().all() else 0.9
    pb_pct = out["valuation_snapshot"]["pb_pct"] / 100

    # 3. 四维评分
    val_history = fetch_valuation_history(client, TS_CODE)
    stock_info = StockInfo(ts_code=TS_CODE, name=STOCK_NAME, industry="半导体",
                           list_status="L", is_cyclical=detect_cyclical("半导体"))
    prosp = calculate_prosperity_score(fin)
    out["prosperity"] = {
        "composite": round(prosp.composite_score, 2), "delta_g": round(prosp.delta_g, 2),
        "revenue_score": round(prosp.revenue_score, 2), "profit_score": round(prosp.profit_score, 2),
        "slope_score": round(prosp.slope_score, 2), "duration_score": round(prosp.duration_score, 2),
    }

    trend_score = 50.0
    if val_history and len(val_history) >= 3:
        dates = pd.to_datetime([v.trade_date for v in val_history], format="%Y%m%d")
        trend_map = batch_trend({TS_CODE: (pd.Series([v.pe_ttm for v in val_history], index=dates),
                                           pd.Series([v.pb for v in val_history], index=dates))},
                                {TS_CODE: stock_info})
        trend_score = trend_map.get(TS_CODE, 50.0)
    out["trend_score"] = round(trend_score, 2)

    distress = calculate_distress_score(
        eps_history=eps_hist, pe_pct=pe_pct, pb_pct=pb_pct,
        debt_ratio=(latest.total_debt or 0) / (latest.total_assets or 1),
        operating_cf=latest.operating_cf or 0.0, total_debt=latest.total_debt or 0.0,
        total_assets=latest.total_assets or 0.0, roe_history=roe_hist,
        revenue_history=rev_g, profit_history=prof_g, delta_g=prosp.delta_g, ts_code=TS_CODE)
    out["distress"] = {"total": round(distress.total_score, 2),
                       "l1": round(distress.layer1_score, 2),
                       "l2": round(distress.layer2_score, 2),
                       "l3": round(distress.layer3_score, 2)}

    # 估值分 score：微利+高PE → 用估值历史算
    val_score = max(0.0, 100 - (pe_pct + pb_pct) / 2 * 100)
    davis = calculate_davis_double_score(val_score, prosp.composite_score,
                                         distress.total_score, trend_score, TS_CODE, STOCK_NAME)
    out["davis"] = {"final": round(davis.final_score, 2)}

    # 4. 5 补充因子
    def sig(o):
        return {k: (round(v, 2) if isinstance(v, float) else v)
                for k, v in o.__dict__.items()} if o else None
    out["momentum"] = sig(analyze_momentum(client, TS_CODE))
    out["dividend"] = sig(analyze_dividend(client, TS_CODE))
    out["forecast"] = sig(analyze_forecast(client, TS_CODE, prosp))
    out["forecast_revision"] = sig(analyze_forecast_revision(client, TS_CODE))
    out["holder_conc"] = sig(analyze_holder_concentration(client, TS_CODE))
    out["profit_quality"] = sig(analyze_profitability_quality(fin))

    # 5. 股东户数
    try:
        h = pro.stk_holdernumber(ts_code=TS_CODE,
                                 fields="ts_code,ann_date,end_date,holder_num")
        h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(8)
        out["holder_num"] = [
            {"end_date": r["end_date"], "holder_num": int(r["holder_num"])} for _, r in h.iterrows()]
    except Exception as e:
        out["holder_num"] = f"error: {e}"

    # 6. 时效
    try:
        inc = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
        out["freshness_income"] = inc.to_dict("records")
        fc = pro.forecast(ts_code=TS_CODE,
                          fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
        fc["ann_date"] = pd.to_numeric(fc["ann_date"])
        fc = fc[fc["ann_date"] >= 20250101]
        out["freshness_forecast"] = fc.to_dict("records")
    except Exception as e:
        out["freshness"] = f"error: {e}"

    # 7. 相对估值
    try:
        from stockhot.valuation import analyze_relative_valuation
        rv = analyze_relative_valuation(pro, TS_CODE, STOCK_NAME)
        out["relative_valuation"] = sig(rv)
    except Exception as e:
        out["relative_valuation"] = f"error: {e}"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
