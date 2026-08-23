#!/usr/bin/env python3
"""三祥新材数据包第 2 部分：修复 §4b 起的段落."""
from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

from stockhot.tushare_config import get_pro_api  # noqa: E402

from davis_analyzer.tushare_client import TushareClient  # noqa: E402
from davis_analyzer.financial_fetcher import fetch_financial_data  # noqa: E402
from davis_analyzer.prosperity import calculate_prosperity_score  # noqa: E402
from davis_analyzer.momentum import analyze_momentum  # noqa: E402
from davis_analyzer.dividend import analyze_dividend  # noqa: E402
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision  # noqa: E402
from davis_analyzer.holder_concentration import analyze_holder_concentration  # noqa: E402
from davis_analyzer.profitability import analyze_profitability_quality  # noqa: E402

TS = "603663.SH"
PEERS = {"002167.SZ": "东方锆业", "603407.SH": "长裕集团", "300285.SZ": "国瓷材料"}

pro = get_pro_api(timeout=30)
client = TushareClient()

print("§4b 十大流通股东")
tf = pro.top10_floatholders(ts_code=TS)
print("columns:", list(tf.columns))
if len(tf):
    tf["end_date"] = tf["end_date"].astype(str)
    for ed in sorted(tf["end_date"].unique())[-2:]:
        sub = tf[tf["end_date"] == ed].copy()
        rcol = "ratio" if "ratio" in sub.columns else sub.columns[-1]
        sub[rcol] = pd.to_numeric(sub[rcol], errors="coerce")
        print(f"  {ed}: top10合计={sub[rcol].sum():.2f}%")
        print(sub.head(6).to_string(index=False))

print("=" * 70)
print("§5 财务引擎序列（fetch_financial_data 12期）")
fin = fetch_financial_data(client, TS, periods=12)
for f in fin:
    print(f"  {f.report_period}: rev={f.revenue / 1e8:.2f}亿 np={f.net_profit} eps={f.eps} "
          f"roe={f.roe}% yoy_rev={f.yoy_revenue_growth} yoy_np={f.yoy_profit_growth} gm={getattr(f, 'grossprofit_margin', None)} rd={getattr(f, 'rd_exp', None)}")

print("=" * 70)
print("§6 5因子引擎")
pscore = calculate_prosperity_score(fin)
print(f"prosperity: composite={pscore.composite_score:.2f} revenue={pscore.revenue_score:.2f} profit={pscore.profit_score:.2f} slope={pscore.slope_score:.2f} duration={pscore.duration_score:.2f} delta_g={pscore.delta_g:.2f}")

mom = analyze_momentum(client, TS)
if mom:
    print(f"momentum: score={mom.momentum_score:.2f} abs={mom.absolute_momentum_score:.2f} rs={mom.rs_percentile} windows={mom.window_returns}")
else:
    print("momentum: None")

div = analyze_dividend(client, TS)
print(f"dividend: score={div.dividend_score:.2f} years={div.consecutive_years} yield={div.latest_yield_pct} payout_years={div.payout_years}")

fcs = analyze_forecast(client, TS, pscore)
if fcs:
    print(f"forecast: leading={fcs.leading_score:.2f} pchg_mid={fcs.p_change_mid} type={fcs.type} stale={fcs.is_stale}")
else:
    print("forecast: None")
rev = analyze_forecast_revision(client, TS)
if rev:
    print(f"forecast_revision: dir={rev.revision_direction} pp={rev.revision_pp} score={rev.revision_score}")
else:
    print("forecast_revision: None")

hc = analyze_holder_concentration(client, TS)
if hc:
    print(f"holder_conc: score={hc.concentration_score:.2f} trend={hc.trend} latest_chg={hc.latest_chg_pct} counts={hc.holder_counts} periods={hc.periods}")
else:
    print("holder_conc: None")

pq = analyze_profitability_quality(fin)
print(f"profit_quality: score={pq.quality_score:.2f} gm={pq.latest_gross_margin} gm_delta={pq.gross_margin_delta} rd={pq.latest_rd_intensity}")

print("=" * 70)
print("§7 相对估值（stockhot.valuation）")
from stockhot.valuation import analyze_relative_valuation  # noqa: E402
rv = analyze_relative_valuation(pro, TS, "三祥新材")
for attr in ["pe_ratio", "pe_ratio_pct", "erp", "quadrant", "quadrant_label", "composite_verdict", "index_pe", "index_pe_pct", "risk_free_rate", "stock_pe", "stock_pe_pct"]:
    print(f"  {attr}={getattr(rv, attr, None)}")
print("  signals:", getattr(rv, "signals", None))

print("=" * 70)
print("§8 可比公司快照（最新交易日）")
for code, name in PEERS.items():
    try:
        d = pro.daily_basic(ts_code=code, limit=1)
        if len(d):
            r = d.iloc[0]
            print(f"  {name}({code}) {r['trade_date']}: close={r['close']} pe={r['pe_ttm']} pb={r['pb']} ps={r['ps']} mv={r['total_mv'] / 1e4:.1f}亿")
        sb2 = pro.stock_basic(ts_code=code, fields="ts_code,name,industry")
        print("    basic:", sb2.iloc[0].to_dict())
        inc2 = pro.income(ts_code=code, fields="ts_code,ann_date,end_date,total_revenue,n_income", period=None)
        inc2 = inc2.drop_duplicates(subset=["end_date"]).sort_values("end_date").tail(3)
        print(inc2.to_string(index=False))
        fc2 = pro.forecast(ts_code=code, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
        if len(fc2):
            print(fc2.head(3).to_string(index=False))
    except Exception as e:
        print(f"  {code} error: {e}")

print("=" * 70)
print("§9 动量手工复核（pro.daily + adj_factor）")
d1 = pro.daily(ts_code=TS, start_date=(date.today() - timedelta(days=420)).strftime("%Y%m%d"), end_date=date.today().strftime("%Y%m%d"))
d2 = pro.adj_factor(ts_code=TS, start_date=(date.today() - timedelta(days=420)).strftime("%Y%m%d"), end_date=date.today().strftime("%Y%m%d"))
if len(d1) and len(d2):
    m = d1.merge(d2[["trade_date", "adj_factor"]], on="trade_date").sort_values("trade_date").reset_index(drop=True)
    m["qfq"] = pd.to_numeric(m["close"], errors="coerce") * pd.to_numeric(m["adj_factor"], errors="coerce")
    m["pct"] = m["close"].pct_change() * 100
    last = m.iloc[-1]
    print(f"  latest {last['trade_date']} close={last['close']} pct={last['pct']:.2f}%")
    for w in [20, 60, 120, 250]:
        if len(m) > w:
            base = m.iloc[-1 - w]["qfq"]
            print(f"  {w}d return (复权): {(last['qfq'] / base - 1) * 100:.1f}%")
    for td in ["20260716", "20260717", "20260721", "20260731", "20260805", "20260814"]:
        row = m[m["trade_date"] == td]
        if len(row):
            print(f"  {td}: close={row.iloc[0]['close']} pct={row.iloc[0]['pct']:.2f}%")
    # 除权检查
    m["adjf"] = pd.to_numeric(m["adj_factor"], errors="coerce")
    m["adjf_chg"] = m["adjf"].pct_change()
    splits = m[m["adjf_chg"].abs() > 0.01]
    print("  除权日(复权因子跳变>1%):")
    print(splits[["trade_date", "close", "adj_factor", "adjf_chg"]].to_string(index=False))
    w52 = m.tail(250)
    closes = pd.to_numeric(w52["close"], errors="coerce")
    print(f"  52w high(不复权)={closes.max():.2f} low={closes.min():.2f}")
    # 年初至今
    ytd = m[m["trade_date"] >= "20260101"]
    qfq_ytd = pd.to_numeric(ytd["qfq"], errors="coerce")
    print(f"  YTD 复权涨幅: {(qfq_ytd.iloc[-1] / qfq_ytd.iloc[0] - 1) * 100:+.1f}%")
    print(f"  年初首日 {ytd['trade_date'].iloc[0]} close={ytd['close'].iloc[0]}")

print("=" * 70)
print("§10 分红记录")
dv = pro.dividend(ts_code=TS, fields="ts_code,end_date,ann_date,div_proc,cash_div_taxable,ex_date")
print(dv.to_string(index=False))

print("=" * 70)
print("§11 总股本（bak_basic）")
try:
    bb = pro.bak_basic(ts_code=TS, limit=2, fields="ts_code,trade_date,total_share,float_share")
    print(bb.to_string(index=False))
except Exception as e:
    print("bak_basic error:", e)

print("DONE")
