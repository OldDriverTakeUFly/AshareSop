#!/usr/bin/env python3
"""国际复材 (301526.SZ) 研报补充取数脚本.

时效校验 / 3年估值分位复算(直连分段) / 股东户数 / 十大流通股东 /
财务轨迹 / 分红 / 相对估值 / 5因子引擎 / YTD与区间涨幅复核.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

from stockhot.tushare_config import get_pro_api  # noqa: E402

TS = "301526.SZ"
pro = get_pro_api(timeout=30)

print("=" * 72)
print("A. 数据时效校验")
print("=" * 72)
db1 = pro.daily_basic(ts_code=TS, limit=5)
print("daily_basic 最新交易日:", db1["trade_date"].tolist())
inc = pro.income(ts_code=TS, fields="ts_code,ann_date,end_date,f_ann_date", limit=3)
print("income 最新报告期:", inc.to_dict("records"))
fc = pro.forecast(ts_code=TS, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min/net_profit_max")
print("forecast:", fc.to_dict("records") if len(fc) else "无")

print()
print("=" * 72)
print("B. 3年 daily_basic 直连分段复算（上市以来全窗口）")
print("=" * 72)
start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
end = date.today().strftime("%Y%m%d")
frames = []
cur = start
while cur < end:
    nxt = (pd.to_datetime(cur, format="%Y%m%d") + timedelta(days=490)).strftime("%Y%m%d")
    if nxt > end:
        nxt = end
    seg = pro.daily_basic(ts_code=TS, start_date=cur, end_date=nxt)
    if len(seg):
        frames.append(seg)
    cur = nxt
db = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True)
db = db.sort_values("trade_date").reset_index(drop=True)
print(f"交易日数: {len(db)}, 首: {db['trade_date'].iloc[0]}, 末: {db['trade_date'].iloc[-1]}")
last = db.iloc[-1]
print(f"最新: close相关 total_mv={last['total_mv']/1e4:.1f}亿 pe={last['pe_ttm']} pb={last['pb']} ps={last['ps']} dv={last['dv_ttm']} turn={last['turnover_rate']}")
for col, name in [("pe_ttm", "PE_TTM"), ("pb", "PB"), ("ps", "PS_TTM"), ("total_mv", "总市值(亿)")]:
    s = pd.to_numeric(db[col], errors="coerce").dropna()
    if len(s) == 0:
        print(f"{name}: 全空")
        continue
    cur_v = s.iloc[-1]
    pct = (s < cur_v).sum() / len(s) * 100
    qs = {p: s.quantile(p / 100) for p in [10, 25, 50, 75, 90, 95]}
    qstr = " ".join(f"{p}%={qs[p]:.2f}" for p in qs)
    print(f"{name}: 当前={cur_v:.2f} 分位={pct:.1f}% (n={len(s)}) | {qstr}")

print()
print("=" * 72)
print("C. 股价与 YTD（pro.daily 直连复核）")
print("=" * 72)
daily = pro.daily(ts_code=TS, start_date=start, end_date=end)
daily = daily.sort_values("trade_date").reset_index(drop=True)
if len(daily):
    c = daily.set_index("trade_date")["close"]
    base_2025 = c.get("20251231")
    last_c = c.iloc[-1]
    if base_2025:
        print(f"YTD: {last_c:.2f} / {base_2025:.2f} - 1 = {(last_c/base_2025-1)*100:.1f}%")
    hi_idx = c.idxmax()
    print(f"年内最高收盘: {c.max():.2f} @ {hi_idx}; 最新 {last_c:.2f} @ {c.index[-1]}")
    print(f"自最高点回撤: {(last_c/c.max()-1)*100:.1f}%")
    # 60/120/250d
    for w in (60, 120, 250):
        if len(c) > w:
            print(f"{w}d 区间收益: {(c.iloc[-1]/c.iloc[-1-w]-1)*100:.1f}%")
    # IPO 首日
    print(f"数据首日: {daily['trade_date'].iloc[0]} 开盘 {daily['open'].iloc[0]} 收 {daily['close'].iloc[0]}")
    ipo_close = daily["close"].iloc[0]
    print(f"自上市首日收盘涨幅: {(last_c/ipo_close-1)*100:.0f}%")

print()
print("=" * 72)
print("D. 股东户数（stk_holdernumber）")
print("=" * 72)
h = pro.stk_holdernumber(ts_code=TS, fields="ts_code,ann_date,end_date,holder_num")
h = h.dropna(subset=["holder_num"]).sort_values("end_date")
prev = None
for _, r in h.iterrows():
    num = int(r["holder_num"])
    chg = f"{(num-prev)/prev*100:+.1f}%" if prev else "基期"
    print(f"  end={r['end_date']} ann={r['ann_date']} 户数={num:,} ({chg})")
    prev = num

print()
print("=" * 72)
print("E. 十大流通股东（最新两期）")
print("=" * 72)
t10 = pro.top10_floatholders(ts_code=TS)
if len(t10):
    for end_date in sorted(t10["end_date"].unique())[-2:]:
        sub = t10[t10["end_date"] == end_date].sort_values("hold_ratio", ascending=False)
        print(f"--- {end_date} 合计 {sub['hold_ratio'].sum():.2f}% ---")
        for _, r in sub.head(10).iterrows():
            print(f"  {r['holder_name'][:28]:30s} {r['hold_ratio']:6.2f}%")

print()
print("=" * 72)
print("F. 财务轨迹（pro.income 归母 + fina_indicator）")
print("=" * 72)
inc2 = pro.income(ts_code=TS, fields="ts_code,end_date,ann_date,total_revenue,n_income,n_income_attr_p", start_date="20230101")
inc2 = inc2.drop_duplicates("end_date").sort_values("end_date")
fi = pro.fina_indicator(ts_code=TS, fields="ts_code,end_date,grossprofit_margin,roe,debt_to_assets,ocf_to_profit,rd_exp_ratio,netprofit_margin", start_date="20230101")
fi = fi.drop_duplicates("end_date").sort_values("end_date")
fim = fi.set_index("end_date")
for _, r in inc2.iterrows():
    e = r["end_date"]
    gm = fim.loc[e] if e in fim.index else None
    print(f"  {e} ann={r['ann_date']} 营收={r['total_revenue']/1e8:8.2f}亿 归母={r['n_income_attr_p'] if pd.notna(r['n_income_attr_p']) else r['n_income']}"
          f" 毛利率={gm['grossprofit_margin'] if gm is not None else 'NA'}")

print()
print("=" * 72)
print("G. 分红历史")
print("=" * 72)
dv = pro.dividend(ts_code=TS, fields="ts_code,end_date,ann_date,div_proc,cash_div,cash_div_tax,record_date,ex_date")
if len(dv):
    print(dv.sort_values("end_date").to_string(index=False))
else:
    print("无分红记录")

print()
print("=" * 72)
print("H. 股本结构（bak_basic）")
print("=" * 72)
try:
    bb = pro.bak_basic(ts_code=TS, fields="ts_code,name,industry,list_date,total_share,float_share", limit=1)
    print(bb.to_dict("records"))
except Exception as ex:
    print("bak_basic err:", ex)
sb = pro.stock_basic(ts_code=TS, fields="ts_code,name,industry,area,list_date")
print(sb.to_dict("records"))
