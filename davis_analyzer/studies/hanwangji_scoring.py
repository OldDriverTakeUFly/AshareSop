#!/usr/bin/env python3
"""寒武纪 (688256.SH) 全维度取数脚本：四维评分 + 5 因子 + 股东户数 + 相对估值 + 时效校验.

复制自 tianyue_scoring.py 模板，扩展为研报一站式取数。
用法: cd /home/leo/Projects/CodeAgentDashboard && PYTHONPATH=. .venv/bin/python davis_analyzer/studies/hanwangji_scoring.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from loguru import logger
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import batch_trend
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo
from davis_analyzer.valuation import (
    calculate_valuation_score,
    detect_cyclical,
    fetch_valuation_history,
)
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality

TS_CODE = "688256.SH"
STOCK_NAME = "寒武纪"
OUT = Path("/tmp/hanwangji_data.json")


def main() -> None:
    result: dict = {}
    client = TushareClient()

    # ── 0. 时效校验 ──
    pro = client._get_pro_api() if hasattr(client, "_get_pro_api") else None
    if pro is None:
        from stockhot.tushare_config import get_pro_api
        pro = get_pro_api(timeout=30)
    sb = pro.stock_basic(ts_code=TS_CODE, fields="ts_code,name,industry,listing_date")
    result["stock_basic"] = sb.iloc[0].to_dict()
    logger.info("stock_basic: {}", result["stock_basic"])

    # ── 1. 财务 12 期 ──
    fin = fetch_financial_data(client, TS_CODE, periods=12)
    result["financial"] = [
        {
            "report_period": f.report_period,
            "revenue_yi": round((f.revenue or 0) / 1e8, 2),
            "net_profit_yi": round((f.net_profit or 0) / 1e8, 2),
            "eps": f.eps,
            "roe": f.roe,
            "operating_cf_yi": round((f.operating_cf or 0) / 1e8, 2),
            "total_assets_yi": round((f.total_assets or 0) / 1e8, 2),
            "yoy_rev": f.yoy_revenue_growth,
            "yoy_prof": f.yoy_profit_growth,
            "gm": getattr(f, "grossprofit_margin", None),
            "rd": getattr(f, "rd_exp", None),
        }
        for f in fin
    ]

    # ── 2. 估值 3 年（分段直连，防缓存截断）──
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=1180)).strftime("%Y%m%d")
    frames = []
    seg_start = start
    d0 = date.today() - timedelta(days=1180)
    while d0 < date.today():
        seg_end = min(d0 + timedelta(days=480), date.today())
        df = pro.daily_basic(
            ts_code=TS_CODE,
            start_date=seg_start,
            end_date=seg_end.strftime("%Y%m%d"),
            fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,close,turnover_rate,dv_ttm",
        )
        frames.append(df)
        d0 = seg_end + timedelta(days=1)
        seg_start = d0.strftime("%Y%m%d")
    db = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True)
    db = db.sort_values("trade_date").reset_index(drop=True)
    db3y = db[db["trade_date"] >= (date.today() - timedelta(days=1095)).strftime("%Y%m%d")].reset_index(drop=True)
    result["valuation_latest"] = db.iloc[-1].to_dict()
    for key in ("pe_ttm", "pb", "ps"):
        s = pd.to_numeric(db3y[key], errors="coerce").dropna()
        if len(s):
            cur = s.iloc[-1]
            pct = (s < cur).sum() / len(s) * 100
            result[f"percentile_{key}"] = {
                "current": round(float(cur), 2),
                "pct": round(float(pct), 1),
                "n": len(s),
                "q": {str(q): round(float(s.quantile(q / 100)), 2) for q in (10, 25, 50, 75, 90, 95)},
            }
    mv = pd.to_numeric(db3y["total_mv"], errors="coerce").dropna()
    result["market_cap_yi_latest"] = round(float(mv.iloc[-1]) / 1e4, 1)
    # 52 周高低
    db1y = db.tail(250)
    result["px_52w"] = {
        "high": float(pd.to_numeric(db1y["close"], errors="coerce").max()),
        "low": float(pd.to_numeric(db1y["close"], errors="coerce").min()),
        "latest": float(pd.to_numeric(db["close"], errors="coerce").iloc[-1]),
    }

    # ValuationData 列表供引擎用
    from davis_analyzer.types import ValuationData
    val_rows = []
    for _, r in db.iterrows():
        pe, pb = r["pe_ttm"], r["pb"]
        if pd.isna(pe) and pd.isna(pb):
            continue
        val_rows.append(ValuationData(
            ts_code=TS_CODE, trade_date=r["trade_date"],
            pe_ttm=None if pd.isna(pe) else float(pe),
            pb=None if pd.isna(pb) else float(pb),
            ps=None, total_mv=None))
    val_rows.sort(key=lambda v: v.trade_date, reverse=True)

    industry = result["stock_basic"].get("industry") or "半导体"
    stock_info = StockInfo(ts_code=TS_CODE, name=STOCK_NAME, industry=industry,
                           list_status="L", is_cyclical=detect_cyclical(industry))
    val_score, pe_pct, pb_pct = calculate_valuation_score(val_rows, stock_info.is_cyclical)
    result["valuation_score"] = {"score": round(val_score, 2), "pe_pct": pe_pct, "pb_pct": pb_pct,
                                 "is_cyclical": stock_info.is_cyclical, "industry": industry,
                                 "val_points": len(val_rows)}

    # ── 3. 景气度 ──
    pscore = calculate_prosperity_score(fin)
    result["prosperity"] = {
        "composite": pscore.composite_score, "delta_g": pscore.delta_g,
        "revenue": pscore.revenue_score, "profit": pscore.profit_score,
        "slope": pscore.slope_score, "duration": pscore.duration_score,
        "stage": classify_stock_stage(pscore),
    }

    # ── 4. 趋势 ──
    dates = pd.to_datetime([v.trade_date for v in val_rows], format="%Y%m%d")
    daily_pe = pd.Series([v.pe_ttm if v.pe_ttm is not None else None for v in val_rows], index=dates).dropna()
    daily_pb = pd.Series([v.pb for v in val_rows], index=dates).dropna()
    try:
        trend_map = batch_trend({TS_CODE: (daily_pe, daily_pb)}, {TS_CODE: stock_info})
        trend_score = trend_map.get(TS_CODE, 50.0)
    except Exception as e:
        logger.exception("trend failed: {}", e)
        trend_score = 50.0
    result["trend_score"] = round(float(trend_score), 2)

    # ── 5. 困境 ──
    latest = fin[0]
    total_debt = latest.total_debt or 0.0
    total_assets = latest.total_assets or 0.0
    distress = calculate_distress_score(
        eps_history=[f.eps for f in fin],
        pe_pct=pe_pct if pe_pct is not None else 0.5,
        pb_pct=pb_pct if pb_pct is not None else 0.5,
        debt_ratio=total_debt / total_assets if total_assets > 0 else 0.0,
        operating_cf=latest.operating_cf or 0.0,
        total_debt=total_debt, total_assets=total_assets,
        roe_history=[f.roe for f in fin],
        revenue_history=[f.yoy_revenue_growth or 0.0 for f in fin],
        profit_history=[f.yoy_profit_growth or 0.0 for f in fin],
        delta_g=pscore.delta_g, ts_code=TS_CODE)
    result["distress"] = {"total": distress.total_score, "l1": distress.layer1_score,
                          "l2": distress.layer2_score, "l3": distress.layer3_score}

    # ── 6. Davis ──
    davis = calculate_davis_double_score(
        valuation_score=val_score, prosperity_score=pscore.composite_score,
        distress_score=distress.total_score, trend_score=trend_score,
        ts_code=TS_CODE, name=STOCK_NAME)
    result["davis"] = {"final": davis.final_score, "rank": getattr(davis, "rank", None)}

    # ── 7. 5 因子 ──
    def eng(fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            logger.exception("engine {} failed: {}", fn.__name__, e)
            return None

    mom = eng(analyze_momentum, client, TS_CODE)
    if mom:
        result["momentum"] = {"score": mom.momentum_score, "abs": mom.absolute_momentum_score,
                              "rs_pct": mom.rs_percentile,
                              "windows": {k: (None if v is None else round(v, 4)) for k, v in (mom.window_returns or {}).items()}}
    div = eng(analyze_dividend, client, TS_CODE)
    if div:
        result["dividend"] = {"score": div.dividend_score, "years": div.consecutive_years,
                              "yield": div.latest_yield_pct}
    fc = eng(analyze_forecast, client, TS_CODE, pscore)
    if fc:
        result["forecast"] = {"leading": fc.leading_score, "pchg_mid": fc.p_change_mid,
                              "type": fc.type, "stale": fc.is_stale}
    hc = eng(analyze_holder_concentration, client, TS_CODE)
    if hc:
        result["holder_conc"] = {"score": hc.concentration_score, "trend": hc.trend,
                                  "latest_chg_pct": hc.latest_chg_pct,
                                  "counts": list(hc.holder_counts or []),
                                  "periods": list(hc.periods or [])}
    pq = eng(analyze_profitability_quality, fin)
    if pq:
        result["profitability"] = {"score": pq.quality_score, "gm": pq.latest_gross_margin,
                                   "gm_delta": pq.gross_margin_delta, "rd": pq.latest_rd_intensity}

    # ── 8. 股东户数明细（直连，防 NaN 崩）──
    try:
        h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num")
        h = h.dropna(subset=["holder_num"]).sort_values("end_date")
        rows = []
        prev = None
        for _, r in h.tail(10).iterrows():
            num = int(r["holder_num"])
            chg = round((num - prev) / prev * 100, 1) if prev else None
            rows.append({"end_date": r["end_date"], "num": num, "chg_pct": chg})
            prev = num
        result["holder_number"] = rows
    except Exception as e:
        logger.exception("holdernumber failed: {}", e)

    # ── 9. 相对估值 ──
    try:
        from stockhot.valuation import analyze_relative_valuation
        rv = analyze_relative_valuation(TS_CODE)
        if rv is not None:
            result["relative_valuation"] = {
                "pe_ratio": rv.pe_ratio, "pe_ratio_pct": rv.pe_ratio_pct,
                "erp": rv.erp, "quadrant": rv.quadrant,
                "stock_pe": rv.stock_pe, "stock_pe_pct": rv.stock_pe_pct,
                "index_pe": rv.index_pe, "index_pe_pct": rv.index_pe_pct,
                "risk_free_rate": rv.risk_free_rate,
                "signals": getattr(rv, "signals", None),
            }
    except Exception as e:
        logger.exception("relative valuation failed: {}", e)

    # ── 10. 业绩预告 + 最新披露 ──
    try:
        fcs = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
        fcs = fcs[pd.to_numeric(fcs["ann_date"]) >= 20250101]
        result["forecast_raw"] = fcs.to_dict("records") if len(fcs) else []
    except Exception as e:
        logger.exception("forecast raw failed: {}", e)
    try:
        inc = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,f_ann_date", limit=3)
        result["income_freshness"] = inc.to_dict("records")
    except Exception as e:
        logger.exception("income freshness failed: {}", e)

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
