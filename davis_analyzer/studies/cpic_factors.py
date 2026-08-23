#!/usr/bin/env python3
"""国际复材 (301526.SZ) 5因子引擎 + 相对估值 + 困境层细节."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

import tushare as ts  # noqa: E402
from davis_analyzer.tushare_client import TushareClient  # noqa: E402
from davis_analyzer.financial_fetcher import fetch_financial_data  # noqa: E402
from davis_analyzer.prosperity import calculate_prosperity_score  # noqa: E402
from davis_analyzer.momentum import analyze_momentum  # noqa: E402
from davis_analyzer.dividend import analyze_dividend  # noqa: E402
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision  # noqa: E402
from davis_analyzer.holder_concentration import analyze_holder_concentration  # noqa: E402
from davis_analyzer.profitability import analyze_profitability_quality  # noqa: E402
from stockhot.valuation import analyze_relative_valuation  # noqa: E402

TS = "301526.SZ"
client = TushareClient()

fin = fetch_financial_data(client, TS, periods=12)
print(f"财务期数: {len(fin)}, 最新 {fin[0].report_period}")
for f in fin[:8]:
    print(f"  {f.report_period} rev={f.revenue/1e8:.2f}亿 np={f.net_profit} roe={f.roe} yoy_rev={f.yoy_revenue_growth} yoy_np={f.yoy_profit_growth} gm={f.grossprofit_margin} rd={f.rd_exp}")

pscore = calculate_prosperity_score(fin)
print(f"\n景气度 composite={pscore.composite_score:.2f} delta_g={pscore.delta_g:.2f}")

print("\n── momentum ──")
mom = analyze_momentum(client, TS)
if mom:
    print(f"score={mom.momentum_score} abs={mom.absolute_momentum_score} rs={mom.rs_percentile}")
    print(f"window_returns={mom.window_returns}")

print("\n── dividend ──")
div = analyze_dividend(client, TS)
print(f"score={div.dividend_score} years={div.consecutive_years} yield={div.latest_yield_pct} payout_years={div.payout_years}")

print("\n── forecast ──")
fc = analyze_forecast(client, TS, pscore)
if fc:
    print(f"leading={fc.leading_score} p_change_mid={fc.p_change_mid} type={fc.type} stale={fc.is_stale}")
rev = analyze_forecast_revision(client, TS)
if rev:
    print(f"revision: {rev.revision_direction} pp={rev.revision_pp} score={rev.revision_score}")

print("\n── holder_concentration ──")
hc = analyze_holder_concentration(client, TS)
if hc:
    print(f"score={hc.concentration_score} trend={hc.trend} latest_chg={hc.latest_chg_pct} counts={hc.holder_counts} periods={hc.periods}")

print("\n── profitability_quality ──")
pq = analyze_profitability_quality(fin)
print(f"score={pq.quality_score} gm={pq.latest_gross_margin} gm_delta={pq.gross_margin_delta} rd={pq.latest_rd_intensity}")

print("\n── relative valuation (基准: 创业板指) ──")
ts.set_token(os.environ["TUSHARE_TOKEN"])
pro = ts.pro_api()
rv = analyze_relative_valuation(pro, TS, "国际复材")
print(f"pe_ratio={rv.pe_ratio} pe_ratio_pct={rv.pe_ratio_pct} erp={rv.erp} risk_free={rv.risk_free_rate}")
print(f"index_pe={rv.index_pe} index_pe_pct={rv.index_pe_pct} stock_pe={getattr(rv,'stock_pe',None)} stock_pe_pct={getattr(rv,'stock_pe_pct',None)}")
print(f"quadrant={rv.quadrant} ({rv.quadrant_label}) verdict={rv.composite_verdict}")
for s in rv.signals:
    print("  -", s)
