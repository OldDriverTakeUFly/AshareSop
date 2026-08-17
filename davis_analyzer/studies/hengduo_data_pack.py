#!/usr/bin/env python3
"""恒力石化(600346.SH)+恒逸石化(000703.SZ) 双标的研报数据包采集脚本.

化纤板块最大分化对:恒逸 250日+181% vs 恒力 120日-32%.
采集:时效校验/财务明细/3年估值(分段直连)/动量复核/业绩预告/股东户数/
十大流通股东/分红/质押/增减持/回购/5因子引擎/四维评分/相对估值/同业全景.
输出: .sisyphus/evidence/hengli_hengyi/data_pack.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")  # editable 安装失效兜底

import pandas as pd
from dotenv import load_dotenv
from loguru import logger

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

from stockhot.tushare_config import get_pro_api  # noqa: E402
from davis_analyzer.tushare_client import TushareClient  # noqa: E402
from davis_analyzer.financial_fetcher import fetch_financial_data  # noqa: E402
from davis_analyzer.prosperity import calculate_prosperity_score  # noqa: E402
from davis_analyzer.prosperity_sector import classify_stock_stage  # noqa: E402
from davis_analyzer.distress import calculate_distress_score  # noqa: E402
from davis_analyzer.scoring import calculate_davis_double_score  # noqa: E402
from davis_analyzer.valuation import detect_cyclical  # noqa: E402
from davis_analyzer.momentum import analyze_momentum  # noqa: E402
from davis_analyzer.dividend import analyze_dividend  # noqa: E402
from davis_analyzer.forecast import analyze_forecast  # noqa: E402
from davis_analyzer.holder_concentration import analyze_holder_concentration  # noqa: E402
from davis_analyzer.profitability import analyze_profitability_quality  # noqa: E402
from davis_analyzer.types import StockInfo  # noqa: E402

OUT = Path(".sisyphus/evidence/hengli_hengyi/data_pack.json")
TODAY = date(2026, 8, 15)
TARGETS = {
    "600346.SH": "恒力石化",
    "000703.SZ": "恒逸石化",
}
PEERS = {
    "601233.SH": "桐昆股份",
    "603225.SH": "新凤鸣",
    "002493.SZ": "荣盛石化",
    "000301.SZ": "东方盛虹",
    "002064.SZ": "华峰化学",
}
pro = get_pro_api(timeout=60)
client = TushareClient()
result: dict = {"targets": {}, "peers": {}, "benchmarks": {}}


def fetch_3y_daily_basic(ts_code: str) -> pd.DataFrame:
    """分段直连 daily_basic 取 3 年估值序列(坑点: 新端点长区间截断 + concat reset_index)."""
    end_d = TODAY
    start_d = end_d - timedelta(days=1120)
    frames, cur = [], start_d
    while cur < end_d:
        seg_end = min(cur + timedelta(days=480), end_d)
        for attempt in range(3):
            seg = pro.daily_basic(
                ts_code=ts_code,
                start_date=cur.strftime("%Y%m%d"),
                end_date=seg_end.strftime("%Y%m%d"),
                fields="ts_code,trade_date,close,pe_ttm,pb,ps_ttm,total_mv,turnover_rate",
            )
            if len(seg):
                break
            time.sleep(1.2)
        if len(seg):
            frames.append(seg)
        cur = seg_end + timedelta(days=1)
        time.sleep(0.35)
    db = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("trade_date")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
    assert len(db) > 600, f"{ts_code} daily_basic rows={len(db)} <600 (22天坑)"
    return db


def fetch_daily_adj(ts_code: str, days: int = 460) -> pd.DataFrame:
    """取日线+复权因子, 计算复权收益(动量复核)."""
    start = (TODAY - timedelta(days=days)).strftime("%Y%m%d")
    d = pro.daily(ts_code=ts_code, start_date=start, end_date=TODAY.strftime("%Y%m%d"))
    for _ in range(3):
        if len(d):
            break
        time.sleep(1.5)
        d = pro.daily(ts_code=ts_code, start_date=start, end_date=TODAY.strftime("%Y%m%d"))
    af = pro.adj_factor(ts_code=ts_code, start_date=start, end_date=TODAY.strftime("%Y%m%d"))
    d = d.sort_values("trade_date").reset_index(drop=True)
    af = af.sort_values("trade_date").reset_index(drop=True)
    m = d.merge(af[["trade_date", "adj_factor"]], on="trade_date", how="left")
    m["adj_close"] = m["close"] * pd.to_numeric(m["adj_factor"], errors="coerce")
    return m


def window_returns(adj: pd.Series) -> dict:
    out = {}
    n = len(adj)
    for w in (20, 60, 120, 250):
        if n > w:
            out[f"d{w}"] = round(float(adj.iloc[-1] / adj.iloc[-1 - w] - 1) * 100, 1)
        else:
            out[f"d{w}"] = None
    return out


for ts_code, name in TARGETS.items():
    logger.info("=" * 60)
    logger.info("采集 {} {}", ts_code, name)
    pack: dict = {}

    # ── 1. 基本信息 + 时效校验 ──
    info = pro.stock_basic(ts_code=ts_code, fields="ts_code,name,industry,area,list_date,actual_controller")
    pack["basic"] = info.to_dict("records")
    industry = str(info.iloc[0]["industry"] or "")
    db1 = pro.daily_basic(ts_code=ts_code, limit=1)
    inc_latest = pro.income(ts_code=ts_code, fields="ts_code,ann_date,end_date", limit=1)
    pack["freshness"] = {
        "daily_basic_latest": db1.iloc[0]["trade_date"],
        "close": float(db1.iloc[0]["close"]),
        "pe_ttm": None if pd.isna(db1.iloc[0]["pe_ttm"]) else float(db1.iloc[0]["pe_ttm"]),
        "pb": float(db1.iloc[0]["pb"]),
        "total_mv_yi": float(db1.iloc[0]["total_mv"]) / 1e4,
        "income_latest_period": inc_latest.iloc[0]["end_date"],
        "income_latest_ann": inc_latest.iloc[0]["ann_date"],
    }

    # ── 2. 财务明细(fin_list + fina_indicator + 资债表现金流) ──
    fin = fetch_financial_data(client, ts_code, periods=12)
    pack["fin_list"] = [
        {
            "report_period": f_.report_period,
            "revenue_yi": round(float(f_.revenue or 0) / 1e8, 1),
            "net_profit_yi": round(float(f_.net_profit or 0) / 1e8, 2),
            "eps": f_.eps,
            "roe": f_.roe,
            "operating_cf_yi": round(float(f_.operating_cf or 0) / 1e8, 1),
            "debt_ratio": round(float(f_.total_debt or 0) / float(f_.total_assets or 1) * 100, 1),
            "total_assets_yi": round(float(f_.total_assets or 0) / 1e8, 0),
            "yoy_rev": None if f_.yoy_revenue_growth is None else round(f_.yoy_revenue_growth * 100, 1),
            "yoy_profit": None if f_.yoy_profit_growth is None else round(f_.yoy_profit_growth * 100, 1),
        }
        for f_ in fin
    ]
    fi = pro.fina_indicator(
        ts_code=ts_code,
        fields="ts_code,end_date,grossprofit_margin,netprofit_margin,roe,debt_to_assets,ocf_to_or",
        limit=12,
    )
    pack["fina_indicator"] = fi.to_dict("records")
    cf = pro.cashflow(
        ts_code=ts_code,
        fields="ts_code,end_date,n_cashflow_act,n_cashflow_inv_act,n_cashflow_fnc_act,free_cashflow",
        limit=10,
    )
    pack["cashflow"] = cf.to_dict("records")

    # ── 3. 3 年估值序列 ──
    db = fetch_3y_daily_basic(ts_code)
    pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
    ps = pd.to_numeric(db["ps_ttm"], errors="coerce").dropna()
    pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
    mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
    val_summary = {}
    for tag, s in [("pb", pb), ("ps", ps), ("pe", pe)]:
        cur_v = float(s.iloc[-1])
        val_summary[tag] = {
            "latest": round(cur_v, 2),
            "latest_date": str(db["trade_date"].iloc[-1]),
            "pct": round(float((s < cur_v).sum() / len(s) * 100), 1),
            **{f"q{p}": round(float(s.quantile(p / 100)), 2) for p in (10, 25, 50, 75, 90, 95)},
            "n": int(len(s)),
        }
    val_summary["total_mv_yi"] = round(float(mv.iloc[-1]) / 1e4, 0)
    pack["valuation_3y"] = val_summary

    # 月度收盘+换手(分化时间线)
    db["month"] = db["trade_date"].str[:6]
    monthly = (
        db.groupby("month")
        .agg(close=("close", "last"), pb=("pb", "last"), to_mean=("turnover_rate", "mean"))
        .reset_index()
    )
    pack["monthly_series"] = monthly.to_dict("records")

    # ── 4. 动量复核(复权) ──
    dpx = fetch_daily_adj(ts_code)
    adj = dpx["adj_close"].reset_index(drop=True)
    pack["momentum_manual"] = window_returns(adj)
    pack["px_latest"] = {"date": str(dpx["trade_date"].iloc[-1]), "close": float(dpx["close"].iloc[-1])}
    pack["px_250d_high_low"] = {
        "high": float(dpx["high"].tail(250).max()),
        "low": float(dpx["low"].tail(250).min()),
        "high_date": str(dpx.loc[dpx["high"].tail(250).idxmax(), "trade_date"]),
    }

    # ── 5. 业绩预告(单位万元!) ──
    fc_raw = pro.forecast(
        ts_code=ts_code,
        fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max",
    )
    fc_rows = []
    for _, r in fc_raw.iterrows():
        fc_rows.append(
            {
                "ann_date": r["ann_date"],
                "end_date": r["end_date"],
                "type": r["type"],
                "p_change_min": None if pd.isna(r["p_change_min"]) else float(r["p_change_min"]),
                "p_change_max": None if pd.isna(r["p_change_max"]) else float(r["p_change_max"]),
                "np_min_yi": None if pd.isna(r["net_profit_min"]) else round(float(r["net_profit_min"]) / 1e4, 1),
                "np_max_yi": None if pd.isna(r["net_profit_max"]) else round(float(r["net_profit_max"]) / 1e4, 1),
            }
        )
    pack["forecast_raw"] = fc_rows

    # ── 6. 股东户数 + 十大流通股东 ──
    h = pro.stk_holdernumber(ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num")
    h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(10)
    pack["holder_number"] = h.to_dict("records")
    t10 = pro.top10_floatholders(ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_name,hold_ratio")
    if len(t10):
        ends = sorted(t10["end_date"].unique())[-5:]
        pack["top10_float_history"] = [
            {
                "end_date": e,
                "total_ratio": round(
                    float(pd.to_numeric(t10[t10["end_date"] == e]["hold_ratio"], errors="coerce").sum()), 2
                ),
            }
            for e in ends
        ]
        latest_end = ends[-1]
        pack["top10_float_latest"] = t10[t10["end_date"] == latest_end][["holder_name", "hold_ratio"]].to_dict(
            "records"
        )

    # ── 7. 分红历史(恒力股息三重校验用) ──
    div = pro.dividend(
        ts_code=ts_code, fields="ts_code,end_date,ann_date,div_proc,cash_div_taxable,cash_div,base_share"
    )
    pack["dividend_raw"] = div.to_dict("records") if len(div) else []

    # ── 8. 质押/增减持/回购 ──
    try:
        ps_ = pro.pledge_stat(ts_code=ts_code, fields="ts_code,end_date,total_share,pledged_share,pledge_ratio")
        pack["pledge_stat"] = ps_.to_dict("records") if len(ps_) else []
    except Exception as e:
        pack["pledge_stat"] = f"unavailable: {e}"
    try:
        ht = pro.stk_holdertrade(
            ts_code=ts_code, start_date="20250101", end_date="20260815",
            fields="ts_code,ann_date,holder_name,holder_type,in_de,change_ratio,after_share",
        )
        pack["holdertrade_2025_2026"] = ht.to_dict("records") if len(ht) else []
    except Exception as e:
        pack["holdertrade_2025_2026"] = f"unavailable: {e}"
    try:
        rp = pro.repurchase(ts_code=ts_code)
        pack["repurchase"] = rp.to_dict("records")[:20] if len(rp) else []
    except Exception as e:
        pack["repurchase"] = f"unavailable: {e}"

    # ── 9. 四维评分(估值/景气/困境/趋势→davis) ──
    is_cyc = detect_cyclical(industry)
    pack["is_cyclical"] = {"industry": industry, "is_cyclical": is_cyc}
    pscore = calculate_prosperity_score(fin)
    stage = classify_stock_stage(pscore)
    pack["prosperity"] = {
        "composite_score": round(pscore.composite_score, 2),
        "delta_g": round(pscore.delta_g, 2),
        "revenue_score": round(pscore.revenue_score, 2),
        "profit_score": round(pscore.profit_score, 2),
        "slope_score": round(pscore.slope_score, 2),
        "duration_score": round(pscore.duration_score, 2),
        "stage": stage,
    }
    latest = fin[0]
    pe_pct = val_summary["pe"]["pct"] / 100 if val_summary["pe"]["n"] > 100 else 0.5
    pb_pct = val_summary["pb"]["pct"] / 100
    debt_ratio = float(latest.total_debt or 0) / float(latest.total_assets or 1)
    dscore = calculate_distress_score(
        eps_history=[f_.eps for f_ in fin],
        pe_pct=pe_pct,
        pb_pct=pb_pct,
        debt_ratio=debt_ratio,
        operating_cf=latest.operating_cf or 0.0,
        total_debt=latest.total_debt or 0.0,
        total_assets=latest.total_assets or 0.0,
        roe_history=[f_.roe for f_ in fin],
        revenue_history=[f_.yoy_revenue_growth or 0.0 for f_ in fin],
        profit_history=[f_.yoy_profit_growth or 0.0 for f_ in fin],
        delta_g=pscore.delta_g,
        ts_code=ts_code,
    )
    pack["distress"] = {
        "total_score": round(dscore.total_score, 2),
        "layer1": round(dscore.layer1_score, 2),
        "layer2": round(dscore.layer2_score, 2),
        "layer3": round(dscore.layer3_score, 2),
    }
    # 估值分: PB-only(周期股)手工合成: 分位越低分越高
    pb_only_score = max(0.0, min(100.0, (1 - pb_pct) * 100))
    pack["valuation_score_pb_only"] = round(pb_only_score, 2)
    davis = calculate_davis_double_score(
        valuation_score=pb_only_score,
        prosperity_score=pscore.composite_score,
        distress_score=dscore.total_score,
        trend_score=50.0,  # 中性, 趋势因子单列
        ts_code=ts_code,
        name=name,
    )
    pack["davis_double"] = {
        "final_score": round(davis.final_score, 2),
        "valuation_score": round(davis.valuation_score, 2),
        "prosperity_score": round(davis.prosperity_score, 2),
        "distress_score": round(davis.distress_score, 2),
    }

    # ── 10. 5 补充因子引擎 ──
    try:
        mom = analyze_momentum(client, ts_code)
        pack["factor_momentum"] = (
            {
                "momentum_score": round(mom.momentum_score, 2),
                "window_returns": mom.window_returns,
                "rs_percentile": mom.rs_percentile,
            }
            if mom
            else None
        )
    except Exception as e:
        pack["factor_momentum"] = f"error: {e}"
    try:
        dv = analyze_dividend(client, ts_code)
        pack["factor_dividend"] = {
            "dividend_score": dv.dividend_score,
            "consecutive_years": dv.consecutive_years,
            "latest_yield_pct": dv.latest_yield_pct,
            "payout_years": dv.payout_years,
        }
    except Exception as e:
        pack["factor_dividend"] = f"error: {e}"
    try:
        fcs = analyze_forecast(client, ts_code, pscore)
        pack["factor_forecast"] = (
            {
                "leading_score": round(fcs.leading_score, 2),
                "p_change_mid": fcs.p_change_mid,
                "type": fcs.type,
                "is_stale": fcs.is_stale,
            }
            if fcs
            else None
        )
    except Exception as e:
        pack["factor_forecast"] = f"error: {e}"
    try:
        hc = analyze_holder_concentration(client, ts_code)
        pack["factor_holder"] = (
            {
                "concentration_score": round(hc.concentration_score, 2),
                "trend": hc.trend,
                "latest_chg_pct": hc.latest_chg_pct,
            }
            if hc
            else None
        )
    except Exception as e:
        pack["factor_holder"] = f"error: {e}"
    try:
        pq = analyze_profitability_quality(fin)
        pack["factor_profitability"] = {
            "quality_score": round(pq.quality_score, 2),
            "latest_gross_margin": pq.latest_gross_margin,
            "gross_margin_delta": pq.gross_margin_delta,
            "latest_rd_intensity": pq.latest_rd_intensity,
        }
    except Exception as e:
        pack["factor_profitability"] = f"error: {e}"

    # ── 11. 相对估值(横向市场锚定) ──
    try:
        from stockhot.valuation import analyze_relative_valuation

        rv = analyze_relative_valuation(pro, ts_code, name)
        pack["relative_valuation"] = {
            "pe_ratio": rv.pe_ratio,
            "pe_ratio_pct": rv.pe_ratio_pct,
            "erp": rv.erp,
            "quadrant": rv.quadrant,
            "quadrant_label": rv.quadrant_label,
            "composite_verdict": rv.composite_verdict,
            "index_pe": getattr(rv, "index_pe", None),
            "index_pe_pct": getattr(rv, "index_pe_pct", None),
            "risk_free_rate": getattr(rv, "risk_free_rate", None),
            "signals": rv.signals,
        }
    except Exception as e:
        pack["relative_valuation"] = f"error: {e}"

    result["targets"][ts_code] = pack
    logger.info("{} 采集完成: PB={}%分位 动量={}", ts_code, val_summary["pb"]["pct"], pack["momentum_manual"])
    time.sleep(1.0)

# ── 同业全景(5 家 + 恒力恒逸的行情/动量) ──
for ts_code, name in {**PEERS, **TARGETS}.items():
    try:
        r = pro.daily_basic(ts_code=ts_code, limit=1)
        dpx = fetch_daily_adj(ts_code)
        adj = dpx["adj_close"].reset_index(drop=True)
        inc25 = pro.income(ts_code=ts_code, period="20251231", fields="ts_code,total_revenue,n_income", limit=1)
        inc26q1 = pro.income(ts_code=ts_code, period="20260331", fields="ts_code,total_revenue,n_income", limit=1)
        fc = pro.forecast(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max",
        )
        fc26 = [r_ for _, r_ in fc.iterrows() if r_["end_date"] == "20260630"]
        result["peers"][ts_code] = {
            "name": name,
            "trade_date": r.iloc[0]["trade_date"],
            "close": float(r.iloc[0]["close"]),
            "pe_ttm": None if pd.isna(r.iloc[0]["pe_ttm"]) else float(r.iloc[0]["pe_ttm"]),
            "pb": float(r.iloc[0]["pb"]),
            "dv_ttm": None if pd.isna(r.iloc[0].get("dv_ttm")) else float(r.iloc[0]["dv_ttm"]),
            "total_mv_yi": round(float(r.iloc[0]["total_mv"]) / 1e4, 0),
            "momentum": window_returns(adj),
            "rev_2025_yi": round(float(inc25.iloc[0]["total_revenue"]) / 1e8, 1) if len(inc25) else None,
            "np_2025_yi": round(float(inc25.iloc[0]["n_income"]) / 1e8, 2) if len(inc25) else None,
            "rev_26q1_yi": round(float(inc26q1.iloc[0]["total_revenue"]) / 1e8, 1) if len(inc26q1) else None,
            "np_26q1_yi": round(float(inc26q1.iloc[0]["n_income"]) / 1e8, 2) if len(inc26q1) else None,
            "fc_26h1": (
                {
                    "type": fc26[0]["type"],
                    "p_min": None if pd.isna(fc26[0]["p_change_min"]) else float(fc26[0]["p_change_min"]),
                    "p_max": None if pd.isna(fc26[0]["p_change_max"]) else float(fc26[0]["p_change_max"]),
                }
                if fc26
                else None
            ),
        }
        logger.info("peer {} {} ok", name, ts_code)
    except Exception as e:
        result["peers"][ts_code] = {"name": name, "error": str(e)}
    time.sleep(0.6)

# ── 基准指数 ──
for tag, code in [("sh000001", "000001.SH"), ("hs300", "000300.SH")]:
    try:
        r = pro.index_daily(ts_code=code, start_date="20250801", end_date="20260815")
        r = r.sort_values("trade_date").reset_index(drop=True)
        closes = r["close"]
        result["benchmarks"][tag] = {
            "latest_date": str(r["trade_date"].iloc[-1]),
            "latest": float(closes.iloc[-1]),
            "d60": round(float(closes.iloc[-1] / closes.iloc[-61] - 1) * 100, 1),
            "d120": round(float(closes.iloc[-1] / closes.iloc[-121] - 1) * 100, 1),
            "d250": round(float(closes.iloc[-1] / closes.iloc[-251] - 1) * 100, 1),
        }
    except Exception as e:
        result["benchmarks"][tag] = str(e)

OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"[saved] {OUT}")
# 打印关键摘要
for tc, p in result["targets"].items():
    print(
        f"\n== {p['basic'][0]['name']} {tc} ==\n"
        f"  fresh: {p['freshness']}\n"
        f"  val3y: pb={p['valuation_3y']['pb']} pe={p['valuation_3y']['pe']}\n"
        f"  momentum_manual: {p['momentum_manual']}\n"
        f"  prosperity: {p['prosperity']}\n"
        f"  distress: {p['distress']}\n"
        f"  davis: {p['davis_double']}\n"
        f"  factor_momentum: {p['factor_momentum']}\n"
        f"  factor_dividend: {p['factor_dividend']}\n"
        f"  factor_forecast: {p['factor_forecast']}\n"
        f"  factor_holder: {p['factor_holder']}\n"
        f"  factor_profitability: {p['factor_profitability']}\n"
        f"  rel_val: {p['relative_valuation'] if isinstance(p['relative_valuation'], str) else {k: v for k, v in p['relative_valuation'].items() if k != 'signals'}}\n"
        f"  pledge: {str(p['pledge_stat'])[:200]}\n"
        f"  holdertrade_n: {len(p['holdertrade_2025_2026']) if isinstance(p['holdertrade_2025_2026'], list) else p['holdertrade_2025_2026']}\n"
        f"  repurchase_n: {len(p['repurchase']) if isinstance(p['repurchase'], list) else p['repurchase']}\n"
        f"  holders: {[(x['end_date'], int(float(x['holder_num']))) for x in p['holder_number']]}"
    )
print("\n== peers ==")
print(json.dumps(result["peers"], ensure_ascii=False, indent=1, default=str)[:3000])
print("\n== benchmarks ==")
print(json.dumps(result["benchmarks"], ensure_ascii=False, indent=1))
