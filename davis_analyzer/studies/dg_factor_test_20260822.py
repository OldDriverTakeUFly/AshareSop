# -*- coding: utf-8 -*-
"""增速拐点因子检验:多周期事件研究 vs 全周期验证(2026-08-22)。

事件定义(复用周期顶底拐点判据研报的逻辑,扩展为多事件检测):
  TOP 信号(顶部/卖出方向):ΔG 首负——季度 yoy 从 >50% 的局部峰回落 >30pct,
        事件日=回落季的首次披露日(ann_first)。预期事件后收益弱。
  BOTTOM 信号(底部/买入方向):衰减拐点——季度 yoy 从 <-50% 的深谷回升 >10pct
        (或亏损单季环比转正),事件日=回升季的首次披露日。预期事件后收益强。

窗口(周期因子为慢变量,比事件因子规范拉长):T+60 / T+120 / T+250 交易日。
基准:沪深300(000300.SH)同期收益 → 超额。
多周期对比:事件按披露年份分组(2020/2021/2022/2023/2024/2025/2026)+ 全期汇总。
样本:20 只周期股(有色/锂/硅料/煤/猪/磷化),2018-2026 季度财务。

输出:分组胜率/均值/中位数/超额 + 全期 t-stat(简化)。
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

import numpy as np
import pandas as pd
from stockhot.tushare_config import get_pro_api

pro = get_pro_api(timeout=60)

TARGETS = {
    "002460.SZ": "赣锋锂业", "002466.SZ": "天齐锂业", "600438.SH": "通威股份",
    "002459.SZ": "晶澳科技", "601088.SH": "中国神华", "601225.SH": "陕西煤业",
    "002714.SZ": "牧原股份", "300498.SZ": "温氏股份", "603799.SH": "华友钴业",
    "600547.SH": "山东黄金", "601600.SH": "中国铝业", "000933.SZ": "神火股份",
    "600096.SH": "云天化", "000422.SZ": "湖北宜化", "600141.SH": "兴发集团",
    "000960.SZ": "锡业股份", "600497.SH": "驰宏锌锗", "601958.SH": "金钼股份",
    "603993.SH": "洛阳钼业", "601899.SH": "紫金矿业",
}


def quarterly(ts: str) -> pd.DataFrame:
    inc = pro.income(ts_code=ts, start_date="20180101", end_date="20260822",
                     fields="ts_code,ann_date,end_date,n_income_attr_p")
    if not len(inc):
        return pd.DataFrame()
    inc["ann_date"] = pd.to_numeric(inc["ann_date"], errors="coerce")
    inc = inc.dropna(subset=["ann_date"]).sort_values("ann_date")
    g = inc.groupby("end_date").agg(ann_first=("ann_date", "min"), cum=("n_income_attr_p", "last")).reset_index()
    g = g[g["end_date"].str.endswith(("0331", "0630", "0930", "1231"))].sort_values("end_date").reset_index(drop=True)
    rows = []
    for i, r in g.iterrows():
        q = r["end_date"]
        if q.endswith("0331"):
            single = r["cum"]
        elif i > 0 and g.iloc[i - 1]["end_date"].startswith(q[:4]):
            single = r["cum"] - g.iloc[i - 1]["cum"]
        else:
            single = None
        rows.append({"q": q, "ann": str(int(r["ann_first"])), "single": single})
    df = pd.DataFrame(rows)
    df["single"] = pd.to_numeric(df["single"], errors="coerce")
    df["yoy"] = np.nan
    for i in range(len(df)):
        q = df.iloc[i]["q"]
        pq = str(int(q[:4]) - 1) + q[4:]
        m = df[df["q"] == pq]
        if len(m) and pd.notna(m.iloc[0]["single"]) and abs(float(m.iloc[0]["single"])) > 5e7 and pd.notna(df.iloc[i]["single"]):
            df.loc[i, "yoy"] = (df.iloc[i]["single"] / m.iloc[0]["single"] - 1) * 100
    return df.dropna(subset=["yoy"]).reset_index(drop=True)


def detect_events(df: pd.DataFrame) -> list:
    """多事件检测:TOP=ΔG首负;BOTTOM=衰减拐点。每类每 8 个季度内去重取首个。"""
    events = []
    y = df["yoy"].tolist()
    qs, anns = df["q"].tolist(), df["ann"].tolist()
    last_top_i, last_bot_i = -99, -99
    for i in range(len(y) - 1):
        # TOP: 局部峰(yoy>50,高于或等于上季) 且下季回落>30pct
        prev = y[i - 1] if i > 0 else -np.inf
        if y[i] > 50 and y[i] >= prev and (y[i] - y[i + 1]) > 30 and (i - last_top_i) >= 8:
            events.append({"type": "TOP", "q_peak": qs[i], "q": qs[i + 1], "ann": anns[i + 1],
                           "yoy_from": round(y[i], 1), "yoy_to": round(y[i + 1], 1)})
            last_top_i = i
        # BOTTOM: 深谷(yoy<-50) 且下季回升>10pct
        if y[i] < -50 and (y[i + 1] - y[i]) > 10 and (i - last_bot_i) >= 8:
            events.append({"type": "BOTTOM", "q_trough": qs[i], "q": qs[i + 1], "ann": anns[i + 1],
                           "yoy_from": round(y[i], 1), "yoy_to": round(y[i + 1], 1)})
            last_bot_i = i
    return events


def price_series(ts: str) -> pd.DataFrame:
    px = pro.daily(ts_code=ts, start_date="20180101", end_date="20260822", fields="ts_code,trade_date,close")
    af = pro.adj_factor(ts_code=ts, start_date="20180101", end_date="20260822", fields="ts_code,trade_date,adj_factor")
    if not len(px) or not len(af):
        return pd.DataFrame()
    m = px.merge(af, on=["ts_code", "trade_date"]).sort_values("trade_date").reset_index(drop=True)
    m["adj"] = m["close"] * m["adj_factor"]
    return m


def fwd_return(series: pd.DataFrame, ann: str, n: int) -> float | None:
    idx = series[series["trade_date"] >= ann].index
    if not len(idx):
        return None
    i = idx[0]
    if i + n >= len(series):
        return None
    return (series.iloc[i + n]["adj"] / series.iloc[i]["adj"] - 1) * 100


def main():
    # 基准:沪深300
    idx = pro.index_daily(ts_code="000300.SH", start_date="20180101", end_date="20260822",
                          fields="ts_code,trade_date,close").sort_values("trade_date").reset_index(drop=True)

    def bench(ann: str, n: int) -> float | None:
        hit = idx[idx["trade_date"] >= ann].index
        if not len(hit):
            return None
        i = hit[0]
        if i + n >= len(idx):
            return None
        return (idx.iloc[i + n]["close"] / idx.iloc[i]["close"] - 1) * 100

    all_events = []
    for ts, name in TARGETS.items():
        try:
            df = quarterly(ts)
            if len(df) < 8:
                continue
            evs = detect_events(df)
            if not evs:
                continue
            series = price_series(ts)
            if not len(series):
                continue
            for e in evs:
                e["ts_code"], e["name"] = ts, name
                for n in (60, 120, 250):
                    r = fwd_return(series, e["ann"], n)
                    b = bench(e["ann"], n)
                    e[f"ret_{n}"] = round(r, 1) if r is not None else None
                    e[f"excess_{n}"] = round(r - b, 1) if (r is not None and b is not None) else None
                all_events.append(e)
            print(f"{ts} {name}: {len(evs)} 事件 "
                  f"[{' | '.join(e['type'] + '@' + e['ann'] for e in evs)}]", flush=True)
        except Exception as ex:  # noqa: BLE001
            print(f"{ts} {name}: ERR {ex}", flush=True)

    ev = pd.DataFrame(all_events)
    ev["year"] = ev["ann"].str[:4]
    out = {"n_total": len(ev), "by_type": {}, "by_type_year": {}}
    print(f"\n════ 汇总(共 {len(ev)} 事件)════", flush=True)
    for etype in ("TOP", "BOTTOM"):
        sub = ev[ev["type"] == etype]
        stat = {}
        for n in (60, 120, 250):
            r = pd.to_numeric(sub[f"ret_{n}"], errors="coerce").dropna()
            ex = pd.to_numeric(sub[f"excess_{n}"], errors="coerce").dropna()
            if len(r):
                win_cond = (r < 0) if etype == "TOP" else (r > 0)
                stat[f"T+{n}"] = {
                    "n": len(r), "mean": round(float(r.mean()), 1), "median": round(float(r.median()), 1),
                    "win_rate": round(float(win_cond.mean() * 100), 1),
                    "excess_mean": round(float(ex.mean()), 1) if len(ex) else None,
                    "t_stat": round(float(r.mean() / (r.std() / np.sqrt(len(r)))), 2) if len(r) > 2 else None}
        out["by_type"][etype] = stat
        print(f"\n── {etype}(n={len(sub)})──", flush=True)
        for k, v in stat.items():
            print(f"  {k}: N={v['n']} 均值={v['mean']}% 中位={v['median']}% "
                  f"胜率={v['win_rate']}% 超额={v['excess_mean']}% t={v['t_stat']}", flush=True)
        # 分年份
        by_year = {}
        for yr, g in sub.groupby("year"):
            r250 = pd.to_numeric(g["ret_250"], errors="coerce").dropna()
            if len(r250):
                win_cond = (r250 < 0) if etype == "TOP" else (r250 > 0)
                by_year[yr] = {"n": len(r250), "mean250": round(float(r250.mean()), 1),
                               "win250": round(float(win_cond.mean() * 100), 1)}
        out["by_type_year"][etype] = by_year
        print(f"  分年份(T+250): {by_year}", flush=True)

    out["events"] = ev.to_dict("records")
    with open("davis_analyzer/studies/dg_factor_test_20260822.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\nJSON → davis_analyzer/studies/dg_factor_test_20260822.json", flush=True)


if __name__ == "__main__":
    main()
