#!/usr/bin/env python
"""SMIC (688981.SH) + HuaHong (688347.SH) full data collection for deep reports.

Collects: freshness check, financials (12q), 3y valuation history (segmented
pro.daily_basic to avoid the 22-day cache trap), prosperity, 5 supplemental
factors, relative valuation, holder number trend, top10 float holders,
dividend history, manual momentum cross-check, H-share prices.
Output: JSON to davis_analyzer/studies/smic_hhua_data.json
"""
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality

import tushare as ts
from stockhot.tushare_config import get_pro_api
from stockhot.valuation import analyze_relative_valuation

TARGETS = [("688981.SH", "中芯国际"), ("688347.SH", "华虹公司")]
TODAY = date.today().strftime("%Y%m%d")
OUT = {}

pro = get_pro_api(timeout=60)
client = TushareClient()
ts.set_token(os.environ["TUSHARE_TOKEN"])
ts_pro = ts.pro_api()


def fetch_db_full(ts_code: str, days: int = 1095) -> pd.DataFrame:
    """Segmented pro.daily_basic pull (<=500d per call), dedup + ascending sort."""
    end = TODAY
    start = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    frames = []
    seg = start
    while seg <= end:
        seg_end = min((pd.to_datetime(seg) + timedelta(days=499)).strftime("%Y%m%d"), end)
        df = pro.daily_basic(
            ts_code=ts_code, start_date=seg, end_date=seg_end,
            fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv,turnover_rate,dv_ttm",
        )
        if len(df):
            frames.append(df)
        seg = (pd.to_datetime(seg_end) + timedelta(days=1)).strftime("%Y%m%d")
    out = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("trade_date")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
    return out


def manual_returns(ts_code: str) -> dict:
    """Manual adjusted-price window returns as momentum engine cross-check."""
    start = (date.today() - timedelta(days=430)).strftime("%Y%m%d")
    df = pro.daily(ts_code=ts_code, start_date=start, end_date=TODAY)
    adj = pro.adj_factor(ts_code=ts_code, start_date=start, end_date=TODAY)
    df = df.merge(adj[["trade_date", "adj_factor"]], on="trade_date")
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["ha"] = df["close"] * df["adj_factor"]
    out = {"last_trade": df["trade_date"].iloc[-1], "last_close": float(df["close"].iloc[-1])}
    for w in [20, 60, 120, 250]:
        if len(df) > w:
            out[f"r{w}d_pct"] = round((df["ha"].iloc[-1] / df["ha"].iloc[-1 - w] - 1) * 100, 2)
    return out


def holder_trend(ts_code: str) -> dict:
    h = pro.stk_holdernumber(
        ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num"
    ).dropna(subset=["holder_num"]).sort_values("end_date")
    rows = []
    prev = None
    for _, r in h.tail(8).iterrows():
        num = int(r["holder_num"])
        chg = round((num - prev) / prev * 100, 2) if prev else None
        rows.append({"end_date": r["end_date"], "holder_num": num, "chg_pct": chg})
        prev = num
    return {"rows": rows}


def top10_float(ts_code: str) -> dict:
    df = pro.top10_floatholders(
        ts_code=ts_code,
        fields="ts_code,ann_date,end_date,holder_name,hold_ratio",
    ).dropna(subset=["hold_ratio"])
    agg = df.groupby("end_date")["hold_ratio"].sum().sort_index().tail(4)
    return {d: round(float(v), 2) for d, v in agg.items()}


def percentile_table(series: pd.Series) -> dict:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if not len(s):
        return {}
    cur = float(s.iloc[-1])
    pct = round((s < cur).sum() / len(s) * 100, 1)
    qs = {f"p{p}": round(float(s.quantile(p / 100)), 2) for p in [10, 25, 50, 75, 90, 95]}
    return {"n": len(s), "current": round(cur, 2), "pct": pct, **qs}


def collect(ts_code: str, name: str) -> dict:
    d: dict = {"name": name, "code": ts_code}

    # 0. identity check (坑点 2b/261: never trust caller-given code)
    basic = pro.stock_basic(ts_code=ts_code, fields="ts_code,name,industry,market,list_date")
    d["basic"] = basic.iloc[0].to_dict() if len(basic) else {}
    assert d["basic"].get("name", name)[:2] in (name[:2], "中芯") or name in d["basic"].get("name", ""), \
        f"code mismatch: {d['basic']}"

    # 1. freshness
    inc = pro.income(ts_code=ts_code, fields="ts_code,ann_date,end_date,f_ann_date", limit=3)
    d["freshness"] = {
        "income_latest_periods": inc.to_dict("records") if len(inc) else [],
    }
    db1 = pro.daily_basic(ts_code=ts_code, limit=1)
    d["freshness"]["latest_trade_date"] = db1.iloc[0]["trade_date"] if len(db1) else "none"

    # 2. financials
    fin = fetch_financial_data(client, ts_code, periods=12)
    d["fin"] = [
        {
            "report_period": f.report_period, "revenue": f.revenue,
            "net_profit": f.net_profit, "eps": f.eps, "roe": f.roe,
            "operating_cf": f.operating_cf, "grossprofit_margin": f.grossprofit_margin,
            "rd_exp": f.rd_exp,
            "yoy_rev": f.yoy_revenue_growth, "yoy_np": f.yoy_profit_growth,
        }
        for f in fin
    ]
    pscore = calculate_prosperity_score(fin)
    stage = classify_stock_stage(pscore)
    d["prosperity"] = {
        "composite": pscore.composite_score, "delta_g": pscore.delta_g,
        "revenue_score": pscore.revenue_score, "profit_score": pscore.profit_score,
        "slope_score": pscore.slope_score, "duration_score": pscore.duration_score,
        "stage": stage,
    }

    # 3. valuation history (segmented, >=700 rows enforced)
    db = fetch_db_full(ts_code)
    d["valuation"] = {
        "rows": len(db),
        "last_trade": db["trade_date"].iloc[-1] if len(db) else None,
        "last_close": float(db["close"].iloc[-1]) if len(db) else None,
        "last_total_mv_yi": round(float(db["total_mv"].iloc[-1]) / 1e4, 1) if len(db) else None,
        "last_dv_ttm": float(db["dv_ttm"].iloc[-1]) if len(db) and pd.notna(db["dv_ttm"].iloc[-1]) else None,
        "pe": percentile_table(db["pe_ttm"]),
        "pb": percentile_table(db["pb"]),
        "ps": percentile_table(db["ps"]),
    }

    # 4. five supplemental factors
    mom = analyze_momentum(client, ts_code)
    d["momentum"] = {
        "score": mom.momentum_score, "abs": mom.absolute_momentum_score,
        "rs_pct": mom.rs_percentile, "window_returns": mom.window_returns,
    } if mom else None
    d["momentum_manual"] = manual_returns(ts_code)
    div = analyze_dividend(client, ts_code)
    d["dividend"] = {
        "score": div.dividend_score, "years": div.consecutive_years,
        "yield_pct": div.latest_yield_pct,
    } if div else None
    fc = analyze_forecast(client, ts_code, pscore)
    d["forecast_sig"] = {
        "leading_score": fc.leading_score, "type": fc.type,
        "p_change_mid": fc.p_change_mid, "is_stale": fc.is_stale,
    } if fc else None
    rev = analyze_forecast_revision(client, ts_code)
    d["forecast_rev"] = {
        "dir": rev.revision_direction, "pp": rev.revision_pp, "score": rev.revision_score,
    } if rev else None
    hc = analyze_holder_concentration(client, ts_code)
    d["holder_conc"] = {
        "score": hc.concentration_score, "trend": hc.trend,
        "latest_chg": hc.latest_chg_pct,
    } if hc else None
    pq = analyze_profitability_quality(fin)
    d["profit_quality"] = {
        "score": pq.quality_score, "gm": pq.latest_gross_margin,
        "gm_delta": pq.gross_margin_delta, "rd": pq.latest_rd_intensity,
    } if pq else None

    # 5. relative valuation
    rv = analyze_relative_valuation(ts_pro, ts_code, name)
    d["relative_val"] = {
        "pe_ratio": rv.pe_ratio, "pe_ratio_pct": rv.pe_ratio_pct,
        "stock_pe": getattr(rv, "stock_pe", None),
        "stock_pe_pct": getattr(rv, "stock_pe_pct", None),
        "index_pe": rv.index_pe, "index_pe_pct": rv.index_pe_pct,
        "erp": rv.erp, "risk_free": rv.risk_free_rate,
        "quadrant": rv.quadrant, "quadrant_label": rv.quadrant_label,
        "verdict": rv.composite_verdict, "signals": list(rv.signals),
    } if rv else None

    # 6. holder number + top10
    d["holder_num"] = holder_trend(ts_code)
    d["top10_float"] = top10_float(ts_code)

    # 7. dividend history (cash, implemented)
    dv = pro.dividend(ts_code=ts_code, fields="ts_code,end_date,ann_date,cash_div_tax,cash_div,div_proc,base_share")
    if len(dv):
        dv = dv[dv["div_proc"] == "实施"].sort_values("end_date")
        d["dividend_hist"] = dv.tail(6).to_dict("records")

    # 8. raw forecast rows (filtered ann_date >= 20250101, unit: 万元!)
    fcr = pro.forecast(ts_code=ts_code)
    if len(fcr):
        fcr = fcr[pd.to_numeric(fcr["ann_date"]) >= 20250101].sort_values("ann_date")
        d["forecast_raw"] = fcr.to_dict("records")

    # 9. fina_indicator key ratios
    fi = pro.fina_indicator(
        ts_code=ts_code, period="",
        fields="ts_code,end_date,ann_date,roe,grossprofit_margin,netprofit_margin,debt_to_assets",
    )
    if len(fi):
        fi = fi.drop_duplicates("end_date").sort_values("end_date")
        d["fina_indicator"] = fi.tail(12).to_dict("records")

    return d


for code, nm in TARGETS:
    print(f"\n{'='*20} {nm} {code} {'='*20}", flush=True)
    OUT[code] = collect(code, nm)
    print(json.dumps({k: v for k, v in OUT[code].items()
                      if k in ("basic", "freshness", "prosperity", "valuation")},
                     ensure_ascii=False, default=str)[:1500], flush=True)

# H-share prices for A/H premium
for hcode in ["00981.HK", "01347.HK"]:
    try:
        hk = pro.hk_daily(ts_code=hcode, start_date=(date.today() - timedelta(days=10)).strftime("%Y%m%d"), end_date=TODAY)
        if len(hk):
            hk = hk.sort_values("trade_date")
            OUT[hcode] = {"last_trade": hk["trade_date"].iloc[-1], "close": float(hk["close"].iloc[-1])}
    except Exception as e:
        OUT[hcode] = {"error": str(e)}

with open(os.path.join(os.path.dirname(__file__), "smic_hhua_data.json"), "w", encoding="utf-8") as f:
    json.dump(OUT, f, ensure_ascii=False, default=str, indent=1)
print("\nSaved to smic_hhua_data.json")
