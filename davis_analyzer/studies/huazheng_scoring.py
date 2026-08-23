#!/usr/bin/env python3
"""华正新材 (603186.SH) 单股戴维斯双击评分 + 研报取数脚本.

复制自 tianyue_scoring.py 模板，按 engine-usage.md 坑点清单加固：
  - load_dotenv(override=True) 防 stale token；PROJECT_ROOT 重新钉扎
  - daily_basic 分段直连（≤500 天/段）防 SQLite 增量缓存只回 22 天
  - 附带：股东户数/十大流通股东/分红/业绩预告时效校验/5 因子/相对估值

用法（仓库根目录）:
    .venv/bin/python davis_analyzer/studies/huazheng_scoring.py

输出:
    .sisyphus/evidence/huazheng/huazheng-score.json + stdout 摘要
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

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)  # 坑点 1b：强制 .env 的新 token
os.environ["PROJECT_ROOT"] = os.getcwd()  # 坑点 2：防 .env 的 /app 值

# ── davis_analyzer 核心模块（只读调用，不修改源码）──
from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import (
    batch_trend,
    calculate_monthly_trend,
    calculate_trend_acceleration,
    calculate_trend_slope,
)
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo, ValuationData
from davis_analyzer.valuation import (
    calculate_valuation_score,
    detect_cyclical,
)

# ── 常量 ──
TS_CODE = "603186.SH"
STOCK_NAME = "华正新材"
PERIODS = 12
OUTPUT_PATH = Path(".sisyphus/evidence/huazheng/huazheng-score.json")


def fetch_daily_basic_full(pro, ts_code: str, days: int = 1095) -> pd.DataFrame:
    """分段直连 pro.daily_basic，拼 3 年估值序列（坑点：增量缓存只回 22 天）."""
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    frames = []
    seg_start = date.today() - timedelta(days=days)
    while seg_start <= date.today():
        seg_end = min(seg_start + timedelta(days=499), date.today())
        df = pro.daily_basic(
            ts_code=ts_code,
            start_date=seg_start.strftime("%Y%m%d"),
            end_date=seg_end.strftime("%Y%m%d"),
            fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,turnover_rate,dv_ttm",
        )
        if len(df):
            frames.append(df)
        seg_start = seg_end + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    out = (
        pd.concat(frames)
        .drop_duplicates("trade_date")
        .reset_index(drop=True)  # 坑点：concat 重复 index
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
    return out


def build_stock_info(client: TushareClient, ts_code: str, name: str) -> StockInfo:
    stock_df = client.get_stock_list()
    row = stock_df[stock_df["ts_code"] == ts_code]
    if not row.empty:
        industry = str(row.iloc[0].get("industry", "") or "")
        real_name = str(row.iloc[0].get("name", name) or name)
    else:
        industry, real_name = "", name
    return StockInfo(
        ts_code=ts_code,
        name=real_name,
        industry=industry,
        list_status="L",
        is_cyclical=detect_cyclical(industry),
    )


def main() -> None:
    logger.info("=" * 70)
    logger.info("华正新材 (603186.SH) 戴维斯双击评分 + 研报取数")
    logger.info("=" * 70)

    client = TushareClient()
    from stockhot.tushare_config import get_pro_api

    pro = get_pro_api(timeout=30)

    # ── 0. 代码核对（坑点 2b：防张冠李戴）──
    basic = pro.stock_basic(ts_code=TS_CODE, fields="ts_code,name,industry,market,list_date")
    logger.info("代码核对: {} -> {} ({}) 行业={}", TS_CODE, basic.iloc[0]["name"],
                basic.iloc[0]["market"], basic.iloc[0]["industry"])

    # ── 1. 财务数据（12 期）──
    fin_data = fetch_financial_data(client, TS_CODE, periods=PERIODS)
    if not fin_data:
        logger.error("财务数据为空")
        sys.exit(1)
    logger.info("获取 {} 期财务数据，最新期 {}", len(fin_data), fin_data[0].report_period)
    fin_rows = []
    for fd in fin_data:
        fin_rows.append({
            "period": fd.report_period,
            "rev_yi": round((fd.revenue or 0) / 1e8, 3),
            "np_yi": round((fd.net_profit or 0) / 1e8, 3),
            "eps": fd.eps,
            "roe": fd.roe,
            "ocf_yi": round((fd.operating_cf or 0) / 1e8, 3),
            "debt_assets": round((fd.total_debt or 0) / (fd.total_assets or 1) * 100, 2),
            "yoy_rev": round(fd.yoy_revenue_growth * 100, 2) if fd.yoy_revenue_growth is not None else None,
            "yoy_np": round(fd.yoy_profit_growth * 100, 2) if fd.yoy_profit_growth is not None else None,
            "gross_margin": fd.grossprofit_margin,
            "rd_exp_yi": round((fd.rd_exp or 0) / 1e8, 3) if fd.rd_exp else None,
        })
        logger.info("  {} rev={:.2f}亿 np={:.3f}亿 yoy_rev={} yoy_np={}",
                    fd.report_period, (fd.revenue or 0) / 1e8, (fd.net_profit or 0) / 1e8,
                    f"{fd.yoy_revenue_growth*100:.1f}%" if fd.yoy_revenue_growth is not None else "NA",
                    f"{fd.yoy_profit_growth*100:.1f}%" if fd.yoy_profit_growth is not None else "NA")

    eps_history = [fd.eps for fd in fin_data]
    roe_history = [fd.roe for fd in fin_data]
    revenue_growth = [fd.yoy_revenue_growth or 0.0 for fd in fin_data]
    profit_growth = [fd.yoy_profit_growth or 0.0 for fd in fin_data]
    latest = fin_data[0]
    total_debt = latest.total_debt or 0.0
    total_assets = latest.total_assets or 0.0
    operating_cf = latest.operating_cf or 0.0
    debt_ratio = total_debt / total_assets if total_assets > 0 else 0.0

    # ── 2. 估值：分段直连 3 年 daily_basic ──
    db = fetch_daily_basic_full(pro, TS_CODE)
    logger.info("daily_basic 3年序列: {} 行，首 {} 末 {}", len(db),
                db["trade_date"].iloc[0] if len(db) else "-", db["trade_date"].iloc[-1] if len(db) else "-")
    if len(db) < 700:
        logger.warning("⚠ daily_basic 行数 {} < 700，分位可能失真！", len(db))

    pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
    pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
    ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
    mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
    dv = pd.to_numeric(db["dv_ttm"], errors="coerce").dropna()

    pe_cur = pe.iloc[-1] if len(pe) else None
    pb_cur = pb.iloc[-1] if len(pb) else None
    ps_cur = ps.iloc[-1] if len(ps) else None
    trade_date_cur = db["trade_date"].iloc[-1]
    logger.info("估值快照 {}: PE={} PB={} PS={} 市值={:.1f}亿 股息率TTM={}",
                trade_date_cur, pe_cur, pb_cur, ps_cur, mv.iloc[-1] / 1e4,
                dv.iloc[-1] if len(dv) else "NA")

    def pct_report(series, cur, label):
        if cur is None or not len(series):
            return None
        p = (series < cur).sum() / len(series) * 100
        qs = {q: round(series.quantile(q / 100), 3) for q in [10, 25, 50, 75, 90, 95]}
        logger.info("{} 当前={} 分位={:.1f}% 分位值={}", label, round(cur, 3), p, qs)
        return {"current": round(float(cur), 3), "pct": round(p, 1), "quantiles": qs}

    val_stats = {
        "snapshot_date": trade_date_cur,
        "market_cap_yi": round(float(mv.iloc[-1]) / 1e4, 1) if len(mv) else None,
        "pe": pct_report(pe, pe_cur, "PE_TTM"),
        "pb": pct_report(pb, pb_cur, "PB"),
        "ps": pct_report(ps, ps_cur, "PS"),
        "dv_ttm": round(float(dv.iloc[-1]), 3) if len(dv) else None,
        "n_days": len(db),
    }

    # 手工构造 ValuationData（过滤 NaN，降序，latest 在首位）
    val_rows = []
    for _, r in db.iterrows():
        if pd.isna(r["pe_ttm"]) or pd.isna(r["pb"]):
            continue
        val_rows.append(ValuationData(
            ts_code=TS_CODE, trade_date=str(r["trade_date"]),
            pe_ttm=float(r["pe_ttm"]), pb=float(r["pb"]),
            ps=float(r["ps"]) if not pd.isna(r["ps"]) else 0.0,
            total_mv=float(r["total_mv"]) if not pd.isna(r["total_mv"]) else 0.0,
        ))
    val_rows.sort(key=lambda v: v.trade_date, reverse=True)
    val_history = val_rows
    logger.info("ValuationData 构造 {} 条（过滤 NaN PE 后）", len(val_history))

    stock_info = build_stock_info(client, TS_CODE, STOCK_NAME)
    logger.info("行业={} 周期判定={}", stock_info.industry, stock_info.is_cyclical)

    if val_history:
        val_score, pe_pct, pb_pct = calculate_valuation_score(val_history, stock_info.is_cyclical)
    else:
        val_score, pe_pct, pb_pct = 50.0, 0.5, 0.5
    logger.info("估值评分={:.2f} PE分位={:.1f}% PB分位={:.1f}%", val_score, pe_pct * 100, pb_pct * 100)

    # ── 3. 景气度 ──
    prosp_score = calculate_prosperity_score(fin_data)
    logger.info("景气度: composite={:.2f} revenue={:.2f} profit={:.2f} slope={:.2f} duration={:.2f} delta_g={:.2f}",
                prosp_score.composite_score, prosp_score.revenue_score, prosp_score.profit_score,
                prosp_score.slope_score, prosp_score.duration_score, prosp_score.delta_g)

    # ── 4. 趋势 ──
    trend_score = 50.0
    trend_detail = {"score": 50.0, "reason": "数据不足"}
    if val_history and len(val_history) >= 3:
        try:
            dates = pd.to_datetime([v.trade_date for v in val_history], format="%Y%m%d")
            daily_pe = pd.Series([v.pe_ttm for v in val_history], index=dates)
            daily_pb = pd.Series([v.pb for v in val_history], index=dates)
            trend_map = batch_trend({TS_CODE: (daily_pe, daily_pb)}, {TS_CODE: stock_info})
            trend_score = trend_map.get(TS_CODE, 50.0)
            monthly_pe, monthly_pb = calculate_monthly_trend(daily_pe, daily_pb)
            trend_detail = {
                "score": round(trend_score, 2),
                "pe_slope": round(calculate_trend_slope(monthly_pe), 4),
                "pb_slope": round(calculate_trend_slope(monthly_pb), 4),
                "pe_accel": round(calculate_trend_acceleration(monthly_pe), 4),
                "pb_accel": round(calculate_trend_acceleration(monthly_pb), 4),
            }
            logger.info("趋势评分={:.2f} 详情={}", trend_score, trend_detail)
        except Exception:
            logger.exception("趋势评分失败，用 50.0")

    # ── 5. 困境 ──
    distress = calculate_distress_score(
        eps_history=eps_history, pe_pct=pe_pct, pb_pct=pb_pct, debt_ratio=debt_ratio,
        operating_cf=operating_cf, total_debt=total_debt, total_assets=total_assets,
        roe_history=roe_history, revenue_history=revenue_growth, profit_history=profit_growth,
        delta_g=prosp_score.delta_g, ts_code=TS_CODE,
    )
    logger.info("困境评分: total={:.2f} L1={:.2f} L2={:.2f} L3={:.2f}",
                distress.total_score, distress.layer1_score, distress.layer2_score, distress.layer3_score)

    # ── 6. 戴维斯双击 ──
    davis = calculate_davis_double_score(
        valuation_score=val_score, prosperity_score=prosp_score.composite_score,
        distress_score=distress.total_score, trend_score=trend_score,
        ts_code=TS_CODE, name=STOCK_NAME,
    )
    logger.info("戴维斯双击 final={:.2f}", davis.final_score)

    # ── 7. 时效校验 ──
    inc = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
    fc = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min(net_profit_min),net_profit_max(net_profit_max)".replace("(net_profit_min)", "").replace("(net_profit_max)", ""))
    freshness = {
        "latest_income_period": str(inc.iloc[0]["end_date"]) if len(inc) else None,
        "latest_income_ann": str(inc.iloc[0]["ann_date"]) if len(inc) else None,
        "forecast": fc.head(3).to_dict("records") if len(fc) else None,
    }
    logger.info("时效: 最新报告期 {} (披露 {}), 预告 {}", freshness["latest_income_period"],
                freshness["latest_income_ann"], freshness["forecast"])

    # ── 8. 股东户数 ──
    holder = {}
    try:
        h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num")
        h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(8)
        rows = []
        prev = None
        for _, r in h.iterrows():
            num = int(r["holder_num"])
            chg = (num - prev) / prev * 100 if prev else None
            rows.append({"end_date": r["end_date"], "ann_date": r["ann_date"],
                         "holder_num": num, "chg_pct": round(chg, 2) if chg else None})
            prev = num
        holder["rows"] = rows
        logger.info("股东户数: {}", rows)
    except Exception:
        logger.exception("股东户数取数失败")
        holder["rows"] = None

    # ── 9. 十大流通股东 ──
    top10 = {}
    try:
        t = pro.top10_floatholders(ts_code=TS_CODE, period="20260331")
        top10["20260331"] = t[["holder_name", "hold_ratio"]].to_dict("records") if len(t) else []
        t2 = pro.top10_floatholders(ts_code=TS_CODE, period="20251231")
        top10["20251231"] = t2[["holder_name", "hold_ratio"]].to_dict("records") if len(t2) else []
        logger.info("十大流通股东 2026Q1 前3: {}", top10["20260331"][:3])
    except Exception:
        logger.exception("十大流通股东取数失败")

    # ── 10. 分红 ──
    dividend_hist = {}
    try:
        d = pro.dividend(ts_code=TS_CODE, fields="ts_code,end_date,ann_date,div_proc,cash_div_tax,cash_div,base_share")
        d_impl = d[d["div_proc"] == "实施"].sort_values("end_date").tail(8)
        dividend_hist["impl"] = d_impl.to_dict("records")
        logger.info("分红(实施): {}", dividend_hist["impl"])
    except Exception:
        logger.exception("分红取数失败")

    # ── 11. 五因子 ──
    factors = {}
    try:
        mom = analyze_momentum(client, TS_CODE)
        if mom:
            wr = {k: (round(v * 100, 2) if v is not None else None) for k, v in mom.window_returns.items()} if mom.window_returns else None
            factors["momentum"] = {"score": mom.momentum_score, "abs_score": mom.absolute_momentum_score,
                                   "rs_percentile": mom.rs_percentile, "window_returns_pct": wr}
            logger.info("动量: {}", factors["momentum"])
    except Exception:
        logger.exception("动量失败")
    try:
        div = analyze_dividend(client, TS_CODE)
        if div:
            factors["dividend"] = {"score": div.dividend_score, "consecutive_years": div.consecutive_years,
                                   "latest_yield_pct": div.latest_yield_pct}
            logger.info("红利: {}", factors["dividend"])
    except Exception:
        logger.exception("红利失败")
    try:
        fcs = analyze_forecast(client, TS_CODE, prosp_score)
        if fcs:
            factors["forecast"] = {"leading_score": fcs.leading_score, "p_change_mid": fcs.p_change_mid,
                                   "type": fcs.type, "is_stale": fcs.is_stale}
            logger.info("预告: {}", factors["forecast"])
        rev = analyze_forecast_revision(client, TS_CODE)
        if rev:
            factors["forecast_revision"] = {"direction": rev.revision_direction, "pp": rev.revision_pp,
                                            "score": rev.revision_score}
    except Exception:
        logger.exception("预告失败")
    try:
        hc = analyze_holder_concentration(client, TS_CODE)
        if hc:
            factors["holder_conc"] = {"score": hc.concentration_score, "trend": hc.trend,
                                      "latest_chg_pct": hc.latest_chg_pct,
                                      "holder_counts": hc.holder_counts, "periods": hc.periods}
            logger.info("筹码: {}", {k: v for k, v in factors["holder_conc"].items() if k != "holder_counts"})
    except Exception:
        logger.exception("筹码失败")
    try:
        pq = analyze_profitability_quality(fin_data)
        factors["profitability"] = {"score": pq.quality_score,
                                    "latest_gross_margin": pq.latest_gross_margin,
                                    "gross_margin_delta": pq.gross_margin_delta,
                                    "latest_rd_intensity": pq.latest_rd_intensity}
        logger.info("盈利质量: {}", factors["profitability"])
    except Exception:
        logger.exception("盈利质量失败")

    # ── 12. 价格区间（人工复权复核动量用）──
    price = {}
    try:
        px = pro.daily(ts_code=TS_CODE, start_date="20250801", end_date=date.today().strftime("%Y%m%d"),
                       fields="trade_date,close,pre_close,pct_chg")
        px = px.sort_values("trade_date").reset_index(drop=True)
        for label, n in [("20d", 20), ("60d", 60), ("120d", 120), ("250d", 250)]:
            if len(px) > n:
                price[label] = round((px["close"].iloc[-1] / px["close"].iloc[-1 - n] - 1) * 100, 2)
        price["latest_close"] = float(px["close"].iloc[-1])
        price["latest_date"] = str(px["trade_date"].iloc[-1])
        # 52 周高低
        year_px = px.tail(250)
        price["high_52w"] = float(year_px["close"].max())
        price["low_52w"] = float(year_px["close"].min())
        logger.info("价格区间: {}", price)
    except Exception:
        logger.exception("价格取数失败")

    # ── 13. 相对估值 ──
    rel_val = {}
    try:
        from stockhot.valuation import analyze_relative_valuation
        rv = analyze_relative_valuation(pro, TS_CODE, STOCK_NAME)
        rel_val = {
            "pe_ratio": rv.pe_ratio, "pe_ratio_pct": rv.pe_ratio_pct,
            "index_pe": rv.index_pe, "index_pe_pct": rv.index_pe_pct,
            "erp_pct": round(rv.erp * 100, 2) if rv.erp is not None else None,
            "risk_free_rate_pct": round(rv.risk_free_rate * 100, 2) if rv.risk_free_rate is not None else None,
            "quadrant": rv.quadrant, "quadrant_label": rv.quadrant_label,
            "composite_verdict": rv.composite_verdict, "signals": rv.signals,
        }
        logger.info("相对估值: {}", rel_val)
    except Exception:
        logger.exception("相对估值失败")

    # ── 组装输出 ──
    result = {
        "ts_code": TS_CODE, "name": STOCK_NAME,
        "basic": basic.to_dict("records")[0] if len(basic) else {},
        "financials": fin_rows,
        "valuation": val_stats,
        "valuation_score": {"score": round(val_score, 2), "pe_pct": round(pe_pct, 4), "pb_pct": round(pb_pct, 4),
                            "is_cyclical": stock_info.is_cyclical, "industry": stock_info.industry},
        "prosperity": {"composite": prosp_score.composite_score, "revenue": prosp_score.revenue_score,
                       "profit": prosp_score.profit_score, "slope": prosp_score.slope_score,
                       "duration": prosp_score.duration_score, "delta_g": prosp_score.delta_g},
        "trend": trend_detail,
        "distress": {"total": distress.total_score, "l1": distress.layer1_score,
                     "l2": distress.layer2_score, "l3": distress.layer3_score,
                     "signals_detail": distress.signals_detail},
        "davis_double": {"final": davis.final_score},
        "freshness": freshness,
        "holders": holder,
        "top10_float": top10,
        "dividend_impl": dividend_hist.get("impl"),
        "factors": factors,
        "price": price,
        "relative_valuation": rel_val,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    logger.info("✅ 完成，写入 {}", OUTPUT_PATH)
    print(json.dumps({k: result[k] for k in ["valuation", "prosperity", "davis_double", "freshness", "price", "relative_valuation"]},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
