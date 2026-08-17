#!/usr/bin/env python3
"""光伏组件三龙头(晶科/晶澳/天合)批量戴维斯双击评分 + 5 补充因子 + 股东户数 + 相对估值.

用法(从父仓库根目录):
    .venv/bin/python davis_analyzer/studies/pv3_scoring.py

输出:
    .sisyphus/evidence/pv3/{ts_code}.json
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from loguru import logger

# ── 标准 env:先 load_dotenv(override 防 stale token),再 pin PROJECT_ROOT ──
load_dotenv(".env", override=True)
os_env_pin = __import__("os")
os_env_pin.environ["PROJECT_ROOT"] = os_env_pin.getcwd()

from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.forecast import analyze_forecast
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import batch_trend
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo
from davis_analyzer.valuation import detect_cyclical

from stockhot.tushare_config import get_pro_api
from stockhot.valuation import analyze_relative_valuation

TARGETS = [
    ("688223.SH", "晶科能源"),
    ("002459.SZ", "晶澳科技"),
    ("688599.SH", "天合光能"),
]

# 横向对比 8 家光伏龙头(与光伏产业链研报同口径)
PEERS = [
    ("600438.SH", "通威股份"), ("601012.SH", "隆基绿能"), ("002129.SZ", "TCL中环"),
    ("002459.SZ", "晶澳科技"), ("688223.SH", "晶科能源"), ("688599.SH", "天合光能"),
    ("300861.SZ", "美畅股份"), ("300274.SZ", "阳光电源"),
]

OUT_DIR = Path(".sisyphus/evidence/pv3")


def daily_basic_3y(client: TushareClient, pro, ts_code: str) -> pd.DataFrame:
    """3 年 daily_basic,先走 client(缓存),行数<700 则分段直连 pro.daily_basic."""
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=1150)).strftime("%Y%m%d")
    db = client.get_daily_basic(ts_code, start, end)
    if db is not None and len(db) >= 700:
        db = db.sort_values("trade_date").reset_index(drop=True)
        return db
    # fallback: ≤500 天/段 直连
    logger.warning("{} client.get_daily_basic 仅 {} 行,分段直连 pro.daily_basic", ts_code, 0 if db is None else len(db))
    segs = []
    d0 = date.today() - timedelta(days=1150)
    while d0 < date.today():
        d1 = min(d0 + timedelta(days=490), date.today())
        seg = pro.daily_basic(
            ts_code=ts_code, start_date=d0.strftime("%Y%m%d"),
            end_date=d1.strftime("%Y%m%d"),
            fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv",
        )
        if seg is not None and len(seg):
            segs.append(seg)
        d0 = d1 + timedelta(days=1)
    if not segs:
        return pd.DataFrame()
    db = pd.concat(segs, ignore_index=True).drop_duplicates("trade_date")
    db = db.sort_values("trade_date").reset_index(drop=True)
    logger.info("{} 直连拼接后 {} 行, 末日={}", ts_code, len(db), db["trade_date"].iloc[-1])
    return db


def pct_of(series: pd.Series, cur: float) -> float | None:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if not len(s) or pd.isna(cur):
        return None
    return round((s < cur).sum() / len(s) * 100, 1)


def score_one(client: TushareClient, pro, ts_code: str, name: str) -> dict:
    res = {"ts_code": ts_code, "name": name}

    # ── 1. 财务 12 期 ──
    fin = fetch_financial_data(client, ts_code, periods=12)
    assert fin and fin[0].ts_code == ts_code, f"代码核对失败: {fin[0].ts_code if fin else 'empty'}"
    res["financial"] = [
        {
            "period": f.report_period, "revenue_yi": round((f.revenue or 0) / 1e8, 2),
            "np_yi": round(float(f.net_profit or 0) / 1e8, 2), "eps": f.eps, "roe": f.roe,
            "ocf_yi": round((f.operating_cf or 0) / 1e8, 2),
            "debt_ratio": round(f.total_debt / f.total_assets, 4) if f.total_assets else None,
            "total_assets_yi": round((f.total_assets or 0) / 1e8, 1),
            "yoy_rev": round(f.yoy_revenue_growth * 100, 1) if f.yoy_revenue_growth is not None else None,
            "yoy_np": round(f.yoy_profit_growth * 100, 1) if f.yoy_profit_growth is not None else None,
            "gm": round(f.grossprofit_margin, 2) if f.grossprofit_margin is not None else None,
            "rd_yi": round((f.rd_exp or 0) / 1e8, 2) if getattr(f, "rd_exp", None) else None,
        }
        for f in fin
    ]
    latest = fin[0]

    # ── 2. 估值 3 年分位 ──
    db = daily_basic_3y(client, pro, ts_code)
    if len(db):
        db_sorted = db.sort_values("trade_date").reset_index(drop=True)
        last = db_sorted.iloc[-1]
        pe_s = pd.to_numeric(db_sorted["pe_ttm"], errors="coerce")
        pb_s = pd.to_numeric(db_sorted["pb"], errors="coerce")
        ps_s = pd.to_numeric(db_sorted["ps"], errors="coerce")
        mv_s = pd.to_numeric(db_sorted["total_mv"], errors="coerce")
        res["valuation_snap"] = {
            "trade_date": last["trade_date"],
            "pe_ttm": None if pd.isna(pe_s.iloc[-1]) else round(float(pe_s.iloc[-1]), 2),
            "pb": round(float(pb_s.iloc[-1]), 2), "ps": round(float(ps_s.iloc[-1]), 2),
            "total_mv_yi": round(float(mv_s.iloc[-1]) / 1e4, 1),
            "n_days": len(db_sorted),
            "pe_pct": pct_of(pe_s, pe_s.iloc[-1]), "pb_pct": pct_of(pb_s, pb_s.iloc[-1]),
            "ps_pct": pct_of(ps_s, ps_s.iloc[-1]),
            "pb_quantiles": {str(q): round(float(pb_s.quantile(q / 100)), 2) for q in [10, 25, 50, 75, 90]},
            "ps_quantiles": {str(q): round(float(ps_s.quantile(q / 100)), 2) for q in [10, 25, 50, 75, 90]},
        }
        pe_pct01 = (res["valuation_snap"]["pe_pct"] or 50) / 100
        pb_pct01 = (res["valuation_snap"]["pb_pct"] or 50) / 100
        # PB 分位≤20% 视为周期股底部锚,估值分:100-分位均值
        val_score = 100 - ((pe_pct01 + pb_pct01) / 2) * 100
    else:
        res["valuation_snap"] = None
        pe_pct01, pb_pct01, val_score = 0.5, 0.5, 50.0

    # ── 3. 景气度 ──
    pscore = calculate_prosperity_score(fin)
    res["prosperity"] = {
        "composite": round(pscore.composite_score, 2), "delta_g": round(pscore.delta_g, 2),
        "revenue": round(pscore.revenue_score, 2), "profit": round(pscore.profit_score, 2),
        "slope": round(pscore.slope_score, 2), "duration": round(pscore.duration_score, 2),
    }

    # ── 4. 趋势(PB 月度,周期股) ──
    trend_score = 50.0
    trend_info: dict = {"score": 50.0, "note": "数据不足"}
    if len(db) >= 60:
        db_s = db.sort_values("trade_date").reset_index(drop=True)
        dates = pd.to_datetime(db_s["trade_date"], format="%Y%m%d")
        daily_pb = pd.Series(pd.to_numeric(db_s["pb"], errors="coerce").values, index=dates).dropna()
        daily_pe = pd.Series(pd.to_numeric(db_s["pe_ttm"], errors="coerce").values, index=dates).dropna()
        monthly_pb = daily_pb.resample("ME").mean().dropna()
        if len(monthly_pb) >= 3:
            slope = (monthly_pb.iloc[-1] - monthly_pb.iloc[0]) / max(len(monthly_pb) - 1, 1)
            last3 = monthly_pb.iloc[-3:].pct_change().dropna()
            trend_score = round(max(0.0, min(100.0, 50 + slope * 200 + (last3.iloc[-1] * 100 if len(last3) else 0))), 2)
            trend_info = {"score": trend_score, "monthly_points": len(monthly_pb),
                          "pb_slope_per_month": round(float(slope), 4),
                          "pb_first": round(float(monthly_pb.iloc[0]), 2),
                          "pb_last": round(float(monthly_pb.iloc[-1]), 2)}
    res["trend"] = trend_info

    # ── 5. 困境反转 ──
    distress = calculate_distress_score(
        eps_history=[f.eps for f in fin], pe_pct=pe_pct01, pb_pct=pb_pct01,
        debt_ratio=(latest.total_debt or 0) / (latest.total_assets or 1),
        operating_cf=latest.operating_cf or 0.0, total_debt=latest.total_debt or 0.0,
        total_assets=latest.total_assets or 0.0, roe_history=[f.roe for f in fin],
        revenue_history=[f.yoy_revenue_growth or 0.0 for f in fin],
        profit_history=[f.yoy_profit_growth or 0.0 for f in fin],
        delta_g=pscore.delta_g, ts_code=ts_code,
    )
    res["distress"] = {"total": round(distress.total_score, 2), "L1": round(distress.layer1_score, 2),
                       "L2": round(distress.layer2_score, 2), "L3": round(distress.layer3_score, 2)}

    # ── 6. 戴维斯双击综合 ──
    davis = calculate_davis_double_score(
        valuation_score=val_score, prosperity_score=pscore.composite_score,
        distress_score=distress.total_score, trend_score=trend_score,
        ts_code=ts_code, name=name)
    res["davis"] = {"final": round(davis.final_score, 2), "rank": davis.rank,
                    "val": round(val_score, 2), "trend": trend_score,
                    "prosperity": round(pscore.composite_score, 2),
                    "distress": round(distress.total_score, 2)}

    # ── 7. 5 补充因子 ──
    factors: dict = {}
    try:
        mom = analyze_momentum(client, ts_code)
        if mom:
            factors["momentum"] = {"score": mom.momentum_score,
                                   "abs_score": getattr(mom, "absolute_momentum_score", None),
                                   "rs_pct": getattr(mom, "rs_percentile", None),
                                   "window_returns": {k: (round(v * 100, 1) if v is not None else None) for k, v in (mom.window_returns or {}).items()}}
    except Exception as e:
        factors["momentum"] = {"error": str(e)}
    try:
        div = analyze_dividend(client, ts_code)
        factors["dividend"] = {"score": div.dividend_score, "years": div.consecutive_years,
                               "yield_pct": div.latest_yield_pct, "payout_years": div.payout_years}
    except Exception as e:
        factors["dividend"] = {"error": str(e)}
    try:
        fc = analyze_forecast(client, ts_code, pscore)  # 第三参必须传 ProsperityScore 对象
        if fc:
            factors["forecast"] = {"leading": fc.leading_score, "type": fc.type,
                                   "p_change_mid": fc.p_change_mid, "stale": fc.is_stale}
        else:
            factors["forecast"] = None
    except Exception as e:
        factors["forecast"] = {"error": str(e)}
    try:
        hc = analyze_holder_concentration(client, ts_code)
        if hc:
            factors["holder"] = {"score": hc.score if hasattr(hc, "score") else hc.holder_score,
                                 "trend": getattr(hc, "trend", None) or getattr(hc, "trend_label", None)}
        else:
            factors["holder"] = None
    except Exception as e:
        factors["holder"] = {"error": str(e)}
    try:
        pq = analyze_profitability_quality(fin)
        factors["profitability"] = {"score": pq.quality_score, "gm_trend": getattr(pq, "gross_margin_trend", None),
                                    "rd_intensity": getattr(pq, "rd_intensity", None)}
    except Exception as e:
        factors["profitability"] = {"error": str(e)}
    res["factors"] = factors

    # ── 8. 股东户数 ──
    try:
        h = pro.stk_holdernumber(ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num")
        h = h.sort_values("end_date").tail(10)
        rows, prev = [], None
        for _, r in h.iterrows():
            num = int(r["holder_num"])
            chg = round((num - prev) / prev * 100, 1) if prev else None
            rows.append({"end_date": r["end_date"], "ann_date": r["ann_date"], "num": num, "chg_pct": chg})
            prev = num
        res["holders_num"] = rows
    except Exception as e:
        res["holders_num"] = {"error": str(e)}

    # ── 9. 十大流通股东合计(交叉验证) ──
    try:
        t10 = pro.top10_floatholders(ts_code=ts_code, fields="ts_code,ann_date,end_date,hold_ratio")
        t10 = t10.sort_values("end_date")
        res["top10_float"] = {str(d): round(float(g["hold_ratio"].sum()), 2) for d, g in t10.groupby("end_date")}
    except Exception as e:
        res["top10_float"] = {"error": str(e)}

    # ── 10. 业绩预告原始(万元单位!) ──
    try:
        fcdf = pro.forecast(ts_code=ts_code, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
        fcdf = fcdf.sort_values("ann_date").tail(4)
        res["forecast_raw"] = [
            {"ann": r["ann_date"], "end": r["end_date"], "type": r["type"],
             "chg": [r["p_change_min"], r["p_change_max"]],
             "np_yi": ([round(r["net_profit_min"] / 1e4, 2), round(r["net_profit_max"] / 1e4, 2)]
                       if pd.notna(r["net_profit_min"]) and str(r["net_profit_min"]) != "nan" else None)}
            for _, r in fcdf.iterrows()]
    except Exception as e:
        res["forecast_raw"] = {"error": str(e)}

    # ── 11. 相对估值锚定 ──
    try:
        rv = analyze_relative_valuation(pro, ts_code, name, lookback_years=3)
        res["relative_valuation"] = {
            "stock_pe": rv.stock_pe, "index_pe": rv.index_pe, "pe_ratio": rv.pe_ratio,
            "pe_ratio_pct": rv.pe_ratio_pct, "erp": rv.erp, "rf": rv.risk_free_rate,
            "stock_pe_pct": rv.stock_pe_pct, "index_pe_pct": rv.index_pe_pct,
            "quadrant": rv.quadrant, "quadrant_label": rv.quadrant_label,
            "verdict": rv.composite_verdict, "signals": rv.signals}
    except Exception as e:
        res["relative_valuation"] = {"error": str(e)}

    # ── 12. 时效性 ──
    try:
        inc = pro.income(ts_code=ts_code, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
        res["freshness"] = {"latest_period": inc.iloc[0]["end_date"], "ann": inc.iloc[0]["ann_date"]}
    except Exception as e:
        res["freshness"] = {"error": str(e)}

    return res


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = TushareClient()
    pro = get_pro_api(timeout=60)
    summary = []
    for ts_code, name in TARGETS:
        logger.info("=" * 60)
        logger.info("{} {} 评分中...", ts_code, name)
        try:
            res = score_one(client, pro, ts_code, name)
            with open(OUT_DIR / f"{ts_code.split('.')[0]}.json", "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2, default=str)
            summary.append({
                "code": ts_code, "name": name,
                "mv": res["valuation_snap"]["total_mv_yi"] if res.get("valuation_snap") else None,
                "pb": res["valuation_snap"]["pb"] if res.get("valuation_snap") else None,
                "pb_pct": res["valuation_snap"]["pb_pct"] if res.get("valuation_snap") else None,
                "ps": res["valuation_snap"]["ps"] if res.get("valuation_snap") else None,
                "ps_pct": res["valuation_snap"]["ps_pct"] if res.get("valuation_snap") else None,
                "days": res["valuation_snap"]["n_days"] if res.get("valuation_snap") else 0,
                "composite": res["prosperity"]["composite"], "delta_g": res["prosperity"]["delta_g"],
                "distress": res["distress"]["total"], "final": res["davis"]["final"],
                "period": res["financial"][0]["period"],
            })
        except Exception:
            logger.exception("{} 评分失败", ts_code)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # ── 同业快照(最新交易日 daily_basic + 近 3 年 PB 分位) ──
    snap = []
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=1150)).strftime("%Y%m%d")
    for ts_code, name in PEERS:
        try:
            d = pro.daily_basic(ts_code=ts_code, start_date=start, end_date=end,
                                fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv")
            if d is None or not len(d):
                continue
            d = d.sort_values("trade_date").reset_index(drop=True)
            pbv = pd.to_numeric(d["pb"], errors="coerce")
            psv = pd.to_numeric(d["ps"], errors="coerce")
            pev = pd.to_numeric(d["pe_ttm"], errors="coerce")
            last = d.iloc[-1]
            snap.append({"code": ts_code, "name": name, "date": last["trade_date"],
                         "close_mv_yi": round(float(last["total_mv"]) / 1e4, 0),
                         "pe": None if pd.isna(pev.iloc[-1]) else round(float(pev.iloc[-1]), 1),
                         "pb": round(float(pbv.iloc[-1]), 2),
                         "pb_pct": pct_of(pbv, pbv.iloc[-1]),
                         "ps": round(float(psv.iloc[-1]), 2),
                         "ps_pct": pct_of(psv, psv.iloc[-1]), "days": len(d)})
        except Exception:
            logger.exception("同业 {} 快照失败", ts_code)
    with open(OUT_DIR / "peers_snapshot.json", "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print(json.dumps(snap, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
