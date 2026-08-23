# -*- coding: utf-8 -*-
"""跷跷板反转不对称性实证(2026-08-22,方法论 v1.1 取数)。

四组历史跷跷板 × 两端标的,月度 PB/PE 采样(2021-01 至 2026-08),
检验假说:反转时受损端 PB 修复是否快于受益端 PB 下跌。

案例与时点(支点变量峰值):
A 锂价(2022-11 峰 60万→2025-04 谷 5.95万→2026-08 反弹 14.8万):
  受益端(2020-22)=赣锋/天齐;受损端=宁德/亿纬
B 煤价(2021-10 峰,2022-09 二次峰→2024-06 回落平台→2026 中位):
  受益端=神华/陕煤;受损端=华能/浙能
C 硅料(2022-08 峰 ~30万→2024-26 磨底):
  受益端=通威;受损端=晶澳(组件)
D 氧化铝(2024-12 峰 5700+→2025-26 回落):
  受益端=中铝(矿);受损端=神火(纯冶炼)
"""
from __future__ import annotations

import json
import sys
from datetime import date

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
import os
os.chdir("/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd
from stockhot.tushare_config import get_pro_api

pro = get_pro_api(timeout=60)

TARGETS = {
    "002460.SZ": "赣锋锂业", "002466.SZ": "天齐锂业", "300750.SZ": "宁德时代", "300014.SZ": "亿纬锂能",
    "601088.SH": "中国神华", "601225.SH": "陕西煤业", "600011.SH": "华能国际", "600023.SH": "浙能电力",
    "600438.SH": "通威股份", "002459.SZ": "晶澳科技",
    "601600.SH": "中国铝业", "000933.SZ": "神火股份",
}

# 切片时点:标签→YYYYMMDD
POINTS = {
    "2021-01": "20210129", "2021-10": "20211029", "2022-08": "20220831",
    "2022-11": "20221130", "2022-12": "20221230", "2023-06": "20230630",
    "2024-06": "20240628", "2024-12": "20241231", "2025-04": "20250430",
    "2025-08": "20250829", "2026-08": "20260821",
}


def seg_daily_basic(ts, start, end):
    frames = []
    s, e = date(int(start[:4]), int(start[4:6]), int(start[6:])), date(int(end[:4]), int(end[4:6]), int(end[6:]))
    from datetime import timedelta
    cur = s
    while cur <= e:
        seg_end = min(cur + timedelta(days=499), e)
        df = pro.daily_basic(ts_code=ts, start_date=cur.strftime("%Y%m%d"), end_date=seg_end.strftime("%Y%m%d"),
                             fields="ts_code,trade_date,close,pe_ttm,pb")
        if len(df):
            frames.append(df)
        cur = seg_end + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)


def main():
    result = {}
    for ts, name in TARGETS.items():
        db = seg_daily_basic(ts, "20210101", "20260822")
        if not len(db):
            print(f"{ts} {name}: NO DATA", flush=True)
            continue
        pb = pd.to_numeric(db["pb"], errors="coerce")
        pe = pd.to_numeric(db["pe_ttm"], errors="coerce")
        dates = db["trade_date"].tolist()
        row = {"name": name, "points": {}, "pb_series_max_min": {}}
        # 每个切片时点取其后第一个交易日(向前找最近≤该日)
        for label, p in POINTS.items():
            idx = None
            for i, d in enumerate(dates):
                if d <= p:
                    idx = i
                else:
                    break
            if idx is not None and pd.notna(pb.iloc[idx]):
                row["points"][label] = {"pb": round(float(pb.iloc[idx]), 2),
                                        "pe": round(float(pe.iloc[idx]), 1) if pd.notna(pe.iloc[idx]) else None,
                                        "date": dates[idx]}
        win = pb.dropna()
        if len(win):
            row["pb_series_max_min"] = {"max": round(float(win.max()), 2),
                                        "max_date": dates[int(win.idxmax())],
                                        "min": round(float(win.min()), 2),
                                        "min_date": dates[int(win.idxmin())]}
        result[ts] = row
        pts = " | ".join(f"{k}:{v['pb']}" for k, v in row["points"].items())
        print(f"{ts} {name}: PB极值[{row['pb_series_max_min'].get('max')}@{row['pb_series_max_min'].get('max_date')} / "
              f"{row['pb_series_max_min'].get('min')}@{row['pb_series_max_min'].get('min_date')}] {pts}", flush=True)
    out = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/seesaw_asymmetry_20260822.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"JSON → {out}", flush=True)


if __name__ == "__main__":
    main()
