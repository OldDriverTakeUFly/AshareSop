#!/usr/bin/env python3
"""圣泉集团 (605589.SH) 研报数据采集：时效校验/3年估值/财务/股东/五因子/相对估值/同行."""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env", override=True)  # 防 shell 导出 stale token
os.environ["PROJECT_ROOT"] = os.getcwd()  # 防 .env 的 /app 值破坏 stockhot mkdir

from stockhot.tushare_config import get_pro_api  # noqa: E402

TS = "605589.SH"
PEERS = {"605589.SH": "圣泉集团", "601208.SH": "东材科技", "603002.SH": "宏昌电子",
         "301555.SH": "惠柏新材", "600183.SH": "生益科技"}

pro = get_pro_api(timeout=60)

print("=" * 70)
print("== 0. 股票基本信息核对 ==")
basic = pro.stock_basic(ts_code=TS, fields="ts_code,name,industry,area,list_date")
print(basic.to_string(index=False))

print("\n== 1. 时效性校验 ==")
db1 = pro.daily_basic(ts_code=TS, limit=3, fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv,turnover_rate")
print(db1.to_string(index=False))
inc = pro.income(ts_code=TS, fields="ts_code,ann_date,end_date,f_ann_date", limit=3)
print("income 披露:", inc.to_string(index=False))
try:
    fc = pro.forecast(ts_code=TS, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
    print("forecast:", fc.to_string(index=False) if len(fc) else "无")
except Exception as e:
    print("forecast err:", e)

print("\n== 2. 3年 daily_basic 分段拉取（防截断）==")
end = date.today().strftime("%Y%m%d")
start = (date.today() - timedelta(days=1130)).strftime("%Y%m%d")
frames = []
cur = start
while cur < end:
    seg_end = (pd.Timestamp(cur) + timedelta(days=480)).strftime("%Y%m%d")
    seg_end = min(seg_end, end)
    d = pro.daily_basic(ts_code=TS, start_date=cur, end_date=seg_end,
                        fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv")
    frames.append(d)
    cur = (pd.Timestamp(seg_end) + timedelta(days=1)).strftime("%Y%m%d")
db = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True)
db = db.sort_values("trade_date").reset_index(drop=True)
print(f"共 {len(db)} 个交易日, 首 {db['trade_date'].iloc[0]} 末 {db['trade_date'].iloc[-1]}")
last = db.iloc[-1]
print(f"最新: close={last['close']} pe={last['pe_ttm']} pb={last['pb']} ps={last['ps']} mv={float(last['total_mv'])/1e4:.1f}亿")

for col in ["pe_ttm", "pb", "ps"]:
    s = pd.to_numeric(db[col], errors="coerce").dropna()
    cur_v = s.iloc[-1]
    pct = (s < cur_v).sum() / len(s) * 100
    qs = {p: round(s.quantile(p / 100), 2) for p in [10, 25, 50, 75, 90, 95]}
    print(f"{col}: 当前={cur_v:.2f} 分位={pct:.1f}% (n={len(s)}) 分位值={qs}")
db.to_csv("/tmp/sq_daily_basic.csv", index=False)

print("\n== 3. 年初至今涨跌幅（YTD）==")
daily = pro.daily(ts_code=TS, start_date="20251231", end_date=end,
                  fields="ts_code,trade_date,close,pre_close,pct_chg")
daily = daily.sort_values("trade_date").reset_index(drop=True)
print(f"2025-12-31 收盘: {daily['close'].iloc[0]}")
print(f"最新收盘: {daily['close'].iloc[-1]} ({daily['trade_date'].iloc[-1]})")
print(f"YTD: {(daily['close'].iloc[-1]/daily['close'].iloc[0]-1)*100:.1f}%")

# 60/120/250d 手工动量（防引擎缓存缺口）
for label, days in [("20d", 20), ("60d", 60), ("120d", 120), ("250d", 250)]:
    d2 = pro.daily(ts_code=TS, start_date="20250101", end_date=end,
                   fields="trade_date,close").sort_values("trade_date").reset_index(drop=True)
    if len(d2) > days:
        r = (d2["close"].iloc[-1] / d2["close"].iloc[-1 - days] - 1) * 100
        print(f"动量 {label}: {r:+.1f}%")
    break

print("\n== 4. 财务序列（income 原生 12 期）==")
inc2 = pro.income(ts_code=TS, fields="ts_code,end_date,revenue,n_income,operate_profit",
                  start_date="20230101", end_date=end)
inc2 = inc2.drop_duplicates("end_date").sort_values("end_date", ascending=False)
print(inc2.head(13).to_string(index=False))

fi = pro.fina_indicator(ts_code=TS, start_date="20230101", end_date=end,
                        fields="ts_code,end_date,grossprofit_margin,netprofit_margin,roe,debt_to_assets,rd_exp,ocf_to_profit")
fi = fi.drop_duplicates("end_date").sort_values("end_date", ascending=False)
print("\nfina_indicator:")
print(fi.head(13).to_string(index=False))

cf = pro.cashflow(ts_code=TS, start_date="20230101", end_date=end,
                  fields="ts_code,end_date,n_cashflow_act", limit=13)
print("\n经营现金流:")
print(cf.to_string(index=False))

print("\n== 5. 股东户数（近 10 期）==")
h = pro.stk_holdernumber(ts_code=TS, fields="ts_code,ann_date,end_date,holder_num")
h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(10)
prev = None
for _, r in h.iterrows():
    chg = f"{(r['holder_num']-prev)/prev*100:+.1f}%" if prev else "基期"
    print(f"  {r['end_date']}: {int(r['holder_num']):,} ({chg}) ann={r['ann_date']}")
    prev = r["holder_num"]

print("\n== 6. 十大流通股东（最新两期合计）==")
for period in ["20260630", "20260331"]:
    t10 = pro.top10_floatholders(ts_code=TS, period=period,
                                 fields="ts_code,holder_name,hold_ratio")
    if len(t10):
        print(f"  {period}: 合计 {t10['hold_ratio'].sum():.2f}%")
        print(t10.head(10).to_string(index=False))

print("\n== 7. 分红（近 5 年实施）==")
div = pro.dividend(ts_code=TS, fields="ts_code,end_date,ann_date,div_proc,cash_div,cash_div_tax,base_share")
div = div[div["div_proc"] == "实施"].sort_values("end_date").tail(6)
print(div.to_string(index=False))

print("\n== 8. 同行估值快照 ==")
for code, name in PEERS.items():
    d = pro.daily_basic(ts_code=code, limit=1, fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv")
    if len(d):
        r = d.iloc[0]
        dy = pro.daily(ts_code=code, start_date="20251231", end_date=end, fields="trade_date,close").sort_values("trade_date")
        ytd = (dy["close"].iloc[-1] / dy["close"].iloc[0] - 1) * 100 if len(dy) else float("nan")
        print(f"  {name}({code}): close={r['close']} PE={r['pe_ttm']} PB={r['pb']} PS={r['ps']} "
              f"MV={float(r['total_mv'])/1e4:.1f}亿 YTD={ytd:+.1f}% ({r['trade_date']})")

print("\n== 9. 相对估值（stockhot）==")
from stockhot.valuation import analyze_relative_valuation  # noqa: E402
rv = analyze_relative_valuation(pro, TS, "圣泉集团")
print(f"  pe_ratio={rv.pe_ratio} pe_ratio_pct={rv.pe_ratio_pct} erp={rv.erp} "
      f"quadrant={rv.quadrant} label={rv.quadrant_label}")
print(f"  verdict={rv.composite_verdict}")
print(f"  index_pe={getattr(rv, 'index_pe', None)} index_pe_pct={getattr(rv, 'index_pe_pct', None)} "
      f"risk_free={getattr(rv, 'risk_free_rate', None)} stock_pe={getattr(rv, 'stock_pe', None)} "
      f"stock_pe_pct={getattr(rv, 'stock_pe_pct', None)}")
for s in (rv.signals or []):
    print("  -", s)

print("\nDONE")
