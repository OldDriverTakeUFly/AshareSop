#!/usr/bin/env python3
"""华海诚科 (688535.SH) 个股深度研报取数脚本.

四维评分（估值/趋势/景气/困境→戴维斯双击）+ 5 补充因子 + 股东户数 + 十大流通股东
+ 相对估值（stockhot.valuation）+ 8 季财务明细 dump。

用法:
    cd /home/leo/Projects/CodeAgentDashboard && \
    PYTHONPATH=/home/leo/Projects/CodeAgentDashboard .venv/bin/python \
    davis_analyzer/studies/hhc_scoring.py

输出: davis_analyzer/studies/hhc_engine_20260830.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
from loguru import logger

from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import batch_trend, calculate_monthly_trend, calculate_trend_slope, calculate_trend_acceleration
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo
from davis_analyzer.valuation import (
    calculate_percentile,
    calculate_valuation_score,
    detect_cyclical,
    fetch_valuation_history,
)

TS_CODE = "688535.SH"
STOCK_NAME = "华海诚科"
PERIODS = 12
OUTPUT_PATH = Path(__file__).parent / "hhc_engine_20260830.json"


def _stock_info(client: TushareClient) -> StockInfo:
    try:
        stock_df = client.get_stock_list()
        row = stock_df[stock_df["ts_code"] == TS_CODE]
        industry = str(row.iloc[0].get("industry", "") or "") if not row.empty else ""
        name = str(row.iloc[0].get("name", STOCK_NAME) or STOCK_NAME) if not row.empty else STOCK_NAME
    except Exception:
        industry, name = "", STOCK_NAME
    return StockInfo(ts_code=TS_CODE, name=name, industry=industry,
                     list_status="L", is_cyclical=detect_cyclical(industry))


def _asdict(obj) -> dict:
    if obj is None:
        return None
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _asdict(getattr(obj, k)) for k in obj.__dataclass_fields__}
    if isinstance(obj, (list, tuple)):
        return [_asdict(x) for x in obj]
    if isinstance(obj, float):
        return round(obj, 4)
    return obj


def main() -> None:
    client = TushareClient()
    out: dict = {"ts_code": TS_CODE, "name": STOCK_NAME, "pulled": "20260830"}

    # ── 财务 12 季 ──
    fin = fetch_financial_data(client, TS_CODE, periods=PERIODS)
    out["fin_12q"] = [{
        "period": f.report_period, "rev": f.revenue, "np": f.net_profit,
        "eps": f.eps, "roe": f.roe, "ocf": f.operating_cf,
        "yoy_rev": f.yoy_revenue_growth, "yoy_np": f.yoy_profit_growth,
        "gm": f.grossprofit_margin, "rd": f.rd_exp,
    } for f in fin]
    eps_h = [f.eps for f in fin]
    roe_h = [f.roe for f in fin]
    rev_g = [f.yoy_revenue_growth or 0.0 for f in fin]
    np_g = [f.yoy_profit_growth or 0.0 for f in fin]
    latest = fin[0]
    debt_ratio = (latest.total_debt or 0) / (latest.total_assets or 1)
    out["fin_latest"] = {"period": latest.report_period,
                         "total_assets": latest.total_assets, "total_debt": latest.total_debt,
                         "ocf": latest.operating_cf, "debt_ratio": debt_ratio}

    # ── 估值百分位 ──
    vh = fetch_valuation_history(client, TS_CODE)
    si = _stock_info(client)
    if vh:
        lv = vh[0]
        pe_pct = calculate_percentile(lv.pe_ttm, [v.pe_ttm for v in vh])
        pb_pct = calculate_percentile(lv.pb, [v.pb for v in vh])
        val_score, pe_pct, pb_pct = calculate_valuation_score(vh, si.is_cyclical)
        out["valuation"] = {"latest": {"date": lv.trade_date, "pe_ttm": lv.pe_ttm, "pb": lv.pb,
                                       "ps": getattr(lv, "ps", None), "total_mv": getattr(lv, "total_mv", None)},
                            "score": val_score, "pe_pct": pe_pct, "pb_pct": pb_pct,
                            "industry": si.industry, "is_cyclical": si.is_cyclical, "n_days": len(vh)}
        dates = pd.to_datetime([v.trade_date for v in vh], format="%Y%m%d")
        daily_pe = pd.Series([v.pe_ttm for v in vh], index=dates)
        daily_pb = pd.Series([v.pb for v in vh], index=dates)
        trend_map = batch_trend({TS_CODE: (daily_pe, daily_pb)}, {TS_CODE: si})
        trend_score = trend_map.get(TS_CODE, 50.0)
        m_pe, m_pb = calculate_monthly_trend(daily_pe, daily_pb)
        out["trend"] = {"score": trend_score,
                        "pe_slope": calculate_trend_slope(m_pe), "pb_slope": calculate_trend_slope(m_pb),
                        "pe_accel": calculate_trend_acceleration(m_pe), "pb_accel": calculate_trend_acceleration(m_pb)}
    else:
        pe_pct = pb_pct = 0.5
        val_score = trend_score = 50.0
        out["valuation"] = out["trend"] = {"error": "无估值历史"}

    # ── 景气 + 困境 + 综合 ──
    pscore = calculate_prosperity_score(fin)
    distress = calculate_distress_score(
        eps_history=eps_h, pe_pct=pe_pct, pb_pct=pb_pct, debt_ratio=debt_ratio,
        operating_cf=latest.operating_cf or 0.0, total_debt=latest.total_debt or 0.0,
        total_assets=latest.total_assets or 0.0, roe_history=roe_h,
        revenue_history=rev_g, profit_history=np_g, delta_g=pscore.delta_g, ts_code=TS_CODE)
    davis = calculate_davis_double_score(valuation_score=val_score,
                                         prosperity_score=pscore.composite_score,
                                         distress_score=distress.total_score,
                                         trend_score=trend_score, ts_code=TS_CODE, name=STOCK_NAME)
    out["prosperity"] = _asdict(pscore)
    out["distress"] = _asdict(distress)
    out["davis"] = _asdict(davis)

    # ── 5 补充因子 ──
    out["momentum"] = _asdict(analyze_momentum(client, TS_CODE))
    out["dividend"] = _asdict(analyze_dividend(client, TS_CODE))
    out["forecast"] = _asdict(analyze_forecast(client, TS_CODE, pscore))
    out["forecast_revision"] = _asdict(analyze_forecast_revision(client, TS_CODE))
    out["holder_concentration"] = _asdict(analyze_holder_concentration(client, TS_CODE))
    out["profitability_quality"] = _asdict(analyze_profitability_quality(fin))

    # ── 股东户数 + 十大流通股东（原生 pro）──
    pro = client._get_pro() if hasattr(client, "_get_pro") else None
    if pro is None:
        # 兜底：直接用 stockhot 的 pro api
        from stockhot.tushare_config import get_pro_api
        pro = get_pro_api(timeout=30)
    try:
        hn = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num")
        out["holder_num"] = hn.sort_values("end_date").tail(8).to_dict("records")
    except Exception as e:
        out["holder_num"] = {"err": str(e)}
    try:
        t10 = pro.top10_floatholders(ts_code=TS_CODE, fields="ts_code,ann_date,holder_name,hold_ratio")
        out["top10_float"] = t10.sort_values("ann_date").tail(10).to_dict("records")
    except Exception as e:
        out["top10_float"] = {"err": str(e)}

    # ── 相对估值 ──
    try:
        from stockhot.valuation import analyze_relative_valuation
        rv = analyze_relative_valuation(pro, TS_CODE, STOCK_NAME)
        out["relative_valuation"] = _asdict(rv)
    except Exception as e:
        out["relative_valuation"] = {"err": str(e)}

    OUTPUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    logger.info("✅ 写入 {}", OUTPUT_PATH)
    # 摘要
    print(f"davis_final={davis.final_score:.2f} val={val_score:.1f} prosp={pscore.composite_score:.1f} "
          f"ΔG={pscore.delta_g:.2f} distress={distress.total_score:.1f} trend={trend_score:.1f}")
    if out.get("momentum"):
        m = out["momentum"]
        print(f"momentum: score={m.get('momentum_score')} windows={m.get('window_returns')}")
    if out.get("holder_concentration"):
        print(f"holder_conc: {out['holder_concence'] if 'holder_concence' in out else out['holder_concentration']}")
    if out.get("relative_valuation") and "err" not in out["relative_valuation"]:
        rv = out["relative_valuation"]
        print("relative_valuation keys:", list(rv.keys()))


if __name__ == "__main__":
    main()
