#!/usr/bin/env python3
"""猪企三标的(神农集团/巨星农牧/华统股份)一体化取数脚本.

对 3 只猪企执行:
  1. 完整四维评分(估值/趋势/景气/困境 → davis_final)
  2. 5 补充因子(momentum/dividend/forecast/holder_concentration/profitability)
  3. 股东户数趋势 + 十大流通股东
  4. 相对市场估值锚定(stockhot.valuation.analyze_relative_valuation)
  5. 数据时效性校验(daily_basic 最新交易日/income 最新报告期/forecast 预告)

用法(从父仓库根目录):
    .venv/bin/python davis_analyzer/studies/pig3_scoring.py

输出:
    davis_analyzer/studies/output/pig3/{ts_code}.json
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from loguru import logger

# ── 环境准备:先 load_dotenv(override 防 stale token),再 pin PROJECT_ROOT ──
import os
import sys

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import batch_trend
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo
from davis_analyzer.valuation import (
    calculate_percentile,
    detect_cyclical,
)

STOCKS = [
    {"ts_code": "605296.SH", "name": "神农集团"},
    {"ts_code": "603477.SH", "name": "巨星农牧"},
    {"ts_code": "002840.SZ", "name": "华统股份"},
]

OUTPUT_DIR = Path(PROJECT_ROOT) / "davis_analyzer" / "studies" / "output" / "pig3"
PERIODS = 12
# 估值历史起点(近似上市/借壳日,拉全历史做分位)
HIST_START = {
    "605296.SH": "20210401",  # 神农集团 2021-04 上市(次新)
    "603477.SH": "20190101",  # 巨星农牧 2019 借壳振静股份(皮革老股更早,2017 上市)
    "002840.SZ": "20170101",  # 华统股份 2017 上市
}


def _fmt_yi(v) -> str:
    """元 → 亿元字符串."""
    if v is None:
        return "N/A"
    return f"{float(v) / 1e8:.2f}"


def score_stock(client: TushareClient, pro, ts_code: str, name: str) -> dict:
    """单股完整评分,返回 JSON 可序列化 dict."""
    result: dict = {"ts_code": ts_code, "name": name, "scored_at": datetime.now().isoformat()}

    # ── 1. 时效性校验 ──
    try:
        db = pro.daily_basic(ts_code=ts_code, limit=1)
        latest_trade = db.iloc[0]["trade_date"] if len(db) else "none"
        latest_close = float(db.iloc[0]["close"]) if len(db) else None
        latest_turnover_rate = float(db.iloc[0]["turnover_rate"]) if len(db) and db.iloc[0].get("turnover_rate") else None
        inc = pro.income(ts_code=ts_code, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
        latest_period = inc.iloc[0]["end_date"] if len(inc) else "none"
        latest_ann = inc.iloc[0]["ann_date"] if len(inc) else "none"
        result["freshness"] = {
            "latest_trade_date": latest_trade,
            "latest_close": latest_close,
            "latest_turnover_rate": latest_turnover_rate,
            "latest_report_period": latest_period,
            "latest_ann_date": latest_ann,
        }
        logger.info("[{}] 估值快照 {} 收盘 {} | 财务最新 {} ({} 披露)", name, latest_trade, latest_close, latest_period, latest_ann)
    except Exception as e:
        logger.warning("时效性校验失败: {}", e)
        result["freshness"] = {"error": str(e)}

    # ── 2. 业绩预告(forecast,注意单位是万元!) ──
    try:
        fc_df = pro.forecast(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max",
        )
        fc_rows = []
        for _, r in fc_df.head(6).iterrows():
            np_min_yi = float(r["net_profit_min"]) / 1e4 if pd.notna(r.get("net_profit_min")) else None
            np_max_yi = float(r["net_profit_max"]) / 1e4 if pd.notna(r.get("net_profit_max")) else None
            fc_rows.append(
                {
                    "ann_date": r["ann_date"],
                    "end_date": r["end_date"],
                    "type": r["type"],
                    "p_change_min": float(r["p_change_min"]) if pd.notna(r.get("p_change_min")) else None,
                    "p_change_max": float(r["p_change_max"]) if pd.notna(r.get("p_change_max")) else None,
                    "net_profit_min_yi": np_min_yi,
                    "net_profit_max_yi": np_max_yi,
                }
            )
        result["raw_forecast"] = fc_rows
        for r in fc_rows[:3]:
            logger.info(
                "[{}] 预告: {} {} type={} 同比[{},{}]% 净利[{},{}]亿",
                name, r["ann_date"], r["end_date"], r["type"],
                r["p_change_min"], r["p_change_max"], r["net_profit_min_yi"], r["net_profit_max_yi"],
            )
    except Exception as e:
        logger.warning("forecast 取数失败: {}", e)
        result["raw_forecast"] = []

    # ── 3. 财务数据(12 期) ──
    fin_data = fetch_financial_data(client, ts_code, periods=PERIODS)
    if not fin_data:
        logger.error("[{}] 财务数据为空", name)
        return result
    result["financials"] = [
        {
            "report_period": fd.report_period,
            "revenue_yi": round(float(fd.revenue or 0) / 1e8, 2),
            "net_profit_yi": round(float(fd.net_profit or 0) / 1e8, 2),
            "eps": fd.eps,
            "roe": fd.roe,
            "operating_cf_yi": round(float(fd.operating_cf or 0) / 1e8, 2),
            "total_debt_yi": round(float(fd.total_debt or 0) / 1e8, 2),
            "total_assets_yi": round(float(fd.total_assets or 0) / 1e8, 2),
            "debt_ratio": round(float(fd.total_debt or 0) / float(fd.total_assets or 1) * 100, 1),
            "yoy_revenue_growth_pct": round(fd.yoy_revenue_growth * 100, 1) if fd.yoy_revenue_growth is not None else None,
            "yoy_profit_growth_pct": round(fd.yoy_profit_growth * 100, 1) if fd.yoy_profit_growth is not None else None,
            "grossprofit_margin": fd.grossprofit_margin,
            "rd_exp": fd.rd_exp,
        }
        for fd in fin_data
    ]
    logger.info("[{}] 财务 {} 期,最新 {} 营收 {} 亿 净利 {} 亿", name, len(fin_data), fin_data[0].report_period,
                _fmt_yi(fin_data[0].revenue), _fmt_yi(fin_data[0].net_profit))

    eps_history = [fd.eps for fd in fin_data]
    roe_history = [fd.roe for fd in fin_data]
    revenue_growth = [fd.yoy_revenue_growth or 0.0 for fd in fin_data]
    profit_growth = [fd.yoy_profit_growth or 0.0 for fd in fin_data]
    latest = fin_data[0]
    total_debt = latest.total_debt or 0.0
    total_assets = latest.total_assets or 0.0
    operating_cf = latest.operating_cf or 0.0
    debt_ratio = total_debt / total_assets if total_assets > 0 else 0.0

    # ── 4. 估值历史 + 分位(直接调 pro.daily_basic 全历史,绕过稀疏缓存) ──
    end = date.today().strftime("%Y%m%d")
    hist_start = HIST_START.get(ts_code, "20210101")
    db_frames = []
    cur = hist_start
    while cur < end:
        seg_end = (datetime.strptime(cur, "%Y%m%d") + timedelta(days=500)).strftime("%Y%m%d")
        if seg_end > end:
            seg_end = end
        df_seg = pro.daily_basic(
            ts_code=ts_code, start_date=cur, end_date=seg_end,
            fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv",
        )
        if df_seg is not None and len(df_seg):
            db_frames.append(df_seg)
        cur = (datetime.strptime(seg_end, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    db_hist = (
        pd.concat(db_frames).drop_duplicates("trade_date")
        .sort_values("trade_date")
        .reset_index(drop=True)  # 关键:重置 index,否则分段 concat 的重复 index 会让 sort_index 打乱顺序
    ) if db_frames else pd.DataFrame()
    logger.info("[{}] daily_basic 全历史 {} 点 [{} → {}]", name, len(db_hist), hist_start, end)
    pe = pd.to_numeric(db_hist["pe_ttm"], errors="coerce").dropna().sort_index()
    pb = pd.to_numeric(db_hist["pb"], errors="coerce").dropna().sort_index()
    ps = pd.to_numeric(db_hist["ps"], errors="coerce").dropna().sort_index()
    mv = pd.to_numeric(db_hist["total_mv"], errors="coerce").dropna().sort_index()

    # 3 年窗口子序列(对照)
    cutoff_3y = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
    db_3y = db_hist[db_hist["trade_date"] >= cutoff_3y]
    pe3 = pd.to_numeric(db_3y["pe_ttm"], errors="coerce").dropna()
    pb3 = pd.to_numeric(db_3y["pb"], errors="coerce").dropna()
    ps3 = pd.to_numeric(db_3y["ps"], errors="coerce").dropna()

    def _pct(series, label):
        if not len(series):
            return None, {}
        cur = series.iloc[-1]
        pct = (series < cur).sum() / len(series)
        qs = {f"p{p}": round(float(series.quantile(p / 100)), 3) for p in [10, 25, 50, 75, 90, 95]}
        logger.info("[{}] {}={:.2f} ({:.1f}%分位, {} 点)", name, label, cur, pct * 100, len(series))
        return {"current": round(float(cur), 3), "percentile": round(pct * 100, 1), "points": int(len(series)), **qs}

    result["valuation_series"] = {
        "pe_ttm": _pct(pe, "PE_TTM"),
        "pb": _pct(pb, "PB"),
        "ps": _pct(ps, "PS"),
        "total_mv_yi": {"current": round(float(mv.iloc[-1]) / 1e4, 1), "percentile": round((mv < mv.iloc[-1]).sum() / len(mv) * 100, 1)} if len(mv) else None,
        "window": f"全历史 {hist_start}→{end}",
        "pe_ttm_3y": _pct(pe3, "PE_TTM_3y"),
        "pb_3y": _pct(pb3, "PB_3y"),
        "ps_3y": _pct(ps3, "PS_3y"),
    }
    pe_pct = (pe < pe.iloc[-1]).sum() / len(pe) if len(pe) else 0.5
    pb_pct = (pb < pb.iloc[-1]).sum() / len(pb) if len(pb) else 0.5

    # 估值评分(周期股 PB 主导)
    try:
        stock_df_list = client.get_stock_list()
        row = stock_df_list[stock_df_list["ts_code"] == ts_code]
        industry = str(row.iloc[0].get("industry", "") or "") if not row.empty else ""
        real_name = str(row.iloc[0].get("name", name) or name) if not row.empty else name
    except Exception:
        industry, real_name = "", name
    is_cyclical = detect_cyclical(industry)
    result["stock_info"] = {"industry": industry, "is_cyclical": is_cyclical, "name": real_name}
    logger.info("[{}] 行业={} 周期股={}", name, industry, is_cyclical)

    # 估值分:周期股用 PB 分位映射,非周期用 PE+PB
    if is_cyclical:
        val_score = max(0.0, min(100.0, 100 - pb_pct * 100))
    else:
        val_score = max(0.0, min(100.0, 100 - (pe_pct + pb_pct) / 2 * 100))
    result["valuation_score_manual"] = round(val_score, 2)

    # ── 5. 景气度 ──
    pscore = calculate_prosperity_score(fin_data)
    stage = classify_stock_stage(pscore)
    result["prosperity"] = {
        "composite_score": round(pscore.composite_score, 2),
        "revenue_score": round(pscore.revenue_score, 2),
        "profit_score": round(pscore.profit_score, 2),
        "slope_score": round(pscore.slope_score, 2),
        "duration_score": round(pscore.duration_score, 2),
        "delta_g": round(pscore.delta_g, 2),
        "stage": stage,
    }
    logger.info("[{}] 景气度 composite={} ΔG={} 阶段={}", name, pscore.composite_score, pscore.delta_g, stage)

    # ── 6. 趋势(月度 PE/PB 斜率) ──
    trend_score = 50.0
    try:
        if len(db_hist) >= 3:
            dates = pd.to_datetime(db_hist["trade_date"], format="%Y%m%d")
            daily_pe = pd.Series(pd.to_numeric(db_hist["pe_ttm"], errors="coerce").values, index=dates).dropna()
            daily_pb = pd.Series(pd.to_numeric(db_hist["pb"], errors="coerce").values, index=dates).dropna()
            stock_info = StockInfo(ts_code=ts_code, name=name, industry=industry, list_status="L", is_cyclical=is_cyclical)
            trend_map = batch_trend({ts_code: (daily_pe, daily_pb)}, {ts_code: stock_info})
            trend_score = trend_map.get(ts_code, 50.0)
    except Exception as e:
        logger.warning("[{}] 趋势计算失败: {}", name, e)
    result["trend_score"] = round(float(trend_score), 2)

    # ── 7. 困境反转三层 ──
    distress = calculate_distress_score(
        eps_history=eps_history,
        pe_pct=pe_pct,
        pb_pct=pb_pct,
        debt_ratio=debt_ratio,
        operating_cf=operating_cf,
        total_debt=total_debt,
        total_assets=total_assets,
        roe_history=roe_history,
        revenue_history=revenue_growth,
        profit_history=profit_growth,
        delta_g=pscore.delta_g,
        ts_code=ts_code,
    )
    result["distress"] = {
        "total_score": round(distress.total_score, 2),
        "layer1_score": round(distress.layer1_score, 2),
        "layer2_score": round(distress.layer2_score, 2),
        "layer3_score": round(distress.layer3_score, 2),
    }
    logger.info("[{}] 困境 total={} L1={} L2={} L3={}", name, distress.total_score, distress.layer1_score, distress.layer2_score, distress.layer3_score)

    # ── 8. 戴维斯双击综合 ──
    davis = calculate_davis_double_score(
        valuation_score=val_score,
        prosperity_score=pscore.composite_score,
        distress_score=distress.total_score,
        trend_score=trend_score,
        ts_code=ts_code,
        name=name,
    )
    result["davis_double"] = {
        "final_score": round(davis.final_score, 2),
        "rank": getattr(davis, "rank", ""),
        "valuation_score": round(davis.valuation_score, 2),
        "trend_score": round(davis.trend_score, 2),
        "prosperity_score": round(davis.prosperity_score, 2),
        "distress_score": round(davis.distress_score, 2),
    }
    logger.info("[{}] 戴维斯双击 final={}", name, davis.final_score)

    # ── 9. 5 补充因子 ──
    supp = {}
    try:
        mom = analyze_momentum(client, ts_code)
        if mom:
            supp["momentum"] = {
                "momentum_score": round(mom.momentum_score, 2),
                "window_returns": {k: round(v, 2) for k, v in (mom.window_returns or {}).items()},
                "rs_percentile": round(mom.rs_percentile, 2) if mom.rs_percentile is not None else None,
            }
    except Exception as e:
        logger.warning("[{}] momentum 失败: {}", name, e)
    try:
        div = analyze_dividend(client, ts_code)
        if div:
            supp["dividend"] = {
                "dividend_score": round(div.dividend_score, 2),
                "consecutive_years": div.consecutive_years,
                "latest_yield_pct": div.latest_yield_pct,
                "payout_years": div.payout_years,
            }
    except Exception as e:
        logger.warning("[{}] dividend 失败: {}", name, e)
    try:
        fc = analyze_forecast(client, ts_code, pscore)
        if fc:
            supp["forecast"] = {
                "leading_score": round(fc.leading_score, 2),
                "type": fc.type,
                "p_change_mid": fc.p_change_mid,
                "is_stale": fc.is_stale,
            }
    except Exception as e:
        logger.warning("[{}] forecast 因子失败: {}", name, e)
    try:
        rev = analyze_forecast_revision(client, ts_code)
        if rev:
            supp["forecast_revision"] = {
                "revision_direction": rev.revision_direction,
                "revision_pp": rev.revision_pp,
                "revision_score": round(rev.revision_score, 2),
            }
    except Exception as e:
        logger.warning("[{}] forecast_revision 失败: {}", name, e)
    try:
        hc = analyze_holder_concentration(client, ts_code)
        if hc:
            supp["holder_concentration"] = {
                "concentration_score": round(hc.concentration_score, 2),
                "trend": hc.trend,
                "latest_chg_pct": round(hc.latest_chg_pct, 1) if hc.latest_chg_pct is not None else None,
                "holder_counts": hc.holder_counts,
                "periods": hc.periods,
            }
    except Exception as e:
        logger.warning("[{}] holder_concentration 失败: {}", name, e)
    try:
        pq = analyze_profitability_quality(fin_data)
        supp["profitability_quality"] = {
            "quality_score": round(pq.quality_score, 2),
            "latest_gross_margin": pq.latest_gross_margin,
            "gross_margin_delta": pq.gross_margin_delta,
            "latest_rd_intensity": pq.latest_rd_intensity,
            "data_sufficient": pq.data_sufficient,
        }
    except Exception as e:
        logger.warning("[{}] profitability 失败: {}", name, e)
    result["supplementary"] = supp

    # ── 10. 股东户数趋势 ──
    try:
        h = pro.stk_holdernumber(ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num").sort_values("end_date")
        h = h[pd.to_numeric(h["holder_num"], errors="coerce").notna()].tail(10)
        rows, prev = [], None
        for _, r in h.iterrows():
            num = int(r["holder_num"])
            chg = (num - prev) / prev * 100 if prev else None
            rows.append({"end_date": r["end_date"], "ann_date": r["ann_date"], "holder_num": num, "chg_pct": round(chg, 1) if chg is not None else None})
            prev = num
        nums4 = [r["holder_num"] for r in rows[-4:]] if len(rows) >= 4 else [r["holder_num"] for r in rows]
        trend = "集中" if nums4[-1] < nums4[0] else "分散"
        result["holder_number"] = {"rows": rows, "trend_4p": trend}
        logger.info("[{}] 股东户数 {} 期,近4期趋势 {} ({}→{})", name, len(rows), trend, nums4[0], nums4[-1])
    except Exception as e:
        logger.warning("[{}] 股东户数失败: {}", name, e)
        result["holder_number"] = {"error": str(e)}

    # ── 11. 十大流通股东 ──
    try:
        t10 = pro.top10_floatholders(ts_code=ts_code)
        # 只取最新报告期
        if len(t10):
            latest_end = sorted(t10["end_date"].unique())[-1]
            t10 = t10[t10["end_date"] == latest_end]
        top_rows = []
        for _, r in t10.head(10).iterrows():
            top_rows.append({
                "end_date": r.get("end_date"),
                "holder_name": r.get("holder_name"),
                "hold_ratio": float(r["ratio"]) if pd.notna(r.get("ratio")) else None,
            })
        top10_total_ratio = round(sum(r["hold_ratio"] for r in top_rows if r["hold_ratio"]), 2)
        result["top10_floatholders"] = top_rows
        result["top10_total_ratio_pct"] = top10_total_ratio
    except Exception as e:
        logger.warning("[{}] 十大流通股东失败: {}", name, e)
        result["top10_floatholders"] = []

    # ── 12. 相对市场估值锚定 ──
    try:
        from stockhot.valuation import analyze_relative_valuation

        rv = analyze_relative_valuation(pro, ts_code, name, lookback_years=3)
        result["relative_valuation"] = {
            "board": rv.board,
            "benchmark": rv.benchmark,
            "stock_pe": rv.stock_pe,
            "index_pe": rv.index_pe,
            "pe_ratio": round(rv.pe_ratio, 3) if rv.pe_ratio is not None else None,
            "pe_ratio_pct": round(rv.pe_ratio_pct * 100, 1) if rv.pe_ratio_pct is not None else None,
            "pe_ratio_label": rv.pe_ratio_label,
            "erp": round(rv.erp, 2) if rv.erp is not None else None,
            "erp_label": rv.erp_label,
            "stock_pe_pct": round(rv.stock_pe_pct * 100, 1) if rv.stock_pe_pct is not None else None,
            "index_pe_pct": round(rv.index_pe_pct * 100, 1) if rv.index_pe_pct is not None else None,
            "quadrant": rv.quadrant,
            "quadrant_label": rv.quadrant_label,
            "signals": rv.signals,
        }
        logger.info("[{}] 相对估值: board={} bench={} 溢价={} ERP={} 象限={}", name, rv.board, rv.benchmark,
                    result["relative_valuation"]["pe_ratio"], result["relative_valuation"]["erp"], rv.quadrant_label)
    except Exception as e:
        logger.warning("[{}] 相对估值失败: {}", name, e)
        result["relative_valuation"] = {"error": str(e)}

    return result


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    client = TushareClient()
    try:
        from stockhot.tushare_config import get_pro_api

        pro = get_pro_api(timeout=30)
    except Exception as e:
        logger.error("get_pro_api 失败: {}", e)
        pro = None

    for s in STOCKS:
        logger.info("=" * 70)
        logger.info("{} ({}) 开始评分", s["name"], s["ts_code"])
        logger.info("=" * 70)
        try:
            result = score_stock(client, pro, s["ts_code"], s["name"])
            out = OUTPUT_DIR / f"{s['ts_code'].split('.')[0]}.json"
            with open(out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2, default=str)
            logger.info("✅ {} 写入 {}", s["name"], out)
        except Exception as e:
            logger.exception("{} 评分失败: {}", s["name"], e)


if __name__ == "__main__":
    main()
