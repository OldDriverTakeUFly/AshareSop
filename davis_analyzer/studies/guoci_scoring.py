#!/usr/bin/env python3
"""国瓷材料 (300285.SZ) v3.0 数据刷新评分脚本（研报版本更新专用）.

基于 tianyue_scoring.py 模板改造，额外集成：
  - 数据时效校验（income 最新报告期/披露日、forecast 业绩预告、daily_basic 最新交易日）
  - 估值 3 年历史分段拉取（规避 client.get_daily_basic 增量缓存只返回 ~22 天的坑）
  - 五个补充因子引擎（momentum/dividend/forecast/holder_concentration/profitability）
  - 相对估值（stockhot.valuation.analyze_relative_valuation）
  - 行情复核（20260717→20260814 区间收益，复权口径）
  - 股东户数趋势（stk_holdernumber 直连，dropna 防 NaN 垃圾行）

用法（必须从仓库根目录运行）:
    cd /home/leo/Projects/CodeAgentDashboard
    .venv/bin/python davis_analyzer/studies/guoci_scoring.py

输出:
    davis_analyzer/studies/output/guoci_v3_20260814.json
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from loguru import logger

# ── 坑点 1b/2b：stale token 覆盖 + PROJECT_ROOT 固定（必须在 import 引擎前）──
load_dotenv(".env", override=True)
os_proj_root = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = os_proj_root

from davis_analyzer.financial_fetcher import fetch_financial_data  # noqa: E402
from davis_analyzer.forecast import analyze_forecast  # noqa: E402
from davis_analyzer.dividend import analyze_dividend  # noqa: E402
from davis_analyzer.holder_concentration import analyze_holder_concentration  # noqa: E402
from davis_analyzer.momentum import analyze_momentum  # noqa: E402
from davis_analyzer.profitability import analyze_profitability_quality  # noqa: E402
from davis_analyzer.prosperity import calculate_prosperity_score  # noqa: E402
from davis_analyzer.prosperity_sector import classify_stock_stage  # noqa: E402
from davis_analyzer.tushare_client import TushareClient  # noqa: E402
from stockhot.tushare_config import get_pro_api  # noqa: E402
from stockhot.valuation import analyze_relative_valuation  # noqa: E402

# ── 常量 ──
TS_CODE = "300285.SZ"
STOCK_NAME = "国瓷材料"
PERIODS = 12
ASOF = "20260814"
OUTPUT_PATH = Path(f"{os_proj_root}/davis_analyzer/studies/output/guoci_v3_{ASOF}.json")


def freshness_check(pro) -> dict:
    """时效三项：daily_basic 最新交易日 / income 最新报告期 / forecast 预告."""
    out: dict = {}

    db = pro.daily_basic(ts_code=TS_CODE, limit=5)
    db = db.sort_values("trade_date", ascending=False)
    out["latest_trade_dates"] = db["trade_date"].tolist()

    inc = pro.income(
        ts_code=TS_CODE, fields="ts_code,ann_date,end_date,f_ann_date", limit=6
    )
    inc = inc.sort_values("end_date", ascending=False)
    out["income_latest"] = inc[["end_date", "ann_date"]].head(3).to_dict("records")

    fc = pro.forecast(
        ts_code=TS_CODE,
        fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max",
    )
    if len(fc):
        fc = fc.sort_values("ann_date", ascending=False)
        out["forecast_latest"] = fc.head(3).to_dict("records")
    else:
        out["forecast_latest"] = []
    return out


def fetch_daily_basic_3y(pro) -> pd.DataFrame:
    """分段（≤500 天）拉取 3 年 daily_basic，规避增量缓存截断坑."""
    end = datetime.strptime(ASOF, "%Y%m%d").date()
    start = end - timedelta(days=1095)
    frames: list[pd.DataFrame] = []
    cur_start = start
    while cur_start <= end:
        cur_end = min(cur_start + timedelta(days=499), end)
        seg = pro.daily_basic(
            ts_code=TS_CODE,
            start_date=cur_start.strftime("%Y%m%d"),
            end_date=cur_end.strftime("%Y%m%d"),
            fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv,turnover_rate,dv_ttm",
        )
        frames.append(seg)
        cur_start = cur_end + timedelta(days=1)
    df = (
        pd.concat(frames)
        .drop_duplicates("trade_date")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
    # 校验末行确实是最新交易日
    logger.info(
        "daily_basic 拉取 {} 行，首末交易日 {} → {}",
        len(df),
        df["trade_date"].iloc[0],
        df["trade_date"].iloc[-1],
    )
    return df


def percentile_of(series: pd.Series, current: float) -> float:
    s = series.dropna()
    return (s < current).sum() / len(s) * 100


def manual_returns(pro) -> dict:
    """复权口径手工复核多窗口收益（7/17→8/14、60/120/250d）."""
    end = datetime.strptime(ASOF, "%Y%m%d").date()
    start = end - timedelta(days=420)
    px = pro.daily(
        ts_code=TS_CODE,
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        fields="ts_code,trade_date,close",
    )
    af = pro.adj_factor(
        ts_code=TS_CODE,
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    m = px.merge(af[["trade_date", "adj_factor"]], on="trade_date").sort_values(
        "trade_date"
    )
    m["qfq"] = m["close"] * m["adj_factor"]
    m = m.reset_index(drop=True)
    last = m["qfq"].iloc[-1]
    last_date = m["trade_date"].iloc[-1]
    out = {"asof": last_date, "close_raw": float(m["close"].iloc[-1])}
    for w in (20, 60, 120, 250):
        if len(m) > w:
            base = m["qfq"].iloc[-1 - w]
            base_date = m["trade_date"].iloc[-1 - w]
            out[f"ret_{w}d_pct"] = round((last / base - 1) * 100, 2)
            out[f"ret_{w}d_base_date"] = base_date
    # 20260717 → 20260814 区间（研报 v2.0 快照前夜 → v3.0）
    for anchor in ("20260717", "20260720", "20260731", "20260801"):
        sub = m[m["trade_date"] >= anchor]
        if len(sub):
            base = m[m["trade_date"] <= anchor]["qfq"].iloc[-1]
            out[f"ret_since_{anchor}_pct"] = round((last / base - 1) * 100, 2)
    # 提价公告次日（7/21 为公告后首个交易日）涨跌幅
    d21 = m[m["trade_date"] == "20260721"]
    d20 = m[m["trade_date"] == "20260720"]
    if len(d21) and len(d20):
        out["chg_20260721_pct"] = round(
            (float(d21["close"].iloc[0]) / float(d20["close"].iloc[0]) - 1) * 100, 2
        )
    # 8 月涨幅（7/31 收盘 → 8/14）
    aug_base = m[m["trade_date"] <= "20260731"]["qfq"].iloc[-1]
    out["ret_aug_pct"] = round((last / aug_base - 1) * 100, 2)
    return out


def holder_trend_manual(pro) -> dict:
    """股东户数近 8 期 + 环比（dropna 防垃圾行）."""
    h = pro.stk_holdernumber(
        ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num"
    )
    h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(8)
    rows = []
    prev = None
    for _, r in h.iterrows():
        num = int(r["holder_num"])
        chg = (num - prev) / prev * 100 if prev else None
        rows.append(
            {
                "end_date": r["end_date"],
                "ann_date": r["ann_date"],
                "holder_num": num,
                "chg_pct": round(chg, 2) if chg is not None else None,
            }
        )
        prev = num
    return {"rows": rows, "latest": rows[-1] if rows else None}


def main() -> None:
    result: dict = {
        "ts_code": TS_CODE,
        "name": STOCK_NAME,
        "asof": ASOF,
        "generated_at": datetime.now().isoformat(),
    }

    client = TushareClient()
    pro = get_pro_api(timeout=30)

    # ── 0. 时效校验 ──
    logger.info("Step 0: 时效校验...")
    result["freshness"] = freshness_check(pro)
    logger.info("时效: {}", result["freshness"])

    # ── 1. 财务 ──
    logger.info("Step 1: 财务数据 {} 期...", PERIODS)
    fin = fetch_financial_data(client, TS_CODE, periods=PERIODS)
    assert fin and fin[0].ts_code == TS_CODE, "ts_code 张冠李戴检查失败"
    result["financial"] = [
        {
            "report_period": f.report_period,
            "revenue_yi": round((f.revenue or 0) / 1e8, 2),
            "net_profit_yi": round(float(f.net_profit or 0) / 1e8, 2),
            "yoy_rev_pct": round(f.yoy_revenue_growth * 100, 2)
            if f.yoy_revenue_growth is not None
            else None,
            "yoy_prof_pct": round(f.yoy_profit_growth * 100, 2)
            if f.yoy_profit_growth is not None
            else None,
            "roe": f.roe,
        }
        for f in fin
    ]
    for row in result["financial"][:6]:
        logger.info(
            "  {} rev={}亿 np={}亿 yoy_rev={}%",
            row["report_period"],
            row["revenue_yi"],
            row["net_profit_yi"],
            row["yoy_rev_pct"],
        )

    # ── 2. 估值（3 年分段拉取 + 分位）──
    logger.info("Step 2: 估值历史与分位...")
    db = fetch_daily_basic_3y(pro)
    assert len(db) >= 700, f"daily_basic 行数 {len(db)} < 700，增量缓存截断风险"
    pe = pd.to_numeric(db["pe_ttm"], errors="coerce")
    pb = pd.to_numeric(db["pb"], errors="coerce")
    ps = pd.to_numeric(db["ps"], errors="coerce")
    mv = pd.to_numeric(db["total_mv"], errors="coerce")
    dv = pd.to_numeric(db["dv_ttm"], errors="coerce")
    latest_row = db.iloc[-1]
    result["valuation"] = {
        "trade_date": latest_row["trade_date"],
        "pe_ttm": float(pe.iloc[-1]),
        "pb": float(pb.iloc[-1]),
        "ps": float(ps.iloc[-1]),
        "total_mv_yi": round(float(mv.iloc[-1]) / 1e4, 1),
        "dv_ttm_pct": float(dv.iloc[-1]) if pd.notna(dv.iloc[-1]) else None,
        "pe_pct": round(percentile_of(pe, float(pe.iloc[-1])), 1),
        "pb_pct": round(percentile_of(pb, float(pb.iloc[-1])), 1),
        "ps_pct": round(percentile_of(ps, float(ps.iloc[-1])), 1),
        "mv_pct": round(percentile_of(mv, float(mv.iloc[-1])), 1),
        "pe_quantiles": {
            str(q): round(float(pe.quantile(q / 100)), 2) for q in (10, 25, 50, 75, 90, 95)
        },
        "n_days": len(db),
        # v2.0 (20260731) 时点对照
        "snapshot_20260731": {
            row["trade_date"]: {
                "pe_ttm": float(row["pe_ttm"]) if pd.notna(row["pe_ttm"]) else None,
                "total_mv_yi": round(float(row["total_mv"]) / 1e4, 1)
                if pd.notna(row["total_mv"])
                else None,
            }
            for _, row in db.iterrows()
            if row["trade_date"] in ("20260717", "20260731", "20260720")
        },
    }
    logger.info(
        "估值@{}: PE={:.1f} ({}%分位) PB={:.2f} ({}%分位) PS={:.2f} 市值={}亿",
        latest_row["trade_date"],
        result["valuation"]["pe_ttm"],
        result["valuation"]["pe_pct"],
        result["valuation"]["pb"],
        result["valuation"]["pb_pct"],
        result["valuation"]["ps"],
        result["valuation"]["total_mv_yi"],
    )

    # ── 3. 景气度 ──
    logger.info("Step 3: 景气度...")
    pscore = calculate_prosperity_score(fin)
    stage = classify_stock_stage(pscore)
    result["prosperity"] = {
        "composite_score": round(pscore.composite_score, 2),
        "delta_g": round(pscore.delta_g, 2),
        "revenue_score": round(pscore.revenue_score, 2),
        "profit_score": round(pscore.profit_score, 2),
        "slope_score": round(pscore.slope_score, 2),
        "duration_score": round(pscore.duration_score, 2),
        "stage": stage,
    }
    logger.info("景气度: {}", result["prosperity"])

    # ── 4. 五因子 ──
    logger.info("Step 4: 五个补充因子...")
    mom = analyze_momentum(client, TS_CODE)
    if mom is not None:
        result["momentum"] = {
            "momentum_score": mom.momentum_score,
            "absolute_momentum_score": mom.absolute_momentum_score,
            "rs_percentile": mom.rs_percentile,
            "window_returns": mom.window_returns,
        }
    div = analyze_dividend(client, TS_CODE)
    result["dividend"] = {
        "dividend_score": div.dividend_score,
        "consecutive_years": div.consecutive_years,
        "latest_yield_pct": div.latest_yield_pct,
        "payout_years": div.payout_years,
    }
    fc = analyze_forecast(client, TS_CODE, pscore)  # 第三参必须是 ProsperityScore 对象
    result["forecast_engine"] = (
        {
            "leading_score": fc.leading_score,
            "p_change_mid": fc.p_change_mid,
            "type": fc.type,
            "is_stale": fc.is_stale,
        }
        if fc is not None
        else None
    )
    hc = analyze_holder_concentration(client, TS_CODE)
    if hc is not None:
        result["holder_concentration"] = {
            "concentration_score": hc.concentration_score,
            "latest_chg_pct": hc.latest_chg_pct,
            "trend": hc.trend,
            "holder_counts": hc.holder_counts,
            "periods": hc.periods,
        }
    pq = analyze_profitability_quality(fin)
    result["profitability"] = {
        "quality_score": pq.quality_score,
        "latest_gross_margin": pq.latest_gross_margin,
        "gross_margin_delta": pq.gross_margin_delta,
        "latest_rd_intensity": pq.latest_rd_intensity,
    }
    logger.info(
        "五因子: momentum={} dividend={} forecast={} holder={} profitability={}",
        result["momentum"]["momentum_score"] if "momentum" in result else None,
        div.dividend_score,
        fc.leading_score if fc else None,
        hc.concentration_score if hc else None,
        pq.quality_score,
    )

    # ── 5. 相对估值 ──
    logger.info("Step 5: 相对估值（stockhot.valuation）...")
    try:
        rv = analyze_relative_valuation(pro, TS_CODE, STOCK_NAME)
        result["relative_valuation"] = {
            "benchmark": getattr(rv, "benchmark", None),
            "index_pe": getattr(rv, "index_pe", None),
            "index_pe_pct": getattr(rv, "index_pe_pct", None),
            "stock_pe": getattr(rv, "stock_pe", None),
            "stock_pe_pct": getattr(rv, "stock_pe_pct", None),
            "pe_ratio": getattr(rv, "pe_ratio", None),
            "pe_ratio_pct": getattr(rv, "pe_ratio_pct", None),
            "erp": getattr(rv, "erp", None),
            "risk_free_rate": getattr(rv, "risk_free_rate", None),
            "quadrant": getattr(rv, "quadrant", None),
            "quadrant_label": getattr(rv, "quadrant_label", None),
            "composite_verdict": getattr(rv, "composite_verdict", None),
            "signals": getattr(rv, "signals", None),
        }
        logger.info("相对估值: {}", result["relative_valuation"])
    except Exception:
        logger.exception("相对估值失败")
        result["relative_valuation"] = None

    # ── 6. 行情复核（复权收益）──
    logger.info("Step 6: 行情复核...")
    result["price_action"] = manual_returns(pro)
    logger.info("行情: {}", result["price_action"])

    # ── 7. 股东户数手工趋势 ──
    logger.info("Step 7: 股东户数趋势...")
    result["holder_trend_manual"] = holder_trend_manual(pro)
    logger.info("户数: {}", result["holder_trend_manual"]["rows"][-3:])

    # ── 输出 ──
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info("✅ 完成，输出 {}", OUTPUT_PATH)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
