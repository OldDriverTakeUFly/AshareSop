# -*- coding: utf-8 -*-
"""周期顶底的增速拐点判据实证(2026-08-22,高阶导框架检验)。

检验假说:
H-顶:增速峰(ΔG 首负=增速拐点)的财报披露日 vs PB 顶日——等增速拐点确认再卖,代价几何?
H-底:衰减拐点(同比降幅收窄/单季环比转正,三阶导)vs 增速转正(二阶导),谁的披露日更接近 PB 底日?

8 标的:赣锋/天齐(锂顶+底)、通威/晶澳(硅料顶)、神华/陕煤(煤顶)、牧原/温氏(猪底)。
方法:pro.income 取 2019-2026 季度归母净利(累计差分成单季,ann_date 取首次披露),
构建单季同比 G 与环比,标记:利润额峰/增速峰/ΔG 首负/减亏拐点/同比转正;
PB 极值日期:6 标的复用 seesaw_asymmetry JSON,牧原/温氏现拉。
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta

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
    "002460.SZ": "赣锋锂业", "002466.SZ": "天齐锂业", "600438.SH": "通威股份",
    "002459.SZ": "晶澳科技", "601088.SH": "中国神华", "601225.SH": "陕西煤业",
    "002714.SZ": "牧原股份", "300498.SZ": "温氏股份",
}


def quarterly_profit(ts: str) -> pd.DataFrame:
    """季度归母净利:income 累计值差分成单季 + 首次披露日。"""
    inc = pro.income(ts_code=ts, start_date="20190101", end_date="20260822",
                     fields="ts_code,ann_date,end_date,n_income_attr_p")
    if not len(inc):
        return pd.DataFrame()
    inc["ann_date"] = pd.to_numeric(inc["ann_date"], errors="coerce")
    inc = inc.dropna(subset=["ann_date"]).sort_values("ann_date")
    g = inc.groupby("end_date").agg(
        ann_first=("ann_date", "min"), profit=("n_income_attr_p", "last")).reset_index()
    g = g[g["end_date"].str.endswith(("0331", "0630", "0930", "1231"))].sort_values("end_date").reset_index(drop=True)
    rows = []
    for i, r in g.iterrows():
        q = r["end_date"]
        if q.endswith("0331"):
            single = r["profit"]
        else:
            prev = g.iloc[i - 1]["end_date"] if i > 0 else None
            prev_cum = g.iloc[i - 1]["profit"] if i > 0 else None
            if prev is None or not prev.startswith(q[:4]):
                single = None  # 缺上期累计,跳过
            else:
                single = r["profit"] - prev_cum
        rows.append({"end_date": q, "ann_first": str(int(r["ann_first"])),
                     "cum": r["profit"], "single": single})
    df = pd.DataFrame(rows)
    # 单季同比与环比
    df["single"] = pd.to_numeric(df["single"], errors="coerce")
    df["yoy"] = None
    df["qoq"] = None
    for i in range(len(df)):
        q = df.iloc[i]["end_date"]
        if pd.isna(df.iloc[i]["single"]):
            continue
        # 去年同季
        prev_year_q = str(int(q[:4]) - 1) + q[4:]
        m = df[df["end_date"] == prev_year_q]
        if len(m) and pd.notna(m.iloc[0]["single"]) and abs(float(m.iloc[0]["single"])) > 1e6:
            df.loc[i, "yoy"] = (df.iloc[i]["single"] / m.iloc[0]["single"] - 1) * 100
        if i > 0 and pd.notna(df.iloc[i - 1]["single"]):
            prev_s = df.iloc[i - 1]["single"]
            if abs(float(prev_s)) > 1e6:
                df.loc[i, "qoq"] = (df.iloc[i]["single"] / prev_s - 1) * 100
    return df


def pb_extremes(ts: str, seesaw: dict) -> dict:
    if ts in seesaw:
        return seesaw[ts]["pb_series_max_min"]
    start, end = "20190101", "20260822"
    frames = []
    cur = date(2019, 1, 1)
    fin = date(2026, 8, 22)
    while cur <= fin:
        seg_end = min(cur + timedelta(days=499), fin)
        d = pro.daily_basic(ts_code=ts, start_date=cur.strftime("%Y%m%d"),
                            end_date=seg_end.strftime("%Y%m%d"), fields="ts_code,trade_date,pb")
        if len(d):
            frames.append(d)
        cur = seg_end + timedelta(days=1)
    if not frames:
        return {}
    db = pd.concat(frames, ignore_index=True).drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)
    pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
    dates = db["trade_date"].tolist()
    return {"max": round(float(pb.max()), 2), "max_date": dates[int(pb.idxmax())],
            "min": round(float(pb.min()), 2), "min_date": dates[int(pb.idxmin())]}


def analyze(df: pd.DataFrame) -> dict:
    """标记关键拐点。"""
    out = {}
    d = df[df["single"].notna()].copy() if len(df) else df
    if not len(d):
        return out
    d["single_yi"] = d["single"] / 1e8
    # 1) 利润额峰(单季绝对额最大,限正利润)
    pos = d[d["single_yi"] > 0]
    if len(pos):
        peak = pos.loc[pos["single_yi"].idxmax()]
        out["profit_peak"] = {"q": peak["end_date"], "yi": round(peak["single_yi"], 2), "ann": peak["ann_first"]}
    # 2) 增速峰(单季同比最高,限 >50%)
    y = d[d["yoy"].notna()].copy()
    y["yoy"] = pd.to_numeric(y["yoy"], errors="coerce")
    y50 = y[y["yoy"] > 50]
    if len(y50):
        gp = y50.loc[y50["yoy"].idxmax()]
        out["growth_peak"] = {"q": gp["end_date"], "yoy": round(gp["yoy"], 1), "ann": gp["ann_first"]}
        # 3) ΔG 首负:增速峰之后首个 yoy 较上季显著回落(降幅>30pct)的季度
        after = y[y["end_date"] > gp["end_date"]]
        prev_yoy = gp["yoy"]
        for _, r in after.iterrows():
            if pd.notna(r["yoy"]) and float(prev_yoy) - float(r["yoy"]) > 30:
                out["dg_first_negative"] = {"q": r["end_date"], "yoy": round(float(r["yoy"]), 1), "ann": r["ann_first"]}
                break
            if pd.notna(r["yoy"]):
                prev_yoy = r["yoy"]
    # 4) 底部信号:最深亏损季度(或增速最低)
    if len(y):
        gy = y.loc[pd.to_numeric(y["yoy"], errors="coerce").idxmin()]
        out["growth_trough"] = {"q": gy["end_date"], "yoy": round(float(gy["yoy"]), 1) if pd.notna(gy["yoy"]) else None, "ann": gy["ann_first"]}
        # 5) 衰减拐点:增速谷之后首个 yoy 较上季回升(升幅>10pct)或单季转正
        after2 = y[y["end_date"] > gy["end_date"]]
        prev_yoy2 = gy["yoy"]
        for _, r in after2.iterrows():
            if pd.notna(r["yoy"]) and pd.notna(prev_yoy2) and float(r["yoy"]) - float(prev_yoy2) > 10:
                out["decay_inflection"] = {"q": r["end_date"], "yoy": round(float(r["yoy"]), 1), "ann": r["ann_first"]}
                break
            if pd.notna(r["yoy"]):
                prev_yoy2 = r["yoy"]
        # 6) 同比转正(增速回正)
        for _, r in y[y["end_date"] > gy["end_date"]].iterrows():
            if pd.notna(r["yoy"]) and float(r["yoy"]) > 0 and pd.notna(r["single"] and float(r["single"]) > 0):
                out["yoy_turn_positive"] = {"q": r["end_date"], "yoy": round(float(r["yoy"]), 1), "ann": r["ann_first"]}
                break
    return out


def main():
    with open("davis_analyzer/studies/seesaw_asymmetry_20260822.json", encoding="utf-8") as f:
        seesaw = json.load(f)
    result = {}
    for ts, name in TARGETS.items():
        try:
            df = quarterly_profit(ts)
            if not len(df):
                print(f"{ts} {name}: income 无数据", flush=True)
                continue
            marks = analyze(df)
            pbx = pb_extremes(ts, seesaw)
            result[ts] = {"name": name, "marks": marks, "pb_extremes": pbx,
                          "series": df[["end_date", "ann_first", "single_yi" if "single_yi" in df else "single"]].round(2).to_dict("records") if "single_yi" in df else df.to_dict("records")}
            m = marks
            print(f"══ {ts} {name} ══", flush=True)
            print(f"  PB 顶 {pbx.get('max')}@{pbx.get('max_date')} | PB 底 {pbx.get('min')}@{pbx.get('min_date')}", flush=True)
            for k, lab in [("profit_peak", "利润额峰"), ("growth_peak", "增速峰"),
                           ("dg_first_negative", "ΔG首负"), ("growth_trough", "增速谷"),
                           ("decay_inflection", "衰减拐点"), ("yoy_turn_positive", "同比转正")]:
                v = m.get(k)
                if v:
                    extra = f" yoy={v['yoy']}" if v.get("yoy") is not None else (f" 利润={v.get('yi')}亿" if v.get("yi") else "")
                    print(f"  {lab}: {v['q']}(披露{v['ann']}){extra}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{ts} {name}: ERR {e}", flush=True)
    out = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/cycle_turning_points_20260822.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"JSON → {out}", flush=True)


if __name__ == "__main__":
    main()
