#!/usr/bin/env python3
"""华大九天 (301269.SZ) 研报取数脚本：四维+5因子+股东户数+相对估值+时效校验."""
from __future__ import annotations
import os, sys, json
from datetime import date, timedelta
import pandas as pd
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"
sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")

from stockhot.tushare_config import get_pro_api
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality

TS_CODE, NAME = "301269.SZ", "华大九天"
pro = get_pro_api(timeout=30)
client = TushareClient()

# 0. 核对代码
basic = pro.stock_basic(ts_code=TS_CODE)
print("STOCK:", basic[["ts_code","name","industry","list_date"]].to_dict("records"))

# 1. 时效校验
db1 = pro.daily_basic(ts_code=TS_CODE, limit=3)
print("LATEST_TRADE:", db1[["trade_date","close","pe_ttm","pb","ps","total_mv","turnover_rate","dv_ttm"]].to_dict("records"))
inc = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date", limit=1)
print("LATEST_INCOME:", inc.to_dict("records"))
fc = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
if len(fc):
    fc = fc[pd.to_numeric(fc["ann_date"]) >= 20250101]
    print("FORECAST:", fc.to_dict("records"))

# 2. 财务
fin = fetch_financial_data(client, TS_CODE, periods=12)
print(f"FIN periods={len(fin)}")
for f in fin:
    print(f"  {f.report_period} rev={float(f.revenue)/1e8:.2f}亿 np={float(f.net_profit)/1e8:.3f}亿 "
          f"rev_yoy={f.yoy_revenue_growth} np_yoy={f.yoy_profit_growth} roe={f.roe} "
          f"ocf={float(f.operating_cf or 0)/1e8:.2f}亿 gm={getattr(f,'grossprofit_margin',None)} rd={float(getattr(f,'rd_exp',None) or 0)/1e8:.2f}亿")

# 3. 估值历史（3年，分段直连防截断）
end = date.today().strftime("%Y%m%d"); start = (date.today()-timedelta(days=1100)).strftime("%Y%m%d")
frames=[]
cur=start
while cur < end:
    nxt = min((pd.Timestamp(cur)+pd.Timedelta(days=490)).strftime("%Y%m%d"), end)
    d = pro.daily_basic(ts_code=TS_CODE, start_date=cur, end_date=nxt,
                        fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,turnover_rate")
    frames.append(d); cur = nxt
val = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True).sort_values("trade_date")
print(f"VAL rows={len(val)} first={val['trade_date'].iloc[0]} last={val['trade_date'].iloc[-1]}")
for col in ["pe_ttm","pb","ps","total_mv"]:
    s = pd.to_numeric(val[col], errors="coerce").dropna()
    if len(s)==0: print(f"{col}: ALL NULL"); continue
    cur_v = s.iloc[-1]
    pct = (s < cur_v).sum()/len(s)*100
    qs = {p: round(float(s.quantile(p/100)),2) for p in [10,25,50,75,90,95]}
    print(f"{col}: cur={cur_v:.2f} pct={pct:.1f}% quantiles={qs} n={len(s)} last_date={val['trade_date'].iloc[-1]}")

# 4. 景气度
pscore = calculate_prosperity_score(fin)
print(f"PROSPERITY: composite={pscore.composite_score} dG={pscore.delta_g} rev_s={pscore.revenue_score} prof_s={pscore.profit_score} slope={pscore.slope_score} dur={pscore.duration_score} stage={classify_stock_stage(pscore)}")

# 5. 价格动量（手工复核）
px = pro.daily(ts_code=TS_CODE, start_date=(date.today()-timedelta(days=400)).strftime("%Y%m%d"), end_date=end)[["trade_date","close","pre_close"]]
px = px.sort_values("trade_date").reset_index(drop=True)
for w in [20,60,120,250]:
    if len(px) > w:
        print(f"RET {w}d: {px['close'].iloc[-1]/px['close'].iloc[-1-w]-1:.2%} (from {px['trade_date'].iloc[-1-w]})")
mom = analyze_momentum(client, TS_CODE)
print("MOMENTUM:", mom)

# 6. 其他4因子
div = analyze_dividend(client, TS_CODE); print("DIVIDEND:", div)
fcsig = analyze_forecast(client, TS_CODE, pscore); print("FC:", fcsig)
rev = analyze_forecast_revision(client, TS_CODE); print("FCREV:", rev)
hc = analyze_holder_concentration(client, TS_CODE); print("HC:", hc)
pq = analyze_profitability_quality(fin); print("PQ:", pq)

# 7. 股东户数
h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num").dropna(subset=["holder_num"]).sort_values("end_date").tail(8)
prev=None
for _,r in h.iterrows():
    n=int(r["holder_num"]); chg=(n-prev)/prev*100 if prev else None
    print(f"HOLDER {r['end_date']}: {n:,} ({('%.1f%%'%chg) if chg is not None else 'base'})"); prev=n

# 8. 相对估值
try:
    from stockhot.valuation import analyze_relative_valuation
    rv = analyze_relative_valuation(TS_CODE)
    print("RELVAL:", rv)
except Exception as e:
    print("RELVAL FAIL:", e)
