#!/usr/bin/env python3
"""盛科通信 (688702.SH) 单股评分脚本（亏损标的，PS/PB 主锚，PE 失效标注）.

复制 tianyue_scoring.py 调用链，另加：
  - 5 因子补充引擎（momentum/dividend/forecast/holder_concentration/profitability）
  - 股东户数趋势（stk_holdernumber）
  - 相对市场估值锚定（stockhot.valuation.analyze_relative_valuation）
  - 数据时效性校验（daily_basic 最新交易日 / income 最新披露 / forecast）
  - PS/PB 3年分位（get_daily_basic 手工校验 ≥700 行）

用法:
    cd /home/leo/Projects/CodeAgentDashboard && PYTHONPATH=. .venv/bin/python davis_analyzer/studies/shengke_scoring.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from loguru import logger

load_dotenv(".env", override=True)
import os
os.environ["PROJECT_ROOT"] = os.getcwd()

from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import batch_trend, calculate_monthly_trend, calculate_trend_slope
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo
from davis_analyzer.valuation import calculate_valuation_score, detect_cyclical, fetch_valuation_history

TS_CODE = "688702.SH"
STOCK_NAME = "盛科通信"
PERIODS = 12
OUTPUT_PATH = Path(".sisyphus/evidence/shengke/scoring.json")


def main() -> None:
    out: dict = {"ts_code": TS_CODE}
    client = TushareClient()

    # ── 0. 核对代码与公司名（防张冠李戴）──
    stock_df = client.get_stock_list()
    row = stock_df[stock_df["ts_code"] == TS_CODE]
    if not row.empty:
        logger.info("核对: {} = {} 行业={}", TS_CODE, row.iloc[0]["name"], row.iloc[0].get("industry", ""))
        out["stock_check"] = {"name": str(row.iloc[0]["name"]), "industry": str(row.iloc[0].get("industry", ""))}
        industry = str(row.iloc[0].get("industry", "") or "")
    else:
        industry = ""

    # ── 1. 财务 ──
    fin = fetch_financial_data(client, TS_CODE, periods=PERIODS)
    logger.info("财务 {} 期，最新 {}", len(fin), fin[0].report_period)
    fin_rows = []
    for fd in fin:
        fin_rows.append({
            "report_period": fd.report_period,
            "revenue_yi": round((fd.revenue or 0) / 1e8, 3),
            "net_profit_yi": round((fd.net_profit or 0) / 1e8, 3),
            "eps": fd.eps,
            "roe": fd.roe,
            "operating_cf_yi": round((fd.operating_cf or 0) / 1e8, 3),
            "total_assets_yi": round((fd.total_assets or 0) / 1e8, 3),
            "total_debt_yi": round((fd.total_debt or 0) / 1e8, 3),
            "yoy_rev": fd.yoy_revenue_growth,
            "yoy_prof": fd.yoy_profit_growth,
        })
    out["financials"] = fin_rows
    latest = fin[0]
    eps_history = [fd.eps for fd in fin]
    roe_history = [fd.roe for fd in fin]
    revenue_growth = [fd.yoy_revenue_growth or 0.0 for fd in fin]
    profit_growth = [fd.yoy_profit_growth or 0.0 for fd in fin]
    debt_ratio = (latest.total_debt or 0) / (latest.total_assets or 1)

    # ── 2. 估值历史（≥700 行校验）──
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
    db = client.get_daily_basic(TS_CODE, start, end)
    logger.info("daily_basic {} 行 ({}~{})", len(db), start, end)
    if len(db) < 700:
        logger.warning("daily_basic 行数 <700，改用 stockhot pro 分段直连")
        from stockhot.tushare_config import get_pro_api
        pro = get_pro_api(timeout=60)
        segs = []
        d0 = date.today() - timedelta(days=1095)
        while d0 < date.today():
            d1 = min(d0 + timedelta(days=490), date.today())
            seg = pro.daily_basic(ts_code=TS_CODE, start_date=d0.strftime("%Y%m%d"),
                                  end_date=d1.strftime("%Y%m%d"),
                                  fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,turnover_rate")
            segs.append(seg)
            d0 = d1 + timedelta(days=1)
        db = pd.concat(segs).drop_duplicates("trade_date").reset_index(drop=True)
        logger.info("分段直连后 {} 行", len(db))
    db = db.sort_values("trade_date").reset_index(drop=True)
    out["valuation_latest"] = {
        "trade_date": str(db["trade_date"].iloc[-1]),
        "pe_ttm": None if pd.isna(db["pe_ttm"].iloc[-1]) else float(db["pe_ttm"].iloc[-1]),
        "pb": float(db["pb"].iloc[-1]),
        "ps": float(db["ps"].iloc[-1]),
        "total_mv_yi": float(db["total_mv"].iloc[-1]) / 1e4,
    }
    pct = {}
    for col, key in [("pe_ttm", "pe"), ("pb", "pb"), ("ps", "ps")]:
        s = pd.to_numeric(db[col], errors="coerce").dropna()
        if len(s) < 50:
            pct[key] = {"n": len(s), "note": "有效点不足"}
            continue
        cur = s.iloc[-1]
        pct[key] = {
            "n": int(len(s)),
            "current": round(float(cur), 2),
            "percentile": round(float((s < cur).sum() / len(s) * 100), 1),
            "q10": round(float(s.quantile(0.10)), 2), "q25": round(float(s.quantile(0.25)), 2),
            "q50": round(float(s.quantile(0.50)), 2), "q75": round(float(s.quantile(0.75)), 2),
            "q90": round(float(s.quantile(0.90)), 2), "q95": round(float(s.quantile(0.95)), 2),
        }
    out["valuation_percentile"] = pct

    # 年初至今涨幅
    from stockhot.tushare_config import get_pro_api
    pro = get_pro_api(timeout=60)
    px = pro.daily(ts_code=TS_CODE, start_date="20251231", end_date=end,
                   fields="trade_date,close,pre_close")
    px = px.sort_values("trade_date").reset_index(drop=True)
    if len(px):
        base = float(px["pre_close"].iloc[0])
        last = float(px["close"].iloc[-1])
        out["ytd_pct"] = round((last / base - 1) * 100, 1)
        out["latest_close"] = last

    # ── 3. 引擎估值分（val_history 过滤 NaN PE）──
    val_history = fetch_valuation_history(client, TS_CODE)
    logger.info("fetch_valuation_history {} 天", len(val_history))
    out["val_history_days"] = len(val_history)
    stock_info = StockInfo(ts_code=TS_CODE, name=STOCK_NAME, industry=industry,
                           list_status="L", is_cyclical=detect_cyclical(industry))
    if val_history:
        val_score, pe_pct, pb_pct = calculate_valuation_score(val_history, stock_info.is_cyclical)
        logger.info("估值分={:.2f} pe_pct={:.2f} pb_pct={:.2f}", val_score, pe_pct, pb_pct)
    else:
        val_score, pe_pct, pb_pct = 50.0, 0.5, 0.5
    # 亏损股：PE 分位失真，用手工 PB 分位覆盖
    if pct.get("pe", {}).get("n", 0) < 50:
        pe_pct = 0.5
        logger.warning("PE 有效点不足（亏损），估值分中 PE 部分失真，报告须标注")
    if pct.get("pb", {}).get("percentile") is not None:
        pb_pct = pct["pb"]["percentile"] / 100
    out["valuation_engine"] = {"score": round(val_score, 2), "pe_pct": round(pe_pct, 4), "pb_pct": round(pb_pct, 4)}

    # ── 4. 景气度 ──
    pscore = calculate_prosperity_score(fin)
    out["prosperity"] = {
        "composite": round(pscore.composite_score, 2), "revenue": round(pscore.revenue_score, 2),
        "profit": round(pscore.profit_score, 2), "slope": round(pscore.slope_score, 2),
        "duration": round(pscore.duration_score, 2), "delta_g": round(pscore.delta_g, 2),
    }
    logger.info("景气度 composite={} delta_g={}", pscore.composite_score, pscore.delta_g)

    # ── 5. 趋势 ──
    trend_score = 50.0
    trend_detail = {"score": 50.0, "reason": "数据不足"}
    if val_history and len(val_history) >= 3:
        try:
            dates = pd.to_datetime([v.trade_date for v in val_history], format="%Y%m%d")
            daily_pe = pd.Series([v.pe_ttm for v in val_history], index=dates)
            daily_pb = pd.Series([v.pb for v in val_history], index=dates)
            trend_map = batch_trend({TS_CODE: (daily_pe, daily_pb)}, {TS_CODE: stock_info})
            trend_score = trend_map.get(TS_CODE, 50.0)
            m_pe, m_pb = calculate_monthly_trend(daily_pe, daily_pb)
            trend_detail = {
                "score": round(trend_score, 2),
                "pb_slope": round(calculate_trend_slope(m_pb), 4),
                "monthly_pb_points": len(m_pb),
                "reason": f"亏损股 PE 失效，趋势分主要锚 PB",
            }
        except Exception as e:
            logger.exception("趋势计算失败: {}", e)
    out["trend"] = trend_detail

    # ── 6. 困境 ──
    distress = calculate_distress_score(
        eps_history=eps_history, pe_pct=pe_pct, pb_pct=pb_pct, debt_ratio=debt_ratio,
        operating_cf=latest.operating_cf or 0.0, total_debt=latest.total_debt or 0.0,
        total_assets=latest.total_assets or 0.0, roe_history=roe_history,
        revenue_history=revenue_growth, profit_history=profit_growth,
        delta_g=pscore.delta_g, ts_code=TS_CODE,
    )
    out["distress"] = {
        "total": round(distress.total_score, 2), "L1": round(distress.layer1_score, 2),
        "L2": round(distress.layer2_score, 2), "L3": round(distress.layer3_score, 2),
    }
    logger.info("困境 total={} L1={} L2={} L3={}", distress.total_score,
                distress.layer1_score, distress.layer2_score, distress.layer3_score)

    # ── 7. 戴维斯双击 ──
    davis = calculate_davis_double_score(
        valuation_score=val_score, prosperity_score=pscore.composite_score,
        distress_score=distress.total_score, trend_score=trend_score,
        ts_code=TS_CODE, name=STOCK_NAME,
    )
    out["davis_double"] = {
        "final": round(davis.final_score, 2), "valuation": round(val_score, 2),
        "trend": round(trend_score, 2), "prosperity": round(pscore.composite_score, 2),
        "distress": round(distress.total_score, 2),
    }
    logger.info("davis final={}", davis.final_score)

    # ── 8. 五因子补充引擎 ──
    try:
        mom = analyze_momentum(client, TS_CODE)
        out["momentum"] = {
            "score": mom.momentum_score, "abs": mom.absolute_momentum_score,
            "rs_pct": mom.rs_percentile, "window_returns": mom.window_returns,
        } if mom else None
    except Exception as e:
        logger.warning("momentum 失败: {}", e)
    try:
        div = analyze_dividend(client, TS_CODE)
        out["dividend"] = {"score": div.dividend_score, "years": div.consecutive_years,
                           "yield": div.latest_yield_pct}
    except Exception as e:
        logger.warning("dividend 失败: {}", e)
    try:
        fc = analyze_forecast(client, TS_CODE, pscore)
        out["forecast"] = {"leading_score": fc.leading_score, "type": fc.type,
                           "p_change_mid": fc.p_change_mid, "is_stale": fc.is_stale} if fc else None
    except Exception as e:
        logger.warning("forecast 失败: {}", e)
    try:
        hc = analyze_holder_concentration(client, TS_CODE)
        out["holder_conc"] = {"score": hc.concentration_score, "trend": hc.trend,
                              "latest_chg": hc.latest_chg_pct} if hc else None
    except Exception as e:
        logger.warning("holder_conc 失败: {}", e)
    try:
        pq = analyze_profitability_quality(fin)
        out["profitability"] = {"score": pq.quality_score,
                                "gross_margin": pq.latest_gross_margin,
                                "gm_delta": pq.gross_margin_delta,
                                "rd_intensity": pq.latest_rd_intensity}
    except Exception as e:
        logger.warning("profitability 失败: {}", e)

    # ── 9. 股东户数 ──
    try:
        h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num")
        h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(8)
        rows, prev = [], None
        for _, r in h.iterrows():
            num = int(r["holder_num"])
            chg = round((num - prev) / prev * 100, 1) if prev else None
            rows.append({"end_date": str(r["end_date"]), "holder_num": num, "chg_pct": chg})
            prev = num
        out["holder_number"] = rows
    except Exception as e:
        logger.warning("股东户数失败: {}", e)

    # ── 10. 相对估值 ──
    try:
        from stockhot.valuation import analyze_relative_valuation
        rel = analyze_relative_valuation(client._client if hasattr(client, "_client") else client, TS_CODE)
        if rel is not None:
            out["relative_valuation"] = {
                "pe_ratio": rel.pe_ratio, "pe_ratio_pct": rel.pe_ratio_pct,
                "erp": rel.erp, "risk_free_rate": rel.risk_free_rate,
                "index_pe": rel.index_pe, "index_pe_pct": rel.index_pe_pct,
                "quadrant": rel.quadrant,
            }
    except Exception as e:
        logger.warning("相对估值失败（亏损股 PE 法失效属预期）: {}", e)

    # ── 11. 时效性 ──
    try:
        db1 = pro.daily_basic(ts_code=TS_CODE, limit=1)
        inc = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
        fc2 = pro.forecast(ts_code=TS_CODE,
                           fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
        fcs = ""
        if len(fc2):
            fc2 = fc2[pd.to_numeric(fc2["ann_date"]) >= "20260101"] if len(fc2) else fc2
            r = fc2.iloc[0]
            fcs = f"{r['type']} ann={r['ann_date']} end={r['end_date']} [{r['p_change_min']},{r['p_change_max']}]%"
        out["freshness"] = {
            "latest_trade": str(db1.iloc[0]["trade_date"]) if len(db1) else None,
            "latest_period": str(inc.iloc[0]["end_date"]) if len(inc) else None,
            "latest_ann": str(inc.iloc[0]["ann_date"]) if len(inc) else None,
            "forecast": fcs,
        }
    except Exception as e:
        logger.warning("时效校验失败: {}", e)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
