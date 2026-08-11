#!/usr/bin/env python3
"""补充采集：股东户数(修NaN) + 股息率 + 基本面信息."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"
sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")

from stockhot.tushare_config import get_pro_api

pro = get_pro_api(timeout=30)
OUTPUT_DIR = Path("/home/leo/Projects/CodeAgentDashboard/.sisyphus/evidence/youse_7")

STOCKS = [
    ("000603.SZ", "盛达资源"),
    ("601958.SH", "金钼股份"),
    ("001257.SZ", "盛龙股份"),
    ("000657.SZ", "中钨高新"),
    ("600531.SH", "豫光金铅"),
    ("000630.SZ", "铜陵有色"),
    ("600259.SH", "中稀有色"),
]


def collect_extras(ts_code, name):
    out = {}
    # 股东户数 (dropna)
    try:
        h = pro.stk_holdernumber(
            ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num"
        )
        h = h.dropna(subset=["holder_num"])
        h = h.sort_values("end_date").tail(10)
        rows = []
        prev = None
        for _, r in h.iterrows():
            num = int(r["holder_num"])
            chg = (num - prev) / prev * 100 if prev else None
            rows.append({"end_date": str(r["end_date"]), "ann_date": str(r["ann_date"]), "holder_num": num, "chg_pct": round(chg, 1) if chg else None})
            prev = num
        out["holder_trend"] = rows
        if len(rows) >= 2:
            out["latest_holder"] = rows[-1]["holder_num"]
            out["base_holder"] = rows[0]["holder_num"]
            out["holder_change_pct"] = round((rows[-1]["holder_num"] - rows[0]["holder_num"]) / rows[0]["holder_num"] * 100, 1)
            out["holder_trend_judgment"] = "集中(动能增强✓)" if rows[-1]["holder_num"] < rows[0]["holder_num"] else "分散(动能减弱⚠)"
        print(f"  {name} 户数: {len(rows)}期, {rows[0]['holder_num'] if rows else 'N/A'}→{rows[-1]['holder_num'] if rows else 'N/A'}")
    except Exception as e:
        out["holder_error"] = str(e)
        print(f"  {name} 户数 ERROR: {e}")

    # 分红送转(近5年) - 拿股息率
    try:
        divs = pro.dividend(ts_code=ts_code, fields="ts_code,end_date,div_proc,cash_div_tax,stk_div,record_date,ex_date")
        if len(divs):
            # 已实施的
            impl = divs[divs["div_proc"] == "实施"]
            recent = impl.sort_values("end_date").tail(6)
            div_rows = []
            for _, r in recent.iterrows():
                div_rows.append({
                    "end_date": str(r["end_date"]),
                    "cash_div_tax": r["cash_div_tax"],  # 每股税前红利(元)
                    "stk_div": r["stk_div"],
                    "ex_date": str(r["ex_date"]) if pd.notna(r["ex_date"]) else None,
                })
            out["dividends"] = div_rows
            print(f"  {name} 分红: {len(div_rows)}次实施, 最近{div_rows[-1]['end_date'] if div_rows else 'N/A'} cash={div_rows[-1]['cash_div_tax'] if div_rows else 'N/A'}")
    except Exception as e:
        out["div_error"] = str(e)

    # 基本信息
    try:
        info = pro.stock_basic(ts_code=ts_code, fields="ts_code,symbol,name,area,industry,market,list_date,exchange")
        if len(info):
            out["basic"] = info.iloc[0].to_dict()
            print(f"  {name} basic: industry={info.iloc[0]['industry']}, list_date={info.iloc[0]['list_date']}")
    except Exception as e:
        out["basic_error"] = str(e)

    return out


def main():
    for ts_code, name in STOCKS:
        print(f"\n--- {name} ({ts_code}) ---")
        extras = collect_extras(ts_code, name)
        # merge into existing json
        out_file = OUTPUT_DIR / f"{ts_code}.json"
        if out_file.exists():
            with open(out_file) as f:
                existing = json.load(f)
            existing.update(extras)
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2, default=str)
        else:
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(extras, f, ensure_ascii=False, indent=2, default=str)


if __name__ == "__main__":
    main()
