#!/usr/bin/env python3
"""光伏双龙头(通威 600438.SH / 隆基 601012.SH)研报取数脚本.

对两只标的执行:
  1. 完整四维评分 (估值/趋势/景气/困境 → davis final)
  2. 5 个补充因子引擎 (momentum/dividend/forecast/holder/profitability)
  3. 股东户数趋势 + 十大流通股东
  4. 相对估值锚定 (stockhot.valuation.analyze_relative_valuation)
  5. 数据新鲜度校验 (daily_basic 最新交易日 / income 披露日 / forecast)
  6. 资产负债表补充字段 (货币资金 money_cap 等)

坑点处理(见 engine-usage.md §8):
  - client.get_daily_basic 增量窗口可能只回 ~22 天 → 校验 ≥700 行,
    不足则 pro.daily_basic 分段直连(≤480 天/段), concat 后 reset_index(drop=True)
  - forecast net_profit_min/max 单位是万元 → /1e4 转亿
  - load_dotenv override=True + 之后重新 pin PROJECT_ROOT

用法 (从父仓库根目录):
    .venv/bin/python davis_analyzer/studies/pvduo_scoring.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from loguru import logger

load_dotenv(".env", override=True)  # 防 stale token
if os.environ.get("PROJECT_ROOT") in ("/app", "", None):
    os.environ["PROJECT_ROOT"] = str(Path.cwd())

from davis_analyzer.distress import calculate_distress_score  # noqa: E402
from davis_analyzer.dividend import analyze_dividend  # noqa: E402
from davis_analyzer.financial_fetcher import fetch_financial_data  # noqa: E402
from davis_analyzer.forecast import analyze_forecast  # noqa: E402
from davis_analyzer.holder_concentration import analyze_holder_concentration  # noqa: E402
from davis_analyzer.momentum import analyze_momentum  # noqa: E402
from davis_analyzer.profitability import analyze_profitability_quality  # noqa: E402
from davis_analyzer.prosperity import calculate_prosperity_score  # noqa: E402
from davis_analyzer.prosperity_sector import classify_stock_stage  # noqa: E402
from davis_analyzer.scoring import calculate_davis_double_score  # noqa: E402
from davis_analyzer.trend import batch_trend  # noqa: E402
from davis_analyzer.tushare_client import TushareClient  # noqa: E402
from davis_analyzer.types import StockInfo, ValuationData  # noqa: E402
from davis_analyzer.valuation import (  # noqa: E402
    calculate_valuation_score,
    detect_cyclical,
)
from stockhot.tushare_config import get_pro_api  # noqa: E402
from stockhot.valuation import analyze_relative_valuation  # noqa: E402

TARGETS = [
    {"ts_code": "600438.SH", "name": "通威股份"},
    {"ts_code": "601012.SH", "name": "隆基绿能"},
]
OUT_DIR = Path("davis_analyzer/studies/output")
DAYS = 1095  # 3 年估值窗口


def _fetch_valuation_df(client: TushareClient, pro, ts_code: str) -> pd.DataFrame:
    """取 3 年 daily_basic,校验行数,不足则分段直连 pro.daily_basic."""
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=DAYS)).strftime("%Y%m%d")
    db = client.get_daily_basic(ts_code, start, end)
    src = "client.get_daily_basic"
    if db is None or len(db) < 700:
        logger.warning("{} 增量窗口只回 {} 行, 分段直连 pro.daily_basic", ts_code, 0 if db is None else len(db))
        frames = []
        seg_days = 480
        cur = date.today() - timedelta(days=DAYS)
        while cur < date.today():
            seg_end = min(cur + timedelta(days=seg_days), date.today())
            df = pro.daily_basic(
                ts_code=ts_code,
                start_date=cur.strftime("%Y%m%d"),
                end_date=seg_end.strftime("%Y%m%d"),
                fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv",
            )
            if df is not None and len(df):
                frames.append(df)
            cur = seg_end + timedelta(days=1)
        db = pd.concat(frames, ignore_index=True)
        db = db.drop_duplicates("trade_date").reset_index(drop=True)
        src = "pro.daily_basic segmented"
    db = db.sort_values("trade_date").reset_index(drop=True)
    logger.info("{} 估值序列 {} 行 ({}), 最新交易日 {}", ts_code, len(db), src, db["trade_date"].iloc[-1])
    return db


def _pct(series: pd.Series) -> tuple[float | None, float]:
    """返回 (当前值, 当前分位%), 亏损时值为 None."""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if not len(s):
        return None, float("nan")
    cur = s.iloc[-1]
    pct = (s < cur).sum() / len(s) * 100
    return float(cur), float(pct)


def _safe_pct(v: float | None) -> float:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0.5
    return v / 100.0


def score_one(target: dict) -> dict:
    ts_code, name = target["ts_code"], target["name"]
    client = TushareClient()
    pro = get_pro_api(timeout=30)

    # ── 0. 行业/StockInfo ──
    industry, real_name = "", name
    try:
        stock_df = client.get_stock_list()
        row = stock_df[stock_df["ts_code"] == ts_code]
        if not row.empty:
            industry = str(row.iloc[0].get("industry", "") or "")
            real_name = str(row.iloc[0].get("name", name) or name)
    except Exception as e:
        logger.warning("stock_list 获取失败: {}", e)
    is_cyc = detect_cyclical(industry)
    info = StockInfo(ts_code=ts_code, name=real_name, industry=industry,
                     list_status="L", is_cyclical=is_cyc)

    # ── 1. 财务 12 期 ──
    fin = fetch_financial_data(client, ts_code, periods=12)
    fin_rows = []
    for fd in fin:
        fin_rows.append({
            "period": fd.report_period,
            "revenue_yi": round((fd.revenue or 0) / 1e8, 2),
            "net_profit_yi": round((fd.net_profit or 0) / 1e8, 2),
            "eps": fd.eps,
            "roe": fd.roe,
            "operating_cf_yi": round((fd.operating_cf or 0) / 1e8, 2),
            "total_debt_yi": round((fd.total_debt or 0) / 1e8, 2),
            "total_assets_yi": round((fd.total_assets or 0) / 1e8, 2),
            "yoy_rev": None if fd.yoy_revenue_growth is None else round(fd.yoy_revenue_growth * 100, 2),
            "yoy_profit": None if fd.yoy_profit_growth is None else round(fd.yoy_profit_growth * 100, 2),
            "gross_margin": getattr(fd, "grossprofit_margin", None),
            "rd_exp": getattr(fd, "rd_exp", None),
        })
    latest = fin[0]
    total_debt = latest.total_debt or 0.0
    total_assets = latest.total_assets or 0.0
    operating_cf = latest.operating_cf or 0.0
    debt_ratio = total_debt / total_assets if total_assets > 0 else 0.0

    # ── 2. 估值序列 + 分位 ──
    db = _fetch_valuation_df(client, pro, ts_code)
    pe_cur, pe_pct = _pct(db["pe_ttm"])
    pb_cur, pb_pct = _pct(db["pb"])
    ps_cur, ps_pct = _pct(db["ps"])
    mv_cur = pd.to_numeric(db["total_mv"], errors="coerce").dropna().iloc[-1] / 1e4
    close_cur = float(pd.to_numeric(db["close"], errors="coerce").dropna().iloc[-1]) if "close" in db.columns else None
    quantiles = {}
    for label, col in [("pe", "pe_ttm"), ("pb", "pb"), ("ps", "ps")]:
        s = pd.to_numeric(db[col], errors="coerce").dropna()
        quantiles[label] = {str(q): (round(float(s.quantile(q / 100)), 3) if len(s) else None)
                            for q in [10, 25, 50, 75, 90]}

    # 引擎口径: 过滤 NaN PE 行(亏损期 PE 失效), 按日期降序(latest 在首位)——
    # 与 fetch_valuation_history 的行为一致。PE anchor 可能滞后,报告中须标注。
    val_history = [ValuationData(ts_code=ts_code, trade_date=str(r.trade_date),
                                 pe_ttm=float(r.pe_ttm), pb=float(r.pb),
                                 ps=float(r.ps) if not pd.isna(r.ps) else 0.0,
                                 total_mv=float(r.total_mv) if not pd.isna(r.total_mv) else 0.0)
                   for r in db.itertuples()
                   if not (pd.isna(r.pe_ttm) or pd.isna(r.pb))]
    val_history.sort(key=lambda v: v.trade_date, reverse=True)
    pe_anchor_date = val_history[0].trade_date if val_history else None
    if val_history:
        val_score, pe_pct_e, pb_pct_e = calculate_valuation_score(val_history, is_cyc)
    else:
        val_score, pe_pct_e, pb_pct_e = 50.0, 0.5, 0.5

    # ── 3. 趋势 ──
    trend_score = 50.0
    try:
        dates = pd.to_datetime(db["trade_date"], format="%Y%m%d")
        daily_pe = pd.Series(pd.to_numeric(db["pe_ttm"], errors="coerce").values, index=dates, dtype=float)
        daily_pb = pd.Series(pd.to_numeric(db["pb"], errors="coerce").values, index=dates, dtype=float)
        trend_map = batch_trend({ts_code: (daily_pe, daily_pb)}, {ts_code: info})
        trend_score = trend_map.get(ts_code, 50.0)
    except Exception as e:
        logger.warning("趋势计算失败: {}", e)

    # ── 4. 景气度 ──
    prosp = calculate_prosperity_score(fin)
    stage = classify_stock_stage(prosp)

    # ── 5. 困境 ──
    eps_history = [fd.eps for fd in fin]
    roe_history = [fd.roe for fd in fin]
    revenue_growth = [fd.yoy_revenue_growth or 0.0 for fd in fin]
    profit_growth = [fd.yoy_profit_growth or 0.0 for fd in fin]
    distress = calculate_distress_score(
        eps_history=eps_history,
        pe_pct=_safe_pct(pe_pct if pe_pct == pe_pct else None) if pe_cur else 0.5,
        pb_pct=_safe_pct(pb_pct),
        debt_ratio=debt_ratio,
        operating_cf=operating_cf,
        total_debt=total_debt,
        total_assets=total_assets,
        roe_history=roe_history,
        revenue_history=revenue_growth,
        profit_history=profit_growth,
        delta_g=prosp.delta_g,
        ts_code=ts_code,
    )

    # ── 6. Davis ──
    davis = calculate_davis_double_score(
        valuation_score=val_score,
        prosperity_score=prosp.composite_score,
        distress_score=distress.total_score,
        trend_score=trend_score,
        ts_code=ts_code, name=name,
    )

    # ── 7. 5 个补充因子 ──
    def _obj_fields(o, keys):
        out = {}
        if o is None:
            return None
        for k in keys:
            v = getattr(o, k, None)
            out[k] = (round(v, 4) if isinstance(v, float) else v)
        return out

    mom = analyze_momentum(client, ts_code)
    div = analyze_dividend(client, ts_code)
    fc = analyze_forecast(client, ts_code, prosp)
    hc = analyze_holder_concentration(client, ts_code)
    pq = analyze_profitability_quality(fin)

    # ── 8. 股东户数 (pro 直连, 先 dropna 过滤 NaN 垃圾行) ──
    holdernum_rows, top10_rows = [], []
    try:
        h = pro.stk_holdernumber(ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num")
        h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(12)
        prev = None
        for _, r in h.iterrows():
            num = int(r["holder_num"])
            chg = round((num - prev) / prev * 100, 1) if prev else None
            holdernum_rows.append({"end_date": r["end_date"], "ann_date": r["ann_date"],
                                   "holder_num": num, "chg_pct": chg})
            prev = num
    except Exception as e:
        logger.warning("stk_holdernumber 失败: {}", e)
    try:
        for period in [latest.report_period, None]:
            t10 = (pro.top10_floatholders(ts_code=ts_code, period=period)
                   if period else pro.top10_floatholders(ts_code=ts_code))
            if t10 is not None and len(t10):
                t10 = t10.sort_values("hold_ratio", ascending=False)
                for _, r in t10.iterrows():
                    top10_rows.append({"holder_name": r.get("holder_name"),
                                       "hold_ratio": float(r.get("hold_ratio") or 0),
                                       "change": r.get("change"),
                                       "ann_date": str(r.get("ann_date"))})
                break
    except Exception as e:
        logger.warning("top10_floatholders 失败: {}", e)

    # ── 9. 相对估值 ──
    rv_obj = None
    try:
        rv = analyze_relative_valuation(pro, ts_code, name)
        rv_obj = {
            "board": rv.board, "benchmark": rv.benchmark,
            "stock_pe": rv.stock_pe, "index_pe": rv.index_pe,
            "pe_ratio": rv.pe_ratio, "pe_ratio_pct": rv.pe_ratio_pct,
            "pe_ratio_label": rv.pe_ratio_label,
            "erp": rv.erp, "erp_label": rv.erp_label,
            "risk_free_rate": rv.risk_free_rate,
            "stock_pe_pct": rv.stock_pe_pct, "index_pe_pct": rv.index_pe_pct,
            "signals": rv.signals, "composite_verdict": rv.composite_verdict,
        }
    except Exception as e:
        logger.warning("相对估值失败: {}", e)

    # ── 10. forecast 原始 (万元→亿) + 新鲜度 ──
    forecast_raw = []
    try:
        fdf = pro.forecast(ts_code=ts_code, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
        for _, r in fdf.iterrows():
            forecast_raw.append({
                "ann_date": r["ann_date"], "end_date": r["end_date"], "type": r["type"],
                "p_change_min": r["p_change_min"], "p_change_max": r["p_change_max"],
                "net_profit_min_yi": (round(float(r["net_profit_min"]) / 1e4, 2)
                                      if r.get("net_profit_min") and not pd.isna(r["net_profit_min"]) else None),
                "net_profit_max_yi": (round(float(r["net_profit_max"]) / 1e4, 2)
                                      if r.get("net_profit_max") and not pd.isna(r["net_profit_max"]) else None),
            })
    except Exception as e:
        logger.warning("forecast 失败: {}", e)

    freshness = {}
    try:
        db1 = pro.daily_basic(ts_code=ts_code, limit=1)
        freshness["latest_trade_date"] = str(db1.iloc[0]["trade_date"]) if len(db1) else "none"
        inc = pro.income(ts_code=ts_code, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
        freshness["latest_report_period"] = str(inc.iloc[0]["end_date"]) if len(inc) else "none"
        freshness["latest_ann_date"] = str(inc.iloc[0]["ann_date"]) if len(inc) else "none"
    except Exception as e:
        logger.warning("新鲜度校验失败: {}", e)

    # ── 11. 资产负债表补充 (货币资金/借款) ──
    balance_extra = []
    try:
        bs = pro.balancesheet(ts_code=ts_code, start_date=(date.today() - timedelta(days=600)).strftime("%Y%m%d"),
                              fields="ts_code,ann_date,end_date,money_cap,lt_borr,st_borr,note_accounts_dp,inventory,fixed_assets")
        for _, r in bs.sort_values("end_date").tail(6).iterrows():
            balance_extra.append({
                "end_date": r["end_date"],
                "money_cap_yi": round(float(r.get("money_cap") or 0) / 1e8, 1) if not pd.isna(r.get("money_cap")) else None,
                "lt_borr_yi": round(float(r.get("lt_borr") or 0) / 1e8, 1) if not pd.isna(r.get("lt_borr")) else None,
                "st_borr_yi": round(float(r.get("st_borr") or 0) / 1e8, 1) if not pd.isna(r.get("st_borr")) else None,
                "inventory_yi": round(float(r.get("inventory") or 0) / 1e8, 1) if not pd.isna(r.get("inventory")) else None,
                "fixed_assets_yi": round(float(r.get("fixed_assets") or 0) / 1e8, 1) if not pd.isna(r.get("fixed_assets")) else None,
            })
    except Exception as e:
        logger.warning("balancesheet 失败: {}", e)

    return {
        "ts_code": ts_code, "name": name, "industry": industry, "is_cyclical": is_cyc,
        "scored_at": datetime.now().isoformat(),
        "freshness": freshness,
        "latest_report_period": latest.report_period,
        "financial": fin_rows,
        "valuation": {
            "trade_date": str(db["trade_date"].iloc[-1]), "close": close_cur,
            "pe_ttm": pe_cur, "pe_pct": round(pe_pct, 1) if pe_pct == pe_pct else None,
            "pb": round(pb_cur, 3) if pb_cur else None, "pb_pct": round(pb_pct, 1),
            "ps": round(ps_cur, 3) if ps_cur else None, "ps_pct": round(ps_pct, 1),
            "total_mv_yi": round(float(mv_cur), 1),
            "data_points": len(db),
            "quantiles": quantiles,
            "val_score": round(val_score, 2),
            "pe_anchor_date": pe_anchor_date,
            "pe_pct_engine": round(pe_pct_e * 100, 1) if pe_pct_e is not None else None,
            "pb_pct_engine": round(pb_pct_e * 100, 1) if pb_pct_e is not None else None,
        },
        "trend_score": round(trend_score, 2),
        "prosperity": _obj_fields(prosp, ["composite_score", "delta_g", "revenue_score",
                                          "profit_score", "slope_score", "duration_score",
                                          "relative_delta_g"]) | {"stage": stage},
        "distress": _obj_fields(distress, ["total_score", "layer1_score", "layer2_score",
                                           "layer3_score"]),
        "davis": _obj_fields(davis, ["final_score", "rank", "valuation_score",
                                     "prosperity_score", "distress_score", "trend_score"]),
        "factors": {
            "momentum": _obj_fields(mom, ["momentum_score", "absolute_momentum_score",
                                          "rs_percentile", "window_returns"]),
            "dividend": _obj_fields(div, ["dividend_score", "consecutive_years",
                                          "latest_yield_pct", "payout_years"]),
            "forecast": _obj_fields(fc, ["leading_score", "p_change_mid", "type", "is_stale"]),
            "holder": _obj_fields(hc, ["score", "trend", "holder_num_latest",
                                       "holder_num_prev", "pct_change"]),
            "profitability": _obj_fields(pq, ["quality_score", "gross_margin_trend",
                                              "rd_intensity", "data_sufficient"]),
        },
        "holder_number": holdernum_rows,
        "top10_float": top10_rows[:10],
        "relative_valuation": rv_obj,
        "forecast_raw": forecast_raw[:6],
        "balance_extra": balance_extra,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for t in TARGETS:
        logger.info("=" * 66)
        logger.info("{} ({}) 开始取数", t["name"], t["ts_code"])
        try:
            res = score_one(t)
            out = OUT_DIR / f"pvduo_{t['ts_code'].split('.')[0]}.json"
            with open(out, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2, default=str)
            logger.info("✅ {} 完成 → {}", t["name"], out)
            # 摘要打印
            v = res["valuation"]
            print(f"\n### {t['name']} {t['ts_code']} 摘要")
            print(f"  行业={res['industry']} 周期股={res['is_cyclical']} 财务期数={len(res['financial'])}")
            print(f"  估值({v['trade_date']}): PB={v['pb']}({v['pb_pct']}%分位) PS={v['ps']}({v['ps_pct']}%分位) "
                  f"PE={v['pe_ttm']}({v['pe_pct']}%分位) 市值={v['total_mv_yi']}亿 数据点={v['data_points']}")
            print(f"  景气: {res['prosperity']}")
            print(f"  困境: total={res['distress']['total_score']} L1={res['distress']['layer1_score']} "
                  f"L2={res['distress']['layer2_score']} L3={res['distress']['layer3_score']}")
            print(f"  Davis: {res['davis']}  趋势分={res['trend_score']}  估值分={v['val_score']}")
            print(f"  因子: momentum={res['factors']['momentum']}")
            print(f"        dividend={res['factors']['dividend']}")
            print(f"        forecast={res['factors']['forecast']}")
            print(f"        holder={res['factors']['holder']}")
            print(f"        profitability={res['factors']['profitability']}")
            print(f"  股东户数(近5期): {[(r['end_date'], r['holder_num'], r['chg_pct']) for r in res['holder_number'][-5:]]}")
            print(f"  预告: {res['forecast_raw'][:2]}")
            print(f"  新鲜度: {res['freshness']}")
            print(f"  资产负债补充: {res['balance_extra'][-2:]}")
            if res["relative_valuation"]:
                print(f"  相对估值: verdict={res['relative_valuation']['composite_verdict']} "
                      f"signals={res['relative_valuation']['signals']}")
        except Exception:
            logger.exception("{} 取数失败", t["name"])
            sys.exit(1)


if __name__ == "__main__":
    main()
