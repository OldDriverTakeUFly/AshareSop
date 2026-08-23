#!/usr/bin/env python3
"""有色金属 7 家公司批量数据采集脚本.

采集内容：
  - 财务数据（12 季度）
  - 估值历史（3 年 daily_basic）+ 分位数
  - 景气度评分（G+ΔG）
  - 5 个补充因子（momentum/dividend/forecast/holder_concentration/profitability）
  - 股东户数趋势
  - 相对市场估值（stockhot.valuation）
  - 数据时效性校验

输出: JSON 文件到 .sisyphus/evidence/youse_7/{ts_code}.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"
sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.valuation import detect_cyclical
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality

# stockhot for relative valuation
from stockhot.tushare_config import get_pro_api
from stockhot.valuation import analyze_relative_valuation

OUTPUT_DIR = Path("/home/leo/Projects/CodeAgentDashboard/.sisyphus/evidence/youse_7")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

STOCKS = [
    ("000603.SZ", "盛达资源", "铅锌"),
    ("601958.SH", "金钼股份", "小金属"),
    ("001257.SZ", "盛龙股份", "小金属"),  # IPO 2026-03-31
    ("000657.SZ", "中钨高新", "小金属"),
    ("600531.SH", "豫光金铅", "铅锌"),
    ("000630.SZ", "铜陵有色", "铜"),
    ("600259.SH", "中稀有色", "小金属"),
]

client = TushareClient()
pro = get_pro_api(timeout=30)


def collect_stock(ts_code: str, name: str, industry: str) -> dict:
    """Collect all data for one stock."""
    result = {"ts_code": ts_code, "name": name, "industry": industry}
    print(f"\n{'='*60}\n采集 {name} ({ts_code}) industry={industry}\n{'='*60}")

    # 1. 财务
    try:
        fin = fetch_financial_data(client, ts_code, periods=12)
        result["fin_periods"] = len(fin)
        if fin:
            result["latest_report_period"] = fin[0].report_period
            fin_rows = []
            for f in fin[:8]:
                fin_rows.append({
                    "report_period": f.report_period,
                    "revenue_yi": round((f.revenue or 0) / 1e8, 2),
                    "net_profit_yi": round((f.net_profit or 0) / 1e8, 2),
                    "eps": round(f.eps or 0, 4),
                    "roe_pct": round(f.roe or 0, 2),
                    "operating_cf_yi": round((f.operating_cf or 0) / 1e8, 2),
                    "total_debt_yi": round((f.total_debt or 0) / 1e8, 2),
                    "total_assets_yi": round((f.total_assets or 0) / 1e8, 2),
                    "debt_ratio_pct": round((f.total_debt or 0) / (f.total_assets or 1) * 100, 1),
                    "yoy_rev_pct": round((f.yoy_revenue_growth or 0) * 100, 1),
                    "yoy_prof_pct": round((f.yoy_profit_growth or 0) * 100, 1),
                    "gross_margin_pct": round(f.grossprofit_margin or 0, 2) if hasattr(f, 'grossprofit_margin') else None,
                    "rd_exp": f.rd_exp if hasattr(f, 'rd_exp') else None,
                })
            result["fin_rows"] = fin_rows
            print(f"  财务: {len(fin)} 期, 最新 {fin[0].report_period}, 营收={fin_rows[0]['revenue_yi']}亿")
    except Exception as e:
        result["fin_error"] = str(e)
        print(f"  财务 ERROR: {e}")

    # 2. 估值历史（get_daily_basic 更可靠）
    try:
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
        db = client.get_daily_basic(ts_code, start, end)
        if len(db) == 0:
            result["val_error"] = "daily_basic 空数据"
            print(f"  估值: 空（新股可能无足够历史）")
        else:
            db = db.sort_values("trade_date")
            pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
            pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
            ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
            mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()

            result["val_data_points"] = len(db)
            result["latest_trade_date"] = str(db["trade_date"].iloc[-1])

            cur_pe = pe.iloc[-1] if len(pe) else None
            cur_pb = pb.iloc[-1] if len(pb) else None
            cur_ps = ps.iloc[-1] if len(ps) else None
            cur_mv_yi = mv.iloc[-1] / 1e4 if len(mv) else None

            pe_pct = (pe < cur_pe).sum() / len(pe) * 100 if len(pe) and cur_pe else None
            pb_pct = (pb < cur_pb).sum() / len(pb) * 100 if len(pb) and cur_pb else None
            ps_pct = (ps < cur_ps).sum() / len(ps) * 100 if len(ps) and cur_ps else None

            result["valuation"] = {
                "latest_trade_date": str(db["trade_date"].iloc[-1]),
                "pe_ttm": round(cur_pe, 2) if cur_pe else None,
                "pb": round(cur_pb, 2) if cur_pb else None,
                "ps": round(cur_ps, 2) if cur_ps else None,
                "total_mv_yi": round(cur_mv_yi, 1) if cur_mv_yi else None,
                "pe_pct": round(pe_pct, 1) if pe_pct else None,
                "pb_pct": round(pb_pct, 1) if pb_pct else None,
                "ps_pct": round(ps_pct, 1) if ps_pct else None,
            }
            # 分位值表
            if len(pe):
                result["pe_percentiles"] = {str(p): round(pe.quantile(p/100), 2) for p in [10,25,50,75,90,95]}
            if len(pb):
                result["pb_percentiles"] = {str(p): round(pb.quantile(p/100), 2) for p in [10,25,50,75,90,95]}
            if len(ps):
                result["ps_percentiles"] = {str(p): round(ps.quantile(p/100), 2) for p in [10,25,50,75,90,95]}
            print(f"  估值: {len(db)} 点, PE={cur_pe}, PB={cur_pb}({pb_pct}%分位), 市值={cur_mv_yi}亿")
    except Exception as e:
        result["val_error"] = str(e)
        print(f"  估值 ERROR: {e}")

    # 3. 景气度
    try:
        if fin and len(fin) >= 2:
            pscore = calculate_prosperity_score(fin)
            stage = classify_stock_stage(pscore)
            result["prosperity"] = {
                "composite": round(pscore.composite_score, 2),
                "delta_g": round(pscore.delta_g, 2),
                "revenue_score": round(pscore.revenue_score, 2),
                "profit_score": round(pscore.profit_score, 2),
                "slope_score": round(pscore.slope_score, 2),
                "duration_score": round(pscore.duration_score, 2),
                "stage": str(stage),
            }
            print(f"  景气度: composite={pscore.composite_score:.1f}, ΔG={pscore.delta_g:.2f}, stage={stage}")
    except Exception as e:
        result["prosperity_error"] = str(e)
        print(f"  景气度 ERROR: {e}")

    # 4. 五个补充因子
    factors = {}
    try:
        mom = analyze_momentum(client, ts_code)
        if mom:
            factors["momentum"] = {
                "score": round(mom.momentum_score, 2),
                "abs_score": round(mom.absolute_momentum_score, 2),
                "rs_pct": round(mom.rs_percentile, 2) if mom.rs_percentile else None,
                "window_returns": {k: round(v, 2) for k, v in mom.window_returns.items()} if mom.window_returns else None,
            }
            print(f"  动量: score={mom.momentum_score:.1f}")
    except Exception as e:
        factors["momentum_error"] = str(e)

    try:
        div = analyze_dividend(client, ts_code)
        if div:
            factors["dividend"] = {
                "score": round(div.dividend_score, 2),
                "consecutive_years": div.consecutive_years,
                "latest_yield_pct": round(div.latest_yield_pct, 2) if div.latest_yield_pct else None,
            }
            print(f"  股息: score={div.dividend_score:.1f}, 连续{div.consecutive_years}年, 收益率={div.latest_yield_pct}%")
    except Exception as e:
        factors["dividend_error"] = str(e)

    try:
        if fin and len(fin) >= 2 and "prosperity" in result:
            # rebuild pscore object for forecast (needs ProsperityScore object)
            pscore_obj = calculate_prosperity_score(fin)
            fc = analyze_forecast(client, ts_code, pscore_obj)
            if fc:
                factors["forecast"] = {
                    "leading_score": round(fc.leading_score, 2),
                    "p_change_mid": fc.p_change_mid,
                    "type": fc.type,
                    "is_stale": fc.is_stale,
                }
                print(f"  预告: leading={fc.leading_score:.1f}, type={fc.type}")
            else:
                factors["forecast"] = None
                print(f"  预告: 无")
        rev = analyze_forecast_revision(client, ts_code)
        if rev:
            factors["forecast_revision"] = {
                "direction": rev.revision_direction,
                "revision_pp": round(rev.revision_pp, 2) if rev.revision_pp else None,
                "revision_score": round(rev.revision_score, 2) if rev.revision_score else None,
            }
    except Exception as e:
        factors["forecast_error"] = str(e)

    try:
        hc = analyze_holder_concentration(client, ts_code)
        if hc:
            factors["holder_concentration"] = {
                "score": round(hc.score, 2) if hasattr(hc, 'score') else None,
                "trend": str(hc.trend) if hasattr(hc, 'trend') else None,
            }
            print(f"  筹码: score={getattr(hc, 'score', 'N/A')}, trend={getattr(hc, 'trend', 'N/A')}")
    except Exception as e:
        factors["holder_error"] = str(e)

    try:
        if fin:
            pq = analyze_profitability_quality(fin)
            factors["profitability"] = {
                "score": round(pq.score, 2) if hasattr(pq, 'score') else None,
            }
            # dump all attributes
            pq_dict = {}
            for attr in dir(pq):
                if not attr.startswith('_'):
                    val = getattr(pq, attr)
                    if not callable(val):
                        pq_dict[attr] = val
            factors["profitability_full"] = {k: (round(v,2) if isinstance(v, float) else v) for k,v in pq_dict.items()}
            print(f"  盈利质量: score={getattr(pq, 'score', 'N/A')}")
    except Exception as e:
        factors["profitability_error"] = str(e)

    result["factors"] = factors

    # 5. 股东户数趋势
    try:
        h = pro.stk_holdernumber(
            ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num"
        ).sort_values("end_date").tail(8)
        holder_rows = []
        prev = None
        for _, r in h.iterrows():
            num = int(r["holder_num"])
            chg = (num - prev) / prev * 100 if prev else None
            holder_rows.append({"end_date": str(r["end_date"]), "holder_num": num, "chg_pct": round(chg, 1) if chg else None})
            prev = num
        result["holder_trend"] = holder_rows
        if len(holder_rows) >= 2:
            latest_num = holder_rows[-1]["holder_num"]
            base_num = holder_rows[0]["holder_num"]
            trend = "集中(动能增强)" if latest_num < base_num else "分散(动能减弱)"
            result["holder_trend_judgment"] = trend
            result["holder_trend_change_pct"] = round((latest_num - base_num) / base_num * 100, 1)
            print(f"  户数: {len(holder_rows)} 期, {base_num}→{latest_num} ({trend})")
    except Exception as e:
        result["holder_error"] = str(e)
        print(f"  户数 ERROR: {e}")

    # 6. 相对市场估值
    try:
        rv = analyze_relative_valuation(pro, ts_code, name)
        result["relative_valuation"] = {
            "pe_ratio": round(rv.pe_ratio, 3) if rv.pe_ratio else None,
            "pe_ratio_pct": round(rv.pe_ratio_pct, 1) if rv.pe_ratio_pct else None,
            "erp": round(rv.erp, 2) if rv.erp else None,
            "quadrant": rv.quadrant,
            "quadrant_label": rv.quadrant_label,
            "composite_verdict": rv.composite_verdict,
            "benchmark": getattr(rv, 'benchmark', None),
            "benchmark_pe": getattr(rv, 'benchmark_pe', None),
        }
        print(f"  相对估值: ratio={rv.pe_ratio}, pct={rv.pe_ratio_pct}%, quadrant={rv.quadrant}, verdict={rv.composite_verdict}")
    except Exception as e:
        result["rv_error"] = str(e)
        print(f"  相对估值 ERROR: {e}")

    # 7. 时效性 - 业绩预告
    try:
        fcp = pro.forecast(ts_code=ts_code,
                           fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,summary",
                           limit=3)
        if len(fcp):
            fc_rows = []
            for _, r in fcp.iterrows():
                fc_rows.append({
                    "ann_date": str(r["ann_date"]),
                    "end_date": str(r["end_date"]),
                    "type": r["type"],
                    "p_change_min": r["p_change_min"],
                    "p_change_max": r["p_change_max"],
                    "summary": str(r.get("summary", ""))[:200],
                })
            result["forecast_raw"] = fc_rows
            print(f"  预告: {len(fc_rows)} 条, 最新 {fc_rows[0]['ann_date']} {fc_rows[0]['type']}")
    except Exception as e:
        result["forecast_raw_error"] = str(e)

    # 8. 十大流通股东（最新）
    try:
        t10 = pro.top10_floatholders(ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_name,hold_amount,hold_ratio")
        if len(t10):
            latest_end = t10["end_date"].max()
            t10_latest = t10[t10["end_date"] == latest_end]
            result["top10_float"] = {
                "end_date": str(latest_end),
                "total_ratio": round(t10_latest["hold_ratio"].sum(), 2),
                "holders": [{"name": r["holder_name"], "ratio": r["hold_ratio"]} for _, r in t10_latest.iterrows()],
            }
            print(f"  十大流通: {latest_end}, 合计{t10_latest['hold_ratio'].sum():.2f}%")
    except Exception as e:
        result["top10_error"] = str(e)

    return result


def main():
    all_results = {}
    for ts_code, name, industry in STOCKS:
        try:
            data = collect_stock(ts_code, name, industry)
            all_results[ts_code] = data
            out_file = OUTPUT_DIR / f"{ts_code}.json"
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            print(f"\n✅ {name} 写入 {out_file}")
        except Exception as e:
            print(f"\n❌ {name} ({ts_code}) 整体失败: {e}")
            import traceback
            traceback.print_exc()
            all_results[ts_code] = {"error": str(e), "name": name}

    # 汇总文件
    with open(OUTPUT_DIR / "_summary.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n{'='*60}\n全部完成, 汇总写入 {OUTPUT_DIR / '_summary.json'}")


if __name__ == "__main__":
    main()
