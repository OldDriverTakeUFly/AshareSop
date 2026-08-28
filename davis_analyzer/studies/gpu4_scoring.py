# gpu4_scoring.py — 国产GPU四小龙之已上市两家(沐曦688693/摩尔线程688795)研报取数
# 用法: cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python davis_analyzer/studies/gpu4_scoring.py
from __future__ import annotations

import os
import sys

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")

from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

from datetime import date, timedelta

import pandas as pd

from stockhot.tushare_config import get_pro_api

pro = get_pro_api(timeout=60)

TARGETS = [
    ("688802.SH", "沐曦股份"),
    ("688795.SH", "摩尔线程"),
]


def section(title: str) -> None:
    print(f"\n{'=' * 70}\n## {title}\n{'=' * 70}")


# ── 0. 代码核对 ──
section("0. 代码核对 stock_basic")
for code, expect in TARGETS:
    b = pro.stock_basic(ts_code=code, fields="ts_code,name,industry,list_date,list_status")
    if len(b) == 0:
        print(f"{code}: NOT FOUND — 代码可能错误!")
    else:
        r = b.iloc[0]
        match = "OK" if expect in str(r["name"]) else "MISMATCH!"
        print(f"{code}: name={r['name']} industry={r['industry']} list_date={r['list_date']} [{match}]")

for code, name in TARGETS:
    section(f"===== {name} ({code}) =====")

    # ── 1. 时效性 ──
    section("1. 时效性校验")
    db1 = pro.daily_basic(ts_code=code, limit=3)
    if len(db1):
        print(f"daily_basic 最新交易日: {db1.iloc[0]['trade_date']} (次新: {list(db1['trade_date'])})")
    inc1 = pro.income(ts_code=code, fields="ts_code,ann_date,end_date,f_ann_date", limit=3)
    if len(inc1):
        for _, r in inc1.iterrows():
            print(f"income 报告期 end={r['end_date']} ann={r['ann_date']}")
    fc1 = pro.forecast(ts_code=code, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
    if len(fc1):
        fcf = fc1[pd.to_numeric(fc1["ann_date"], errors="coerce") >= 20250101]
        for _, r in fcf.iterrows():
            print(f"forecast: {r['end_date']} {r['type']} ann={r['ann_date']} p_chg=[{r['p_change_min']},{r['p_change_max']}]")
    else:
        print("forecast: 无记录")

    # ── 2. daily_basic 分段拉取(全上市历史) ──
    section("2. daily_basic 全历史(分段)")
    b0 = pro.stock_basic(ts_code=code, fields="list_date")
    list_date = str(b0.iloc[0]["list_date"])
    start, end = list_date, date.today().strftime("%Y%m%d")
    frames, cur = [], start
    while int(cur) <= int(end):
        seg_end = (pd.Timestamp(cur) + timedelta(days=499)).strftime("%Y%m%d")
        seg_end = min(seg_end, end)
        d = pro.daily_basic(ts_code=code, start_date=cur, end_date=seg_end,
                            fields="ts_code,trade_date,close,pe_ttm,pb,ps_ttm,total_mv,turnover_rate,total_share")
        if len(d):
            frames.append(d)
        cur = (pd.Timestamp(seg_end) + timedelta(days=1)).strftime("%Y%m%d")
    db = pd.concat(frames, ignore_index=True).drop_duplicates("trade_date").reset_index(drop=True)
    db = db.sort_values("trade_date").reset_index(drop=True)
    print(f"共 {len(db)} 个交易日: {db['trade_date'].iloc[0]} → {db['trade_date'].iloc[-1]}")
    latest = db.iloc[-1]
    print(f"最新[{latest['trade_date']}]: close={latest['close']} pe_ttm={latest['pe_ttm']} pb={latest['pb']} ps_ttm={latest['ps_ttm']} total_mv={latest['total_mv']/1e4:.1f}亿 turnover={latest['turnover_rate']}%")
    # 分位数(以上市以来全部历史为分母,注明局限)
    for col in ["pe_ttm", "pb", "ps_ttm"]:
        s = pd.to_numeric(db[col], errors="coerce").dropna()
        if len(s):
            cur_v = s.iloc[-1]
            pct = (s < cur_v).sum() / len(s) * 100
            qs = {p: s.quantile(p / 100) for p in [10, 25, 50, 75, 90]}
            qstr = " ".join(f"P{p}={v:.2f}" for p, v in qs.items())
            print(f"{col}: 当前={cur_v:.2f} ({pct:.0f}%分位, n={len(s)}) | {qstr}")
        else:
            print(f"{col}: 全空(亏损/未定义)")
    # 上市以来价格走势
    closes = pd.to_numeric(db["close"], errors="coerce").dropna()
    hi, lo = closes.max(), closes.min()
    hi_dt = db.loc[closes.idxmax(), "trade_date"]; lo_dt = db.loc[closes.idxmin(), "trade_date"]
    print(f"close 区间: low={lo}({lo_dt}) high={hi}({hi_dt}), 现价距高点 {(closes.iloc[-1]/hi-1)*100:.1f}%")

    # ── 3. 财务(收入/利润,累计→单季差分) ──
    section("3. income 累计值 + 单季差分")
    inc = pro.income(ts_code=code, fields="ts_code,ann_date,end_date,total_revenue,n_income,rd_exp,oper_cost",
                     start_date="20230101")
    inc = inc.drop_duplicates("end_date").sort_values("end_date")
    rows = []
    prev_rev, prev_np = {}, {}
    for _, r in inc.iterrows():
        ep = str(r["end_date"])
        rev = float(r["total_revenue"]) if pd.notna(r["total_revenue"]) else None
        np_ = float(r["n_income"]) if pd.notna(r["n_income"]) else None
        rd = float(r["rd_exp"]) if pd.notna(r["rd_exp"]) else None
        year = ep[:4]
        base_ep = f"{int(year)-1}{ep[4:]}"
        prev_rev_y, prev_np_y = prev_rev.get(base_ep), prev_np.get(base_ep)
        q_rev = rev - prev_rev_y if (rev is not None and prev_rev_y is not None) else None
        q_np = np_ - prev_np_y if (np_ is not None and prev_np_y is not None) else None
        rows.append({"end": ep, "ann": r["ann_date"], "rev_cum亿": round(rev/1e8, 2) if rev else None,
                     "rev_q亿": round(q_rev/1e8, 2) if q_rev else None,
                     "np_cum亿": round(np_/1e8, 2) if np_ is not None else None,
                     "np_q亿": round(q_np/1e8, 2) if q_np is not None else None,
                     "rd_cum亿": round(rd/1e8, 2) if rd else None})
        prev_rev[ep], prev_np[ep] = rev, np_
    print(pd.DataFrame(rows).to_string(index=False))

    # ── 4. fina_indicator ──
    section("4. fina_indicator 毛利率/ROE/负债率")
    fi = pro.fina_indicator(ts_code=code, fields="ts_code,end_date,grossprofit_margin,roe,debt_to_assets,netprofit_margin,rd_exp_to_revenue",
                            start_date="20230101")
    fi = fi.drop_duplicates("end_date").sort_values("end_date")
    print(fi.to_string(index=False))

    # ── 5. 现金流/资产负债关键项 ──
    section("5. cashflow/balancesheet")
    cf = pro.cashflow(ts_code=code, fields="ts_code,end_date,n_cashflow_act,n_cashflow_inv_act", start_date="20230101")
    cf = cf.drop_duplicates("end_date").sort_values("end_date")
    for _, r in cf.iterrows():
        act = float(r["n_cashflow_act"])/1e8 if pd.notna(r["n_cashflow_act"]) else None
        inv = float(r["n_cashflow_inv_act"])/1e8 if pd.notna(r["n_cashflow_inv_act"]) else None
        print(f"  {r['end_date']}: 经营CF={act if act is None else round(act,2)}亿 投资CF={inv if inv is None else round(inv,2)}亿")
    bs = pro.balancesheet(ts_code=code, fields="ts_code,end_date,total_assets,total_liab,contract_liab,inventories,money_cap", start_date="20230101")
    bs = bs.drop_duplicates("end_date").sort_values("end_date")
    for _, r in bs.iterrows():
        ta = float(r["total_assets"])/1e8 if pd.notna(r["total_assets"]) else None
        tl = float(r["total_liab"])/1e8 if pd.notna(r["total_liab"]) else None
        cl = float(r["contract_liab"])/1e8 if pd.notna(r["contract_liab"]) else None
        inv_ = float(r["inventories"])/1e8 if pd.notna(r["inventories"]) else None
        mc = float(r["money_cap"])/1e8 if pd.notna(r["money_cap"]) else None
        print(f"  {r['end_date']}: 总资产={ta and round(ta,1)}亿 总负债={tl and round(tl,1)}亿 合同负债={cl and round(cl,2)}亿 存货={inv_ and round(inv_,1)}亿 货币资金={mc and round(mc,1)}亿")

    # ── 6. 股东户数 ──
    section("6. 股东户数趋势")
    h = pro.stk_holdernumber(ts_code=code, fields="ts_code,ann_date,end_date,holder_num")
    h = h.dropna(subset=["holder_num"]).sort_values("end_date")
    prev = None
    for _, r in h.iterrows():
        num = int(r["holder_num"])
        chg = f"{(num-prev)/prev*100:+.1f}%" if prev else "基期"
        print(f"  {r['end_date']}: {num:,} ({chg})")
        prev = num

    # ── 7. 解禁 ──
    section("7. share_float 解禁")
    sf = pro.share_float(ts_code=code, fields="ts_code,ann_date,float_date,float_share,holder_name")
    if len(sf):
        sf = sf.sort_values("float_date").tail(8)
        total_share = float(latest["total_share"]) if pd.notna(latest["total_share"]) else None
        for _, r in sf.iterrows():
            share = float(r["float_share"]) if pd.notna(r["float_share"]) else None
            pct = share/total_share*100 if (share and total_share) else None
            print(f"  {r['float_date']}: {share and round(share/1e6,1)}百万股({pct and round(pct,1)}%) {r['holder_name']}")
    else:
        print("  无记录")

    # ── 8. 动量(手工复核,不复权近似—科创板-U无分红) ──
    section("8. 价格动量(手工复核)")
    dl = pro.daily(ts_code=code, start_date=list_date, end_date=end, fields="ts_code,trade_date,close")
    dl = dl.sort_values("trade_date").reset_index(drop=True)
    cl = pd.to_numeric(dl["close"], errors="coerce").dropna()
    for w in [20, 60, 120, 250]:
        if len(cl) > w:
            print(f"  {w}d: {(cl.iloc[-1]/cl.iloc[-w]-1)*100:+.1f}%")
        else:
            print(f"  {w}d: 上市未满{w}日(共{len(cl)}日)")
    # 上市首日
    if len(cl):
        print(f"  上市首日收盘={cl.iloc[0]}, 最新={cl.iloc[-1]}, 相对首日 {(cl.iloc[-1]/cl.iloc[0]-1)*100:+.1f}%")

print("\n\nDONE")
