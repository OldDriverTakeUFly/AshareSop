#!/usr/bin/env python3
"""东材科技 (601208.SH) 研报综合取数脚本.

覆盖：时效校验 / 原生财务轨迹 / 3年估值分位(分段直连) / 股东户数 /
十大流通股东 / 分红 / 5 因子引擎 / 相对估值 / 同业估值快照 / 主营构成.

用法: PYTHONPATH=. .venv/bin/python davis_analyzer/studies/dongcai_data.py
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta

import pandas as pd
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

from stockhot.tushare_config import get_pro_api  # noqa: E402

pro = get_pro_api(timeout=30)
TS = "601208.SH"
OUT: dict = {}

# ── 0. 代码核对（防张冠李戴）──
basic = pro.stock_basic(ts_code=TS, fields="ts_code,name,industry,area,list_date")
print("[0] stock_basic:", basic.to_dict("records"))
OUT["stock_basic"] = basic.to_dict("records")

# ── 1. 时效校验 ──
db1 = pro.daily_basic(ts_code=TS, limit=1)
inc1 = pro.income(ts_code=TS, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
fc = pro.forecast(ts_code=TS, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,network_min,network_max")
print("[1] latest trade:", db1.iloc[0]["trade_date"] if len(db1) else "none")
print("    latest income period:", inc1.iloc[0]["end_date"], "ann:", inc1.iloc[0]["ann_date"])
print("    forecast:", fc.to_dict("records") if len(fc) else "none")
OUT["freshness"] = {
    "latest_trade": db1.iloc[0]["trade_date"] if len(db1) else None,
    "latest_income_period": inc1.iloc[0]["end_date"] if len(inc1) else None,
    "latest_income_ann": inc1.iloc[0]["ann_date"] if len(inc1) else None,
    "forecast": fc.to_dict("records"),
}

# ── 2. 原生财务轨迹（累计口径, 2018 年起）──
inc = pro.income(ts_code=TS, start_date="20180101",
                 fields="ts_code,end_date,ann_date,total_revenue,n_income_attr_p")
fi = pro.fina_indicator(ts_code=TS, start_date="20180101",
                        fields="ts_code,end_date,grossprofit_margin,netprofit_margin,roe,debt_to_assets,rd_exp_ratio")
cf = pro.cashflow(ts_code=TS, start_date="20180101",
                  fields="ts_code,end_date,n_cashflow_act")
inc = inc.drop_duplicates("end_date").sort_values("end_date")
fi = fi.drop_duplicates("end_date").sort_values("end_date")
cf = cf.drop_duplicates("end_date").sort_values("end_date")
m = inc.merge(fi, on="end_date").merge(cf, on="end_date")
print("[2] income trajectory:")
for _, r in m.iterrows():
    print(f"  {r['end_date']} ann={r['ann_date']} rev={r['total_revenue']/1e8:.2f}亿 "
          f"np={r['n_income_attr_p']/1e8:.3f}亿 gm={r['grossprofit_margin']}% "
          f"roe={r['roe']}% debt={r['debt_to_assets']}% ocf={r['n_cashflow_act']/1e8:.2f}亿")
OUT["fin_trajectory"] = m.to_dict("records")

# ── 3. 3 年估值分位（分段直连 ≤500 天/段）──
end_d = date(2026, 8, 14)
start_d = end_d - timedelta(days=1095)
frames = []
cur = start_d
while cur < end_d:
    seg_end = min(cur + timedelta(days=499), end_d)
    seg = pro.daily_basic(ts_code=TS, start_date=cur.strftime("%Y%m%d"),
                          end_date=seg_end.strftime("%Y%m%d"),
                          fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,close,turnover_rate")
    if len(seg):
        frames.append(seg)
    cur = seg_end + timedelta(days=1)
val = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True)
val = val.sort_values("trade_date").reset_index(drop=True)
print(f"[3] valuation rows={len(val)} first={val['trade_date'].iloc[0]} last={val['trade_date'].iloc[-1]}")
for col, label in [("pe_ttm", "PE"), ("pb", "PB"), ("ps", "PS")]:
    s = pd.to_numeric(val[col], errors="coerce").dropna()
    cur_v = s.iloc[-1]
    pct = (s < cur_v).sum() / len(s) * 100
    qs = {p: round(s.quantile(p / 100), 2) for p in [10, 25, 50, 75, 90, 95]}
    print(f"  {label}: cur={cur_v:.2f} pct={pct:.1f}% q={qs}")
    OUT.setdefault("valuation_3y", {})[label] = {
        "current": float(cur_v), "pct": float(pct), "quantiles": qs,
        "n_days": len(s), "last_date": val["trade_date"].iloc[-1],
    }
mv = pd.to_numeric(val["total_mv"], errors="coerce")
OUT["valuation_3y"]["total_mv_yi"] = float(mv.iloc[-1] / 1e4)
OUT["valuation_3y"]["close"] = float(pd.to_numeric(val["close"], errors="coerce").iloc[-1])

# 年内涨跌幅（2025-12-31 收盘基准）
d0 = pro.daily(ts_code=TS, start_date="20251220", end_date="20260110")
base_close = pd.to_numeric(d0.sort_values("trade_date")["close"], errors="coerce").iloc[-1]
cur_close = float(pd.to_numeric(val["close"], errors="coerce").iloc[-1])
ytd = (cur_close / base_close - 1) * 100
print(f"  年内涨跌幅: {ytd:+.1f}% (base {base_close} @2025年末)")
OUT["ytd_pct"] = float(ytd)
OUT["base_close_2025"] = float(base_close)

# 年内高低点
dy = pro.daily(ts_code=TS, start_date="20260101", end_date="20260814",
               fields="ts_code,trade_date,close,high,low,pre_close,vol,amount")
dy = dy.sort_values("trade_date")
hi_i = pd.to_numeric(dy["high"], errors="coerce").idxmax()
lo_i = pd.to_numeric(dy["low"], errors="coerce").idxmin()
print(f"  年内高点: {dy.loc[hi_i,'high']} @ {dy.loc[hi_i,'trade_date']} | 低点: {dy.loc[lo_i,'low']} @ {dy.loc[lo_i,'trade_date']}")
OUT["year_high"] = {"price": float(dy.loc[hi_i, "high"]), "date": dy.loc[hi_i, "trade_date"]}
OUT["year_low"] = {"price": float(dy.loc[lo_i, "low"]), "date": dy.loc[lo_i, "trade_date"]}
# 关键窗口动量（未复权手工复核）
OUT["momentum_manual"] = {}
for days, base_date in [(60, "20260521"), (120, "20260212"), (250, "20250814")]:
    dd = pro.daily(ts_code=TS, start_date=base_date, end_date="20260814",
                   fields="ts_code,trade_date,close")
    dd = dd.sort_values("trade_date")
    if len(dd):
        b = float(pd.to_numeric(dd["close"], errors="coerce").iloc[0])
        ret = (cur_close / b - 1) * 100
        print(f"  动量 {days}d: {ret:+.1f}% (base {b} @ {dd['trade_date'].iloc[0]})")
        OUT["momentum_manual"][f"{days}d"] = {"ret_pct": float(ret), "base_date": dd["trade_date"].iloc[0]}

# ── 4. 股东户数（近 10 期）──
h = pro.stk_holdernumber(ts_code=TS, fields="ts_code,ann_date,end_date,holder_num")
h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(10)
print("[4] holder numbers:")
OUT["holders"] = []
prev = None
for _, r in h.iterrows():
    num = int(r["holder_num"])
    chg = (num - prev) / prev * 100 if prev else None
    print(f"  {r['end_date']}: {num:,} ({('↑' if chg and chg > 0 else '↓') + f'{abs(chg):.1f}%' if chg else '基期'})")
    OUT["holders"].append({"end_date": r["end_date"], "holder_num": num, "chg_pct": chg})
    prev = num

# ── 5. 十大流通股东（最新两期对比）──
t10 = pro.top10_floatholders(ts_code=TS, fields="ts_code,ann_date,end_date,holder_name,hold_ratio")
if len(t10):
    for ed in sorted(t10["end_date"].unique())[-2:]:
        sub = t10[t10["end_date"] == ed]
        print(f"[5] top10 floaters @{ed}: 合计 {sub['hold_ratio'].sum():.2f}%")
        OUT.setdefault("top10", []).append({
            "end_date": ed, "total_pct": float(sub["hold_ratio"].sum()),
            "detail": sub[["holder_name", "hold_ratio"]].to_dict("records"),
        })

# ── 6. 分红历史 ──
dv = pro.dividend(ts_code=TS, fields="ts_code,end_date,ann_date,div_proc,cash_div_tax,cash_div,base_share")
dv = dv[dv["div_proc"] == "实施"].sort_values("end_date")
print("[6] dividends(实施):")
OUT["dividends"] = dv.to_dict("records")
for _, r in dv.iterrows():
    print(f"  {r['end_date']}: 每股派息 {r['cash_div_tax']} 元")

# ── 7. 主营构成（业务板块）──
mb = pro.fina_mainbz(ts_code=TS, start_date="20240101")
mb = mb.drop_duplicates(["end_date", "bz_item"]).sort_values("end_date")
print("[7] main business composition:")
OUT["mainbz"] = mb.to_dict("records")
for _, r in mb.iterrows():
    print(f"  {r['end_date']} | {r.get('bz_item')} | 主收入={r.get('mainbusiness_income')} "
          f"占比={r.get('main_business_ratio')}% 毛利率={r.get('gross_profit_margin')}%")

# ── 8. 5 因子引擎 ──
from davis_analyzer.tushare_client import TushareClient  # noqa: E402
from davis_analyzer.financial_fetcher import fetch_financial_data  # noqa: E402
from davis_analyzer.prosperity import calculate_prosperity_score  # noqa: E402
from davis_analyzer.momentum import analyze_momentum  # noqa: E402
from davis_analyzer.dividend import analyze_dividend  # noqa: E402
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision  # noqa: E402
from davis_analyzer.holder_concentration import analyze_holder_concentration  # noqa: E402
from davis_analyzer.profitability import analyze_profitability_quality  # noqa: E402

client = TushareClient()
fin_list = fetch_financial_data(client, TS, periods=12)
print(f"[8] engine fin periods={len(fin_list)} latest={fin_list[0].report_period}")
OUT["engine_fin"] = [
    {"report_period": f.report_period, "revenue": f.revenue, "net_profit": f.net_profit,
     "eps": f.eps, "roe": f.roe, "operating_cf": f.operating_cf,
     "yoy_rev": f.yoy_revenue_growth, "yoy_np": f.yoy_profit_growth}
    for f in fin_list
]
pscore = calculate_prosperity_score(fin_list)
OUT["prosperity_engine"] = {
    "composite": pscore.composite_score, "delta_g": pscore.delta_g,
    "revenue": pscore.revenue_score, "profit": pscore.profit_score,
    "slope": pscore.slope_score, "duration": pscore.duration_score,
}
print("    prosperity:", OUT["prosperity_engine"])

mom = analyze_momentum(client, TS)
if mom:
    OUT["factor_momentum"] = {
        "score": mom.momentum_score, "abs": mom.absolute_momentum_score,
        "rs_percentile": mom.rs_percentile, "window_returns": mom.window_returns,
    }
    print("    momentum:", OUT["factor_momentum"])

divsig = analyze_dividend(client, TS)
OUT["factor_dividend"] = {
    "score": divsig.dividend_score, "consecutive_years": divsig.consecutive_years,
    "latest_yield_pct": divsig.latest_yield_pct,
}
print("    dividend:", OUT["factor_dividend"])

fc2 = analyze_forecast(client, TS, pscore)
if fc2:
    OUT["factor_forecast"] = {
        "leading_score": fc2.leading_score, "type": fc2.type,
        "p_change_mid": fc2.p_change_mid, "is_stale": fc2.is_stale,
    }
    print("    forecast:", OUT["factor_forecast"])
rev2 = analyze_forecast_revision(client, TS)
if rev2:
    OUT["factor_forecast_revision"] = {
        "direction": rev2.revision_direction, "pp": rev2.revision_pp, "score": rev2.revision_score,
    }
    print("    forecast_revision:", OUT["factor_forecast_revision"])

hc = analyze_holder_concentration(client, TS)
if hc:
    OUT["factor_holder"] = {
        "score": hc.concentration_score, "trend": hc.trend,
        "latest_chg_pct": hc.latest_chg_pct,
    }
    print("    holder_concentration:", OUT["factor_holder"])

pq = analyze_profitability_quality(fin_list)
OUT["factor_profitability"] = {
    "score": pq.quality_score, "latest_gross_margin": pq.latest_gross_margin,
    "gross_margin_delta": pq.gross_margin_delta, "latest_rd_intensity": pq.latest_rd_intensity,
}
print("    profitability_quality:", OUT["factor_profitability"])

# ── 9. 相对估值 ──
from stockhot.valuation import analyze_relative_valuation  # noqa: E402

rv = analyze_relative_valuation(pro, TS, "东材科技")
OUT["relative_valuation"] = {
    "pe_ratio": rv.pe_ratio, "pe_ratio_pct": rv.pe_ratio_pct, "erp": rv.erp,
    "risk_free_rate": rv.risk_free_rate, "quadrant": rv.quadrant,
    "quadrant_label": rv.quadrant_label, "composite_verdict": rv.composite_verdict,
    "stock_pe": getattr(rv, "stock_pe", None), "stock_pe_pct": getattr(rv, "stock_pe_pct", None),
    "index_pe": getattr(rv, "index_pe", None), "index_pe_pct": getattr(rv, "index_pe_pct", None),
    "signals": rv.signals,
}
print("[9] relative valuation:", json.dumps(OUT["relative_valuation"], ensure_ascii=False, default=str))

# ── 10. 同业估值快照 ──
peers = ["605589.SH", "603002.SH", "301630.SZ", "301555.SZ", "600183.SH", "002709.SZ"]
OUT["peers"] = []
print("[10] peers snapshot @latest:")
for p in peers:
    try:
        b = pro.daily_basic(ts_code=p, limit=1,
                            fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv")
        nm = pro.stock_basic(ts_code=p, fields="ts_code,name").iloc[0]["name"]
        row = b.iloc[0]
        print(f"  {row['ts_code']} {nm}: PE={row['pe_ttm']} PB={row['pb']} PS={row['ps']} "
              f"MV={float(row['total_mv'])/1e4:.1f}亿 @{row['trade_date']}")
        OUT["peers"].append({"ts_code": p, "name": nm, **row.to_dict()})
    except Exception as e:
        print(f"  {p}: FAIL {e}")

with open(".sisyphus/evidence/dongcai/data-pack.json", "w", encoding="utf-8") as f:
    json.dump(OUT, f, ensure_ascii=False, default=str, indent=1)
print("\nSaved -> .sisyphus/evidence/dongcai/data-pack.json")

