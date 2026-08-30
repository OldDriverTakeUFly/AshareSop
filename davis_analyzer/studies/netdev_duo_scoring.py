#!/usr/bin/env python3
"""紫光股份(000938.SZ) + 锐捷网络(301165.SZ) 联合取数脚本.

四维评分（估值/趋势/景气/困境→Davis）+ 5 补充因子 + 股东户数 + 时效校验 + 相对估值.
"""
from __future__ import annotations

import os
import sys
import json
from datetime import date, timedelta

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")

import pandas as pd
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.valuation import (
    fetch_valuation_history, calculate_percentile, calculate_valuation_score,
    detect_cyclical,
)
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import batch_trend
from davis_analyzer.types import StockInfo
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality
from stockhot.tushare_config import get_pro_api

TARGETS = [("000938.SZ", "紫光股份"), ("301165.SZ", "锐捷网络")]


def seg_daily_basic(pro, ts_code: str, days: int = 1095) -> pd.DataFrame:
    """分段拉取 daily_basic，规避长区间截断 + 缓存未命中坑."""
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    frames = []
    cur_start = start
    while cur_start < end:
        cur_end = (pd.to_datetime(cur_start) + timedelta(days=490)).strftime("%Y%m%d")
        if cur_end > end:
            cur_end = end
        df = pro.daily_basic(ts_code=ts_code, start_date=cur_start, end_date=cur_end,
                             fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,close,turnover_rate,dv_ttm")
        frames.append(df)
        cur_start = (pd.to_datetime(cur_end) + timedelta(days=1)).strftime("%Y%m%d")
    out = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True)
    return out.sort_values("trade_date").reset_index(drop=True)


def holder_trend(pro, ts_code: str):
    h = pro.stk_holdernumber(ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num")
    h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(8)
    rows, prev = [], None
    for _, r in h.iterrows():
        num = int(r["holder_num"])
        chg = (num - prev) / prev * 100 if prev else None
        rows.append({"end_date": r["end_date"], "holder_num": num, "chg_pct": chg})
        prev = num
    return rows


def freshness(pro, ts_code: str):
    db = pro.daily_basic(ts_code=ts_code, limit=1)
    latest_trade = db.iloc[0]["trade_date"] if len(db) else "none"
    inc = pro.income(ts_code=ts_code, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
    fc = pro.forecast(ts_code=ts_code, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
    fcs = ""
    if len(fc):
        r = fc.iloc[0]
        fcs = f"{r['type']} ann={r['ann_date']} end={r['end_date']} [{r['p_change_min']},{r['p_change_max']}]%"
    return {"latest_trade": latest_trade,
            "latest_period": inc.iloc[0]["end_date"] if len(inc) else "none",
            "latest_ann": inc.iloc[0]["ann_date"] if len(inc) else "none",
            "forecast": fcs}


def analyze(client, pro, ts_code: str, name: str) -> dict:
    print(f"\n{'='*70}\n{name} ({ts_code})\n{'='*70}")
    res: dict = {"ts_code": ts_code, "name": name}

    # 时效
    res["freshness"] = freshness(pro, ts_code)
    print("freshness:", res["freshness"])

    # 财务
    fin = fetch_financial_data(client, ts_code, periods=12)
    print(f"财务 {len(fin)} 期, 最新 {fin[0].report_period}, ts={fin[0].ts_code}")
    rows = []
    for fd in fin:
        rows.append({
            "period": fd.report_period, "rev_yi": round((fd.revenue or 0)/1e8, 2),
            "np_yi": round((fd.net_profit or 0)/1e8, 2), "eps": fd.eps, "roe": fd.roe,
            "yoy_rev": None if fd.yoy_revenue_growth is None else round(fd.yoy_revenue_growth*100, 2),
            "yoy_np": None if fd.yoy_profit_growth is None else round(fd.yoy_profit_growth*100, 2),
            "gm": fd.grossprofit_margin, "rd": fd.rd_exp,
            "ocf_yi": round((fd.operating_cf or 0)/1e8, 2),
            "debt_ratio": round((fd.total_debt or 0)/(fd.total_assets or 1)*100, 1),
        })
    res["financials"] = rows
    for r in rows:
        print(r)

    # 估值（分段直连 + 分位）
    db = seg_daily_basic(pro, ts_code)
    print(f"daily_basic {len(db)} 行, 最新 {db['trade_date'].iloc[-1]}")
    pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna().reset_index(drop=True)
    pb = pd.to_numeric(db["pb"], errors="coerce").dropna().reset_index(drop=True)
    ps = pd.to_numeric(db["ps"], errors="coerce").dropna().reset_index(drop=True)
    res["valuation_snapshot"] = {
        "trade_date": db["trade_date"].iloc[-1],
        "pe_ttm": round(pe.iloc[-1], 2), "pb": round(pb.iloc[-1], 2), "ps": round(ps.iloc[-1], 2),
        "mv_yi": round(float(pd.to_numeric(db['total_mv']).iloc[-1])/1e4, 1),
        "close": float(db["close"].iloc[-1]),
        "dv_ttm": db["dv_ttm"].iloc[-1],
        "pe_pct": round((pe < pe.iloc[-1]).sum()/len(pe)*100, 1),
        "pb_pct": round((pb < pb.iloc[-1]).sum()/len(pb)*100, 1),
        "ps_pct": round((ps < ps.iloc[-1]).sum()/len(ps)*100, 1),
        "n_days": len(db),
        "pe_quantiles": {str(p): round(pe.quantile(p/100), 2) for p in [10, 25, 50, 75, 90, 95]},
        "pb_quantiles": {str(p): round(pb.quantile(p/100), 2) for p in [10, 25, 50, 75, 90, 95]},
        "ps_quantiles": {str(p): round(ps.quantile(p/100), 2) for p in [10, 25, 50, 75, 90, 95]},
    }
    print("valuation:", res["valuation_snapshot"])

    # YTD 涨幅（前复权用 close 近似，派息少误差可忽略）
    daily = pro.daily(ts_code=ts_code, start_date=(date.today() - timedelta(days=280)).strftime("%Y%m%d"),
                      end_date=date.today().strftime("%Y%m%d"), fields="trade_date,close,pre_close,pct_chg")
    daily = daily.sort_values("trade_date").reset_index(drop=True)
    y0 = daily[daily["trade_date"] >= date(date.today().year, 1, 1).strftime("%Y%m%d")]
    base = daily[daily["trade_date"] < y0.iloc[0]["trade_date"]]
    if len(base):
        ytd = (y0.iloc[-1]["close"] / base.iloc[-1]["close"] - 1) * 100
    else:
        ytd = None
    r20 = (daily["close"].iloc[-1]/daily["close"].iloc[-21]-1)*100 if len(daily) > 21 else None
    r60 = (daily["close"].iloc[-1]/daily["close"].iloc[-61]-1)*100 if len(daily) > 61 else None
    r120 = (daily["close"].iloc[-1]/daily["close"].iloc[-121]-1)*100 if len(daily) > 121 else None
    r250 = (daily["close"].iloc[-1]/daily["close"].iloc[-min(251, len(daily)-1)]-1)*100 if len(daily) > 30 else None
    res["returns"] = {"ytd": round(ytd, 1) if ytd else None, "r20d": round(r20, 1) if r20 else None,
                      "r60d": round(r60, 1) if r60 else None, "r120d": round(r120, 1) if r120 else None,
                      "r250d": round(r250, 1) if r250 else None}
    print("returns:", res["returns"])

    # 引擎四维
    industry = ""
    slist = client.get_stock_list()
    row = slist[slist["ts_code"] == ts_code]
    if not row.empty:
        industry = str(row.iloc[0].get("industry", "") or "")
    res["industry"] = industry
    info = StockInfo(ts_code=ts_code, name=name, industry=industry, list_status="L",
                     is_cyclical=detect_cyclical(industry))

    val_hist = fetch_valuation_history(client, ts_code)
    print(f"val_history {len(val_hist)} 点")
    pe_pct = (pe < pe.iloc[-1]).sum()/len(pe)
    pb_pct = (pb < pb.iloc[-1]).sum()/len(pb)
    if val_hist and len(val_hist) >= 300:
        v_score, pe_pct, pb_pct = calculate_valuation_score(val_hist, info.is_cyclical)
        val_source = f"fetch_valuation_history({len(val_hist)}d)"
    else:
        v_score = 50.0
        val_source = "fallback manual percentiles"
    res["engine_valuation"] = {"score": round(v_score, 2), "pe_pct": round(pe_pct, 4),
                               "pb_pct": round(pb_pct, 4), "source": val_source}

    pscore = calculate_prosperity_score(fin)
    stage = classify_stock_stage(pscore)
    res["prosperity"] = {"composite": round(pscore.composite_score, 2),
                         "revenue": round(pscore.revenue_score, 2), "profit": round(pscore.profit_score, 2),
                         "slope": round(pscore.slope_score, 2), "duration": round(pscore.duration_score, 2),
                         "delta_g": round(pscore.delta_g, 2), "stage": stage}
    print("prosperity:", res["prosperity"])

    # 趋势
    dates = pd.to_datetime(db["trade_date"], format="%Y%m%d")
    daily_pe = pd.Series(pd.to_numeric(db["pe_ttm"], errors="coerce").values, index=dates).dropna()
    daily_pb = pd.Series(pd.to_numeric(db["pb"], errors="coerce").values, index=dates).dropna()
    try:
        tmap = batch_trend({ts_code: (daily_pe, daily_pb)}, {ts_code: info})
        t_score = tmap.get(ts_code, 50.0)
    except Exception as e:
        print("trend fail:", e)
        t_score = 50.0
    res["trend_score"] = round(t_score, 2)

    # 困境
    latest = fin[0]
    eps_h = [f.eps for f in fin]
    roe_h = [f.roe for f in fin]
    rev_g = [f.yoy_revenue_growth or 0.0 for f in fin]
    np_g = [f.yoy_profit_growth or 0.0 for f in fin]
    ds = calculate_distress_score(
        eps_history=eps_h, pe_pct=pe_pct, pb_pct=pb_pct,
        debt_ratio=(latest.total_debt or 0)/(latest.total_assets or 1),
        operating_cf=latest.operating_cf or 0.0, total_debt=latest.total_debt or 0.0,
        total_assets=latest.total_assets or 0.0, roe_history=roe_h,
        revenue_history=rev_g, profit_history=np_g, delta_g=pscore.delta_g, ts_code=ts_code)
    res["distress"] = {"total": round(ds.total_score, 2), "l1": round(ds.layer1_score, 2),
                       "l2": round(ds.layer2_score, 2), "l3": round(ds.layer3_score, 2)}
    print("distress:", res["distress"])

    davis = calculate_davis_double_score(valuation_score=v_score, prosperity_score=pscore.composite_score,
                                         distress_score=ds.total_score, trend_score=t_score,
                                         ts_code=ts_code, name=name)
    res["davis"] = {"final": round(davis.final_score, 2)}
    print("davis final:", res["davis"])

    # 5 补充因子
    try:
        mom = analyze_momentum(client, ts_code)
        res["momentum"] = {"score": mom.momentum_score,
                           "abs": mom.absolute_momentum_score, "rs_pct": mom.rs_percentile,
                           "windows": {k: (round(v*100, 1) if v is not None and abs(v) < 50 else str(v))
                                       for k, v in (mom.window_returns or {}).items()}}
        print("momentum(engine):", res["momentum"])
    except Exception as e:
        print("momentum fail:", e)
    res["momentum_manual"] = res["returns"]

    div = analyze_dividend(client, ts_code)
    res["dividend"] = {"score": div.dividend_score, "years": div.consecutive_years,
                       "yield": div.latest_yield_pct}
    print("dividend:", res["dividend"])

    fc = analyze_forecast(client, ts_code, pscore)
    res["forecast_engine"] = None if fc is None else {
        "leading": fc.leading_score, "p_mid": fc.p_change_mid, "type": fc.type, "stale": fc.is_stale}
    rev = analyze_forecast_revision(client, ts_code)
    res["forecast_revision"] = None if rev is None else {
        "dir": rev.revision_direction, "pp": rev.revision_pp, "score": rev.revision_score}
    print("forecast:", res["forecast_engine"], res["forecast_revision"])

    hc = analyze_holder_concentration(client, ts_code)
    res["holder_conc"] = None if hc is None else {
        "score": hc.concentration_score, "trend": hc.trend, "latest_chg": hc.latest_chg_pct,
        "counts": hc.holder_counts}
    print("holder_conc:", res["holder_conc"])

    pq = analyze_profitability_quality(fin)
    res["profit_quality"] = {"score": pq.quality_score, "gm": pq.latest_gross_margin,
                             "gm_delta": pq.gross_margin_delta, "rd": pq.latest_rd_intensity}
    print("profit_quality:", res["profit_quality"])

    # 股东户数原始
    res["holder_number_rows"] = holder_trend(pro, ts_code)
    print("holder numbers:", res["holder_number_rows"])

    # 相对估值
    try:
        from stockhot.valuation import analyze_relative_valuation
        rv = analyze_relative_valuation(pro, ts_code, name)
        if rv:
            res["relative_valuation"] = {
                "pe_ratio_pct": rv.pe_ratio_pct, "stock_pe_pct": rv.stock_pe_pct,
                "index_pe_pct": rv.index_pe_pct, "index_pe": rv.index_pe,
                "erp_pct": round((rv.erp or 0)*100, 2) if rv.erp is not None else None,
                "risk_free": round((rv.risk_free_rate or 0)*100, 2) if rv.risk_free_rate is not None else None,
                "quadrant": rv.quadrant, "signals": rv.signals}
            print("relative_valuation:", res["relative_valuation"])
    except Exception as e:
        print("relative_valuation fail:", e)

    return res


def main() -> None:
    client = TushareClient()
    pro = get_pro_api(timeout=60)
    out = {}
    for ts_code, name in TARGETS:
        out[ts_code] = analyze(client, pro, ts_code, name)
    path = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/netdev_duo_result.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\nSaved:", path)


if __name__ == "__main__":
    main()
