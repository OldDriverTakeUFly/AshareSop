#!/usr/bin/env python3
"""华润微/香农芯创/深科技 三标的研报取数脚本（四维+5因子+股东户数+相对估值+时效校验）.

用法: cd /home/leo/Projects/CodeAgentDashboard && PYTHONPATH=. .venv/bin/python davis_analyzer/studies/guochan3_scoring.py
"""
from __future__ import annotations

import os
import sys
import json
from datetime import date, timedelta

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd
from stockhot.tushare_config import get_pro_api
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.valuation import calculate_valuation_score, detect_cyclical
from davis_analyzer.types import StockInfo, ValuationData
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import batch_trend
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality
from stockhot.valuation import analyze_relative_valuation

TARGETS = [
    ("688396.SH", "华润微"),
    ("300475.SZ", "香农芯创"),
    ("000021.SZ", "深科技"),
]

pro = get_pro_api(timeout=60)
client = TushareClient()


def fetch_daily_basic_full(ts_code: str, days: int = 1095) -> pd.DataFrame:
    """分段直连 pro.daily_basic 取全量 3 年（≤480 天/段），防增量截断。"""
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    all_start = date.fromisoformat(f"{start[:4]}-{start[4:6]}-{start[6:]}")
    frames = []
    seg_end = date.fromisoformat(f"{end[:4]}-{end[4:6]}-{end[6:]}")
    while True:
        seg_start = seg_end - timedelta(days=480)
        if seg_start < all_start:
            seg_start = all_start
        s, e = seg_start.strftime("%Y%m%d"), seg_end.strftime("%Y%m%d")
        df = pro.daily_basic(ts_code=ts_code, start_date=s, end_date=e,
                             fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv")
        frames.append(df)
        if seg_start <= all_start:
            break
        seg_end = seg_start - timedelta(days=1)
    out = pd.concat(frames, ignore_index=True).drop_duplicates("trade_date")
    out = out.sort_values("trade_date").reset_index(drop=True)
    assert out["trade_date"].iloc[-1] >= "20260820", f"daily_basic 不新鲜: {out['trade_date'].iloc[-1]}"
    return out


def pct_rank(s: pd.Series, cur: float) -> float:
    return (s < cur).sum() / len(s) * 100


def analyze(ts_code: str, name: str) -> dict:
    print(f"\n{'='*70}\n{name} ({ts_code})\n{'='*70}")
    res: dict = {"ts_code": ts_code, "name": name}

    # ── 0. 校验代码与公司名 ──
    basic = pro.stock_basic(ts_code=ts_code, fields="ts_code,name,industry,actual_controller")
    print("stock_basic:", basic.iloc[0].to_dict())
    res["industry"] = basic.iloc[0]["industry"]

    # ── 1. 时效校验 ──
    db1 = pro.daily_basic(ts_code=ts_code, limit=1)
    inc1 = pro.income(ts_code=ts_code, fields="ts_code,ann_date,end_date", limit=1)
    res["freshness"] = {
        "latest_trade_date": db1.iloc[0]["trade_date"],
        "latest_report_period": inc1.iloc[0]["end_date"],
        "latest_ann_date": inc1.iloc[0]["ann_date"],
    }
    print("freshness:", res["freshness"])

    # ── 2. 财务 12 期 ──
    fin = fetch_financial_data(client, ts_code, periods=12)
    print(f"财务 {len(fin)} 期, 最新 {fin[0].report_period}")
    rows = []
    for f in fin:
        rows.append({
            "period": f.report_period,
            "rev_yi": round((f.revenue or 0) / 1e8, 2),
            "np_yi": round((f.net_profit or 0) / 1e8, 2) if isinstance(f.net_profit, (int, float)) else f.net_profit,
            "eps": f.eps, "roe": f.roe,
            "yoy_rev": round(f.yoy_revenue_growth * 100, 2) if f.yoy_revenue_growth is not None else None,
            "yoy_np": round(f.yoy_profit_growth * 100, 2) if f.yoy_profit_growth is not None else None,
            "gross_margin": getattr(f, "grossprofit_margin", None),
            "rd_exp_yi": round((getattr(f, "rd_exp", 0) or 0) / 1e8, 2),
        })
    res["fin"] = rows
    for r in rows[:6]:
        print(r)

    # ── 3. 估值 3 年（直连全量）──
    db = fetch_daily_basic_full(ts_code)
    pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
    pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
    ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
    mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
    res["valuation"] = {
        "n_days": len(db), "pe_n": len(pe),
        "pe_cur": round(pe.iloc[-1], 2), "pe_pct": round(pct_rank(pe, pe.iloc[-1]), 1),
        "pb_cur": round(pb.iloc[-1], 2), "pb_pct": round(pct_rank(pb, pb.iloc[-1]), 1),
        "ps_cur": round(ps.iloc[-1], 2), "ps_pct": round(pct_rank(ps, ps.iloc[-1]), 1),
        "mv_yi": round(mv.iloc[-1] / 1e4, 1),
        "last_trade": db["trade_date"].iloc[-1],
        "quantiles": {
            "pe": {str(p): round(pe.quantile(p / 100), 2) for p in [10, 25, 50, 75, 90, 95]},
            "pb": {str(p): round(pb.quantile(p / 100), 2) for p in [10, 25, 50, 75, 90, 95]},
            "ps": {str(p): round(ps.quantile(p / 100), 2) for p in [10, 25, 50, 75, 90, 95]},
        },
    }
    print("valuation:", json.dumps(res["valuation"], ensure_ascii=False))

    # YTD
    d = pro.daily(ts_code=ts_code, start_date="20251230", end_date=date.today().strftime("%Y%m%d"),
                  fields="trade_date,close,pre_close")
    d = d.sort_values("trade_date")
    base = d.iloc[0]
    # pre_close of first trading day of 2026 ≈ 2025 year-end close
    res["ytd_pct"] = round((d["close"].iloc[-1] / base["pre_close"] - 1) * 100, 1)
    res["close"] = d["close"].iloc[-1]
    print(f"close={res['close']} YTD={res['ytd_pct']}%")

    # ── 4. 景气度 ──
    pscore = calculate_prosperity_score(fin)
    stage = classify_stock_stage(pscore)
    res["prosperity"] = {
        "composite": round(pscore.composite_score, 2), "delta_g": round(pscore.delta_g, 2),
        "revenue": round(pscore.revenue_score, 2), "profit": round(pscore.profit_score, 2),
        "slope": round(pscore.slope_score, 2), "duration": round(pscore.duration_score, 2),
        "stage": str(stage),
    }
    print("prosperity:", res["prosperity"])

    # ── 5. 四维：估值分/趋势/困境/戴维斯 ──
    val_list = []
    for _, r in db.iterrows():
        try:
            if pd.notna(r["pe_ttm"]) and pd.notna(r["pb"]):
                val_list.append(ValuationData(ts_code=ts_code, trade_date=str(r["trade_date"]),
                                              pe_ttm=float(r["pe_ttm"]), pb=float(r["pb"]),
                                              ps=float(r["ps"]) if pd.notna(r["ps"]) else None,
                                              total_mv=float(r["total_mv"]) if pd.notna(r["total_mv"]) else None))
        except Exception:
            continue
    val_list.sort(key=lambda v: v.trade_date, reverse=True)
    industry = res["industry"]
    is_cyc = detect_cyclical(industry)
    sinfo = StockInfo(ts_code=ts_code, name=name, industry=industry, list_status="L", is_cyclical=is_cyc)
    val_score, pe_pct_u, pb_pct_u = calculate_valuation_score(val_list, is_cyc)
    dates = pd.to_datetime(db["trade_date"], format="%Y%m%d")
    daily_pe = pd.Series(pd.to_numeric(db["pe_ttm"], errors="coerce").values, index=dates).dropna()
    daily_pb = pd.Series(pd.to_numeric(db["pb"], errors="coerce").values, index=dates).dropna()
    trend_score = batch_trend({ts_code: (daily_pe, daily_pb)}, {ts_code: sinfo}).get(ts_code, 50.0)

    latest = fin[0]
    debt_ratio = (latest.total_debt or 0) / (latest.total_assets or 1)
    dscore = calculate_distress_score(
        eps_history=[f.eps for f in fin], pe_pct=pe_pct_u, pb_pct=pb_pct_u,
        debt_ratio=debt_ratio, operating_cf=latest.operating_cf or 0,
        total_debt=latest.total_debt or 0, total_assets=latest.total_assets or 0,
        roe_history=[f.roe for f in fin],
        revenue_history=[f.yoy_revenue_growth or 0 for f in fin],
        profit_history=[f.yoy_profit_growth or 0 for f in fin],
        delta_g=pscore.delta_g, ts_code=ts_code)
    davis = calculate_davis_double_score(valuation_score=val_score,
                                         prosperity_score=pscore.composite_score,
                                         distress_score=dscore.total_score,
                                         trend_score=trend_score, ts_code=ts_code, name=name)
    res["four_dim"] = {
        "is_cyclical": is_cyc, "val_score": round(val_score, 2),
        "pe_pct_engine": round(pe_pct_u * 100, 1), "pb_pct_engine": round(pb_pct_u * 100, 1),
        "trend": round(trend_score, 2),
        "distress": {"total": round(dscore.total_score, 2), "l1": round(dscore.layer1_score, 2),
                     "l2": round(dscore.layer2_score, 2), "l3": round(dscore.layer3_score, 2)},
        "davis_final": round(davis.final_score, 2),
    }
    print("four_dim:", res["four_dim"])

    # ── 6. 5 因子 ──
    fac = {}
    try:
        mom = analyze_momentum(client, ts_code)
        if mom:
            fac["momentum"] = {"score": mom.momentum_score, "abs": mom.absolute_momentum_score,
                               "rs_pct": mom.rs_percentile,
                               "windows": {k: (round(v * 100, 1) if v is not None and abs(v) < 10 else v)
                                           for k, v in (mom.window_returns or {}).items()}}
    except Exception as e:
        fac["momentum"] = f"ERR {e}"
    div = analyze_dividend(client, ts_code)
    fac["dividend"] = {"score": div.dividend_score, "years": div.consecutive_years,
                       "yield": div.latest_yield_pct}
    try:
        f_ = analyze_forecast(client, ts_code, pscore)
        rev = analyze_forecast_revision(client, ts_code)
        fac["forecast"] = {"leading": f_.leading_score if f_ else None,
                           "type": f_.type if f_ else None,
                           "p_change_mid": f_.p_change_mid if f_ else None,
                           "stale": f_.is_stale if f_ else None}
        fac["forecast_revision"] = {"dir": rev.revision_direction if rev else None,
                                    "pp": rev.revision_pp if rev else None} if rev else None
    except Exception as e:
        fac["forecast"] = f"ERR {e}"
    try:
        hc = analyze_holder_concentration(client, ts_code)
        if hc:
            fac["holder"] = {"score": hc.concentration_score, "trend": hc.trend,
                             "latest_chg": hc.latest_chg_pct}
    except Exception as e:
        fac["holder"] = f"ERR {e}"
    try:
        pq = analyze_profitability_quality(fin)
        fac["profitability"] = {"score": pq.quality_score, "gm": pq.latest_gross_margin,
                                "gm_delta": pq.gross_margin_delta, "rd": pq.latest_rd_intensity}
    except Exception as e:
        fac["profitability"] = f"ERR {e}"
    res["factors"] = fac
    print("factors:", json.dumps(fac, ensure_ascii=False, default=str))

    # ── 7. 股东户数 ──
    try:
        h = pro.stk_holdernumber(ts_code=ts_code,
                                 fields="ts_code,ann_date,end_date,holder_num").dropna(subset=["holder_num"])
        h = h.sort_values("end_date").tail(8)
        res["holders"] = [{"end_date": r["end_date"], "num": int(r["holder_num"])}
                          for _, r in h.iterrows()]
        print("holders:", res["holders"])
    except Exception as e:
        res["holders"] = f"ERR {e}"

    # ── 8. 业绩预告（原始，含金额）──
    try:
        fc = pro.forecast(ts_code=ts_code,
                          fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
        if len(fc):
            fc = fc[pd.to_numeric(fc["ann_date"]) >= 20250101].sort_values("ann_date")
            res["forecast_raw"] = fc.tail(3).to_dict("records")
            print("forecast_raw:", res["forecast_raw"])
    except Exception as e:
        res["forecast_raw"] = f"ERR {e}"

    # ── 9. 相对估值 ──
    try:
        rv = analyze_relative_valuation(pro, ts_code, name)
        res["relative_valuation"] = {
            "pe_ratio": rv.pe_ratio, "pe_ratio_pct": rv.pe_ratio_pct,
            "erp": rv.erp, "quadrant": rv.quadrant, "quadrant_label": rv.quadrant_label,
            "verdict": rv.composite_verdict, "index_pe": rv.index_pe,
            "index_pe_pct": rv.index_pe_pct, "rf": rv.risk_free_rate,
            "signals": rv.signals,
        }
        print("relative_valuation:", json.dumps(res["relative_valuation"], ensure_ascii=False, default=str))
    except Exception as e:
        res["relative_valuation"] = f"ERR {e}"
        print("relative_valuation ERR:", e)

    return res


def main():
    out = {}
    for code, name in TARGETS:
        try:
            out[code] = analyze(code, name)
        except Exception as e:
            import traceback
            traceback.print_exc()
            out[code] = {"error": str(e)}
    path = "/tmp/guochan3_data.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n\nSAVED -> {path}")


if __name__ == "__main__":
    main()
