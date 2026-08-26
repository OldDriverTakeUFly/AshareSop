#!/usr/bin/env python3
"""三安光电 (600703.SH) 个股研报数据采集脚本.

产出个股深度研报所需的全部引擎数据：
  1. 身份校验（代码↔公司名，防张冠李戴）
  2. 财务 12 季度（含单季拆分）+ 年度历史（2018-2025 + 2026H1）
  3. 估值：3 年 daily_basic（≥700 点校验 + 分段 fallback），PB/PS 分位表（亏损股 PE 失效）
  4. 四维评分：估值/趋势/景气/困境 → 戴维斯双击
  5. 数据时效性校验（daily_basic 最新交易日 / income 披露日 / forecast 预告）
  6. 五因子引擎：momentum / dividend / forecast / holder_concentration / profitability
  7. 动量手工复核（pro.daily + adj_factor，防缓存缺口错位）
  8. 股东户数趋势 + 十大流通股东交叉验证
  9. 相对估值锚定（stockhot.valuation.analyze_relative_valuation）
 10. 分红历史（实施口径，股息可持续性校验）
 11. 同业速查（PB/PS/市值/最新增速）

用法（仓库根目录）:
    .venv/bin/python davis_analyzer/studies/sanan_scoring.py

输出:
    .sisyphus/evidence/sanan/t1-scoring.json
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from loguru import logger

# ── 引导：必须在 import davis/stockhot 之前 ──
_REPO_ROOT = "/home/leo/Projects/CodeAgentDashboard"
if os.getcwd() != _REPO_ROOT:
    os.chdir(_REPO_ROOT)
sys.path.insert(0, _REPO_ROOT)
from dotenv import load_dotenv

load_dotenv(".env", override=True)  # 防 shell stale token
os.environ["PROJECT_ROOT"] = _REPO_ROOT  # 防 .env 的 /app 值破坏 stockhot mkdir

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
from davis_analyzer.types import StockInfo, ValuationData
from davis_analyzer.valuation import detect_cyclical

TS_CODE = "600703.SH"
STOCK_NAME = "三安光电"
PERIODS = 12
OUTPUT_PATH = Path(".sisyphus/evidence/sanan/t1-scoring.json")
PEERS = {
    "300323.SZ": "华灿光电",
    "002429.SZ": "兆驰股份",
    "688234.SH": "天岳先进",
    "300102.SZ": "干照光电",
    "300708.SZ": "聚灿光电",
}


def _fmt_yoy(v: float | None) -> str:
    return f"{v * 100:.1f}%" if v is not None else "N/A"


def _seg_daily_basic(pro, ts_code: str, start: str, end: str) -> pd.DataFrame:
    """分段（≤500 天）直连 daily_basic，规避新端点长区间截断."""
    frames = []
    s = date(int(start[:4]), int(start[4:6]), int(start[6:]))
    e = date(int(end[:4]), int(end[4:6]), int(end[6:]))
    while s <= e:
        seg_end = min(s + timedelta(days=499), e)
        df = pro.daily_basic(
            ts_code=ts_code,
            start_date=s.strftime("%Y%m%d"),
            end_date=seg_end.strftime("%Y%m%d"),
            fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv",
        )
        if len(df):
            frames.append(df)
        s = seg_end + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).drop_duplicates("trade_date")
    return out.sort_values("trade_date").reset_index(drop=True)


def main() -> None:  # noqa: PLR0915
    result: dict = {"ts_code": TS_CODE, "name": STOCK_NAME}

    client = TushareClient()
    from stockhot.tushare_config import get_pro_api

    pro = get_pro_api(timeout=30)

    # ── 1. 身份校验 ──
    basic = pro.stock_basic(ts_code=TS_CODE, fields="ts_code,name,industry,area,list_date")
    assert len(basic) and basic.iloc[0]["name"] == STOCK_NAME, f"代码校验失败: {basic}"
    industry = str(basic.iloc[0]["industry"])
    result["identity"] = {
        "name": basic.iloc[0]["name"],
        "industry": industry,
        "area": basic.iloc[0]["area"],
        "list_date": basic.iloc[0]["list_date"],
        "is_cyclical": detect_cyclical(industry),
    }
    logger.info("身份: {} {} 行业={} 周期={}", TS_CODE, STOCK_NAME, industry, result["identity"]["is_cyclical"])

    # ── 2. 财务 12 季度 ──
    fin = fetch_financial_data(client, TS_CODE, periods=PERIODS)
    logger.info("财务: {} 期, 最新 {}", len(fin), fin[0].report_period)
    rows: list[dict] = []
    prev_cum: dict[str, float] = {}
    for fd in reversed(fin):  # 升序遍历做累计差分
        q_rev = fd.revenue - prev_cum.get("rev", 0.0) if fd.revenue else None
        q_np = fd.net_profit - prev_cum.get("np", 0.0) if fd.net_profit is not None else None
        rows.append(
            {
                "period": fd.report_period,
                "rev_cum_yi": round((fd.revenue or 0) / 1e8, 2),
                "rev_q_yi": round(q_rev / 1e8, 2) if q_rev is not None else None,
                "np_cum_yi": round((fd.net_profit or 0) / 1e8, 3),
                "np_q_yi": round(q_np / 1e8, 3) if q_np is not None else None,
                "yoy_rev": _fmt_yoy(fd.yoy_revenue_growth),
                "yoy_np": _fmt_yoy(fd.yoy_profit_growth),
                "roe": fd.roe,
                "gm": fd.grossprofit_margin,
                "rd_exp_yi": round((fd.rd_exp or 0) / 1e8, 2) if fd.rd_exp else None,
                "ocf_yi": round((fd.operating_cf or 0) / 1e8, 2),
            }
        )
        prev_cum = {"rev": fd.revenue or 0.0, "np": fd.net_profit or 0.0}
    result["quarterly"] = list(reversed(rows))

    # ── 3. 年度历史（2018-2025 年报 + 2026H1）──
    periods_annual = [f"{y}1231" for y in range(2018, 2026)] + ["20260630"]
    ann_rows = []
    for p in periods_annual:
        inc = pro.income(
            ts_code=TS_CODE, period=p,
            fields="ts_code,ann_date,end_date,revenue,n_income_attr_p",
        )
        fi = pro.fina_indicator(
            ts_code=TS_CODE, period=p, fields="ts_code,end_date,grossprofit_margin,roe,debt_to_assets"
        )
        r = {"period": p, "ann_date": None, "rev_yi": None, "np_yi": None, "gm": None, "roe": None, "debt_ratio": None}
        if len(inc):
            r.update(
                ann_date=inc.iloc[0]["ann_date"],
                rev_yi=round(inc.iloc[0]["revenue"] / 1e8, 2) if inc.iloc[0]["revenue"] else None,
                np_yi=round(inc.iloc[0]["n_income_attr_p"] / 1e8, 3) if inc.iloc[0]["n_income_attr_p"] is not None else None,
            )
        if len(fi):
            r.update(
                gm=fi.iloc[0]["grossprofit_margin"],
                roe=fi.iloc[0]["roe"],
                debt_ratio=fi.iloc[0]["debt_to_assets"],
            )
        ann_rows.append(r)
    result["annual"] = ann_rows

    # ── 4. 估值：3 年 daily_basic（≥700 校验）──
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
    db = client.get_daily_basic(TS_CODE, start, end)
    if len(db) < 700:
        logger.warning("client.get_daily_basic 仅 {} 行，切换分段直连", len(db))
        db = _seg_daily_basic(pro, TS_CODE, start, end)
    db = db.sort_values("trade_date").reset_index(drop=True)
    assert db["trade_date"].iloc[-1] >= (date.today() - timedelta(days=7)).strftime("%Y%m%d"), "估值数据非最新"
    pe = pd.to_numeric(db["pe_ttm"], errors="coerce")
    pb = pd.to_numeric(db["pb"], errors="coerce")
    ps = pd.to_numeric(db["ps"], errors="coerce")
    mv = pd.to_numeric(db["total_mv"], errors="coerce")
    val_summary = {
        "trade_date": db["trade_date"].iloc[-1],
        "close_mv_yi": round(mv.iloc[-1] / 1e4, 1),
        "pe_points": int(pe.notna().sum()),
        "pe_latest": None if pd.isna(pe.iloc[-1]) else round(pe.iloc[-1], 2),
        "pb_latest": round(pb.iloc[-1], 2),
        "ps_latest": round(ps.iloc[-1], 2),
    }
    for key, s in (("pb", pb.dropna()), ("ps", ps.dropna())):
        cur = s.iloc[-1]
        val_summary[f"{key}_pct"] = round((s < cur).sum() / len(s) * 100, 1)
        val_summary[f"{key}_quantiles"] = {str(p): round(s.quantile(p / 100), 2) for p in [10, 25, 50, 75, 90, 95]}
    result["valuation"] = val_summary
    logger.info("估值: {} 市值{}亿 PB={}(p{}%) PS={}(p{}%) PE有效点={}",
                val_summary["trade_date"], val_summary["close_mv_yi"], val_summary["pb_latest"],
                val_summary["pb_pct"], val_summary["ps_latest"], val_summary["ps_pct"], val_summary["pe_points"])

    # ── 5. 四维评分 ──
    val_list = [
        ValuationData(ts_code=TS_CODE, trade_date=r.trade_date, pe_ttm=None if pd.isna(r.pe_ttm) else r.pe_ttm,
                      pb=r.pb, ps=r.ps, total_mv=r.total_mv)
        for r in db[::-1].itertuples()
        if not pd.isna(r.pe_ttm) and not pd.isna(r.pb)
    ]
    from davis_analyzer.valuation import calculate_valuation_score

    stock_info = StockInfo(ts_code=TS_CODE, name=STOCK_NAME, industry=industry, list_status="L",
                           is_cyclical=detect_cyclical(industry))
    val_score, pe_pct, pb_pct = calculate_valuation_score(val_list, stock_info.is_cyclical)
    pscore = calculate_prosperity_score(fin)
    stage = classify_stock_stage(pscore)
    dates = pd.to_datetime(db["trade_date"], format="%Y%m%d")
    trend_map = batch_trend({TS_CODE: (pd.Series(pe.values, index=dates), pd.Series(pb.values, index=dates))},
                            {TS_CODE: stock_info})
    trend_score = trend_map.get(TS_CODE, 50.0)
    latest = fin[0]
    debt_ratio = (latest.total_debt or 0) / (latest.total_assets or 1)
    distress = calculate_distress_score(
        eps_history=[f.eps for f in fin], pe_pct=pe_pct, pb_pct=pb_pct, debt_ratio=debt_ratio,
        operating_cf=latest.operating_cf or 0, total_debt=latest.total_debt or 0,
        total_assets=latest.total_assets or 0, roe_history=[f.roe for f in fin],
        revenue_history=[f.yoy_revenue_growth or 0 for f in fin],
        profit_history=[f.yoy_profit_growth or 0 for f in fin],
        delta_g=pscore.delta_g, ts_code=TS_CODE,
    )
    davis = calculate_davis_double_score(valuation_score=val_score, prosperity_score=pscore.composite_score,
                                         distress_score=distress.total_score, trend_score=trend_score,
                                         ts_code=TS_CODE, name=STOCK_NAME)
    result["scoring"] = {
        "valuation_score": round(val_score, 2), "pe_pct": round(pe_pct, 4), "pb_pct": round(pb_pct, 4),
        "prosperity": {"composite": pscore.composite_score, "revenue": pscore.revenue_score,
                        "profit": pscore.profit_score, "slope": pscore.slope_score,
                        "duration": pscore.duration_score, "delta_g": pscore.delta_g, "stage": stage},
        "trend_score": round(trend_score, 2),
        "distress": {"total": distress.total_score, "l1": distress.layer1_score,
                      "l2": distress.layer2_score, "l3": distress.layer3_score},
        "davis_final": davis.final_score, "davis_rank": davis.rank,
    }
    logger.info("评分: 景气={:.1f} ΔG={:.2f} 阶段={} 困境={:.1f} 趋势={:.1f} davis={:.1f}",
                pscore.composite_score, pscore.delta_g, stage, distress.total_score, trend_score, davis.final_score)

    # ── 6. 时效性校验 ──
    inc_latest = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
    fc_raw = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
    fc = fc_raw[pd.to_numeric(fc_raw["ann_date"]) >= 20260101].sort_values("end_date") if len(fc_raw) else fc_raw
    result["freshness"] = {
        "latest_trade": val_summary["trade_date"],
        "latest_period": inc_latest.iloc[0]["end_date"] if len(inc_latest) else None,
        "latest_ann": inc_latest.iloc[0]["ann_date"] if len(inc_latest) else None,
        "forecast": None if not len(fc) else {
            "ann_date": fc.iloc[-1]["ann_date"], "end_date": fc.iloc[-1]["end_date"], "type": fc.iloc[-1]["type"],
            "p_change": [fc.iloc[-1]["p_change_min"], fc.iloc[-1]["p_change_max"]],
            "np_yi": [None if fc.iloc[-1]["net_profit_min"] is None else round(fc.iloc[-1]["net_profit_min"] / 1e4, 2),
                      None if fc.iloc[-1]["net_profit_max"] is None else round(fc.iloc[-1]["net_profit_max"] / 1e4, 2)],
        },
    }

    # ── 7. 五因子引擎 ──
    mom = analyze_momentum(client, TS_CODE)
    div = analyze_dividend(client, TS_CODE)
    fc_sig = analyze_forecast(client, TS_CODE, pscore)
    fc_rev = analyze_forecast_revision(client, TS_CODE)
    hc = analyze_holder_concentration(client, TS_CODE)
    pq = analyze_profitability_quality(fin)
    result["factors"] = {
        "momentum": None if mom is None else {
            "score": mom.momentum_score, "abs_score": mom.absolute_momentum_score,
            "rs_pct": mom.rs_percentile, "window_returns": mom.window_returns},
        "dividend": {"score": div.dividend_score, "years": div.consecutive_years,
                      "yield_pct": div.latest_yield_pct},
        "forecast": None if fc_sig is None else {"leading": fc_sig.leading_score, "type": fc_sig.type,
                                                  "p_mid": fc_sig.p_change_mid, "stale": fc_sig.is_stale},
        "forecast_revision": None if fc_rev is None else {"dir": fc_rev.revision_direction,
                                                           "pp": fc_rev.revision_pp, "score": fc_rev.revision_score},
        "holder_conc": None if hc is None else {"score": hc.concentration_score, "trend": hc.trend,
                                                 "chg_pct": hc.latest_chg_pct, "periods": hc.periods},
        "profit_quality": {"score": pq.quality_score, "gm": pq.latest_gross_margin,
                            "gm_delta": pq.gross_margin_delta, "rd": pq.latest_rd_intensity},
    }

    # ── 8. 动量手工复核 ──
    dly = pro.daily(ts_code=TS_CODE, start_date=(date.today() - timedelta(days=420)).strftime("%Y%m%d"),
                    end_date=end, fields="ts_code,trade_date,close")
    adj = pro.adj_factor(ts_code=TS_CODE, start_date=(date.today() - timedelta(days=420)).strftime("%Y%m%d"),
                          end_date=end, fields="ts_code,trade_date,adj_factor")
    m = dly.merge(adj, on=["ts_code", "trade_date"]).sort_values("trade_date").reset_index(drop=True)
    m["hfq"] = m["close"] * m["adj_factor"]
    rets = {}
    for w in (20, 60, 120, 250):
        rets[f"{w}d"] = round((m["hfq"].iloc[-1] / m["hfq"].iloc[-w - 1] - 1) * 100, 1) if len(m) > w + 1 else None
    result["momentum_manual"] = {"last_trade": m["trade_date"].iloc[-1], "returns_pct": rets}

    # ── 9. 股东户数 + 十大流通股东 ──
    h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num")
    h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(8)
    hrows, prev = [], None
    for _, r in h.iterrows():
        num = int(r["holder_num"])
        hrows.append({"end_date": r["end_date"], "holder_num": num,
                      "chg_pct": None if prev is None else round((num - prev) / prev * 100, 1)})
        prev = num
    result["holder_number"] = hrows
    t10 = pro.top10_floatholders(ts_code=TS_CODE, period="20260630")
    if not len(t10):
        t10 = pro.top10_floatholders(ts_code=TS_CODE, period="20260331")
    result["top10_float"] = None
    if len(t10) and "ratio" in t10.columns:
        try:
            t10_prev_p = "20260331" if t10.iloc[0]["end_date"] == "20260630" else "20251231"
            t10_prev = pro.top10_floatholders(ts_code=TS_CODE, period=t10_prev_p)
            cur_ratio = pd.to_numeric(t10["ratio"], errors="coerce").dropna().sum()
            prev_ratio = pd.to_numeric(t10_prev["ratio"], errors="coerce").dropna().sum() if len(t10_prev) else None
            result["top10_float"] = {
                "period": t10.iloc[0]["end_date"], "sum_ratio_pct": round(cur_ratio, 2),
                "prev_period": t10_prev_p, "prev_sum_ratio_pct": None if prev_ratio is None else round(prev_ratio, 2),
            }
        except Exception:
            logger.exception("top10 流通股东计算失败")

    # ── 10. 相对估值 ──
    try:
        from stockhot.valuation import analyze_relative_valuation

        rv = analyze_relative_valuation(pro, TS_CODE, STOCK_NAME, lookback_years=3)
        result["relative_valuation"] = {
            "pe_ratio": rv.pe_ratio, "pe_ratio_pct": rv.pe_ratio_pct, "erp": rv.erp,
            "quadrant": rv.quadrant, "index_pe": rv.index_pe, "index_pe_pct": rv.index_pe_pct,
            "risk_free_rate": rv.risk_free_rate, "signals": [str(s) for s in (rv.signals or [])],
        }
    except Exception:
        logger.exception("相对估值失败")
        result["relative_valuation"] = None

    # ── 11. 分红历史（实施口径）──
    dv = pro.dividend(ts_code=TS_CODE, fields="ts_code,end_date,ann_date,div_proc,cash_div_taxable,base_share")
    dv_done = dv[dv["div_proc"] == "实施"] if len(dv) and "div_proc" in dv.columns else dv
    if len(dv_done) and "cash_div_taxable" in dv_done.columns:
        result["dividends"] = [
            {"end_date": r["end_date"], "dps": r.get("cash_div_taxable"), "ann": r.get("ann_date")}
            for _, r in dv_done.tail(6).iterrows()
        ]
    else:
        result["dividends"] = {"note": "dividend 端点字段裁剪，实施明细不可用", "rows": len(dv_done)}

    # ── 12. 同业速查 ──
    peers_out = {}
    for code, name in PEERS.items():
        try:
            pdb = pro.daily_basic(ts_code=code, limit=1, fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv")
            pfin = fetch_financial_data(client, code, periods=2)
            peers_out[code] = {
                "name": name, "trade_date": pdb.iloc[0]["trade_date"] if len(pdb) else None,
                "pb": None if not len(pdb) or pd.isna(pdb.iloc[0]["pb"]) else round(pdb.iloc[0]["pb"], 2),
                "ps": None if not len(pdb) or pd.isna(pdb.iloc[0]["ps"]) else round(pdb.iloc[0]["ps"], 2),
                "mv_yi": None if not len(pdb) else round(pdb.iloc[0]["total_mv"] / 1e4, 1),
                "yoy_rev": _fmt_yoy(pfin[0].yoy_revenue_growth) if pfin else None,
                "yoy_np": _fmt_yoy(pfin[0].yoy_profit_growth) if pfin else None,
                "latest_np_yi": round((pfin[0].net_profit or 0) / 1e8, 2) if pfin else None,
            }
        except Exception:
            logger.exception("同业 {} 取数失败", name)
    result["peers"] = peers_out

    # ── 输出 ──
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info("✅ 完成 → {}", OUTPUT_PATH)
    print(json.dumps({k: result[k] for k in ("identity", "valuation", "scoring", "freshness", "factors",
                                              "momentum_manual")}, ensure_ascii=False, indent=2)[:6000])


if __name__ == "__main__":
    main()
