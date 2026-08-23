# 修正版: 3年估值分位(分段直连) + 预告过滤(ann_date>=20260101) + 财务口径校验
import os
import sys

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
os.chdir("/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

import warnings
from datetime import date, timedelta

import pandas as pd

warnings.filterwarnings("ignore")
from stockhot.tushare_config import get_pro_api

pro = get_pro_api(timeout=60)

CODES = [
    "688506.SH", "688331.SH", "688266.SH", "002422.SZ", "600276.SH", "000963.SZ",
    "688166.SH", "603087.SH", "688253.SH", "688336.SH", "600196.SH", "603259.SH",
    "002821.SZ", "300347.SZ", "688131.SH", "603127.SH", "300363.SZ", "600535.SH",
    "600557.SH", "002603.SZ", "688235.SH", "688192.SH", "688578.SH", "300558.SZ",
    "688222.SH", "300725.SZ", "688356.SH", "688690.SH", "688293.SH",
]


def daily_basic_3y(ts_code: str) -> pd.DataFrame:
    """分段直连拉3年 daily_basic, concat后 reset_index (速查表坑点解法)."""
    end = date.today()
    frames = []
    while True:
        seg_start = end - timedelta(days=490)
        for _ in range(3):
            df = pro.daily_basic(
                ts_code=ts_code,
                start_date=seg_start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv",
            )
            if len(df):
                frames.append(df)
                break
        end = seg_start - timedelta(days=1)
        if end < date.today() - timedelta(days=1120):
            break
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).drop_duplicates("trade_date")
    return out.sort_values("trade_date").reset_index(drop=True)


def pct(series: pd.Series):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 100:
        return None, None, len(s)
    cur = float(s.iloc[-1])
    return cur, round((s < cur).sum() / len(s) * 100, 0), len(s)


rows = []
for code in CODES:
    db = daily_basic_3y(code)
    rec = {"代码": code, "天数": len(db)}
    if len(db):
        rec["市值亿"] = round(float(pd.to_numeric(db["total_mv"], errors="coerce").iloc[-1]) / 1e4, 0)
        for col, key in [("pe_ttm", "PE"), ("pb", "PB"), ("ps", "PS")]:
            cur, p, _ = pct(db[col])
            rec[key] = round(cur, 1) if cur else None
            rec[f"{key}分位"] = p
        rec["末日"] = db["trade_date"].iloc[-1]
    # 预告: 只要2026年披露的
    try:
        fc = pro.forecast(
            ts_code=code,
            fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min",
        )
        fc = fc[pd.to_numeric(fc["ann_date"], errors="coerce") >= 20260101].sort_values("end_date")
        if len(fc):
            r = fc.iloc[-1]
            per = "H1" if str(r["end_date"])[4:6] == "06" else "FY"
            np_min = pd.to_numeric(r.get("net_profit_min"), errors="coerce")
            np_str = f", 净利{np_min/1e4:.1f}亿" if pd.notna(np_min) else ""
            rec["预告26"] = f"{per}{r['type']} {r['p_change_min']}~{r['p_change_max']}%{np_str}"
    except Exception as e:
        rec["预告26"] = f"err{str(e)[:20]}"
    rows.append(rec)
    print("done", code, flush=True)

df = pd.DataFrame(rows)
df.to_csv("/tmp/pharma_valfix.csv", index=False)
with open("/tmp/pharma_valfix.txt", "w", encoding="utf-8") as fh:
    fh.write(df.to_string(index=False))
print("saved /tmp/pharma_valfix.txt")

# ── 财务口径校验: fetch_financial_data 是单季还是累计 ──
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.tushare_client import TushareClient

client = TushareClient()
for code in ["600276.SH", "603259.SH"]:
    fin = fetch_financial_data(client, code, periods=3)
    inc = pro.income(ts_code=code, start_date="20260101", end_date="20261231",
                     fields="ts_code,end_date,total_revenue,n_income", period="2")
    print(f"\n== {code} fetch_financial_data(前3期) ==")
    for f in fin[:3]:
        print(f"  {f.report_period}: rev={f.revenue/1e8:.2f}亿 net={float(f.net_profit)/1e8:.2f}亿")
    print(f"== {code} pro.income(累计口径) ==")
    print(inc.to_string(index=False))
