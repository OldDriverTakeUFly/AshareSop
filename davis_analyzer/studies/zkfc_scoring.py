#!/usr/bin/env python3
"""中科飞测 (688361.SH) 深度研报取数脚本.

复制自 tianyue_scoring.py 模式，扩展：
  - 四维评分（估值/趋势/景气/困境）+ 戴维斯综合
  - 5 补充因子（momentum/dividend/forecast/holder_concentration/profitability）
  - 股东户数趋势 + 时效校验 + 相对估值
  - 估值分位手工核算（防 client.get_daily_basic 只返回 ~22 天的坑，行数校验）

用法:
    cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python davis_analyzer/studies/zkfc_scoring.py
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)

import os

os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import batch_trend
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo
from davis_analyzer.valuation import (
    calculate_percentile,
    calculate_valuation_score,
    detect_cyclical,
)

TS_CODE = "688361.SH"
STOCK_NAME = "中科飞测"
OUT = Path("/tmp/zkfc_scoring_output.json")


def main() -> None:
    out: dict = {"ts_code": TS_CODE}
    client = TushareClient()

    # ── 0. 代码核对（防张冠李戴）──
    sl = client.get_stock_list()
    row = sl[sl["ts_code"] == TS_CODE]
    if not row.empty:
        out["stock_check"] = {"name": row.iloc[0]["name"], "industry": row.iloc[0]["industry"]}
        logger.info("代码核对: {} = {} ({})", TS_CODE, row.iloc[0]["name"], row.iloc[0]["industry"])
    else:
        out["stock_check"] = None
        logger.warning("stock_list 未命中 {}", TS_CODE)

    # ── 1. 时效校验 ──
    pro = client._get_pro_api() if hasattr(client, "_get_pro_api") else None
    from stockhot.tushare_config import get_pro_api

    pro = get_pro_api(timeout=60)
    db1 = pro.daily_basic(ts_code=TS_CODE, limit=1)
    inc1 = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date", limit=1)
    fc1 = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
    out["freshness"] = {
        "latest_trade_date": db1.iloc[0]["trade_date"] if len(db1) else None,
        "latest_report_period": inc1.iloc[0]["end_date"] if len(inc1) else None,
        "latest_ann_date": inc1.iloc[0]["ann_date"] if len(inc1) else None,
    }
    if len(fc1):
        fc1 = fc1[pd.to_numeric(fc1["ann_date"], errors="coerce") >= 20250101]
        out["freshness"]["forecast"] = fc1.to_dict("records")
    logger.info("时效: {}", out["freshness"])

    # ── 2. 财务 12 期 ──
    fin = fetch_financial_data(client, TS_CODE, periods=12)
    out["financial"] = [
        {
            "period": f.report_period,
            "revenue_yi": round((f.revenue or 0) / 1e8, 2),
            "np_yi": round((f.net_profit or 0) / 1e8, 3),
            "eps": f.eps,
            "roe": f.roe,
            "ocf_yi": round((f.operating_cf or 0) / 1e8, 2),
            "debt_ratio": round((f.total_debt or 0) / (f.total_assets or 1), 4),
            "yoy_rev": f.yoy_revenue_growth,
            "yoy_np": f.yoy_profit_growth,
            "gm": getattr(f, "grossprofit_margin", None),
            "rd": getattr(f, "rd_exp", None),
        }
        for f in fin
    ]
    for r in out["financial"]:
        logger.info("{}", r)

    # ── 3. 估值 3 年（分段直连 pro.daily_basic 防缓存缩水）──
    end_d = date.today()
    frames = []
    cur = end_d
    while cur > end_d - timedelta(days=1150):
        seg_start = max(cur - timedelta(days=120), end_d - timedelta(days=1150))
        d = pro.daily_basic(
            ts_code=TS_CODE,
            start_date=seg_start.strftime("%Y%m%d"),
            end_date=cur.strftime("%Y%m%d"),
            fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,close",
        )
        if len(d):
            frames.append(d)
        cur = seg_start - timedelta(days=1)
    db = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True)
    db = db.sort_values("trade_date").reset_index(drop=True)
    out["valuation_rows"] = len(db)
    out["valuation_last_date"] = db["trade_date"].iloc[-1]
    assert len(db) >= 400, f"daily_basic rows={len(db)} too few"
    for col in ["pe_ttm", "pb", "ps", "total_mv", "close"]:
        db[col] = pd.to_numeric(db[col], errors="coerce")
    latest = db.iloc[-1]
    out["snapshot"] = {
        "date": latest["trade_date"],
        "close": latest["close"],
        "pe_ttm": None if pd.isna(latest["pe_ttm"]) else latest["pe_ttm"],
        "pb": latest["pb"],
        "ps": latest["ps"],
        "mv_yi": round(latest["total_mv"] / 1e4, 1),
    }
    pct = {}
    for col, key in [("pb", "pb"), ("ps", "ps"), ("pe_ttm", "pe")]:
        s = db[col].dropna()
        if len(s) and not pd.isna(latest[col]):
            pct[key] = {
                "current": round(float(latest[col]), 2),
                "pct": round(float((s < latest[col]).sum() / len(s) * 100), 1),
                "q": {str(p): round(float(s.quantile(p / 100)), 2) for p in [10, 25, 50, 75, 90, 95]},
            }
        else:
            pct[key] = {"current": None, "pct": None, "note": "失效/数据缺失"}
    out["valuation_pct"] = pct
    # ytd 涨幅
    db_y = db[pd.to_numeric(db["trade_date"], errors="coerce") >= 20260101]
    if len(db_y):
        y0 = db.iloc[pd.to_numeric(db["trade_date"]).searchsorted(20260101) - 1] if pd.to_numeric(db["trade_date"]).searchsorted(20260101) > 0 else db_y.iloc[0]
        out["ytd_pct"] = round(float((latest["close"] / y0["close"] - 1) * 100), 1)
    logger.info("snapshot={} pct={}", out["snapshot"], {k: v.get("pct") for k, v in pct.items()})

    # ── 4. 景气度 ──
    pscore = calculate_prosperity_score(fin)
    stage = classify_stock_stage(pscore)
    out["prosperity"] = {
        "composite": pscore.composite_score,
        "revenue": pscore.revenue_score,
        "profit": pscore.profit_score,
        "slope": pscore.slope_score,
        "duration": pscore.duration_score,
        "delta_g": pscore.delta_g,
        "stage": stage,
    }

    # ── 5. 估值分（引擎口径）──
    pe_pct = (pct.get("pe", {}).get("pct") or 50) / 100
    pb_pct = (pct.get("pb", {}).get("pct") or 50) / 100
    stock_info = StockInfo(
        ts_code=TS_CODE, name=STOCK_NAME,
        industry=out.get("stock_check", {}) or {},
        list_status="L", is_cyclical=False,
    ) if False else StockInfo(
        ts_code=TS_CODE, name=STOCK_NAME,
        industry=(out["stock_check"] or {}).get("industry", "") or "半导体",
        list_status="L", is_cyclical=False,
    )
    # 微利股 PE 失效：估值分用 PB/PS 口径手工合成，引擎 calculate_valuation_score 跳过
    val_score = 100 - (pb_pct * 100)  # 简化：PB 分位越高估值分越低

    # ── 6. 趋势 ──
    dates = pd.to_datetime(db["trade_date"], format="%Y%m%d")
    daily_pe = pd.Series(db["pe_ttm"].tolist(), index=dates)
    daily_pb = pd.Series(db["pb"].tolist(), index=dates)
    try:
        tm = batch_trend({TS_CODE: (daily_pe, daily_pb)}, {TS_CODE: stock_info})
        trend_score = tm.get(TS_CODE, 50.0)
    except Exception:
        trend_score = 50.0
    out["trend_score"] = trend_score

    # ── 7. 困境 ──
    latest_fin = fin[0]
    distress = calculate_distress_score(
        eps_history=[f.eps for f in fin],
        pe_pct=pe_pct, pb_pct=pb_pct,
        debt_ratio=(latest_fin.total_debt or 0) / (latest_fin.total_assets or 1),
        operating_cf=latest_fin.operating_cf or 0.0,
        total_debt=latest_fin.total_debt or 0.0,
        total_assets=latest_fin.total_assets or 0.0,
        roe_history=[f.roe for f in fin],
        revenue_history=[f.yoy_revenue_growth or 0.0 for f in fin],
        profit_history=[f.yoy_profit_growth or 0.0 for f in fin],
        delta_g=pscore.delta_g,
        ts_code=TS_CODE,
    )
    out["distress"] = {
        "total": distress.total_score,
        "l1": distress.layer1_score, "l2": distress.layer2_score, "l3": distress.layer3_score,
        "signals": distress.signals_detail,
    }

    # ── 8. 戴维斯综合 ──
    davis = calculate_davis_double_score(
        valuation_score=val_score,
        prosperity_score=pscore.composite_score,
        distress_score=distress.total_score,
        trend_score=trend_score,
        ts_code=TS_CODE, name=STOCK_NAME,
    )
    out["davis"] = {
        "final": davis.final_score, "rank": getattr(davis, "rank", None),
        "val": val_score, "trend": trend_score,
        "prosperity": pscore.composite_score, "distress": distress.total_score,
    }

    # ── 9. 5 补充因子 ──
    mom = analyze_momentum(client, TS_CODE)
    if mom:
        out["momentum"] = {
            "score": mom.momentum_score, "abs": mom.absolute_momentum_score,
            "rs": mom.rs_percentile,
            "windows": {k: (round(v, 3) if v is not None else None) for k, v in mom.window_returns.items()},
        }
    div = analyze_dividend(client, TS_CODE)
    out["dividend"] = {"score": div.dividend_score, "years": div.consecutive_years, "yield": div.latest_yield_pct}
    fcsig = analyze_forecast(client, TS_CODE, pscore)
    out["forecast"] = {"score": fcsig.leading_score, "type": fcsig.type, "p_mid": fcsig.p_change_mid, "stale": fcsig.is_stale} if fcsig else None
    hc = analyze_holder_concentration(client, TS_CODE)
    out["holder_conc"] = {
        "score": hc.concentration_score, "trend": hc.trend,
        "latest_chg": hc.latest_chg_pct, "counts": hc.holder_counts, "periods": hc.periods,
    } if hc else None
    pq = analyze_profitability_quality(fin)
    out["profit_quality"] = {
        "score": pq.quality_score, "gm": pq.latest_gross_margin,
        "gm_delta": pq.gross_margin_delta, "rd": pq.latest_rd_intensity,
    }

    # ── 10. 相对估值 ──
    try:
        from stockhot.valuation import analyze_relative_valuation

        rv = analyze_relative_valuation(pro, TS_CODE, STOCK_NAME)
        out["relative_valuation"] = {
            "pe_ratio_pct": getattr(rv, "pe_ratio_pct", None),
            "erp": getattr(rv, "erp", None),
            "index_pe": getattr(rv, "index_pe", None),
            "index_pe_pct": getattr(rv, "index_pe_pct", None),
            "quadrant": getattr(rv, "quadrant", None),
            "risk_free": getattr(rv, "risk_free_rate", None),
            "signals": str(getattr(rv, "signals", None))[:500],
        }
    except Exception as e:
        out["relative_valuation"] = {"error": str(e)[:300]}
    logger.info("rv={}", out.get("relative_valuation"))

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
