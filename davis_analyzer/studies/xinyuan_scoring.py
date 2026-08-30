#!/usr/bin/env python3
"""芯源微 (688037.SH) 研报取数脚本：四维评分 + 5 补充因子 + 股东户数 + 时效校验 + 相对估值."""
from __future__ import annotations

import os, sys, json
sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv
load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd
from datetime import date, timedelta

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.valuation import fetch_valuation_history
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality

TS = "688037.SH"
NAME = "芯源微"
out: dict = {}

client = TushareClient()

# 0. 时效校验 + 名称核对
from stockhot.tushare_config import get_pro_api
pro = get_pro_api(timeout=30)
basic = pro.stock_basic(ts_code=TS)
print("== stock_basic =="); print(basic.to_string())
db1 = pro.daily_basic(ts_code=TS, limit=1)
print("latest trade:", db1.iloc[0]["trade_date"] if len(db1) else "none",
      "| close_mv(万):", db1.iloc[0]["total_mv"] if len(db1) else None,
      "| pe:", db1.iloc[0]["pe_ttm"] if len(db1) else None,
      "| pb:", db1.iloc[0]["pb"] if len(db1) else None,
      "| ps:", db1.iloc[0]["ps"] if len(db1) else None)
fc = pro.forecast(ts_code=TS, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
if len(fc):
    fc = fc.sort_values("ann_date")
    print("== forecast(全部,按披露日) =="); print(fc.tail(6).to_string())

# 1. 财务 12 期
fin = fetch_financial_data(client, TS, periods=12)
print(f"\n== 财务 {len(fin)} 期 ==")
for f in fin:
    print(f"{f.report_period} rev={f.revenue/1e8:.2f}亿 np={f.net_profit/1e8:.4f}亿 "
          f"yoy_rev={f.yoy_revenue_growth if f.yoy_revenue_growth is None else round(f.yoy_revenue_growth*100,1)}% "
          f"yoy_np={f.yoy_profit_growth if f.yoy_profit_growth is None else round(f.yoy_profit_growth*100,1)}% "
          f"roe={f.roe:.2f}% gm={getattr(f,'grossprofit_margin',None)} rd={getattr(f,'rd_exp',None)} "
          f"ocf={f.operating_cf/1e8 if f.operating_cf is not None else None}")

# 2. 估值：pro.daily_basic 分段直连取 3 年（避免增量缓存缩水坑）
frames = []
end = date.today(); start = end - timedelta(days=1150)
cur = start
while cur < end:
    seg_end = min(cur + timedelta(days=480), end)
    d = pro.daily_basic(ts_code=TS, start_date=cur.strftime("%Y%m%d"), end_date=seg_end.strftime("%Y%m%d"),
                        fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv")
    if len(d): frames.append(d)
    cur = seg_end + timedelta(days=1)
db = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True).sort_values("trade_date").reset_index(drop=True)
print(f"\n== 估值 {len(db)} 交易日, 首日 {db['trade_date'].iloc[0]} 末日 {db['trade_date'].iloc[-1]} ==")
pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
print(f"PE有效点 {len(pe)}, 最新PE {pe.iloc[-1] if len(pe) else None}, 最新PB {pb.iloc[-1]:.2f} ({(pb<pb.iloc[-1]).mean()*100:.1f}%分位), PS {ps.iloc[-1]:.2f} ({(ps<ps.iloc[-1]).mean()*100:.1f}%分位), 市值 {mv.iloc[-1]/1e4:.1f}亿")
for s, nm in [(pb,"PB"),(ps,"PS"),(pe,"PE")]:
    if len(s)==0: print(nm, "全空"); continue
    print(nm, " ".join(f"{p}%:{s.quantile(p/100):.2f}" for p in [10,25,50,75,90,95]), f"当前:{s.iloc[-1]:.2f} 分位:{(s<s.iloc[-1]).mean()*100:.1f}%")
# YTD 收益
d0 = pro.daily(ts_code=TS, start_date="20251230", end_date=date.today().strftime("%Y%m%d"))
d0 = d0.sort_values("trade_date")
if len(d0)>1:
    prev_year_last = d0[d0["trade_date"] <= "20251231"]
    px = d0["close"].iloc[-1]
    base = prev_year_last["close"].iloc[-1] if len(prev_year_last) else None
    if base: print(f"最新收盘 {px} ({d0['trade_date'].iloc[-1]}), 2025年末 {base}, YTD {(px/base-1)*100:.1f}%")
    for w, lbl in [(60,"60d"),(120,"120d"),(250,"250d")]:
        if len(d0) > w:
            print(f"{lbl} 涨幅: {(px/d0['close'].iloc[-1-w]-1)*100:.1f}%")

# 3. 景气度
pscore = calculate_prosperity_score(fin)
print(f"\n== 景气度 == composite={pscore.composite_score:.1f} rev={pscore.revenue_score:.1f} prof={pscore.profit_score:.1f} slope={pscore.slope_score:.1f} dur={pscore.duration_score:.1f} ΔG={pscore.delta_g:.2f}")

# 4. 补充因子
mom = analyze_momentum(client, TS)
if mom:
    print(f"动量: score={mom.momentum_score:.1f} abs={mom.absolute_momentum_score:.1f} rs_pct={mom.rs_percentile} windows={mom.window_returns}")
div = analyze_dividend(client, TS)
print(f"分红: score={div.dividend_score} 连续{div.consecutive_years}年 yield={div.latest_yield_pct}%")
fcs = analyze_forecast(client, TS, pscore)
print(f"预告: {fcs}")
hc = analyze_holder_concentration(client, TS)
if hc:
    print(f"筹码: score={hc.concentration_score} trend={hc.trend} latest_chg={hc.latest_chg_pct} counts={hc.holder_counts}")
pq = analyze_profitability_quality(fin)
print(f"盈利质量: score={pq.quality_score:.1f} gm={pq.latest_gross_margin} gm_delta={pq.gross_margin_delta} rd_intensity={pq.latest_rd_intensity}")

# 5. 股东户数
h = pro.stk_holdernumber(ts_code=TS, fields="ts_code,ann_date,end_date,holder_num").dropna(subset=["holder_num"]).sort_values("end_date")
print("\n== 股东户数 ==")
print(h.tail(8).to_string())

# 6. 相对估值
try:
    from stockhot.valuation import analyze_relative_valuation
    rv = analyze_relative_valuation(pro, TS)
    print("\n== 相对估值 ==")
    for k, v in vars(rv).items():
        print(f"{k}: {v}")
except Exception as e:
    print("relative valuation failed:", e)

print("\nDONE")
