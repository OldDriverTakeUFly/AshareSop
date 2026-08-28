# gpu4_engines.py — 沐曦/摩尔线程 5因子引擎 + 景气度 + 相对估值
from __future__ import annotations

import os
import sys

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")

from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality

client = TushareClient()

for code, name in [("688802.SH", "沐曦股份"), ("688795.SH", "摩尔线程")]:
    print(f"\n{'=' * 60}\n===== {name} ({code}) =====\n{'=' * 60}")

    fin = fetch_financial_data(client, code, periods=12)
    print(f"\n[财务] {len(fin)} 期, 最新 {fin[0].report_period}")
    for f in fin[:6]:
        rev = f"{f.revenue/1e8:.2f}亿" if f.revenue else "NA"
        np_ = f"{float(f.net_profit)/1e8:.2f}亿" if f.net_profit not in (None, "") else "NA"
        yoy = f"{f.yoy_revenue_growth*100:+.1f}%" if f.yoy_revenue_growth is not None else "NA"
        print(f"  {f.report_period}: rev={rev} np={np_} yoy={yoy} roe={f.roe}")

    try:
        ps = calculate_prosperity_score(fin)
        print(f"\n[景气度] composite={ps.composite_score:.1f} ΔG={ps.delta_g:.2f} "
              f"(营收分={ps.revenue_score:.1f} 利润分={ps.profit_score:.1f} "
              f"斜率分={ps.slope_score:.1f} 持续分={ps.duration_score:.1f})")
        print(f"[阶段] {classify_stock_stage(ps)}")
    except Exception as e:
        ps = None
        print(f"[景气度] 失败: {e}")

    print("\n[5因子]")
    try:
        mom = analyze_momentum(client, code)
        if mom:
            print(f"  动量: score={mom.momentum_score} abs={mom.absolute_momentum_score} RS%={mom.rs_percentile} windows={mom.window_returns}")
        else:
            print("  动量: None")
    except Exception as e:
        print(f"  动量: 异常 {e}")
    try:
        div = analyze_dividend(client, code)
        print(f"  股息: score={div.dividend_score} 连续={div.consecutive_years}年 yield={div.latest_yield_pct}%")
    except Exception as e:
        print(f"  股息: 异常 {e}")
    try:
        if ps is not None:
            fc = analyze_forecast(client, code, ps)
            print(f"  预告: {'None' if fc is None else f'score={fc.leading_score} type={fc.type} p_mid={fc.p_change_mid} stale={fc.is_stale}'}")
        else:
            print("  预告: 跳过(无pscore)")
    except Exception as e:
        print(f"  预告: 异常 {e}")
    try:
        rev_ = analyze_forecast_revision(client, code)
        print(f"  预告修正: {'None' if rev_ is None else f'{rev_.revision_direction} {rev_.revision_pp}pp score={rev_.revision_score}'}")
    except Exception as e:
        print(f"  预告修正: 异常 {e}")
    try:
        hc = analyze_holder_concentration(client, code)
        if hc:
            print(f"  筹码: score={hc.concentration_score} trend={hc.trend} latest_chg={hc.latest_chg_pct}% periods={hc.periods}")
        else:
            print("  筹码: None")
    except Exception as e:
        print(f"  筹码: 异常 {e}")
    try:
        pq = analyze_profitability_quality(fin)
        print(f"  盈利质量: score={pq.quality_score} 毛利率={pq.latest_gross_margin} 毛利率Δ={pq.gross_margin_delta} 研发强度={pq.latest_rd_intensity}")
    except Exception as e:
        print(f"  盈利质量: 异常 {e}")

# 相对估值
print(f"\n{'=' * 60}\n===== 相对估值(科创50基准) =====\n{'=' * 60}")
from stockhot.valuation import analyze_relative_valuation

from stockhot.tushare_config import get_pro_api
pro = get_pro_api(timeout=30)
for code, name in [("688802.SH", "沐曦股份"), ("688795.SH", "摩尔线程")]:
    try:
        rv = analyze_relative_valuation(pro, code, name)
        print(f"\n{name}: pe_ratio={rv.pe_ratio} pe_ratio_pct={rv.pe_ratio_pct} erp={rv.erp} "
              f"quadrant={rv.quadrant}({rv.quadrant_label}) verdict={rv.composite_verdict}")
        print(f"  index_pe={rv.index_pe} index_pe_pct={rv.index_pe_pct} rf={rv.risk_free_rate}")
        for s in rv.signals:
            print(f"  - {s}")
    except Exception as e:
        print(f"{name}: 异常 {e}")
print("\nDONE")
