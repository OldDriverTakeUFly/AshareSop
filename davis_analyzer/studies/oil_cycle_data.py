# ── 油价周期三篇研报 A股取数脚本（2026-09-15）──
# 取油气/炼化/煤化工标的行情、估值、中报业绩 + 申万行业指数表现
from __future__ import annotations

import json
import os
import time

import pandas as pd
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env")
import tushare as ts

ts.set_token(os.environ["TUSHARE_TOKEN"])
pro = ts.pro_api()

STOCKS = {
    # 上游油气
    "601857.SH": "中国石油", "600028.SH": "中国石化", "600938.SH": "中国海油",
    "600256.SH": "广汇能源", "603619.SH": "中曼石油",
    # 大炼化/石化
    "600346.SH": "恒力石化", "002493.SZ": "荣盛石化", "000301.SZ": "东方盛虹",
    "601233.SH": "桐昆股份", "000703.SZ": "恒逸石化", "002648.SZ": "卫星化学",
    "600688.SH": "上海石化", "600309.SH": "万华化学",
    # 煤化工/煤炭
    "600989.SH": "宝丰能源", "600426.SH": "华鲁恒升", "000830.SZ": "鲁西化工",
    "000990.SZ": "诚志股份", "600844.SH": "丹化科技", "600075.SH": "新疆天业",
    "601088.SH": "中国神华",
}

INDEXES = {"801960.SI": "申万石油石化", "801950.SI": "申万煤炭", "801030.SI": "申万基础化工", "000300.SH": "沪深300"}


def latest_trade_date() -> str:
    df = pro.trade_cal(exchange="SSE", start_date="20260820", end_date="20260915", is_open="1")
    return df.sort_values("cal_date").iloc[-1]["cal_date"]


def main() -> None:
    out: dict = {}
    end_date = latest_trade_date()
    out["_meta"] = {"latest_trade_date": end_date, "run_date": "20260915"}

    # 1) 最新估值快照
    val = pro.daily_basic(trade_date=end_date, fields="ts_code,close,pe_ttm,pb,total_mv,turnover_rate")
    val = val[val["ts_code"].isin(STOCKS)]

    # 2) 区间涨幅：YTD(20251231) / 9月以来(20260831) / 3个月(20260615)
    rows = []
    for code, name in STOCKS.items():
        rec = {"ts_code": code, "name": name}
        for tag, d in [("close_20251231", "20251231"), ("close_20260831", "20260831"), ("close_20260615", "20260615")]:
            d2 = (pd.Timestamp(d) + pd.Timedelta(days=30)).strftime("%Y%m%d")
            dfr = pro.daily(ts_code=code, start_date=d, end_date=d2, fields="trade_date,close")
            if len(dfr):
                rec[tag] = float(dfr.sort_values("trade_date").iloc[0]["close"])
            time.sleep(0.12)
        rows.append(rec)
        print(f"done {name}", flush=True)

    df = pd.DataFrame(rows).merge(val, on="ts_code", how="left")
    for base, col in [("close_20251231", "chg_ytd"), ("close_20260831", "chg_sep"), ("close_20260615", "chg_3m")]:
        df[col] = ((df["close"] / df[base]) - 1) * 100
    out["stocks"] = df.drop(columns=[c for c in df.columns if c.startswith("close_2")]).round(2).to_dict("records")

    # 3) 2026中报业绩同比
    inc_rows = []
    for code, name in STOCKS.items():
        dfr = pro.income(
            ts_code=code, start_date="20250601", end_date="20260915",
            fields="ts_code,end_date,ann_date,revenue,n_income_attr_p", report_type=1,
        )
        dfr = dfr[dfr["end_date"].isin(["20260630", "20250630"])]
        dfr = dfr.sort_values("ann_date").drop_duplicates(["end_date"], keep="last")
        rec = {"ts_code": code, "name": name}
        for _, r in dfr.iterrows():
            tag = "h1_2026" if r["end_date"] == "20260630" else "h1_2025"
            rec[f"rev_{tag}"] = round(float(r["revenue"]) / 1e8, 2) if pd.notna(r["revenue"]) else None
            rec[f"ni_{tag}"] = round(float(r["n_income_attr_p"]) / 1e8, 2) if pd.notna(r["n_income_attr_p"]) else None
        if rec.get("rev_h1_2025") and rec.get("rev_h1_2026") is not None:
            rec["rev_yoy"] = round((rec["rev_h1_2026"] / rec["rev_h1_2025"] - 1) * 100, 1)
        if rec.get("ni_h1_2025") and rec.get("ni_h1_2026") is not None:
            rec["ni_yoy"] = round((rec["ni_h1_2026"] / rec["ni_h1_2025"] - 1) * 100, 1)
        inc_rows.append(rec)
        time.sleep(0.12)
    out["income_h1"] = inc_rows

    # 4) 指数区间表现
    idx_rows = []
    for code, name in INDEXES.items():
        rec = {"ts_code": code, "name": name}
        for tag, d in [("close_20251231", "20251231"), ("close_20260831", "20260831"), ("close_20260615", "20260615"), ("close_now", end_date)]:
            d2 = (pd.Timestamp(d) + pd.Timedelta(days=7)).strftime("%Y%m%d")
            dfr = pro.index_daily(ts_code=code, start_date=d, end_date=d2, fields="trade_date,close")
            if len(dfr):
                rec[tag] = float(dfr.sort_values("trade_date").iloc[0]["close"])
            time.sleep(0.12)
        for base, col in [("close_20251231", "chg_ytd"), ("close_20260831", "chg_sep"), ("close_20260615", "chg_3m")]:
            if base in rec and "close_now" in rec:
                rec[col] = round((rec["close_now"] / rec[base] - 1) * 100, 2)
        idx_rows.append({k: v for k, v in rec.items() if not k.startswith("close_2") and k != "close_now"})
    out["indexes"] = idx_rows

    path = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/oil_cycle_data.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("saved", path, "| latest_trade_date =", end_date)


if __name__ == "__main__":
    main()
