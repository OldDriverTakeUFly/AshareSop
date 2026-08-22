# -*- coding: utf-8 -*-
"""固态电池 A 股卡位标的快扫(2026-08-22,v2.0 版本更新取数)。

17 标的:估值分位(PB/PE/PS 3年)+手工复权动量+2026 预告+市值。
复用 youse_sector_scan 模式:分段 daily_basic+drop_duplicates+升序校验。
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
    # 电芯(5)
    "300750.SZ": "宁德时代", "300014.SZ": "亿纬锂能", "002074.SZ": "国轩高科",
    "300207.SZ": "欣旺达", "688567.SH": "孚能科技",
    # 材料(9)
    "603200.SH": "上海洗霸", "688005.SH": "容百科技", "300073.SZ": "当升科技",
    "688778.SH": "厦钨新能", "603659.SH": "璞泰来", "688116.SH": "天奈科技",
    "002812.SZ": "恩捷股份", "600206.SH": "有研新材", "002882.SZ": "金龙羽",
    # 设备(3)
    "300450.SZ": "先导智能", "688499.SH": "利元亨",
    # 资源+回收(1)
    "002340.SZ": "格林美",
}


def seg_daily_basic(ts, start, end):
    frames, cur, fin = [], date(int(start[:4]), int(start[4:6]), int(start[6:])), date(int(end[:4]), int(end[4:6]), int(end[6:]))
    while cur <= fin:
        seg_end = min(cur + timedelta(days=499), fin)
        df = pro.daily_basic(ts_code=ts, start_date=cur.strftime("%Y%m%d"), end_date=seg_end.strftime("%Y%m%d"),
                             fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv")
        if len(df):
            frames.append(df)
        cur = seg_end + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)


def pct(s, latest):
    s = s.dropna()
    return float((s < latest).sum() / len(s) * 100) if len(s) else float("nan")


def manual_returns(ts):
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=460)).strftime("%Y%m%d")
    px = pro.daily(ts_code=ts, start_date=start, end_date=end, fields="ts_code,trade_date,close")
    af = pro.adj_factor(ts_code=ts, start_date=start, end_date=end, fields="ts_code,trade_date,adj_factor")
    if not len(px) or not len(af):
        return {}
    m = px.merge(af, on=["ts_code", "trade_date"]).sort_values("trade_date").reset_index(drop=True)
    m["adj"] = m["close"] * m["adj_factor"]
    last = m.iloc[-1]
    out = {"last_close": float(last["close"])}
    for w in (60, 120, 250):
        if len(m) > w:
            out[f"ret_{w}d"] = round((last["adj"] / m.iloc[-1 - w]["adj"] - 1) * 100, 1)
    return out


def main():
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
    result = {}
    for ts, name in TARGETS.items():
        row = {"name": name}
        try:
            db = seg_daily_basic(ts, start, end)
            if len(db) >= 700:
                pe = pd.to_numeric(db["pe_ttm"], errors="coerce")
                pb = pd.to_numeric(db["pb"], errors="coerce")
                ps = pd.to_numeric(db["ps"], errors="coerce")
                mv = pd.to_numeric(db["total_mv"], errors="coerce")
                row.update({
                    "trade_date": str(db["trade_date"].iloc[-1]),
                    "pe": round(float(pe.iloc[-1]), 2), "pe_pct": round(pct(pe, float(pe.iloc[-1])), 1),
                    "pb": round(float(pb.iloc[-1]), 2), "pb_pct": round(pct(pb, float(pb.iloc[-1])), 1),
                    "ps": round(float(ps.iloc[-1]), 2), "ps_pct": round(pct(ps, float(ps.iloc[-1])), 1),
                    "mv_yi": round(float(mv.iloc[-1]) / 1e4, 1), "days": len(db)})
            else:
                row["warn"] = f"rows={len(db)}"
        except Exception as e:  # noqa: BLE001
            row["err"] = str(e)
        try:
            row.update(manual_returns(ts))
        except Exception as e:  # noqa: BLE001
            row["mom_err"] = str(e)
        try:
            fc = pro.forecast(ts_code=ts, fields="ann_date,end_date,type,p_change_min,p_change_max")
            if len(fc):
                fc = fc[pd.to_numeric(fc["ann_date"]) >= 20260101].sort_values("ann_date")
                row["forecast"] = f"{fc.iloc[-1]['type']} {fc.iloc[-1]['p_change_min']}~{fc.iloc[-1]['p_change_max']}%" if len(fc) else "-"
            else:
                row["forecast"] = "-"
        except Exception:  # noqa: BLE001
            row["forecast"] = "-"
        result[ts] = row
        print(f"{ts} {name}: PB {row.get('pb')}({row.get('pb_pct')}%) PE {row.get('pe')}({row.get('pe_pct')}%) "
              f"250d {row.get('ret_250d')}% 预告 {row.get('forecast')}", flush=True)
    out = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/ssb_scan_20260822.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"JSON → {out}", flush=True)


if __name__ == "__main__":
    main()
