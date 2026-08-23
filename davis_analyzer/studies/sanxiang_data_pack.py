#!/usr/bin/env python3
"""三祥新材 (603663.SH) 研报数据包采集脚本.

一次性采集：时效性校验 / 3年估值分位(分段batch) / 股东户数 / 十大流通股东 /
5因子引擎 / 相对估值 / 可比公司快照 / 原生财务序列 / 动量手工复核 / 分红.
"""
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

print("=" * 70)
print("§1 时效性校验")
sb = pro.stock_basic(ts_code=TS, fields="ts_code,name,industry,market,list_date")
print(sb.to_string(index=False))
db1 = pro.daily_basic(ts_code=TS, limit=1)
print("daily_basic latest:", db1[["trade_date", "close", "pe_ttm", "pb", "ps", "total_mv", "turnover_rate", "dv_ratio"]].to_string(index=False))
inc1 = pro.income(ts_code=TS, fields="ts_code,ann_date,end_date,f_ann_date", limit=3)
print("income latest 3 rows:\n", inc1.to_string(index=False))
fc = pro.forecast(ts_code=TS, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,npm_min,npm_max,net_profit_min,net_profit_max")
print("forecast all:\n", fc.to_string(index=False))

print("=" * 70)
print("§2 3年 daily_basic 分段拉取 → PE/PB/PS 分位")


def batch_daily_basic(ts_code: str, lookback_years: float = 3.0, chunk_days: int = 240):
    end = date.today()
    total = int(lookback_years * 365)
    frames = []
    cursor = end - timedelta(days=total)
    while cursor <= end:
        nxt = min(cursor + timedelta(days=chunk_days), end)
        df = pro.daily_basic(
            ts_code=ts_code,
            start_date=cursor.strftime("%Y%m%d"),
            end_date=nxt.strftime("%Y%m%d"),
            fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv,turnover_rate,dv_ratio",
        )
        if df is not None and len(df):
            frames.append(df)
        cursor = nxt + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["trade_date"])
    return out.sort_values("trade_date").reset_index(drop=True)


db = batch_daily_basic(TS)
print(f"rows={len(db)}, first={db['trade_date'].iloc[0]}, last={db['trade_date'].iloc[-1]}")
for col in ["pe_ttm", "pb", "ps", "total_mv"]:
    s = pd.to_numeric(db[col], errors="coerce").dropna()
    cur = s.iloc[-1]
    pct = (s < cur).sum() / len(s) * 100
    q = {p: s.quantile(p / 100) for p in [10, 25, 50, 75, 90, 95]}
    print(f"{col}: cur={cur:.2f} pct={pct:.1f}% n={len(s)} | " +
          " ".join(f"p{p}={v:.2f}" for p, v in q.items()))
print("recent 8 trade days:\n", db.tail(8)[["trade_date", "close", "pe_ttm", "pb", "total_mv", "turnover_rate"]].to_string(index=False))
# 7/17 与 8/14 区间
db["close"] = pd.to_numeric(db["close"], errors="coerce")
sub = db[db["trade_date"] >= "20260701"]
print("since 20260701:\n", sub[["trade_date", "close", "pct_chg" if "pct_chg" in sub.columns else "close"]].to_string(index=False) if len(sub) else "none")

print("=" * 70)
print("§3 原生 income/fina_indicator 近 10 期")
inc = pro.income(ts_code=TS, fields="ts_code,ann_date,end_date,total_revenue,n_income", period=None)
inc = inc.drop_duplicates(subset=["end_date"]).sort_values("end_date").tail(10)
print(inc.to_string(index=False))
fi = pro.fina_indicator(ts_code=TS, fields="ts_code,end_date,grossprofit_margin,netprofit_margin,roe,debt_to_assets,ocf_to_profit,rd_exp")
fi = fi.drop_duplicates(subset=["end_date"]).sort_values("end_date").tail(10)
print("fina_indicator:\n", fi.to_string(index=False))

print("=" * 70)
print("§4 股东户数（近 10 期）")
h = pro.stk_holdernumber(ts_code=TS, fields="ts_code,ann_date,end_date,holder_num")
h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(10)
prev = None
for _, r in h.iterrows():
    num = int(r["holder_num"])
    chg = f"{(num - prev) / prev * 100:+.1f}%" if prev else "基期"
    print(f"  {r['end_date']} (ann {r['ann_date']}): {num:,} ({chg})")
    prev = num

print("§4b 十大流通股东（最近 2 个报告期合计）")
tf = pro.top10_floatholders(ts_code=TS)
if len(tf):
    tf["end_date"] = tf["end_date"].astype(str)
    for ed in sorted(tf["end_date"].unique())[-2:]:
        sub = tf[tf["end_date"] == ed]
        total_ratio = pd.to_numeric(sub["ratio"], errors="coerce").sum()
        print(f"  {ed}: top10合计={total_ratio:.2f}%")
        print(sub.head(5)[["holder_name", "ratio"]].to_string(index=False))

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
    except Exception as e:
        print(f"  {code} error: {e}")

print("=" * 70)
print("§9 动量手工复核（pro.daily + adj_factor）")
d1 = pro.daily(ts_code=TS, start_date=(date.today() - timedelta(days=400)).strftime("%Y%m%d"), end_date=date.today().strftime("%Y%m%d"))
d2 = pro.adj_factor(ts_code=TS, start_date=(date.today() - timedelta(days=400)).strftime("%Y%m%d"), end_date=date.today().strftime("%Y%m%d"))
if len(d1) and len(d2):
    m = d1.merge(d2[["trade_date", "adj_factor"]], on="trade_date").sort_values("trade_date").reset_index(drop=True)
    m["qfq"] = pd.to_numeric(m["close"], errors="coerce") * pd.to_numeric(m["adj_factor"], errors="coerce")
    m["pct"] = m["close"].pct_change() * 100
    last = m.iloc[-1]
    print(f"  latest {last['trade_date']} close={last['close']} pct={last['pct']:.2f}%")
    for w in [20, 60, 120, 250]:
        if len(m) > w:
            base = m.iloc[-1 - w]["qfq"]
            print(f"  {w}d return: {(last['qfq'] / base - 1) * 100:.1f}%")
    jul = m[m["trade_date"] >= "20260716"]
    print("  since 0716 daily:")
    print(jul[["trade_date", "close", "pct"]].to_string(index=False))
    # 关键日期
    for td in ["20260717", "20260721", "20260731", "20260814"]:
        row = m[m["trade_date"] == td]
        if len(row):
            print(f"  {td}: close={row.iloc[0]['close']}")
    c717 = m[m["trade_date"] == "20260717"]
    c814 = m[m["trade_date"] == "20260814"]
    if len(c717) and len(c814):
        print(f"  区间 0717→0814: {(float(c814.iloc[0]['close']) / float(c717.iloc[0]['close']) - 1) * 100:+.1f}%")
    # 52周高低
    w52 = m.tail(250)
    print(f"  52w high={pd.to_numeric(w52['close']).max():.2f} low={pd.to_numeric(w52['close']).min():.2f}")

print("=" * 70)
print("§10 分红记录")
dv = pro.dividend(ts_code=TS, fields="ts_code,end_date,ann_date,div_proc,cash_div_taxable,ex_date")
print(dv.to_string(index=False))

print("=" * 70)
print("§11 总股本（bak_basic）")
try:
    bb = pro.bak_basic(ts_code=TS, limit=1, fields="ts_code,trade_date,total_share,float_share")
    print(bb.to_string(index=False))
except Exception as e:
    print("bak_basic error:", e)
    # fallback: daily_basic 没有 share; 用 close*total_mv 推
    print("用市值/收盘价推算股本")

print("DONE")
