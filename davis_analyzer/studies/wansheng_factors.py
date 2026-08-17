#!/usr/bin/env python3
"""万盛股份: 5因子引擎 + 相对估值 + 年度财务史."""
from __future__ import annotations

import json
import os

import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

from davis_analyzer.tushare_client import TushareClient  # noqa: E402
from davis_analyzer.financial_fetcher import fetch_financial_data  # noqa: E402
from davis_analyzer.prosperity import calculate_prosperity_score  # noqa: E402
from davis_analyzer.momentum import analyze_momentum  # noqa: E402
from davis_analyzer.dividend import analyze_dividend  # noqa: E402
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision  # noqa: E402
from davis_analyzer.holder_concentration import analyze_holder_concentration  # noqa: E402
from davis_analyzer.profitability import analyze_profitability_quality  # noqa: E402
from stockhot.valuation import analyze_relative_valuation  # noqa: E402
from stockhot.tushare_config import get_pro_api  # noqa: E402

TS = "603010.SH"
client = TushareClient()
pro = get_pro_api(timeout=60)
out = {}

fin = fetch_financial_data(client, TS, periods=12)
pscore = calculate_prosperity_score(fin)

mom = analyze_momentum(client, TS)
if mom:
    out["momentum"] = {"score": mom.momentum_score, "abs": mom.absolute_momentum_score,
                       "rs": mom.rs_percentile, "windows": mom.window_returns}

div = analyze_dividend(client, TS)
out["dividend"] = {"score": div.dividend_score, "years": div.consecutive_years,
                   "yield": div.latest_yield_pct, "payout_years": div.payout_years}

fcs = analyze_forecast(client, TS, pscore)
out["forecast_engine"] = {"score": fcs.leading_score, "p_change_mid": fcs.p_change_mid,
                          "type": fcs.type, "stale": fcs.is_stale} if fcs else None

rev = analyze_forecast_revision(client, TS)
out["forecast_revision"] = {"direction": rev.revision_direction, "pp": rev.revision_pp, "score": rev.revision_score} if rev else None

hc = analyze_holder_concentration(client, TS)
out["holder_conc"] = {"score": hc.concentration_score, "trend": hc.trend,
                      "latest_chg": hc.latest_chg_pct, "counts": hc.holder_counts} if hc else None

pq = analyze_profitability_quality(fin)
out["profitability"] = {"score": pq.quality_score, "gm": pq.latest_gross_margin,
                        "gm_delta": pq.gross_margin_delta, "rd": pq.latest_rd_intensity,
                        "sufficient": pq.data_sufficient} if pq else None

out["prosperity"] = {"composite": pscore.composite_score, "delta_g": pscore.delta_g,
                     "rev_score": pscore.revenue_score, "profit_score": pscore.profit_score,
                     "slope": pscore.slope_score, "duration": pscore.duration_score}

# 相对估值
import tushare as ts  # noqa: E402
ts.set_token(os.environ["TUSHARE_TOKEN"])
rv = analyze_relative_valuation(pro, TS, "万盛股份")
out["relative_valuation"] = {
    "pe_ratio": rv.pe_ratio, "pe_ratio_pct": rv.pe_ratio_pct, "erp": rv.erp,
    "quadrant": rv.quadrant, "quadrant_label": rv.quadrant_label,
    "composite_verdict": rv.composite_verdict, "signals": rv.signals,
    "index_pe": getattr(rv, "index_pe", None), "index_pe_pct": getattr(rv, "index_pe_pct", None),
    "risk_free": getattr(rv, "risk_free_rate", None), "stock_pe": getattr(rv, "stock_pe", None),
    "stock_pe_pct": getattr(rv, "stock_pe_pct", None),
}

# 年度财务史 2019-2025
rows = []
for y in range(2019, 2026):
    r = pro.income(ts_code=TS, period=f"{y}1231", fields="ts_code,end_date,total_revenue,n_income,n_income_attr_p,rd_exp", limit=1)
    fi = pro.fina_indicator(ts_code=TS, period=f"{y}1231", fields="ts_code,end_date,grossprofit_margin,roe,debt_to_assets", limit=1)
    row = {"year": y}
    if len(r):
        row["rev_yi"] = round(float(r.iloc[0]["total_revenue"]) / 1e8, 2)
        row["np_yi"] = round(float(r.iloc[0]["n_income"]) / 1e8, 3)
        row["np_attr_yi"] = round(float(r.iloc[0]["n_income_attr_p"]) / 1e8, 3) if pd.notna(r.iloc[0]["n_income_attr_p"]) else None
        row["rd_yi"] = round(float(r.iloc[0]["rd_exp"]) / 1e8, 2) if pd.notna(r.iloc[0]["rd_exp"]) else None
    if len(fi):
        row["gm"] = round(float(fi.iloc[0]["grossprofit_margin"]), 2) if pd.notna(fi.iloc[0]["grossprofit_margin"]) else None
        row["roe"] = round(float(fi.iloc[0]["roe"]), 2) if pd.notna(fi.iloc[0]["roe"]) else None
        row["debt"] = round(float(fi.iloc[0]["debt_to_assets"]), 2) if pd.notna(fi.iloc[0]["debt_to_assets"]) else None
    rows.append(row)
    pd.io.common  # noqa
    import time; time.sleep(0.35)
out["annual"] = rows

# 2025/2026 单季拆分
q = []
for p in ["20250331", "20250630", "20250930", "20251231", "20260331"]:
    r = pro.income(ts_code=TS, period=p, fields="ts_code,end_date,total_revenue,n_income,n_income_attr_p", limit=1)
    if len(r):
        q.append({"period": p, "rev_yi": round(float(r.iloc[0]["total_revenue"]) / 1e8, 2),
                  "np_attr_yi": round(float(r.iloc[0]["n_income_attr_p"]) / 1e8, 3) if pd.notna(r.iloc[0]["n_income_attr_p"]) else None})
    import time; time.sleep(0.35)
out["quarters"] = q

print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
with open(".sisyphus/evidence/wansheng/factors.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2, default=str)
