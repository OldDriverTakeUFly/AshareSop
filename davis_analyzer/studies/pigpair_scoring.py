#!/usr/bin/env python3
"""温氏股份/新希望 双标的研报取数脚本(2026-08).

对 300498.SZ(温氏股份) 与 000876.SZ(新希望) 执行:
  1. 四维评分(估值/景气/困境/趋势 → davis double)
  2. 5 补充因子(momentum/dividend/forecast/holder_concentration/profitability)
  3. 股东户数 + 十大流通股东
  4. 时效性校验 + 业绩预告明细(单位:万元!)
  5. 相对市场估值锚定(stockhot.valuation)

输出: davis_analyzer/studies/pigpair_data.json
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)   # 坑点1b: 防 shell 导出 stale token
os.environ["PROJECT_ROOT"] = os.getcwd()  # 坑点2b: 防 .env 的 /app 破坏 mkdir

import pandas as pd  # noqa: E402
from loguru import logger  # noqa: E402

from davis_analyzer.distress import calculate_distress_score  # noqa: E402
from davis_analyzer.financial_fetcher import fetch_financial_data  # noqa: E402
from davis_analyzer.forecast import analyze_forecast  # noqa: E402
from davis_analyzer.dividend import analyze_dividend  # noqa: E402
from davis_analyzer.holder_concentration import analyze_holder_concentration  # noqa: E402
from davis_analyzer.momentum import analyze_momentum  # noqa: E402
from davis_analyzer.profitability import analyze_profitability_quality  # noqa: E402
from davis_analyzer.prosperity import calculate_prosperity_score  # noqa: E402
from davis_analyzer.prosperity_sector import classify_stock_stage  # noqa: E402
from davis_analyzer.scoring import calculate_davis_double_score  # noqa: E402
from davis_analyzer.trend import batch_trend  # noqa: E402
from davis_analyzer.tushare_client import TushareClient  # noqa: E402
from davis_analyzer.types import StockInfo  # noqa: E402
from davis_analyzer.valuation import (  # noqa: E402
    calculate_percentile,
    calculate_valuation_score,
    detect_cyclical,
    fetch_valuation_history,
)

from stockhot.tushare_config import get_pro_api  # noqa: E402
from stockhot.valuation import analyze_relative_valuation  # noqa: E402

TARGETS = [
    ("300498.SZ", "温氏股份"),
    ("000876.SZ", "新希望"),
]
OUT = "davis_analyzer/studies/pigpair_data.json"
PERIODS = 12


def pct(series: pd.Series, cur: float) -> float:
    """当前值在序列中的分位(%)."""
    s = series.dropna()
    return round((s < cur).sum() / len(s) * 100, 1)


def collect_one(client: TushareClient, pro, ts_code: str, name: str) -> dict:
    out: dict = {"ts_code": ts_code, "name": name}

    # ── 1. 财务 12 期 ──
    fin = fetch_financial_data(client, ts_code, periods=PERIODS)
    out["financial"] = [
        {
            "period": f.report_period,
            "revenue_yi": round((f.revenue or 0) / 1e8, 2),
            "net_profit_yi": round((f.net_profit or 0) / 1e8, 2) if f.net_profit is not None else None,
            "eps": f.eps,
            "roe": f.roe,
            "opcf_yi": round((f.operating_cf or 0) / 1e8, 2),
            "total_debt_yi": round((f.total_debt or 0) / 1e8, 2),
            "total_assets_yi": round((f.total_assets or 0) / 1e8, 2),
            "debt_ratio": round((f.total_debt or 0) / (f.total_assets or 1) * 100, 1),
            "yoy_rev": round(f.yoy_revenue_growth * 100, 1) if f.yoy_revenue_growth is not None else None,
            "yoy_profit": round(f.yoy_profit_growth * 100, 1) if f.yoy_profit_growth is not None else None,
            "gross_margin": f.grossprofit_margin,
        }
        for f in fin
    ]

    # ── 2. 估值(3 年 daily_basic,升序!) ──
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
    db = client.get_daily_basic(ts_code, start, end).sort_values("trade_date")  # 坑:必须升序
    pe = pd.to_numeric(db["pe_ttm"], errors="coerce")
    pb = pd.to_numeric(db["pb"], errors="coerce")
    ps = pd.to_numeric(db["ps"], errors="coerce")
    mv = pd.to_numeric(db["total_mv"], errors="coerce")
    latest_row = db.iloc[-1]
    out["valuation"] = {
        "snapshot_date": latest_row["trade_date"],
        "close": float(latest_row["close"]) if "close" in db.columns and pd.notna(latest_row.get("close")) else None,
        "total_mv_yi": round(float(mv.iloc[-1]) / 1e4, 1),  # 万元→亿
        "circ_mv_yi": round(float(pd.to_numeric(db["circ_mv"], errors="coerce").iloc[-1]) / 1e4, 1) if "circ_mv" in db.columns else None,
        "pe_ttm": float(pe.iloc[-1]) if pd.notna(pe.iloc[-1]) else None,
        "pe_pct": pct(pe, float(pe.iloc[-1])) if pd.notna(pe.iloc[-1]) else None,
        "pb": float(pb.iloc[-1]),
        "pb_pct": pct(pb, float(pb.iloc[-1])),
        "ps": float(ps.iloc[-1]) if pd.notna(ps.iloc[-1]) else None,
        "ps_pct": pct(ps, float(ps.iloc[-1])) if pd.notna(ps.iloc[-1]) else None,
        "mv_pct": pct(mv, float(mv.iloc[-1])),
        "n_days": len(db),
        "pb_quantiles": {f"p{p}": round(float(pb.quantile(p / 100)), 2) for p in [10, 25, 50, 75, 90, 95]},
        "pe_quantiles": {f"p{p}": round(float(pe.quantile(p / 100)), 2) for p in [10, 25, 50, 75, 90, 95]},
        "ps_quantiles": {f"p{p}": round(float(ps.quantile(p / 100)), 2) for p in [10, 25, 50, 75, 90, 95]},
    }

    # ── 3. 景气度 + 阶段 ──
    pscore = calculate_prosperity_score(fin)
    stage = classify_stock_stage(pscore)
    out["prosperity"] = {
        "composite": round(pscore.composite_score, 2),
        "delta_g": round(pscore.delta_g, 2),
        "revenue_score": round(pscore.revenue_score, 2),
        "profit_score": round(pscore.profit_score, 2),
        "slope_score": round(pscore.slope_score, 2),
        "duration_score": round(pscore.duration_score, 2),
        "stage": stage,
    }

    # ── 4. 估值评分(引擎口径) ──
    val_history = fetch_valuation_history(client, ts_code)
    stock_list = client.get_stock_list()
    row = stock_list[stock_list["ts_code"] == ts_code]
    industry = str(row.iloc[0].get("industry", "") or "") if not row.empty else ""
    out["industry"] = industry
    out["is_cyclical"] = detect_cyclical(industry)
    sinfo = StockInfo(ts_code=ts_code, name=name, industry=industry, list_status="L", is_cyclical=out["is_cyclical"])
    if val_history:
        val_score, pe_pct_v, pb_pct_v = calculate_valuation_score(val_history, sinfo.is_cyclical)
        out["valuation_score_engine"] = {
            "score": round(val_score, 2),
            "pe_pct": round(pe_pct_v * 100, 1),
            "pb_pct": round(pb_pct_v * 100, 1),
        }
    else:
        out["valuation_score_engine"] = None

    # ── 5. 趋势 ──
    trend_score = 50.0
    if val_history and len(val_history) >= 3:
        dates = pd.to_datetime([v.trade_date for v in val_history], format="%Y%m%d")
        daily_pe = pd.Series([v.pe_ttm for v in val_history], index=dates)
        daily_pb = pd.Series([v.pb for v in val_history], index=dates)
        trend_map = batch_trend({ts_code: (daily_pe, daily_pb)}, {ts_code: sinfo})
        trend_score = trend_map.get(ts_code, 50.0)
    out["trend_score"] = round(float(trend_score), 2)

    # ── 6. 困境评分 ──
    latest = fin[0]
    eps_hist = [f.eps for f in fin]
    roe_hist = [f.roe for f in fin]
    rev_g = [f.yoy_revenue_growth or 0.0 for f in fin]
    prof_g = [f.yoy_profit_growth or 0.0 for f in fin]
    pe_pct_01 = (out["valuation"]["pe_pct"] or 50) / 100
    pb_pct_01 = out["valuation"]["pb_pct"] / 100
    distress = calculate_distress_score(
        eps_history=eps_hist, pe_pct=pe_pct_01, pb_pct=pb_pct_01,
        debt_ratio=(latest.total_debt or 0) / (latest.total_assets or 1),
        operating_cf=latest.operating_cf or 0.0,
        total_debt=latest.total_debt or 0.0, total_assets=latest.total_assets or 0.0,
        roe_history=roe_hist, revenue_history=rev_g, profit_history=prof_g,
        delta_g=pscore.delta_g, ts_code=ts_code,
    )
    out["distress"] = {
        "total": round(distress.total_score, 2), "l1": round(distress.layer1_score, 2),
        "l2": round(distress.layer2_score, 2), "l3": round(distress.layer3_score, 2),
    }

    # ── 7. davis double ──
    davis = calculate_davis_double_score(
        valuation_score=(out["valuation_score_engine"]["score"] if out["valuation_score_engine"] else 50.0),
        prosperity_score=pscore.composite_score,
        distress_score=distress.total_score,
        trend_score=trend_score, ts_code=ts_code, name=name,
    )
    out["davis"] = {"final": round(davis.final_score, 2), "rank": davis.rank}

    # ── 8. 5 补充因子 ──
    mom = analyze_momentum(client, ts_code)
    div = analyze_dividend(client, ts_code)
    fcast = analyze_forecast(client, ts_code, pscore)  # 坑13:传 ProsperityScore 对象
    hc = analyze_holder_concentration(client, ts_code)
    pq = analyze_profitability_quality(fin)
    out["momentum"] = {
        "score": round(mom.momentum_score, 2), "abs": round(mom.absolute_momentum_score, 2),
        "rs": round(mom.rs_percentile, 1) if mom.rs_percentile is not None else None,
        "windows": {str(k): round(v, 2) for k, v in mom.window_returns.items()},
    } if mom else None
    out["dividend"] = {
        "score": round(div.dividend_score, 2), "consecutive_years": div.consecutive_years,
        "yield_pct": div.latest_yield_pct, "payout_years": div.payout_years,
    }
    out["forecast_signal"] = {
        "leading_score": round(fcast.leading_score, 2), "type": fcast.type,
        "ann_date": fcast.ann_date, "end_date": fcast.end_date,
        "p_min": fcast.p_change_min, "p_max": fcast.p_change_max, "p_mid": fcast.p_change_mid,
        "is_stale": fcast.is_stale,
    } if fcast else None
    out["holder_conc"] = {
        "score": round(hc.concentration_score, 2), "trend": hc.trend,
        "latest_chg_pct": hc.latest_chg_pct,
        "counts": hc.holder_counts, "periods": hc.periods,
    } if hc else None
    out["profitability_q"] = {
        "quality_score": round(pq.quality_score, 2),
        "latest_gross_margin": pq.latest_gross_margin, "gm_delta": pq.gross_margin_delta,
        "rd_intensity": pq.latest_rd_intensity, "data_sufficient": pq.data_sufficient,
    }

    # ── 9. 股东户数原始(近 10 期) + 十大流通股东 ──
    h = pro.stk_holdernumber(ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num"
                            ).dropna(subset=["holder_num"]).sort_values("end_date").tail(10)
    out["holder_num_rows"] = [
        {"end_date": r["end_date"], "ann_date": r["ann_date"], "num": int(r["holder_num"])}
        for _, r in h.iterrows()
    ]
    try:
        t10 = pro.top10_floatholders(ts_code=ts_code, period=fin[0].report_period)
        out["top10_float"] = [
            {"holder_name": r["holder_name"], "hold": float(r["hold_ratio"]) if pd.notna(r.get("hold_ratio")) else None}
            for _, r in t10.iterrows()
        ][:10]
        out["top10_float_period"] = fin[0].report_period
    except Exception as e:
        logger.warning("top10_floatholders 失败 {}: {}", ts_code, e)
        out["top10_float"] = None

    # ── 10. 时效性校验 + 业绩预告明细(万元!) ──
    db1 = pro.daily_basic(ts_code=ts_code, limit=1)
    out["freshness"] = {
        "latest_trade": db1.iloc[0]["trade_date"] if len(db1) else None,
        "latest_close": float(db1.iloc[0]["close"]) if len(db1) else None,
    }
    fc_rows = pro.forecast(ts_code=ts_code, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
    out["forecast_rows"] = [
        {"ann_date": r["ann_date"], "end_date": r["end_date"], "type": r["type"],
         "p_min": r["p_change_min"], "p_max": r["p_change_max"],
         "np_min_yi": round(float(r["net_profit_min"]) / 1e4, 2) if pd.notna(r["net_profit_min"]) else None,  # 万元→亿
         "np_max_yi": round(float(r["net_profit_max"]) / 1e4, 2) if pd.notna(r["net_profit_max"]) else None}
        for _, r in fc_rows.iterrows()
    ]

    # ── 11. 相对市场估值锚定 ──
    try:
        rv = analyze_relative_valuation(pro, ts_code, name)
        out["relative_valuation"] = {
            "benchmark": rv.benchmark, "stock_pe": rv.stock_pe, "index_pe": rv.index_pe,
            "pe_ratio": rv.pe_ratio, "pe_ratio_pct": rv.pe_ratio_pct, "pe_ratio_label": rv.pe_ratio_label,
            "erp": rv.erp, "erp_label": rv.erp_label, "risk_free_rate": rv.risk_free_rate,
            "stock_pe_pct": rv.stock_pe_pct, "index_pe_pct": rv.index_pe_pct,
            "quadrant": rv.quadrant, "quadrant_label": rv.quadrant_label,
            "verdict": rv.composite_verdict, "signals": rv.signals,
        }
    except Exception as e:
        logger.warning("relative_valuation 失败 {}: {}", ts_code, e)
        out["relative_valuation"] = None

    return out


def main() -> None:
    client = TushareClient()
    pro = get_pro_api(timeout=60)
    results = []
    for ts_code, name in TARGETS:
        logger.info("=" * 60)
        logger.info("采集 {} {}", ts_code, name)
        try:
            r = collect_one(client, pro, ts_code, name)
            results.append(r)
            logger.success("{} 完成: davis={} prosperity={} pb={}%分位", name, r["davis"]["final"], r["prosperity"]["composite"], r["valuation"]["pb_pct"])
        except Exception:
            logger.exception("采集 {} 失败", ts_code)
            results.append({"ts_code": ts_code, "name": name, "error": "failed"})
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    logger.success("写入 {}", OUT)


if __name__ == "__main__":
    main()
