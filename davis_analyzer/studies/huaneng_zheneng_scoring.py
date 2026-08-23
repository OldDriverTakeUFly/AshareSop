#!/usr/bin/env python3
"""华能国际 (600011.SH) + 浙能电力 (600023.SH) 火电双标的引擎取数脚本.

复制 tianyue_scoring.py 的四维评分调用链,一次跑两个标的,并叠加:
  - 全历史 daily_basic 分段直连(坑点:client.get_daily_basic 增量窗口仅 22 天)
  - 5 补充因子引擎(momentum/dividend/forecast/holder_concentration/profitability)
  - 股东户数趋势(stk_holdernumber, dropna 防垃圾行)
  - 十大流通股东(top10_floatholders)
  - 分红记录(pro.dividend)
  - 相对市场估值锚定(stockhot.valuation.analyze_relative_valuation)
  - 火电同业快照(华电/大唐/申能/皖能/国电)
  - 年度利润轨迹(2020-2025, pro.income)
  - 数据时效性校验(daily_basic 最新交易日 / income 最新披露 / forecast)

用法:
    cd /home/leo/Projects/CodeAgentDashboard
    .venv/bin/python davis_analyzer/studies/huaneng_zheneng_scoring.py

输出:
    .sisyphus/evidence/huaneng_zheneng/dual-scoring.json
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from loguru import logger
from dotenv import load_dotenv

# ── 标准 env(防 stale token + 防 /app PROJECT_ROOT)──
load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)

import os

os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.forecast import analyze_forecast
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.trend import batch_trend
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo, ValuationData
from davis_analyzer.valuation import (
    calculate_valuation_score,
    detect_cyclical,
)
from stockhot.tushare_config import get_pro_api
from stockhot.valuation import analyze_relative_valuation

TARGETS = [
    ("600011.SH", "华能国际"),
    ("600023.SH", "浙能电力"),
]
PEERS = [
    ("600011.SH", "华能国际"),
    ("600023.SH", "浙能电力"),
    ("600027.SH", "华电国际"),
    ("601991.SH", "大唐发电"),
    ("600642.SH", "申能股份"),
    ("000543.SZ", "皖能电力"),
    ("600795.SH", "国电电力"),
]
OUTPUT_PATH = Path(".sisyphus/evidence/huaneng_zheneng/dual-scoring.json")


def _fetch_full_daily_basic(pro, ts_code: str, start: str, end: str) -> pd.DataFrame:
    """分段(≤500天)直连 pro.daily_basic,concat 后 reset_index(坑点修复)."""
    frames: list[pd.DataFrame] = []
    cur = start
    while cur <= end:
        seg_end = (datetime.strptime(cur, "%Y%m%d") + timedelta(days=500)).strftime("%Y%m%d")
        if seg_end > end:
            seg_end = end
        df_seg = pro.daily_basic(
            ts_code=ts_code,
            start_date=cur,
            end_date=seg_end,
            fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,dv_ratio,dv_ttm",
        )
        if df_seg is not None and len(df_seg):
            frames.append(df_seg)
        cur = (datetime.strptime(seg_end, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    if not frames:
        return pd.DataFrame()
    out = (
        pd.concat(frames)
        .drop_duplicates("trade_date")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
    return out


def _series_stats(s: pd.Series) -> dict | None:
    if not len(s):
        return None
    cur = float(s.iloc[-1])
    pct = float((s < cur).sum() / len(s) * 100)
    qs = {f"p{p}": round(float(s.quantile(p / 100)), 3) for p in [10, 25, 50, 75, 90]}
    return {
        "current": round(cur, 3),
        "percentile": round(pct, 1),
        "points": int(len(s)),
        "latest_date": None,
        **qs,
    }


def score_one(client: TushareClient, pro, ts_code: str, name: str) -> dict:
    """单标的全量取数 + 四维评分 + 5 因子,返回 JSON 可序列化 dict."""
    logger.info("=" * 66)
    logger.info("{} ({}) 引擎取数", name, ts_code)

    # ── 1. 财务(12 季度)──
    fin = fetch_financial_data(client, ts_code, periods=12)
    fin_rows = []
    for fd in fin:
        fin_rows.append(
            {
                "report_period": fd.report_period,
                "revenue_yi": round(float(fd.revenue or 0) / 1e8, 2),
                "net_profit_yi": round(float(fd.net_profit or 0) / 1e8, 2),
                "eps": round(float(fd.eps or 0), 4),
                "roe": round(float(fd.roe or 0), 2),
                "operating_cf_yi": round(float(fd.operating_cf or 0) / 1e8, 2),
                "total_assets_yi": round(float(fd.total_assets or 0) / 1e8, 1),
                "total_debt_yi": round(float(fd.total_debt or 0) / 1e8, 1),
                "debt_ratio_pct": round(
                    float(fd.total_debt or 0) / float(fd.total_assets or 1) * 100, 1
                ),
                "yoy_rev_pct": round(fd.yoy_revenue_growth * 100, 1) if fd.yoy_revenue_growth is not None else None,
                "yoy_profit_pct": round(fd.yoy_profit_growth * 100, 1) if fd.yoy_profit_growth is not None else None,
            }
        )
    logger.info("财务 {} 期, 最新 {}", len(fin), fin[0].report_period if fin else "N/A")

    # ── 2. 全历史 daily_basic(直连分段,校验行数)──
    end = date.today().strftime("%Y%m%d")
    start = "20180101"  # 全历史(2018 起,覆盖 2021 巨亏周期)
    db = _fetch_full_daily_basic(pro, ts_code, start, end)
    latest_trade = db["trade_date"].iloc[-1] if len(db) else "none"
    logger.info("daily_basic {} 点, 最新交易日 {}", len(db), latest_trade)
    assert len(db) >= 700, f"daily_basic 行数不足({len(db)}),增量窗口坑"

    pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
    pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
    ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
    mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
    dv = pd.to_numeric(db["dv_ttm"], errors="coerce").dropna()

    cutoff_3y = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
    db3 = db[db["trade_date"] >= cutoff_3y].reset_index(drop=True)
    pe3 = pd.to_numeric(db3["pe_ttm"], errors="coerce").dropna()
    pb3 = pd.to_numeric(db3["pb"], errors="coerce").dropna()
    ps3 = pd.to_numeric(db3["ps"], errors="coerce").dropna()

    val_series = {
        "full": {
            "window": f"{db['trade_date'].iloc[0]}→{latest_trade}",
            "pe_ttm": _series_stats(pe),
            "pb": _series_stats(pb),
            "ps": _series_stats(ps),
            "total_mv_yi": _series_stats(mv),
            "dv_ttm": _series_stats(dv),
        },
        "y3": {
            "pe_ttm": _series_stats(pe3),
            "pb": _series_stats(pb3),
            "ps": _series_stats(ps3),
        },
    }

    # ── 3. 构造 ValuationData 列表(过滤 NaN,日期降序 latest 在首位)──
    db_valid = db.dropna(subset=["pe_ttm", "pb"]).copy()
    db_valid = db_valid.sort_values("trade_date", ascending=False).reset_index(drop=True)
    val_history = [
        ValuationData(
            ts_code=ts_code,
            trade_date=str(r["trade_date"]),
            pe_ttm=float(r["pe_ttm"]),
            pb=float(r["pb"]),
            ps=float(r["ps"]) if pd.notna(r["ps"]) else None,
            total_mv=float(r["total_mv"]) if pd.notna(r["total_mv"]) else None,
        )
        for _, r in db_valid.iterrows()
    ]
    logger.info("ValuationData {} 点(有效 PE/PB)", len(val_history))

    # ── 4. 行业 + 周期判定 ──
    stock_df = client.get_stock_list()
    row = stock_df[stock_df["ts_code"] == ts_code]
    industry = str(row.iloc[0].get("industry", "") or "") if not row.empty else ""
    real_name = str(row.iloc[0].get("name", name) or name) if not row.empty else name
    is_cyclical = detect_cyclical(industry)
    stock_info = StockInfo(
        ts_code=ts_code, name=real_name, industry=industry, list_status="L", is_cyclical=is_cyclical
    )
    logger.info("行业={} 周期={}", industry, is_cyclical)

    # ── 5. 四维评分 ──
    val_score, pe_pct, pb_pct = calculate_valuation_score(val_history, is_cyclical)
    pscore = calculate_prosperity_score(fin)
    stage = classify_stock_stage(pscore)

    # 趋势(全历史日频 → batch_trend)
    dates = pd.to_datetime([v.trade_date for v in val_history], format="%Y%m%d")
    daily_pe = pd.Series([v.pe_ttm for v in val_history], index=dates).sort_index()
    daily_pb = pd.Series([v.pb for v in val_history], index=dates).sort_index()
    db3_valid = db3.dropna(subset=["pe_ttm", "pb"]).sort_values("trade_date")
    d3 = pd.to_datetime(db3_valid["trade_date"], format="%Y%m%d")
    daily_pe3 = pd.Series(pd.to_numeric(db3_valid["pe_ttm"]).values, index=d3)
    daily_pb3 = pd.Series(pd.to_numeric(db3_valid["pb"]).values, index=d3)
    trend_map_full = batch_trend({ts_code: (daily_pe, daily_pb)}, {ts_code: stock_info})
    trend_map_3y = batch_trend({ts_code: (daily_pe3, daily_pb3)}, {ts_code: stock_info})

    latest = fin[0]
    debt_ratio = float(latest.total_debt or 0) / float(latest.total_assets or 1)
    distress = calculate_distress_score(
        eps_history=[f.eps for f in fin],
        pe_pct=pe_pct,
        pb_pct=pb_pct,
        debt_ratio=debt_ratio,
        operating_cf=float(latest.operating_cf or 0),
        total_debt=float(latest.total_debt or 0),
        total_assets=float(latest.total_assets or 0),
        roe_history=[f.roe for f in fin],
        revenue_history=[f.yoy_revenue_growth or 0.0 for f in fin],
        profit_history=[f.yoy_profit_growth or 0.0 for f in fin],
        delta_g=pscore.delta_g,
        ts_code=ts_code,
    )

    from davis_analyzer.scoring import calculate_davis_double_score

    davis = calculate_davis_double_score(
        valuation_score=val_score,
        prosperity_score=pscore.composite_score,
        distress_score=distress.total_score,
        trend_score=float(trend_map_3y.get(ts_code, 50.0)),
        ts_code=ts_code,
        name=real_name,
    )
    logger.info(
        "四维: val={:.1f} prosp={:.1f} distress={:.1f} trend3y={:.1f} → final={:.1f}",
        val_score, pscore.composite_score, distress.total_score,
        trend_map_3y.get(ts_code, 50.0), davis.final_score,
    )

    # ── 6. 5 补充因子 ──
    mom = analyze_momentum(client, ts_code)
    div = analyze_dividend(client, ts_code)
    fc = analyze_forecast(client, ts_code, pscore)
    hc = analyze_holder_concentration(client, ts_code)
    pq = analyze_profitability_quality(fin)

    # ── 7. 股东户数(dropna 防垃圾行)──
    h = pro.stk_holdernumber(
        ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num"
    ).dropna(subset=["holder_num"]).sort_values("end_date")
    holder_rows = []
    prev = None
    for _, r in h.tail(10).iterrows():
        num = int(r["holder_num"])
        chg = round((num - prev) / prev * 100, 1) if prev else None
        holder_rows.append(
            {"end_date": r["end_date"], "ann_date": r["ann_date"], "holder_num": num, "chg_pct": chg}
        )
        prev = num

    # 十大流通股东(近 4 期合计)
    t10 = pro.top10_floatholders(ts_code=ts_code, fields="ts_code,ann_date,end_date,hold_ratio")
    t10_rows = []
    if t10 is not None and len(t10):
        t10v = t10.dropna(subset=["hold_ratio"])
        for ed, grp in t10v.groupby("end_date"):
            t10_rows.append(
                {"end_date": ed, "top10_pct": round(float(grp["hold_ratio"].sum()), 2)}
            )
        t10_rows = sorted(t10_rows, key=lambda x: x["end_date"])[-5:]

    # ── 8. 分红记录 ──
    dv_df = pro.dividend(
        ts_code=ts_code,
        fields="ts_code,end_date,ann_date,div_proc,cash_div_tax,cash_div,base_share,pay_date",
    )
    dv_rows = []
    if dv_df is not None and len(dv_df):
        dvv = dv_df[dv_df["div_proc"] == "实施"].dropna(subset=["cash_div_tax"])
        for _, r in dvv.sort_values("end_date").tail(10).iterrows():
            dv_rows.append(
                {
                    "end_date": r["end_date"],
                    "cash_div_per_10sh": float(r["cash_div_tax"]),
                    "pay_date": r.get("pay_date"),
                }
            )

    # ── 9. 年度利润轨迹(2020-2025)──
    annual_rows = []
    for period in ["20201231", "20211231", "20221231", "20231231", "20241231", "20251231", "20260331", "20260630"]:
        try:
            inc = pro.income(
                ts_code=ts_code, period=period,
                fields="ts_code,ann_date,end_date,total_revenue,n_income,n_income_attr_p",
            )
            if inc is not None and len(inc):
                r0 = inc.iloc[0]
                annual_rows.append(
                    {
                        "period": period,
                        "ann_date": r0.get("ann_date"),
                        "revenue_yi": round(float(r0["total_revenue"]) / 1e8, 1),
                        "n_income_yi": round(float(r0["n_income"]) / 1e8, 1),
                        "n_income_attr_yi": round(float(r0["n_income_attr_p"]) / 1e8, 1),
                    }
                )
        except Exception as e:
            logger.warning("income {} 失败: {}", period, e)

    # ── 10. 业绩预告原始(单位万元,坑点 14)──
    fc_raw_rows = []
    try:
        fcr = pro.forecast(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,norm_val_nm",
        )
        if fcr is not None and len(fcr):
            for _, r in fcr.head(5).iterrows():
                np_min_yi = None
                if pd.notna(r.get("net_profit_min")):
                    np_min_yi = round(float(r["net_profit_min"]) / 1e4, 1)  # 万元→亿
                fc_raw_rows.append(
                    {
                        "ann_date": r["ann_date"], "end_date": r["end_date"], "type": r["type"],
                        "p_change": f"[{r['p_change_min']},{r['p_change_max']}]",
                        "np_min_yi": np_min_yi,
                    }
                )
    except Exception as e:
        logger.warning("forecast {} 失败: {}", ts_code, e)

    # ── 11. 相对估值锚定 ──
    try:
        rv = analyze_relative_valuation(pro, ts_code, real_name, lookback_years=3)
        rv_dict = {
            "benchmark": rv.benchmark,
            "stock_pe": rv.stock_pe, "index_pe": rv.index_pe,
            "pe_ratio": rv.pe_ratio, "pe_ratio_pct": rv.pe_ratio_pct, "pe_ratio_label": rv.pe_ratio_label,
            "earnings_yield": rv.earnings_yield, "risk_free_rate": rv.risk_free_rate,
            "erp": rv.erp, "erp_label": rv.erp_label,
            "stock_pe_pct": rv.stock_pe_pct, "index_pe_pct": rv.index_pe_pct,
            "quadrant": rv.quadrant, "quadrant_label": rv.quadrant_label,
            "composite_verdict": rv.composite_verdict, "signals": rv.signals,
        }
    except Exception as e:
        logger.warning("相对估值失败: {}", e)
        rv_dict = {"error": str(e)}

    # ── 12. 动量手工复核(pro.daily)──
    mom_check = {}
    try:
        px = pro.daily(ts_code=ts_code, start_date="20250101", end_date=end,
                       fields="ts_code,trade_date,close")
        px = px.sort_values("trade_date").reset_index(drop=True)
        closes = pd.to_numeric(px["close"])
        for w in [60, 120, 250]:
            if len(closes) > w:
                mom_check[f"wr{w}"] = round(float((closes.iloc[-1] / closes.iloc[-w] - 1) * 100), 2)
        mom_check["latest_close"] = float(closes.iloc[-1])
        mom_check["latest_date"] = px["trade_date"].iloc[-1]
    except Exception as e:
        mom_check = {"error": str(e)}

    return {
        "ts_code": ts_code,
        "name": real_name,
        "industry": industry,
        "is_cyclical": is_cyclical,
        "scored_at": datetime.now().isoformat(),
        "financial_quarterly": fin_rows,
        "annual_track": annual_rows,
        "valuation_series": val_series,
        "latest_trade_date": latest_trade,
        "four_dim": {
            "valuation_score": round(val_score, 2),
            "pe_pct": round(pe_pct * 100, 1),
            "pb_pct": round(pb_pct * 100, 1),
            "prosperity": {
                "composite": round(pscore.composite_score, 2),
                "revenue": round(pscore.revenue_score, 2),
                "profit": round(pscore.profit_score, 2),
                "slope": round(pscore.slope_score, 2),
                "duration": round(pscore.duration_score, 2),
                "delta_g": round(pscore.delta_g, 2),
                "stage": stage,
            },
            "distress": {
                "total": round(distress.total_score, 2),
                "layer1": round(distress.layer1_score, 2),
                "layer2": round(distress.layer2_score, 2),
                "layer3": round(distress.layer3_score, 2),
            },
            "trend_full": round(float(trend_map_full.get(ts_code, 50.0)), 2),
            "trend_3y": round(float(trend_map_3y.get(ts_code, 50.0)), 2),
            "davis_final": round(davis.final_score, 2),
        },
        "factors": {
            "momentum": {
                "score": mom.momentum_score if mom else None,
                "window_returns": mom.window_returns if mom else None,
                "rs_percentile": mom.rs_percentile if mom else None,
            },
            "momentum_manual_check": mom_check,
            "dividend": {
                "score": div.dividend_score,
                "consecutive_years": div.consecutive_years,
                "latest_yield_pct": div.latest_yield_pct,
                "payout_years": div.payout_years,
            },
            "forecast": {
                "type": fc.type if fc else None,
                "p_change_mid": fc.p_change_mid if fc else None,
                "leading_score": fc.leading_score if fc else None,
                "is_stale": fc.is_stale if fc else None,
            },
            "forecast_raw": fc_raw_rows,
            "holder_concentration": {
                "score": hc.concentration_score if hc else None,
                "trend": hc.trend if hc else None,
                "latest_chg_pct": hc.latest_chg_pct if hc else None,
                "holder_counts": hc.holder_counts if hc else None,
            },
            "profitability": {
                "quality_score": pq.quality_score if pq else None,
                "latest_gross_margin": pq.latest_gross_margin if pq else None,
                "gross_margin_delta": pq.gross_margin_delta if pq else None,
                "latest_rd_intensity": pq.latest_rd_intensity if pq else None,
            },
        },
        "holder_number_trend": holder_rows,
        "top10_float": t10_rows,
        "dividend_records": dv_rows,
        "relative_valuation": rv_dict,
    }


def peers_snapshot(pro) -> list[dict]:
    """火电同业估值快照(最新交易日)."""
    rows = []
    for code, nm in PEERS:
        try:
            d = pro.daily_basic(ts_code=code, fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,dv_ttm,dv_ratio", limit=3)
            if d is not None and len(d):
                d = d.sort_values("trade_date")
                r = d.iloc[-1]
                rows.append(
                    {
                        "ts_code": code, "name": nm,
                        "trade_date": r["trade_date"],
                        "pe_ttm": round(float(r["pe_ttm"]), 2) if pd.notna(r["pe_ttm"]) else None,
                        "pb": round(float(r["pb"]), 3) if pd.notna(r["pb"]) else None,
                        "ps": round(float(r["ps"]), 3) if pd.notna(r["ps"]) else None,
                        "total_mv_yi": round(float(r["total_mv"]) / 1e4, 1) if pd.notna(r["total_mv"]) else None,
                        "dv_ttm_pct": round(float(r["dv_ttm"]), 2) if pd.notna(r["dv_ttm"]) else None,
                    }
                )
        except Exception as e:
            logger.warning("peer {} 失败: {}", code, e)
    return rows


def main() -> None:
    client = TushareClient()
    pro = get_pro_api(timeout=60)

    results = {}
    for ts_code, name in TARGETS:
        results[ts_code] = score_one(client, pro, ts_code, name)

    results["peers_snapshot"] = peers_snapshot(pro)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.info("✅ 完成,输出 {}", OUTPUT_PATH)

    # 控制台摘要(短)
    for ts_code, name in TARGETS:
        r = results[ts_code]
        fd = r["four_dim"]
        print(f"\n===== {name} {ts_code} =====")
        print(f"  估值分={fd['valuation_score']} PE分位={fd['pe_pct']}% PB分位={fd['pb_pct']}%")
        print(f"  景气 composite={fd['prosperity']['composite']} ΔG={fd['prosperity']['delta_g']} 阶段={fd['prosperity']['stage']}")
        print(f"  困境={fd['distress']['total']} 趋势3y={fd['trend_3y']} → davis={fd['davis_final']}")
        print(f"  PB={r['valuation_series']['full']['pb']['current']}({r['valuation_series']['full']['pb']['percentile']}%分位/全) 3y={r['valuation_series']['y3']['pb']['current']}({r['valuation_series']['y3']['pb']['percentile']}%)")
        print(f"  股息率TTM={r['valuation_series']['full']['dv_ttm']['current'] if r['valuation_series']['full']['dv_ttm'] else 'N/A'}%")
        fct = r["factors"]
        print(f"  因子: mom={fct['momentum']['score']} div={fct['dividend']['score']}(连{fct['dividend']['consecutive_years']}年,{fct['dividend']['latest_yield_pct']}%) fc={fct['forecast']['type']}/{fct['forecast']['leading_score']} hc={fct['holder_concentration']['score']}({fct['holder_concentration']['trend']}) pq={fct['profitability']['quality_score']}")


if __name__ == "__main__":
    main()
