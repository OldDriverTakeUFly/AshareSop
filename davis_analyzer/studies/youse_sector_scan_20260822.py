# -*- coding: utf-8 -*-
"""有色板块 2026-08-22 板块级取数脚本(板块综合研判报告专用)。

取数内容:
1. 20 只有色代表股(铜/铝/金/锂/稀土/小金属)的 PE/PB/PS 最新值 + 3 年分位
2. 手工复权动量(20/60/120/250d,规避 analyze_momentum 缓存缺口坑)
3. 2026 年业绩预告(过滤 ann_date >= 20260101)
4. 申万有色金属指数(801050.SI)近 18 个月走势关键点

注意坑点(见 engine-usage.md):
- daily_basic 分段拉取(≤500 天/段),concat 后 reset_index(drop=True)
- 排序 sort_values("trade_date") 升序后再取 iloc[-1]
- forecast 单位万元,/1e4 转亿
- 脚本模式 sys.path.insert 兜底
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
os.chdir("/home/leo/Projects/CodeAgentDashboard")

from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd

from stockhot.tushare_config import get_pro_api

pro = get_pro_api(timeout=60)

TARGETS = {
    # 铜
    "601899.SH": "紫金矿业", "603993.SH": "洛阳钼业", "600362.SH": "江西铜业",
    "000630.SZ": "铜陵有色",
    # 铝
    "601600.SH": "中国铝业", "000807.SZ": "云铝股份", "000933.SZ": "神火股份",
    # 金
    "600547.SH": "山东黄金", "600489.SH": "中金黄金", "600988.SH": "赤峰黄金",
    # 锂
    "002460.SZ": "赣锋锂业", "002466.SZ": "天齐锂业",
    # 稀土磁材
    "600111.SH": "北方稀土", "000831.SZ": "中国稀土", "300748.SZ": "金力永磁",
    # 小金属(锡/锌锗/钼/钴/锑)
    "000960.SZ": "锡业股份", "600497.SH": "驰宏锌锗", "601958.SH": "金钼股份",
    "603799.SH": "华友钴业", "002428.SZ": "云南锗业",
}


def fetch_daily_basic_full(ts_code: str, start: str, end: str) -> pd.DataFrame:
    """分段拉取 daily_basic(≤500 天/段),合并去重,升序返回。"""
    frames = []
    cur_start = date(int(start[:4]), int(start[4:6]), int(start[6:]))
    cur_end = date(int(end[:4]), int(end[4:6]), int(end[6:]))
    while cur_start <= cur_end:
        seg_end = min(cur_start + timedelta(days=499), cur_end)
        df = pro.daily_basic(
            ts_code=ts_code,
            start_date=cur_start.strftime("%Y%m%d"),
            end_date=seg_end.strftime("%Y%m%d"),
            fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv,turnover_rate",
        )
        if len(df):
            frames.append(df)
        cur_start = seg_end + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)
    return out


def pct_rank(series: pd.Series, latest: float) -> float:
    s = series.dropna()
    if not len(s):
        return float("nan")
    return float((s < latest).sum() / len(s) * 100)


def manual_returns(ts_code: str) -> dict:
    """pro.daily + adj_factor 手工复权动量。"""
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=420)).strftime("%Y%m%d")
    px = pro.daily(ts_code=ts_code, start_date=start, end_date=end,
                   fields="ts_code,trade_date,close")
    af = pro.adj_factor(ts_code=ts_code, start_date=start, end_date=end,
                        fields="ts_code,trade_date,adj_factor")
    if not len(px) or not len(af):
        return {}
    m = px.merge(af, on=["ts_code", "trade_date"]).sort_values("trade_date").reset_index(drop=True)
    m["adj"] = m["close"] * m["adj_factor"]
    last = m.iloc[-1]
    out = {"last_close": float(last["close"]), "last_date": last["trade_date"]}
    for w in (20, 60, 120, 250):
        if len(m) > w:
            base = m.iloc[-1 - w]["adj"]
            out[f"ret_{w}d"] = round((last["adj"] / base - 1) * 100, 1)
    return out


def fetch_forecast(ts_code: str) -> dict | None:
    fc = pro.forecast(
        ts_code=ts_code,
        fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,"
               "net_profit_min,net_profit_max",
    )
    if not len(fc):
        return None
    fc = fc[pd.to_numeric(fc["ann_date"]) >= 20260101]
    if not len(fc):
        return None
    fc = fc.sort_values("ann_date")
    r = fc.iloc[-1]
    np_min = pd.to_numeric(r.get("net_profit_min"), errors="coerce")
    np_max = pd.to_numeric(r.get("net_profit_max"), errors="coerce")
    return {
        "ann_date": r["ann_date"], "end_date": r["end_date"], "type": r["type"],
        "p_change": f"[{r['p_change_min']}, {r['p_change_max']}]%",
        "net_profit_yi": (f"{np_min/1e4:.1f}~{np_max/1e4:.1f}"
                          if pd.notna(np_min) and pd.notna(np_max) else None),
    }


def main() -> None:
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
    result = {}
    for ts_code, name in TARGETS.items():
        row: dict = {"name": name}
        try:
            db = fetch_daily_basic_full(ts_code, start, end)
            if len(db) >= 700:
                pe = pd.to_numeric(db["pe_ttm"], errors="coerce")
                pb = pd.to_numeric(db["pb"], errors="coerce")
                ps = pd.to_numeric(db["ps"], errors="coerce")
                mv = pd.to_numeric(db["total_mv"], errors="coerce")
                row["trade_date"] = db["trade_date"].iloc[-1]
                row["close"] = float(db["close"].iloc[-1])
                row["pe"] = round(float(pe.iloc[-1]), 2)
                row["pe_pct"] = round(pct_rank(pe, float(pe.iloc[-1])), 1)
                row["pb"] = round(float(pb.iloc[-1]), 2)
                row["pb_pct"] = round(pct_rank(pb, float(pb.iloc[-1])), 1)
                row["ps"] = round(float(ps.iloc[-1]), 2)
                row["mv_yi"] = round(float(mv.iloc[-1]) / 1e4, 1)
                row["days"] = len(db)
            else:
                row["warn"] = f"daily_basic rows={len(db)} <700, 分位不可用"
        except Exception as e:  # noqa: BLE001
            row["val_err"] = str(e)
        try:
            row.update(manual_returns(ts_code))
        except Exception as e:  # noqa: BLE001
            row["mom_err"] = str(e)
        try:
            row["forecast"] = fetch_forecast(ts_code)
        except Exception as e:  # noqa: BLE001
            row["fc_err"] = str(e)
        result[ts_code] = row
        fc_s = (row.get("forecast") or {}).get("type", "-")
        print(f"{ts_code} {name}: PB {row.get('pb')} ({row.get('pb_pct')}%分位) "
              f"250d {row.get('ret_250d')}% 预告 {fc_s}", flush=True)

    # 申万有色指数走势
    idx_start = (date.today() - timedelta(days=550)).strftime("%Y%m%d")
    for idx_code, idx_name in (("801050.SI", "申万有色金属"),):
        try:
            idx = pro.index_daily(ts_code=idx_code, start_date=idx_start, end_date=end)
            if len(idx):
                idx = idx.sort_values("trade_date").reset_index(drop=True)
                close = pd.to_numeric(idx["close"], errors="coerce")
                peak_i = close.idxmax()
                result["_index_" + idx_code] = {
                    "name": idx_name,
                    "last_date": idx["trade_date"].iloc[-1],
                    "last": float(close.iloc[-1]),
                    "peak_date": idx["trade_date"].iloc[peak_i],
                    "peak": float(close.max()),
                    "drawdown_from_peak_pct": round((close.iloc[-1] / close.max() - 1) * 100, 1),
                    "ytd_pct": round((close.iloc[-1] / close.iloc[
                        max(0, next((i for i, d in enumerate(idx["trade_date"]) if d >= "20260101"), 0))
                    ] - 1) * 100, 1) if any(d >= "20260101" for d in idx["trade_date"]) else None,
                }
                # 5/6 高点与 8/6 低点区间表现(验证 21 财经 -10.29% 口径)
                may = idx[idx["trade_date"] >= "20260506"]
                if len(may):
                    may_close = pd.to_numeric(may["close"], errors="coerce")
                    result["_index_" + idx_code]["since_0506_pct"] = round(
                        (float(close.iloc[-1]) / float(may_close.iloc[0]) - 1) * 100, 1)
        except Exception as e:  # noqa: BLE001
            result["_index_" + idx_code] = {"err": str(e)}

    out_path = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/youse_sector_scan_20260822.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nJSON → {out_path}", flush=True)


if __name__ == "__main__":
    main()
