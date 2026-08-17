# ── 化纤板块启动分化归因 批量取数脚本(2026-08) ──
# 数据项:月度收益分解/动量复核/年内回撤/换手率/股东户数/十大流通股东/
#        2025H1 基数/2026H1 预告/PB 分位(当前 3y/5y + 2025 年初起点)
# 坑点对策:pro.daily_basic 直连 ≤400 天分段;pro.daily 空返回重试;
#          stk_holdernumber dropna;forecast 万元单位;concat 后 reset_index
import os
import sys
import time
from datetime import date, timedelta

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

from stockhot.tushare_config import get_pro_api  # noqa: E402

pro = get_pro_api(timeout=60)

STOCKS = [
    ("000703.SZ", "恒逸石化"),
    ("601233.SH", "桐昆股份"),
    ("603225.SH", "新凤鸣"),
    ("002064.SZ", "华峰化学"),
    ("000301.SZ", "东方盛虹"),
    ("002493.SZ", "荣盛石化"),
    ("600346.SH", "恒力石化"),
]
TODAY = "20260815"
SLEEP = 0.25


def _retry(fn, **kw):
    for i in range(4):
        try:
            df = fn(**kw)
            if df is not None and not df.empty:
                return df
        except Exception as e:  # noqa: BLE001
            print(f"    [retry {i}] {e}")
        time.sleep(1.5 * (i + 1))
    return pd.DataFrame()


def batch_daily_basic(ts_code, start, end):
    """分段直连 daily_basic,≤400 天/段。"""
    frames = []
    cur = pd.Timestamp(start)
    endd = pd.Timestamp(end)
    while cur <= endd:
        nxt = min(cur + pd.Timedelta(days=399), endd)
        df = _retry(
            pro.daily_basic, ts_code=ts_code,
            start_date=cur.strftime("%Y%m%d"), end_date=nxt.strftime("%Y%m%d"),
            fields="ts_code,trade_date,close,pe_ttm,pb,total_mv,turnover_rate,dv_ttm",
        )
        if not df.empty:
            frames.append(df)
        cur = nxt + pd.Timedelta(days=1)
        time.sleep(SLEEP)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).drop_duplicates("trade_date")
    return out.sort_values("trade_date").reset_index(drop=True)


def pct_rank(series, cur):
    s = series.dropna()
    return (s < cur).sum() / len(s) * 100 if len(s) else float("nan")


print("=" * 100)
print("STEP 0 代码-公司名校验(防张冠李戴)")
for code, name in STOCKS:
    b = _retry(pro.stock_basic, ts_code=code, fields="ts_code,name,industry")
    print(f"  {code}: name={b.iloc[0]['name'] if len(b) else '?'} industry={b.iloc[0]['industry'] if len(b) else '?'} (期望 {name})")
    time.sleep(SLEEP)

# ── 基准指数月度收益 ──
print("\n" + "=" * 100)
print("STEP 1 沪深300 月度收益(基准)")
idx = _retry(pro.index_daily, ts_code="000300.SH", start_date="20241201", end_date=TODAY,
             fields="ts_code,trade_date,close")
idx = idx.sort_values("trade_date").reset_index(drop=True)
idx["m"] = idx["trade_date"].str[:6]
im = idx.groupby("m")["close"].last()
im_ret = im.pct_change() * 100
print("  " + " ".join(f"{m}:{r:+.1f}" for m, r in im_ret.items() if m >= "202501"))
IDX_M = im_ret

# ── 股东户数(全部历史,打印 2024-06 以后) ──
print("\n" + "=" * 100)
print("STEP 2 股东户数(筹码集中度)")
HOLDER = {}
for code, name in STOCKS:
    h = _retry(pro.stk_holdernumber, ts_code=code,
               fields="ts_code,ann_date,end_date,holder_num")
    if h.empty:
        print(f"  {name}: 无数据")
        continue
    h = h.dropna(subset=["holder_num"]).sort_values("end_date")
    h["end_date"] = h["end_date"].astype(str)
    h = h.drop_duplicates("end_date")
    h = h[h["end_date"] >= "20240601"]
    HOLDER[code] = h[["end_date", "holder_num", "ann_date"]]
    nums = h["holder_num"].astype(float).tolist()
    eds = h["end_date"].tolist()
    seq = " | ".join(f"{e}:{int(n):,}" for e, n in zip(eds, nums))
    base_chg = (nums[-1] / nums[0] - 1) * 100 if nums[0] else float("nan")
    print(f"  {name}({code}): {seq}")
    print(f"    → 期间({eds[0]}→{eds[-1]})变化 {base_chg:+.1f}%")
    time.sleep(SLEEP)

# ── 十大流通股东合计比例(最近 4 期) ──
print("\n" + "=" * 100)
print("STEP 3 十大流通股东持股比例合计(近 4 期,交叉验证筹码)")
for code, name in STOCKS:
    t = _retry(pro.top10_floatholders, ts_code=code,
               fields="ts_code,end_date,holder_name,hold_ratio")
    if t.empty:
        print(f"  {name}: 无数据")
        continue
    t["end_date"] = t["end_date"].astype(str)
    g = t[t["end_date"] >= "20250301"].groupby("end_date")["hold_ratio"].sum()
    print(f"  {name}: " + " ".join(f"{e[:6]}:{v:.1f}%" for e, v in g.items()))
    time.sleep(SLEEP)

# ── 财务:2025H1/2025FY/2026Q1 净利 + 2026H1 预告 ──
print("\n" + "=" * 100)
print("STEP 4 净利基数(2025H1/2025FY/2026Q1)+ 2026H1 预告(注意 forecast 单位万元)")
FIN = {}
for code, name in STOCKS:
    row = {}
    for label, period in [("H1_25", "20250630"), ("FY25", "20251231"), ("Q1_26", "20260331")]:
        d = _retry(pro.income, ts_code=code, period=period)
        if not d.empty:
            if "n_income_attrp" not in d.columns:
                print(f"    [warn] {code} {period} cols={list(d.columns)[:12]}")
                cand = [c for c in d.columns if "n_income" in c]
                col = cand[0] if cand else None
            else:
                col = "n_income_attrp"
            if col:
                dd = d.dropna(subset=[col])
                row[label] = dd.iloc[-1][col] / 1e8
        time.sleep(SLEEP)
    f = _retry(pro.forecast, ts_code=code,
               fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
    f26 = f[(f["end_date"].astype(str) == "20260630")] if not f.empty else pd.DataFrame()
    if not f26.empty:
        r = f26.iloc[0]
        row["fc_min_yi"] = r["net_profit_min"] / 1e4  # 万元→亿
        row["fc_max_yi"] = r["net_profit_max"] / 1e4
        row["fc_chg"] = f"[{r['p_change_min']},{r['p_change_max']}]"
        row["fc_ann"] = str(r["ann_date"])
        row["fc_type"] = r["type"]
    FIN[code] = row
    print(f"  {name}: 2025H1={row.get('H1_25', float('nan')):.2f}亿 FY25={row.get('FY25', float('nan')):.2f}亿 "
          f"Q1_26={row.get('Q1_26', float('nan')):.2f}亿 | 预告 {row.get('fc_type', '?')} "
          f"{row.get('fc_min_yi', float('nan')):.1f}~{row.get('fc_max_yi', float('nan')):.1f}亿 "
          f"{row.get('fc_chg', '?')}% ann={row.get('fc_ann', '?')}")
    time.sleep(SLEEP)

# ── 行情:复权日价(月度分解/动量/回撤) + daily_basic(换手/PB 分位) ──
print("\n" + "=" * 100)
print("STEP 5 复权行情:60/120/250d 动量复核 + 月度收益 + 年内回撤")
MOM = {}
for code, name in STOCKS:
    d = _retry(pro.daily, ts_code=code, start_date="20241201", end_date=TODAY,
               fields="ts_code,trade_date,close,high")
    af = _retry(pro.adj_factor, ts_code=code, start_date="20241201", end_date=TODAY,
                fields="ts_code,trade_date,adj_factor")
    if d.empty or af.empty:
        print(f"  {name}: 行情缺失!")
        continue
    m = d.merge(af[["trade_date", "adj_factor"]], on="trade_date")
    m = m.sort_values("trade_date").reset_index(drop=True)
    m["adj"] = m["close"] * m["adj_factor"]
    adj = m["adj"]
    r60 = (adj.iloc[-1] / adj.iloc[-61] - 1) * 100 if len(adj) > 61 else float("nan")
    r120 = (adj.iloc[-1] / adj.iloc[-121] - 1) * 100 if len(adj) > 121 else float("nan")
    r250 = (adj.iloc[-1] / adj.iloc[-251] - 1) * 100 if len(adj) > 251 else float("nan")
    hi250 = m["close"].iloc[-250:].max()
    dd = (m["close"].iloc[-1] / hi250 - 1) * 100
    hi_date = m["trade_date"].iloc[-250:][m["close"].iloc[-250:].idxmax() - m.index[0]] \
        if len(m) >= 250 else "?"
    m["mo"] = m["trade_date"].str[:6]
    mm = m.groupby("mo")["adj"].last()
    mm_ret = mm.pct_change() * 100
    seq = " ".join(f"{mo[2:]}:{r:+.1f}" for mo, r in mm_ret.items() if mo >= "202501")
    MOM[code] = {"r60": r60, "r120": r120, "r250": r250, "dd": dd, "mo": mm_ret}
    print(f"  {name}: 60d={r60:+.1f}% 120d={r120:+.1f}% 250d={r250:+.1f}% | 距250日高点 {dd:+.1f}%(高点日 {hi_date})")
    print(f"    月度: {seq}")
    time.sleep(SLEEP)

# 超额月度(相对沪深300)
print("\n  相对沪深300 的月度超额(2025-01 起):")
for code, name in STOCKS:
    if code not in MOM:
        continue
    ex = []
    for mo, r in MOM[code]["mo"].items():
        if mo >= "202501" and mo in IDX_M.index and not pd.isna(IDX_M[mo]) and not pd.isna(r):
            ex.append(f"{mo[2:]}:{r - IDX_M[mo]:+.1f}")
    print(f"    {name}: {' '.join(ex)}")

# ── daily_basic:换手率 + PB 分位 ──
print("\n" + "=" * 100)
print("STEP 6 估值与换手(daily_basic 直连分段:2021-08 ~ 今,5 年窗)")
VAL = {}
for code, name in STOCKS:
    db = batch_daily_basic(code, "20210801", TODAY)
    if db.empty:
        print(f"  {name}: daily_basic 缺失!")
        continue
    db["pb"] = pd.to_numeric(db["pb"], errors="coerce")
    db["turn"] = pd.to_numeric(db["turnover_rate"], errors="coerce")
    db["dv"] = pd.to_numeric(db["dv_ttm"], errors="coerce")
    cur_pb = db["pb"].iloc[-1]
    w3 = db[db["trade_date"] >= "20230815"]["pb"]
    w5 = db["pb"]
    pb3 = pct_rank(w3, cur_pb)
    pb5 = pct_rank(w5, cur_pb)
    t60 = db["turn"].iloc[-60:].mean()
    t250 = db["turn"].iloc[-250:].mean()
    t2025 = db[(db["trade_date"] >= "20250101") & (db["trade_date"] <= "20251231")]["turn"].mean()
    dv = db["dv"].iloc[-1]
    # 2025 年初起点(行情启动前)
    pre = db[db["trade_date"] <= "20250110"]
    pb_start = pre["pb"].iloc[-1] if len(pre) else float("nan")
    pre3 = pre[pre["trade_date"] >= "20220110"]["pb"]
    pb_start_pct = pct_rank(pre3, pb_start)
    start_date = pre["trade_date"].iloc[-1] if len(pre) else "?"
    VAL[code] = dict(pb=cur_pb, pb3=pb3, pb5=pb5, pb_start=pb_start, pb_start_pct=pb_start_pct)
    print(f"  {name}: PB={cur_pb:.2f} 3y分位={pb3:.1f}% 5y分位={pb5:.1f}% | "
          f"2025年初({start_date}) PB={pb_start:.2f} 启动前3y分位={pb_start_pct:.1f}% | "
          f"换手率 60d均值={t60:.2f}% 250d={t250:.2f}% 2025年均={t2025:.2f}% | 股息TTM={dv:.2f}%")
    time.sleep(SLEEP)

# ── 相关性:预增倍数 vs 涨幅 ──
print("\n" + "=" * 100)
print("STEP 7 相关性检验(预增中值倍数 vs 动量/基数 vs 动量)")
rows = []
for code, name in STOCKS:
    f = FIN.get(code, {})
    mo = MOM.get(code, {})
    fc_mid = (f.get("fc_min_yi", float("nan")) + f.get("fc_max_yi", float("nan"))) / 2
    base = f.get("H1_25", float("nan"))
    multiple = fc_mid / base if base and base > 0 else float("nan")
    rows.append(dict(name=name, mult=multiple, r250=mo.get("r250"), r60=mo.get("r60"),
                     base=base, fc_mid=fc_mid))
cdf = pd.DataFrame(rows)
print(cdf.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
for col in ["mult", "base", "fc_mid"]:
    for tgt in ["r250", "r60"]:
        pair = cdf[[col, tgt]].dropna()
        if len(pair) >= 5:
            pr = pair[col].corr(pair[tgt], method="pearson")
            sr = pair[col].corr(pair[tgt], method="spearman")
            print(f"  {col} vs {tgt}: pearson={pr:+.2f} spearman={sr:+.2f} (n={len(pair)})")

print("\nDONE")
