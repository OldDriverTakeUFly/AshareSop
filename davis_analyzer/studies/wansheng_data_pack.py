#!/usr/bin/env python3
"""万盛股份 (603010.SH) 研报数据包采集脚本.

采集：时效校验 / 财务明细 / 3年估值(分段直连) / 业绩预告 / 股东户数 /
十大流通股东 / 分红 / 5因子引擎 / 相对估值 / 同业行情。
输出: .sisyphus/evidence/wansheng/data_pack.json
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

from stockhot.tushare_config import get_pro_api  # noqa: E402

TS_CODE = "603010.SH"
PEERS = ["002409.SZ", "603585.SH", "600596.SH", "600141.SH", "000698.SZ"]
OUT = Path(".sisyphus/evidence/wansheng/data_pack.json")
pro = get_pro_api(timeout=60)
result: dict = {}

# ── 1. 时效校验 ──
db1 = pro.daily_basic(ts_code=TS_CODE, limit=3)
result["freshness"] = {
    "daily_basic_latest": db1.iloc[0]["trade_date"] if len(db1) else None,
    "snapshot": db1.head(1)[["trade_date", "close", "pe_ttm", "pb", "ps_ttm", "total_mv", "turnover_rate"]].to_dict("records"),
}
inc = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,total_revenue,n_income,n_income_attr_p,rd_exp", limit=8)
result["income_native"] = inc.to_dict("records")

fc = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min(net_profit_min),net_profit_max".replace("(net_profit_min)", ""))
result["forecast"] = fc.to_dict("records")

# ── 2. 财务指标明细（fina_indicator 近 12 期）─
fi = pro.fina_indicator(ts_code=TS_CODE, fields="ts_code,end_date,grossprofit_margin,netprofit_margin,roe,debt_to_assets,ocf_to_profit,rd_exp_to_revenue", limit=12)
result["fina_indicator"] = fi.to_dict("records")

# 资产负债表关键项
bs = pro.balancesheet(ts_code=TS_CODE, fields="ts_code,end_date,total_assets,total_liab,total_equity,monetary_capital,inventory,fix_assets,cip,goodwill,st_borr,lt_borr", limit=6)
result["balancesheet"] = bs.to_dict("records")

cf = pro.cashflow(ts_code=TS_CODE, fields="ts_code,end_date,n_cashflow_act,n_cashflow_inv_act,n_cashflow_fnc_act,free_cashflow", limit=8)
result["cashflow"] = cf.to_dict("records")

# ── 3. 3 年估值历史（分段直连 daily_basic）──
end_d = date(2026, 8, 14)
start_d = end_d - timedelta(days=1120)
frames = []
cur = start_d
while cur < end_d:
    seg_end = min(cur + timedelta(days=480), end_d)
    seg = pro.daily_basic(ts_code=TS_CODE, start_date=cur.strftime("%Y%m%d"), end_date=seg_end.strftime("%Y%m%d"),
                          fields="ts_code,trade_date,close,pe_ttm,pb,ps_ttm,total_mv,turnover_rate")
    if len(seg):
        frames.append(seg)
    cur = seg_end + timedelta(days=1)
    import time; time.sleep(0.4)
db = pd.concat(frames, ignore_index=True).drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)
result["daily_basic_rows"] = len(db)
result["daily_basic_first_last"] = [db["trade_date"].iloc[0], db["trade_date"].iloc[-1]]

pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
ps = pd.to_numeric(db["ps_ttm"], errors="coerce").dropna()
pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
val_summary = {}
for name, s in [("pb", pb), ("ps", ps), ("pe", pe)]:
    if len(s):
        cur_v = s.iloc[-1]
        val_summary[name] = {
            "latest": float(cur_v), "latest_date": str(s.index[-1]),
            "pct": float((s < cur_v).sum() / len(s) * 100),
            **{f"q{p}": float(s.quantile(p / 100)) for p in [10, 25, 50, 75, 90, 95]},
            "n": int(len(s)),
        }
val_summary["total_mv_yi"] = float(mv.iloc[-1] / 1e4)
val_summary["close_latest"] = float(db["close"].iloc[-1])
result["valuation_3y"] = val_summary

# 年初至今涨幅
d2026 = pro.daily(ts_code=TS_CODE, start_date="20251231", end_date="20260814")
d2026 = d2026.sort_values("trade_date")
if len(d2026):
    result["ytd_2026"] = {
        "first_close": float(d2026["close"].iloc[0]), "first_date": d2026["trade_date"].iloc[0],
        "last_close": float(d2026["close"].iloc[-1]),
        "ytd_pct": float((d2026["close"].iloc[-1] / d2026["pre_close"].iloc[0] - 1) * 100),
        "high": float(d2026["high"].max()), "low": float(d2026["low"].min()),
    }
# 52 周动量复核
d1y = pro.daily(ts_code=TS_CODE, start_date="20250801", end_date="20260814").sort_values("trade_date")
if len(d1y) > 30:
    closes = d1y["close"].reset_index(drop=True)
    result["momentum_manual"] = {
        "d20": float(closes.iloc[-1] / closes.iloc[-21] - 1) * 100 if len(closes) > 21 else None,
        "d60": float(closes.iloc[-1] / closes.iloc[-61] - 1) * 100 if len(closes) > 61 else None,
        "d120": float(closes.iloc[-1] / closes.iloc[-121] - 1) * 100 if len(closes) > 121 else None,
        "d250": float(closes.iloc[-1] / closes.iloc[-251] - 1) * 100 if len(closes) > 251 else None,
    }

# ── 4. 股东户数（近 10 期）──
h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num")
h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(10)
result["holder_number"] = h.to_dict("records")

# ── 5. 十大流通股东（近 3 期）──
t10 = pro.top10_floatholders(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_name,hold_ratio")
if len(t10):
    latest_end = sorted(t10["end_date"].unique())[-1]
    t10_latest = t10[t10["end_date"] == latest_end]
    result["top10_float"] = {
        "end_date": latest_end,
        "total_ratio": float(pd.to_numeric(t10_latest["hold_ratio"], errors="coerce").sum()),
        "holders": t10_latest[["holder_name", "hold_ratio"]].to_dict("records"),
    }
    ends = sorted(t10["end_date"].unique())[-4:]
    result["top10_float_history"] = [
        {"end_date": e, "total_ratio": float(pd.to_numeric(t10[t10["end_date"] == e]["hold_ratio"], errors="coerce").sum())}
        for e in ends
    ]

# ── 6. 分红 ──
div = pro.dividend(ts_code=TS_CODE, fields="ts_code,end_date,ann_date,div_proc,cash_div_taxable,cash_div,base_share")
result["dividend"] = div.tail(12).to_dict("records") if len(div) else []

# ── 7. 同业行情（20260814）──
peer_rows = []
for p in PEERS + [TS_CODE]:
    try:
        r = pro.daily_basic(ts_code=p, trade_date="20260814")
        info = pro.stock_basic(ts_code=p, fields="ts_code,name,industry")
        if len(r):
            row = {"ts_code": p, "name": info.iloc[0]["name"] if len(info) else "",
                   "close": float(r.iloc[0]["close"]), "pe_ttm": float(r.iloc[0]["pe_ttm"]) if pd.notna(r.iloc[0]["pe_ttm"]) else None,
                   "pb": float(r.iloc[0]["pb"]), "ps_ttm": float(r.iloc[0]["ps_ttm"]) if pd.notna(r.iloc[0]["ps_ttm"]) else None,
                   "total_mv_yi": float(r.iloc[0]["total_mv"]) / 1e4}
            # 年报营收/净利
            inc_p = pro.income(ts_code=p, period="20251231", fields="ts_code,total_revenue,n_income", limit=1)
            if len(inc_p):
                row["rev_2025_yi"] = float(inc_p.iloc[0]["total_revenue"]) / 1e8
                row["np_2025_yi"] = float(inc_p.iloc[0]["n_income"]) / 1e8
            peer_rows.append(row)
    except Exception as e:
        peer_rows.append({"ts_code": p, "error": str(e)})
    import time; time.sleep(0.4)
result["peers"] = peer_rows

# ── 8. 基准指数（上证/沪深300）──
for idx, code in [("sh000001", "000001.SH"), ("hs300", "000300.SH")]:
    try:
        r = pro.index_daily(ts_code=code, start_date="20251231", end_date="20260814")
        r = r.sort_values("trade_date")
        if len(r):
            result[f"idx_{idx}"] = {"ytd_pct": float((r["close"].iloc[-1] / r["close"].iloc[0] - 1) * 100)}
    except Exception:
        pass

OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(json.dumps(result, ensure_ascii=False, indent=2, default=str)[:12000])
print(f"\n[saved] {OUT}")
