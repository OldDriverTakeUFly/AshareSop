#!/usr/bin/env python3
"""海螺水泥 600500.SH / 塔牌集团 002233.SZ 水泥双标的完整取数脚本.

产出(每只标的):
  1. 时效性校验(daily_basic 最新交易日 / income 最新报告期 / forecast 预告)
  2. 四维评分:估值分位 + 趋势 + 景气度 + 困境 → 戴维斯双击综合分
  3. 5 补充因子:momentum / dividend / forecast / holder_concentration / profitability
  4. 股东户数趋势(stk_holdernumber 近 8 期)
  5. 相对市场估值锚定(stockhot.valuation.analyze_relative_valuation)
  6. 资产负债表净现金拆解(balancesheet)
  7. 分红历史(dividend)
同业快照:华新水泥/上峰水泥/祁连山 + 两主标的(市值/PE/PB/PS/dv_ttm + 3 年分位)

用法(从仓库根目录):
    .venv/bin/python davis_analyzer/studies/cement_scoring.py

输出:
    .sisyphus/evidence/cement/{hailuo,tapai}_scoring.json
    .sisyphus/evidence/cement/peers_snapshot.json
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from loguru import logger

# ── 环境:先 load_dotenv(拿新 token),再 pin PROJECT_ROOT(修路径),再 import 引擎 ──
load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.dividend import analyze_dividend
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
from davis_analyzer.valuation import (
    calculate_valuation_score,
    detect_cyclical,
    fetch_valuation_history,
)
from stockhot.tushare_config import get_pro_api
from stockhot.valuation import analyze_relative_valuation

TARGETS = [
    {"ts_code": "600585.SH", "name": "海螺水泥", "key": "hailuo"},  # 注意:海螺水泥 A 股真实代码 600585(600500 是中化国际)
    {"ts_code": "002233.SZ", "name": "塔牌集团", "key": "tapai"},
]
PEERS = [
    {"ts_code": "600801.SH", "name": "华新水泥"},
    {"ts_code": "000672.SZ", "name": "上峰水泥"},
    {"ts_code": "600720.SH", "name": "祁连山"},
]
OUT_DIR = Path(".sisyphus/evidence/cement")


# ════════════════════════════════════════════════════════════════════════════
# 工具
# ════════════════════════════════════════════════════════════════════════════


def _daily_basic_3y(pro, client: TushareClient, ts_code: str) -> pd.DataFrame:
    """取 3 年 daily_basic,校验行数;不足 700 行则 pro.daily_basic 分段直连."""
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
    db = client.get_daily_basic(ts_code, start, end)
    if db is None or len(db) < 700:
        logger.warning("{} client.get_daily_basic 仅 {} 行,分段直连 pro.daily_basic", ts_code, 0 if db is None else len(db))
        segs = []
        cur = date.today()
        while cur.strftime("%Y%m%d") > start:
            seg_start = max(
                (cur - timedelta(days=500)).strftime("%Y%m%d"), start
            )
            seg_end = cur.strftime("%Y%m%d")
            df = pro.daily_basic(
                ts_code=ts_code,
                start_date=seg_start,
                end_date=seg_end,
                fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv,circ_mv,dv_ttm,turnover_rate,total_share",
            )
            if df is not None and len(df):
                segs.append(df)
            cur = cur - timedelta(days=501)
        db = (
            pd.concat(segs, ignore_index=True)
            .drop_duplicates("trade_date")
            .reset_index(drop=True)
            if segs
            else pd.DataFrame()
        )
    db = db.sort_values("trade_date").reset_index(drop=True)
    logger.info("{} daily_basic {} 行, 首末 {} ~ {}", ts_code, len(db), db["trade_date"].iloc[0] if len(db) else "-", db["trade_date"].iloc[-1] if len(db) else "-")
    return db


def _pct(series: pd.Series, current: float) -> float:
    """当前值的历史分位(有多少比例历史值 < 当前值)."""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if not len(s):
        return float("nan")
    return round((s < current).sum() / len(s) * 100, 1)


def _quantiles(series: pd.Series) -> dict:
    s = pd.to_numeric(series, errors="coerce").dropna()
    out = {}
    for p in [10, 25, 50, 75, 90]:
        out[p] = round(float(s.quantile(p / 100)), 3) if len(s) else None
    return out


def _manual_valuation_history(db: pd.DataFrame, ts_code: str) -> list[ValuationData]:
    """从 daily_basic DataFrame 手工构造 ValuationData 列表(过滤 NaN PE,按日期降序)."""
    rows = []
    for _, r in db.iterrows():
        pe_v = pd.to_numeric(pd.Series([r.get("pe_ttm")]), errors="coerce").iloc[0]
        pb_v = pd.to_numeric(pd.Series([r.get("pb")]), errors="coerce").iloc[0]
        ps_v = pd.to_numeric(pd.Series([r.get("ps")]), errors="coerce").iloc[0]
        mv_v = pd.to_numeric(pd.Series([r.get("total_mv")]), errors="coerce").iloc[0]
        if pd.isna(pb_v) or pd.isna(pe_v):
            # 对齐引擎 fetch_valuation_history 行为:过滤 PE/PB NaN 行,防止 calculate_percentile 崩溃
            continue
        rows.append(
            ValuationData(
                ts_code=ts_code,
                trade_date=str(r["trade_date"]),
                pe_ttm=None if pd.isna(pe_v) else float(pe_v),
                pb=float(pb_v),
                ps=None if pd.isna(ps_v) else float(ps_v),
                total_mv=None if pd.isna(mv_v) else float(mv_v),
            )
        )
    rows.sort(key=lambda v: v.trade_date, reverse=True)
    return rows


def _holder_trend(pro, ts_code: str, periods: int = 8) -> dict:
    h = pro.stk_holdernumber(
        ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num"
    )
    h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(periods)
    rows = []
    prev = None
    for _, r in h.iterrows():
        num = int(r["holder_num"])
        chg = round((num - prev) / prev * 100, 1) if prev else None
        rows.append(
            {"end_date": str(r["end_date"]), "ann_date": str(r["ann_date"]), "holder_num": num, "chg_pct": chg}
        )
        prev = num
    nums4 = [r["holder_num"] for r in rows[-4:]] if len(rows) >= 4 else [r["holder_num"] for r in rows]
    trend = "集中(动能增强)" if (len(nums4) >= 2 and nums4[-1] < nums4[0]) else "分散(动能减弱)"
    return {"rows": rows, "trend": trend, "latest": nums4[-1] if nums4 else None}


def _balancesheet_cash(pro, ts_code: str) -> dict:
    """最新报告期资产负债表:货币资金/交易性金融资产/有息负债 → 净现金."""
    bs = pro.balancesheet(
        ts_code=ts_code,
        fields="ts_code,end_date,ann_date,monetary_capital,tradable_fin_assets,"
        "other_equity_invest,st_borr,lt_borr,bonds_payable,non_cur_liab_1year,"
        "total_assets,total_liab,notes_payable",
        limit=8,
    )
    if bs is None or not len(bs):
        return {}
    bs = bs.sort_values("end_date")
    latest = bs.iloc[-1]
    cash = float(latest.get("monetary_capital") or 0)
    trad = float(latest.get("tradable_fin_assets") or 0)
    oth = float(latest.get("other_equity_invest") or 0)
    st_b = float(latest.get("st_borr") or 0)
    lt_b = float(latest.get("lt_borr") or 0)
    bond = float(latest.get("bonds_payable") or 0)
    ncl1y = float(latest.get("non_cur_liab_1year") or 0)
    interest_debt = st_b + lt_b + bond + ncl1y
    return {
        "end_date": str(latest["end_date"]),
        "monetary_capital_yi": round(cash / 1e8, 1),
        "tradable_fin_yi": round(trad / 1e8, 1),
        "other_equity_invest_yi": round(oth / 1e8, 1),
        "st_borr_yi": round(st_b / 1e8, 1),
        "lt_borr_yi": round(lt_b / 1e8, 1),
        "bonds_yi": round(bond / 1e8, 1),
        "non_cur_1y_yi": round(ncl1y / 1e8, 1),
        "interest_bearing_debt_yi": round(interest_debt / 1e8, 1),
        "net_cash_yi": round((cash + trad - interest_debt) / 1e8, 1),
        "total_assets_yi": round(float(latest.get("total_assets") or 0) / 1e8, 1),
        "total_liab_yi": round(float(latest.get("total_liab") or 0) / 1e8, 1),
    }


# ════════════════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════════════════


def score_one(client: TushareClient, pro, spec: dict) -> dict:
    ts_code, name = spec["ts_code"], spec["name"]
    logger.info("=" * 66)
    logger.info("{} ({}) 完整取数", name, ts_code)
    logger.info("=" * 66)
    result: dict = {"ts_code": ts_code, "name": name}

    # ── 1. 时效性校验 ──
    db1 = pro.daily_basic(ts_code=ts_code, limit=1)
    inc1 = pro.income(ts_code=ts_code, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
    fc1 = pro.forecast(ts_code=ts_code, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
    result["freshness"] = {
        "latest_trade_date": str(db1.iloc[0]["trade_date"]) if len(db1) else "none",
        "latest_report_period": str(inc1.iloc[0]["end_date"]) if len(inc1) else "none",
        "latest_ann_date": str(inc1.iloc[0]["ann_date"]) if len(inc1) else "none",
        "forecast": (
            {
                "type": str(fc1.iloc[0]["type"]),
                "ann_date": str(fc1.iloc[0]["ann_date"]),
                "end_date": str(fc1.iloc[0]["end_date"]),
                "p_change": [fc1.iloc[0]["p_change_min"], fc1.iloc[0]["p_change_max"]],
            }
            if len(fc1)
            else None
        ),
    }

    # ── 2. 财务 12 期 ──
    fin = fetch_financial_data(client, ts_code, periods=12)
    assert fin and fin[0].ts_code == ts_code, f"财务数据代码不符: {fin[0].ts_code if fin else None}"
    fin_rows = []
    for f in fin:
        fin_rows.append(
            {
                "report_period": f.report_period,
                "revenue_yi": round((f.revenue or 0) / 1e8, 2),
                "net_profit_yi": round(float(f.net_profit or 0) / 1e8, 2),
                "eps": f.eps,
                "roe_pct": round(f.roe, 2) if f.roe is not None else None,
                "operating_cf_yi": round((f.operating_cf or 0) / 1e8, 2),
                "debt_ratio_pct": round((f.total_debt or 0) / (f.total_assets or 1) * 100, 1),
                "yoy_rev_pct": round(f.yoy_revenue_growth * 100, 1) if f.yoy_revenue_growth is not None else None,
                "yoy_profit_pct": round(f.yoy_profit_growth * 100, 1) if f.yoy_profit_growth is not None else None,
                "gross_margin_pct": round(f.grossprofit_margin, 2) if getattr(f, "grossprofit_margin", None) is not None else None,
                "rd_exp_yi": round((getattr(f, "rd_exp", 0) or 0) / 1e8, 3),
            }
        )
    result["financial"] = fin_rows

    # ── 3. 估值 3 年(带行数校验)──
    db = _daily_basic_3y(pro, client, ts_code)
    pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna().sort_index()
    pb = pd.to_numeric(db["pb"], errors="coerce").dropna().sort_index()
    ps = pd.to_numeric(db["ps"], errors="coerce").dropna().sort_index()
    mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna().sort_index()
    dv = pd.to_numeric(db["dv_ttm"], errors="coerce").dropna().sort_index()
    latest_row = db.iloc[-1]
    result["valuation_snapshot"] = {
        "trade_date": str(latest_row["trade_date"]),
        "close": float(latest_row["close"]),
        "pe_ttm": float(latest_row["pe_ttm"]) if pd.notna(latest_row["pe_ttm"]) else None,
        "pb": float(latest_row["pb"]),
        "ps": float(latest_row["ps"]) if pd.notna(latest_row["ps"]) else None,
        "total_mv_yi": round(float(latest_row["total_mv"]) / 1e4, 1),
        "dv_ttm_pct": float(latest_row["dv_ttm"]) if pd.notna(latest_row["dv_ttm"]) else None,
        "pe_pct": _pct(db["pe_ttm"], float(latest_row["pe_ttm"])) if pd.notna(latest_row["pe_ttm"]) else None,
        "pb_pct": _pct(db["pb"], float(latest_row["pb"])),
        "ps_pct": _pct(db["ps"], float(latest_row["ps"])) if pd.notna(latest_row["ps"]) else None,
        "mv_pct": _pct(db["total_mv"], float(latest_row["total_mv"])),
        "dv_ttm_pct_percentile": _pct(db["dv_ttm"], float(latest_row["dv_ttm"])) if pd.notna(latest_row["dv_ttm"]) else None,
        "n_days": len(db),
        "pb_quantiles": _quantiles(db["pb"]),
        "pe_quantiles": _quantiles(db["pe_ttm"]),
        "ps_quantiles": _quantiles(db["ps"]),
        "dv_quantiles": _quantiles(db["dv_ttm"]),
    }

    # ── 4. 引擎估值分(fetch_valuation_history,行数不足则手工构造)──
    val_history = fetch_valuation_history(client, ts_code)
    if not val_history or len(val_history) < 700:
        logger.warning("{} 引擎估值历史仅 {} 天,手工构造 3 年序列", ts_code, len(val_history) if val_history else 0)
        val_history = _manual_valuation_history(db, ts_code)
    stock_df = client.get_stock_list()
    srow = stock_df[stock_df["ts_code"] == ts_code]
    industry = str(srow.iloc[0].get("industry", "") or "") if not srow.empty else ""
    real_name = str(srow.iloc[0].get("name", name) or name) if not srow.empty else name
    stock_info = StockInfo(
        ts_code=ts_code, name=real_name, industry=industry, list_status="L",
        is_cyclical=detect_cyclical(industry),
    )
    val_score, pe_pct_eng, pb_pct_eng = calculate_valuation_score(val_history, stock_info.is_cyclical)
    result["engine_valuation"] = {
        "score": round(val_score, 2),
        "pe_pct": round(pe_pct_eng * 100, 1),
        "pb_pct": round(pb_pct_eng * 100, 1),
        "n_days": len(val_history),
        "industry": industry,
        "is_cyclical": stock_info.is_cyclical,
    }

    # ── 5. 景气度 ──
    pscore = calculate_prosperity_score(fin)
    stage = classify_stock_stage(pscore)
    result["prosperity"] = {
        "composite": round(pscore.composite_score, 2),
        "delta_g": round(pscore.delta_g, 2),
        "revenue_score": round(pscore.revenue_score, 2),
        "profit_score": round(pscore.profit_score, 2),
        "slope_score": round(pscore.slope_score, 2),
        "duration_score": round(pscore.duration_score, 2),
        "stage": stage,
    }

    # ── 6. 趋势 ──
    trend_score = 50.0
    try:
        dates = pd.to_datetime([v.trade_date for v in val_history], format="%Y%m%d")
        daily_pe = pd.Series([v.pe_ttm if v.pe_ttm is not None else float("nan") for v in val_history], index=dates)
        daily_pb = pd.Series([v.pb for v in val_history], index=dates)
        trend_map = batch_trend({ts_code: (daily_pe, daily_pb)}, {ts_code: stock_info})
        trend_score = trend_map.get(ts_code, 50.0)
    except Exception as e:
        logger.warning("趋势计算失败: {}", e)
    result["trend_score"] = round(trend_score, 2)

    # ── 7. 困境 + 戴维斯综合 ──
    latest = fin[0]
    debt_ratio = (latest.total_debt or 0) / (latest.total_assets or 1)
    distress = calculate_distress_score(
        eps_history=[f.eps for f in fin],
        pe_pct=pe_pct_eng,
        pb_pct=pb_pct_eng,
        debt_ratio=debt_ratio,
        operating_cf=latest.operating_cf or 0.0,
        total_debt=latest.total_debt or 0.0,
        total_assets=latest.total_assets or 0.0,
        roe_history=[f.roe for f in fin],
        revenue_history=[f.yoy_revenue_growth or 0.0 for f in fin],
        profit_history=[f.yoy_profit_growth or 0.0 for f in fin],
        delta_g=pscore.delta_g,
        ts_code=ts_code,
    )
    davis = calculate_davis_double_score(
        valuation_score=val_score,
        prosperity_score=pscore.composite_score,
        distress_score=distress.total_score,
        trend_score=trend_score,
        ts_code=ts_code,
        name=name,
    )
    result["distress"] = {
        "total": round(distress.total_score, 2),
        "layer1": round(distress.layer1_score, 2),
        "layer2": round(distress.layer2_score, 2),
        "layer3": round(distress.layer3_score, 2),
    }
    result["davis_double"] = {
        "final": round(davis.final_score, 2),
        "rank": davis.rank,
        "valuation": round(davis.valuation_score, 2),
        "prosperity": round(davis.prosperity_score, 2),
        "distress": round(davis.distress_score, 2),
        "trend": round(davis.trend_score, 2),
    }

    # ── 8. 5 补充因子 ──
    factors: dict = {}
    try:
        mom = analyze_momentum(client, ts_code)
        if mom:
            factors["momentum"] = {
                "score": round(mom.momentum_score, 2),
                "abs_score": round(mom.absolute_momentum_score, 2),
                "rs_percentile": round(mom.rs_percentile, 1) if mom.rs_percentile is not None else None,
                "window_returns": {k: round(v * 100, 2) for k, v in (mom.window_returns or {}).items()},
            }
    except Exception as e:
        factors["momentum"] = {"error": str(e)}
    try:
        div = analyze_dividend(client, ts_code)
        factors["dividend"] = {
            "score": round(div.dividend_score, 2),
            "consecutive_years": div.consecutive_years,
            "latest_yield_pct": div.latest_yield_pct,
        }
    except Exception as e:
        factors["dividend"] = {"error": str(e)}
    try:
        fc = analyze_forecast(client, ts_code, pscore)  # 第三参必须是 ProsperityScore 对象
        if fc:
            factors["forecast"] = {
                "leading_score": round(fc.leading_score, 2),
                "p_change_mid": fc.p_change_mid,
                "type": fc.type,
                "is_stale": fc.is_stale,
            }
    except Exception as e:
        factors["forecast"] = {"error": str(e)}
    try:
        hc = analyze_holder_concentration(client, ts_code)
        if hc:
            factors["holder_concentration"] = {
                "concentration_score": round(hc.concentration_score, 2),
                "trend": hc.trend,
                "latest_chg_pct": hc.latest_chg_pct,
            }
    except Exception as e:
        factors["holder_concentration"] = {"error": str(e)}
    try:
        pq = analyze_profitability_quality(fin)
        factors["profitability"] = {
            "quality_score": round(pq.quality_score, 2) if pq.quality_score is not None else None,
            "margin_trend": pq.margin_trend,
            "rd_intensity_pct": pq.rd_intensity_pct,
            "data_sufficient": pq.data_sufficient,
        }
    except Exception as e:
        factors["profitability"] = {"error": str(e)}
    result["factors"] = factors

    # ── 9. 股东户数 ──
    try:
        result["holder_trend"] = _holder_trend(pro, ts_code)
    except Exception as e:
        result["holder_trend"] = {"error": str(e)}

    # ── 10. 相对估值锚定 ──
    try:
        rv = analyze_relative_valuation(pro, ts_code, name, lookback_years=3)
        result["relative_valuation"] = {
            "benchmark": rv.benchmark,
            "index_pe": rv.index_pe,
            "index_pe_pct": round(rv.index_pe_pct * 100, 1) if rv.index_pe_pct is not None else None,
            "risk_free_rate_pct": rv.risk_free_rate * 100 if rv.risk_free_rate is not None else None,
            "pe_ratio": rv.pe_ratio,
            "pe_ratio_pct": round(rv.pe_ratio_pct * 100, 1) if rv.pe_ratio_pct is not None else None,
            "erp_pct": round(rv.erp * 100, 2) if rv.erp is not None else None,
            "pe_band_quadrant": rv.pe_band_quadrant,
            "signals": rv.signals,
        }
    except Exception as e:
        result["relative_valuation"] = {"error": str(e)}

    # ── 11. 净现金 + 资产负债表 ──
    try:
        result["balance_sheet"] = _balancesheet_cash(pro, ts_code)
    except Exception as e:
        result["balance_sheet"] = {"error": str(e)}

    # ── 12. 分红历史 ──
    try:
        dv_hist = pro.dividend(
            ts_code=ts_code,
            fields="ts_code,end_date,ann_date,cash_div,cash_div_tax,base_share,div_proc",
        )
        dv_hist = dv_hist[dv_hist["div_proc"] == "实施"].sort_values("end_date").tail(12)
        rows = []
        for _, r in dv_hist.iterrows():
            cd = r.get("cash_div")
            bs = r.get("base_share")
            total_yi = round(float(cd) * float(bs) / 1e8, 2) if cd and bs and not pd.isna(cd) and not pd.isna(bs) else None
            rows.append({"end_date": str(r["end_date"]), "dps_pre_tax": None if pd.isna(cd) else float(cd), "total_cash_yi": total_yi})
        result["dividend_history"] = rows
    except Exception as e:
        result["dividend_history"] = {"error": str(e)}

    return result


def peer_snapshot(pro, client: TushareClient) -> list[dict]:
    """同业 + 主标的快照:市值/PE/PB/PS/dv_ttm + 3 年分位 + 最新净利."""
    out = []
    all_codes = PEERS + [{"ts_code": t["ts_code"], "name": t["name"]} for t in TARGETS]
    for p in all_codes:
        ts_code = p["ts_code"]
        try:
            db = _daily_basic_3y(pro, client, ts_code)
            lr = db.iloc[-1]
            inc = pro.income(ts_code=ts_code, fields="ts_code,end_date,n_income", period=None, limit=1)
            # income 无 limit 排序问题:直接取 ann 最近一行
            row = {
                "ts_code": ts_code,
                "name": p["name"],
                "trade_date": str(lr["trade_date"]),
                "total_mv_yi": round(float(lr["total_mv"]) / 1e4, 1),
                "pe_ttm": float(lr["pe_ttm"]) if pd.notna(lr["pe_ttm"]) else None,
                "pe_pct": _pct(db["pe_ttm"], float(lr["pe_ttm"])) if pd.notna(lr["pe_ttm"]) else None,
                "pb": float(lr["pb"]),
                "pb_pct": _pct(db["pb"], float(lr["pb"])),
                "ps": float(lr["ps"]) if pd.notna(lr["ps"]) else None,
                "dv_ttm_pct": float(lr["dv_ttm"]) if pd.notna(lr["dv_ttm"]) else None,
                "dv_pct_percentile": _pct(db["dv_ttm"], float(lr["dv_ttm"])) if pd.notna(lr["dv_ttm"]) else None,
            }
            if inc is not None and len(inc):
                inc2 = inc.sort_values("end_date").iloc[-1]
                row["latest_income_period"] = str(inc2["end_date"])
                row["latest_net_profit_yi"] = round(float(inc2["n_income"] or 0) / 1e8, 2)
            out.append(row)
        except Exception as e:
            out.append({"ts_code": ts_code, "name": p["name"], "error": str(e)})
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = TushareClient()
    pro = get_pro_api(timeout=60)

    for spec in TARGETS:
        result = score_one(client, pro, spec)
        path = OUT_DIR / f"{spec['key']}_scoring.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        logger.success("✅ {} → {}", spec["name"], path)

    peers = peer_snapshot(pro, client)
    with open(OUT_DIR / "peers_snapshot.json", "w", encoding="utf-8") as f:
        json.dump(peers, f, ensure_ascii=False, indent=2, default=str)
    logger.success("✅ peers_snapshot → {}", OUT_DIR / "peers_snapshot.json")


if __name__ == "__main__":
    main()
