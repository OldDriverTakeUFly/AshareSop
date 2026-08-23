# 荣昌生物688331 + 艾力斯688578 全套引擎取数（深度研报 Phase 2）
# 产出: /tmp/{tag}_engine.json —— 四维评分/5因子/股东户数/相对估值/时效校验
import os
import sys

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
os.chdir("/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

import json
import warnings
from datetime import date, datetime, timedelta

import pandas as pd

warnings.filterwarnings("ignore")

from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.forecast import analyze_forecast
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import batch_trend
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo, ValuationData
from davis_analyzer.valuation import calculate_valuation_score, detect_cyclical
from stockhot.tushare_config import get_pro_api
from stockhot.valuation import analyze_relative_valuation

pro = get_pro_api(timeout=60)
client = TushareClient()

TARGETS = {
    "600276.SH": "hengrui",
    "688506.SH": "baili",
    "688266.SH": "zejing",
    "688192.SH": "dizhe",
}


def daily_basic_3y(ts_code):
    end = date.today()
    frames = []
    while True:
        seg_start = end - timedelta(days=490)
        for _ in range(3):
            df = pro.daily_basic(
                ts_code=ts_code, start_date=seg_start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,turnover_rate,dv_ttm",
            )
            if len(df):
                frames.append(df)
                break
        end = seg_start - timedelta(days=1)
        if end < date.today() - timedelta(days=1120):
            break
    return (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("trade_date")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )


def collect(ts_code, tag):
    out = {"ts_code": ts_code, "collected_at": datetime.now().isoformat()}

    # ── 基本信息 ──
    sl = pro.stock_basic(ts_code=ts_code, fields="ts_code,name,industry,area,list_date")
    out["basic"] = sl.iloc[0].to_dict() if len(sl) else {}
    industry = out["basic"].get("industry", "")

    # ── 财务 12 期 ──
    fin = fetch_financial_data(client, ts_code, periods=12)
    out["fin_table"] = [
        {
            "period": f.report_period,
            "rev_yi": round(f.revenue / 1e8, 2) if f.revenue else None,
            "np_yi": round(float(f.net_profit) / 1e8, 2) if f.net_profit is not None else None,
            "rev_yoy": round(f.yoy_revenue_growth * 100, 1) if f.yoy_revenue_growth is not None else None,
            "np_yoy": round(f.yoy_profit_growth * 100, 1) if f.yoy_profit_growth is not None else None,
            "roe": f.roe,
            "ocf_yi": round(f.operating_cf / 1e8, 2) if f.operating_cf else None,
            "gross_margin": getattr(f, "grossprofit_margin", None),
            "rd_exp_yi": round(f.rd_exp / 1e8, 2) if getattr(f, "rd_exp", None) else None,
        }
        for f in fin
    ]
    latest = fin[0]
    debt_ratio = (latest.total_debt or 0) / (latest.total_assets or 1)

    # ── 景气度 ──
    pscore = calculate_prosperity_score(fin)
    out["prosperity"] = {
        "composite": pscore.composite_score, "delta_g": pscore.delta_g,
        "revenue": pscore.revenue_score, "profit": pscore.profit_score,
        "slope": pscore.slope_score, "duration": pscore.duration_score,
        "stage": classify_stock_stage(pscore),
    }

    # ── 估值: 3年直连 + ValuationData 手工构造(过滤NaN pe/pb, 日期降序) ──
    db = daily_basic_3y(ts_code)
    db_num = db.copy()
    for c in ["pe_ttm", "pb", "ps", "total_mv"]:
        db_num[c] = pd.to_numeric(db_num[c], errors="coerce")
    pe_s, pb_s, ps_s = db_num["pe_ttm"], db_num["pb"], db_num["ps"]
    out["valuation_snapshot"] = {
        "trade_date": db["trade_date"].iloc[-1], "days": len(db),
        "mv_yi": round(db_num["total_mv"].iloc[-1] / 1e4, 1),
        "pe": pe_s.iloc[-1] if pd.notna(pe_s.iloc[-1]) else None,
        "pe_valid_days": int(pe_s.notna().sum()),
        "pe_pct": round((pe_s.dropna() < pe_s.iloc[-1]).sum() / pe_s.notna().sum() * 100, 1) if pe_s.notna().sum() >= 100 else None,
        "pb": pb_s.iloc[-1], "pb_pct": round((pb_s.dropna() < pb_s.iloc[-1]).sum() / len(pb_s) * 100, 1),
        "ps": ps_s.iloc[-1], "ps_pct": round((ps_s.dropna() < ps_s.iloc[-1]).sum() / len(ps_s) * 100, 1),
        "dv_ttm": pd.to_numeric(db_num.get("dv_ttm", pd.Series([0])), errors="coerce").iloc[-1],
        "pb_percentiles": {p: round(pb_s.quantile(p / 100), 2) for p in [10, 25, 50, 75, 90]},
        "pe_percentiles": {p: round(pe_s.quantile(p / 100), 2) for p in [10, 25, 50, 75, 90]},
        "ps_percentiles": {p: round(ps_s.quantile(p / 100), 2) for p in [10, 25, 50, 75, 90]},
        "pb_min": round(pb_s.min(), 2), "pb_max": round(pb_s.max(), 2),
        "ps_min": round(ps_s.min(), 2), "ps_max": round(ps_s.max(), 2),
    }
    val_list = []
    for _, r in db.iloc[::-1].iterrows():  # 降序
        pe, pb_ = pd.to_numeric(r["pe_ttm"], errors="coerce"), pd.to_numeric(r["pb"], errors="coerce")
        if pd.isna(pe) or pd.isna(pb_):
            continue
        val_list.append(ValuationData(ts_code=ts_code, trade_date=str(r["trade_date"]), pe_ttm=float(pe), pb=float(pb_), ps=float(ps_s.loc[r.name]) if pd.notna(ps_s.loc[r.name]) else 0.0, total_mv=float(db_num["total_mv"].loc[r.name]) if pd.notna(db_num["total_mv"].loc[r.name]) else 0.0))
    is_cyc = detect_cyclical(industry)
    val_score, pe_pct, pb_pct = calculate_valuation_score(val_list, is_cyc)
    out["valuation_score"] = {"score": val_score, "pe_pct": pe_pct, "pb_pct": pb_pct, "is_cyclical": is_cyc, "industry": industry, "val_points": len(val_list)}

    # ── 趋势 ──
    try:
        dt_idx = pd.to_datetime(db["trade_date"], format="%Y%m%d")
        info = StockInfo(ts_code=ts_code, name=out["basic"].get("name", ""), industry=industry, list_status="L", is_cyclical=is_cyc)
        trend_map = batch_trend({ts_code: (pe_s.dropna(), pb_s)}, {ts_code: info})
        out["trend_score"] = trend_map.get(ts_code)
    except Exception as e:
        out["trend_score"] = None
        out["trend_err"] = str(e)[:80]

    # ── 困境反转(荣昌重点) ──
    try:
        d = calculate_distress_score(
            eps_history=[f.eps for f in fin], pe_pct=pe_pct, pb_pct=pb_pct,
            debt_ratio=debt_ratio, operating_cf=latest.operating_cf or 0,
            total_debt=latest.total_debt or 0, total_assets=latest.total_assets or 0,
            roe_history=[f.roe for f in fin],
            revenue_history=[f.yoy_revenue_growth or 0.0 for f in fin],
            profit_history=[f.yoy_profit_growth or 0.0 for f in fin],
            delta_g=pscore.delta_g, ts_code=ts_code,
        )
        out["distress"] = {"total": d.total_score, "l1": d.layer1_score, "l2": d.layer2_score, "l3": d.layer3_score, "signals": d.signals_detail}
    except Exception as e:
        out["distress_err"] = str(e)[:120]

    # ── 戴维斯综合 ──
    davis = calculate_davis_double_score(
        valuation_score=val_score, prosperity_score=pscore.composite_score,
        distress_score=(out.get("distress") or {}).get("total", 50.0),
        trend_score=out.get("trend_score") or 50.0, ts_code=ts_code, name=out["basic"].get("name", ""),
    )
    out["davis"] = {"final": davis.final_score, "val": davis.valuation_score, "trend": davis.trend_score, "prosperity": davis.prosperity_score, "distress": davis.distress_score}

    # ── 5 因子 ──
    fac = {}
    try:
        m = analyze_momentum(client, ts_code)
        fac["momentum"] = {"score": m.momentum_score, "abs": m.absolute_momentum_score, "rs_pct": m.rs_percentile, "windows": m.window_returns} if m else None
    except Exception as e:
        fac["momentum_err"] = str(e)[:80]
    try:
        d_ = pro.daily(ts_code=ts_code, start_date=(date.today() - timedelta(days=430)).strftime("%Y%m%d"), end_date=date.today().strftime("%Y%m%d")).sort_values("trade_date").reset_index(drop=True)
        c = d_["close"]
        fac["manual_returns"] = {
            "60d": round((c.iloc[-1] / c.iloc[-61] - 1) * 100, 1), "120d": round((c.iloc[-1] / c.iloc[-121] - 1) * 100, 1),
            "250d": round((c.iloc[-1] / c.iloc[-251] - 1) * 100, 1),
            "high_250d": c.iloc[-250:].max(), "low_250d": c.iloc[-250:].min(),
            "drawdown_from_high_pct": round((c.iloc[-1] / c.iloc[-250:].max() - 1) * 100, 1),
        }
    except Exception as e:
        fac["manual_returns_err"] = str(e)[:80]
    try:
        dv = analyze_dividend(client, ts_code)
        fac["dividend"] = {"score": dv.dividend_score, "years": dv.consecutive_years, "yield_pct": dv.latest_yield_pct}
    except Exception as e:
        fac["dividend_err"] = str(e)[:80]
    try:
        fc = analyze_forecast(client, ts_code, pscore)
        fac["forecast"] = {"score": fc.leading_score, "type": fc.type, "p_change_mid": fc.p_change_mid, "stale": fc.is_stale} if fc else None
    except Exception as e:
        fac["forecast_err"] = str(e)[:80]
    try:
        hc = analyze_holder_concentration(client, ts_code)
        fac["holder_conc"] = {"score": hc.concentration_score, "trend": hc.trend, "latest_chg_pct": hc.latest_chg_pct} if hc else None
    except Exception as e:
        fac["holder_conc_err"] = str(e)[:80]
    try:
        pq = analyze_profitability_quality(fin)
        fac["profit_quality"] = {"score": pq.quality_score, "gm": pq.latest_gross_margin, "gm_delta": pq.gross_margin_delta, "rd": pq.latest_rd_intensity}
    except Exception as e:
        fac["profit_quality_err"] = str(e)[:80]
    out["factors"] = fac

    # ── 股东户数明细 ──
    try:
        h = pro.stk_holdernumber(ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num").dropna(subset=["holder_num"]).sort_values("end_date")
        rows = []
        prev = None
        for _, r in h.tail(8).iterrows():
            num = int(r["holder_num"])
            rows.append({"end_date": r["end_date"], "ann_date": r["ann_date"], "num": num,
                         "chg_pct": round((num - prev) / prev * 100, 1) if prev else None})
            prev = num
        out["holder_numbers"] = rows
    except Exception as e:
        out["holder_numbers_err"] = str(e)[:80]

    # ── 十大流通股东(近两期合计) ──
    try:
        top10 = {}
        for period in ["20260630", "20260331", "20251231"]:
            t = pro.top10_floatholders(ts_code=ts_code, period=period)
            if len(t):
                top10[period] = round(float(pd.to_numeric(t["hold_ratio"], errors="coerce").sum()), 2)
        out["top10_float_ratio"] = top10
    except Exception as e:
        out["top10_err"] = str(e)[:80]

    # ── 相对估值 ──
    try:
        rv = analyze_relative_valuation(pro, ts_code, out["basic"].get("name", ""))
        out["relative_valuation"] = {
            "pe_ratio_pct": rv.pe_ratio_pct, "erp": rv.erp, "quadrant": getattr(rv, "quadrant", None),
            "index_pe": rv.index_pe, "index_pe_pct": rv.index_pe_pct,
            "risk_free_rate": rv.risk_free_rate, "signals": getattr(rv, "signals", None),
        }
    except Exception as e:
        out["relative_valuation_err"] = str(e)[:120]

    # ── 时效性 ──
    inc = pro.income(ts_code=ts_code, fields="ts_code,ann_date,end_date", limit=3)
    out["freshness"] = {
        "latest_trade": db["trade_date"].iloc[-1],
        "latest_income_period": inc.iloc[0]["end_date"] if len(inc) else None,
        "latest_income_ann": inc.iloc[0]["ann_date"] if len(inc) else None,
    }
    fcs = pro.forecast(ts_code=ts_code, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min")
    fcs = fcs[pd.to_numeric(fcs["ann_date"], errors="coerce") >= 20250101].sort_values("end_date")
    out["forecasts_2025_26"] = fcs.tail(3).to_dict("records")

    path = f"/tmp/{tag}_engine.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, default=str)
    print("saved", path)


for code_, tag_ in TARGETS.items():
    print("=== collecting", code_, "===")
    collect(code_, tag_)
