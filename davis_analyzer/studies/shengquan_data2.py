#!/usr/bin/env python3
"""圣泉集团 补充采集：五因子引擎/归母净利/2026Q2股东/惠柏新材/主营构成/手动动量."""
from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

from stockhot.tushare_config import get_pro_api  # noqa: E402

TS = "605589.SH"
pro = get_pro_api(timeout=60)
end = date.today().strftime("%Y%m%d")

print("== A. 归母净利（n_income_attr_p）近 6 期 ==")
inc = pro.income(ts_code=TS, start_date="20240101", end_date=end,
                 fields="ts_code,end_date,ann_date,revenue,n_income_attr_p,n_income,minority_gain")
inc = inc.drop_duplicates("end_date").sort_values("end_date", ascending=False)
print(inc.head(8).to_string(index=False))

print("\n== B. 2026Q2 十大流通股东 ==")
t10 = pro.top10_floatholders(ts_code=TS, period="20260630", fields="ts_code,holder_name,hold_ratio")
if len(t10):
    print(f"合计 {t10['hold_ratio'].sum():.2f}%")
    print(t10.to_string(index=False))
else:
    print("无 20260630 数据")

print("\n== C. 惠柏新材代码检索 ==")
sb = pro.stock_basic(fields="ts_code,name,industry,list_date", status="L")
for kw in ["惠柏", "宏昌", "东材", "生益", "同宇", "上纬"]:
    hit = sb[sb["name"].str.contains(kw, na=False)]
    if len(hit):
        print(hit.to_string(index=False))

print("\n== D. 手动动量（pro.daily 复核 60/120/250d）==")
d2 = pro.daily(ts_code=TS, start_date="20250601", end_date=end, fields="trade_date,close").sort_values("trade_date").reset_index(drop=True)
print(f"日线 {len(d2)} 条, 末 {d2['trade_date'].iloc[-1]}")
for label, days in [("60d", 60), ("120d", 120), ("250d", 250)]:
    if len(d2) > days:
        r = (d2["close"].iloc[-1] / d2["close"].iloc[-1 - days] - 1) * 100
        base_date = d2["trade_date"].iloc[-1 - days]
        print(f"  {label}: {r:+.1f}% (基准日 {base_date})")

print("\n== E. 五因子引擎 ==")
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
pscore = calculate_prosperity_score(fin)

mom = analyze_momentum(client, TS)
print(f"momentum: score={mom.momentum_score} abs={mom.absolute_momentum_score} rs={mom.rs_percentile}")
print(f"  windows={mom.window_returns}")

div = analyze_dividend(client, TS)
print(f"dividend: score={div.dividend_score} years={div.consecutive_years} yield={div.latest_yield_pct}%")

fcsig = analyze_forecast(client, TS, pscore)
print(f"forecast: {fcsig}")
rev = analyze_forecast_revision(client, TS)
print(f"revision: dir={rev.revision_direction} pp={rev.revision_pp} score={rev.revision_score}")

hc = analyze_holder_concentration(client, TS)
print(f"holder_conc: score={hc.concentration_score} trend={hc.trend} chg={hc.latest_chg_pct}")
print(f"  counts={hc.holder_counts} periods={hc.periods}")

pq = analyze_profitability_quality(fin)
print(f"profitability: score={pq.quality_score} gm={pq.latest_gross_margin} gm_delta={pq.gross_margin_delta} rd={pq.latest_rd_intensity} sufficient={getattr(pq, 'data_sufficient', 'NA')}")

print("\n== F. 财务 dataclass 摘要（12 期）==")
for f in fin:
    yr = f"{f.yoy_revenue_growth*100:+.1f}%" if f.yoy_revenue_growth is not None else "NA"
    yp = f"{f.yoy_profit_growth*100:+.1f}%" if f.yoy_profit_growth is not None else "NA"
    print(f"  {f.report_period}: rev={f.revenue/1e8:.2f}亿 np={float(f.net_profit)/1e8:.2f}亿 eps={f.eps} roe={f.roe} 营收yoy={yr} 利润yoy={yp}")

print("\n== G. 主营构成（fina_mainbz 最新）==")
try:
    mb = pro.fina_mainbz_vip(ts_code=TS, type="P")
except Exception:
    try:
        mb = pro.fina_mainbz(ts_code=TS, type="P")
    except Exception as e:
        mb = pd.DataFrame()
        print("mainbz err:", e)
if len(mb):
    mb = mb.sort_values("end_date", ascending=False)
    print(mb.head(15).to_string(index=False))

print("\n== H. bak_basic 股本 ==")
bb = pro.bak_basic(ts_code=TS, fields="ts_code,trade_date,total_share,float_share,name,industry,list_date", limit=2)
print(bb.to_string(index=False))

print("\nDONE2")
