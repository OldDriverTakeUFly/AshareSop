#!/usr/bin/env python3
"""长裕集团 (603407.SH) 研报数据采集脚本.

采集: 时效性校验 / 原始财务明细 / 股东户数 / 十大流通股东 / 5 因子引擎 / 相对估值 /
日线行情 / 分红 / 可比公司估值快照.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

from stockhot.tushare_config import get_pro_api  # noqa: E402

pro = get_pro_api(timeout=30)

TS = "603407.SH"
OUT = []


def log(*args):
    line = " ".join(str(a) for a in args)
    OUT.append(line)
    print(line)


# ── 0. 股票基本信息核对（防张冠李戴）──
basic = pro.stock_basic(ts_code=TS, fields="ts_code,name,industry,area,list_date,market")
log("== stock_basic ==")
log(basic.to_string(index=False))

# ── 1. 时效性校验 ──
log("\n== freshness ==")
db = pro.daily_basic(ts_code=TS, limit=3, fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv,turnover_rate,dv_ratio")
log("daily_basic latest 3:")
log(db.to_string(index=False))

inc = pro.income(ts_code=TS, fields="ts_code,ann_date,end_date,f_ann_date,revenue,n_income", limit=10)
log("\nincome latest 10 (ann_date,end_date,revenue,n_income):")
log(inc.to_string(index=False))

fc = pro.forecast(ts_code=TS, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
log("\nforecast all:")
if len(fc):
    log(fc.to_string(index=False))
else:
    log("(empty)")

# ── 2. 原始财务明细（income + fina_indicator 全部期数）──
log("\n== income detail ==")
inc2 = pro.income(ts_code=TS, fields="ts_code,end_date,revenue,total_cogs,operate_profit,n_income,n_income_attr_p,income_tax")
log(inc2.to_string(index=False))

log("\n== fina_indicator ==")
fi = pro.fina_indicator(ts_code=TS, fields="ts_code,end_date,ann_date,grossprofit_margin,netprofit_margin,roe,roe_dt,debt_to_assets,ocf_to_profit,or_yoy,netprofit_yoy,eps,rd_exp")
log(fi.to_string(index=False))

log("\n== balancesheet key ==")
bs = pro.balancesheet(ts_code=TS, fields="ts_code,end_date,total_assets,total_liab,total_share,money_cap,inventories,fix_assets")
log(bs.to_string(index=False))

log("\n== cashflow ==")
cf = pro.cashflow(ts_code=TS, fields="ts_code,end_date,n_cashflow_act,n_cashflow_inv_act,n_cashflow_finance_act,free_cashflow")
log(cf.to_string(index=False))

# ── 3. 股东户数 ──
log("\n== stk_holdernumber ==")
h = pro.stk_holdernumber(ts_code=TS, fields="ts_code,ann_date,end_date,holder_num")
h = h.dropna(subset=["holder_num"]).sort_values("end_date")
log(h.to_string(index=False))

# ── 4. 十大流通股东 ──
log("\n== top10_floatholders ==")
t10 = pro.top10_floatholders(ts_code=TS, fields="ts_code,ann_date,end_date,holder_name,hold_ratio")
t10 = t10.sort_values(["end_date", "hold_ratio"], ascending=[True, False])
log(t10.to_string(index=False))

# ── 5. 分红 ──
log("\n== dividend ==")
dv = pro.dividend(ts_code=TS, fields="ts_code,end_date,ann_date,div_proc,cash_div_taxless,cash_div,div_ratio,base_share")
log(dv.to_string(index=False) if len(dv) else "(empty)")

# ── 6. 日线（上市以来）──
log("\n== daily since listing ==")
d = pro.daily(ts_code=TS, start_date="20260401", end_date="20260814",
              fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount")
d = d.sort_values("trade_date")
log(f"rows={len(d)} first={d['trade_date'].iloc[0]} last={d['trade_date'].iloc[-1]}")
log("close series:")
for _, r in d.iterrows():
    log(f"  {r['trade_date']} close={r['close']} pct={r['pct_chg']}%")

# ── 7. 5 因子引擎 ──
log("\n== factor engines ==")
from davis_analyzer.tushare_client import TushareClient  # noqa: E402
from davis_analyzer.financial_fetcher import fetch_financial_data  # noqa: E402
from davis_analyzer.prosperity import calculate_prosperity_score  # noqa: E402
from davis_analyzer.momentum import analyze_momentum  # noqa: E402
from davis_analyzer.dividend import analyze_dividend  # noqa: E402
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision  # noqa: E402
from davis_analyzer.holder_concentration import analyze_holder_concentration  # noqa: E402
from davis_analyzer.profitability import analyze_profitability_quality  # noqa: E402

client = TushareClient()
fin = fetch_financial_data(client, TS, periods=12)
log(f"fin periods={len(fin)}")
for f in fin:
    rev_yoy = f"{f.yoy_revenue_growth*100:.1f}%" if f.yoy_revenue_growth is not None else "None"
    prof_yoy = f"{f.yoy_profit_growth*100:.1f}%" if f.yoy_profit_growth is not None else "None"
    log(f"  {f.report_period} rev={f.revenue} np={f.net_profit} eps={f.eps} roe={f.roe} "
        f"ocf={f.operating_cf} rev_yoy={rev_yoy} prof_yoy={prof_yoy} gm={f.grossprofit_margin} rd={f.rd_exp}")

pscore = calculate_prosperity_score(fin)
log(f"prosperity: composite={pscore.composite_score} delta_g={pscore.delta_g} "
    f"rev={pscore.revenue_score} profit={pscore.profit_score} slope={pscore.slope_score} dur={pscore.duration_score}")

mom = analyze_momentum(client, TS)
log(f"momentum: score={mom.momentum_score} abs={mom.absolute_momentum_score} rs_pct={mom.rs_percentile} windows={mom.window_returns}")

div = analyze_dividend(client, TS)
log(f"dividend: score={div.dividend_score} years={div.consecutive_years} yield={div.latest_yield_pct} payout_years={div.payout_years}")

fcst = analyze_forecast(client, TS, pscore)
log(f"forecast signal: {fcst}")

rev = analyze_forecast_revision(client, TS)
log(f"forecast revision: {rev}")

hc = analyze_holder_concentration(client, TS)
log(f"holder_conc: score={hc.concentration_score} trend={hc.trend} latest_chg={hc.latest_chg_pct} counts={hc.holder_counts} periods={hc.periods}")

pq = analyze_profitability_quality(fin)
log(f"profitability_quality: score={pq.quality_score} gm={pq.latest_gross_margin} gm_delta={pq.gross_margin_delta} rd={pq.latest_rd_intensity}")

# ── 8. 相对估值 ──
log("\n== relative valuation ==")
from stockhot.valuation import analyze_relative_valuation  # noqa: E402
rv = analyze_relative_valuation(pro, TS, "长裕集团")
log(f"pe_ratio={rv.pe_ratio} pe_ratio_pct={rv.pe_ratio_pct} erp={rv.erp} quadrant={rv.quadrant} "
    f"label={rv.quadrant_label} verdict={rv.composite_verdict}")
log(f"signals={rv.signals}")
for attr in ("stock_pe", "stock_pe_pct", "index_pe", "index_pe_pct", "risk_free_rate", "benchmark"):
    log(f"  {attr}={getattr(rv, attr, None)}")

# ── 9. 可比公司估值快照 ──
log("\n== comps daily_basic latest ==")
for code in ["002167.SZ", "603663.SH", "300285.SZ"]:
    c = pro.daily_basic(ts_code=code, limit=1,
                        fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv,turnover_rate")
    log(c.to_string(index=False))

log("\n== comps fina_indicator 2025 annual + latest ==")
for code in ["002167.SZ", "603663.SH", "300285.SZ"]:
    f2 = pro.fina_indicator(ts_code=code, period="20251231",
                            fields="ts_code,end_date,grossprofit_margin,netprofit_margin,roe,debt_to_assets,or_yoy,netprofit_yoy,rd_exp")
    log(f2.to_string(index=False))

log("\n== comps income 2023-2025 annual ==")
for code in ["002167.SZ", "603663.SH", "300285.SZ", "603407.SH"]:
    i2 = pro.income(ts_code=code, period="20251231", fields="ts_code,end_date,revenue,n_income")
    log(i2.to_string(index=False))

# 保存
with open(".sisyphus/evidence/changyu/data_collect.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(OUT))
print("\n[saved] .sisyphus/evidence/changyu/data_collect.txt")
