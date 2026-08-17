#!/usr/bin/env python3
"""光伏 8 家横向对比取数(通威/隆基/晶科/晶澳/天合/中环/大全/阿特斯).

输出: davis_analyzer/studies/output/pvduo_peers.json
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env", override=True)
if os.environ.get("PROJECT_ROOT") in ("/app", "", None):
    os.environ["PROJECT_ROOT"] = str(Path.cwd())

from stockhot.tushare_config import get_pro_api  # noqa: E402

PEERS = [
    ("600438.SH", "通威股份"),
    ("601012.SH", "隆基绿能"),
    ("688223.SH", "晶科能源"),
    ("002459.SZ", "晶澳科技"),
    ("688599.SH", "天合光能"),
    ("002129.SZ", "TCL中环"),
    ("688303.SH", "大全能源"),
    ("688472.SH", "阿特斯"),
]
DAYS = 1095


def val_df(pro, ts_code):
    frames = []
    seg_days = 480
    cur = date.today() - timedelta(days=DAYS)
    while cur < date.today():
        seg_end = min(cur + timedelta(days=seg_days), date.today())
        df = pro.daily_basic(
            ts_code=ts_code, start_date=cur.strftime("%Y%m%d"),
            end_date=seg_end.strftime("%Y%m%d"),
            fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv")
        if df is not None and len(df):
            frames.append(df)
        cur = seg_end + timedelta(days=1)
    db = pd.concat(frames, ignore_index=True).drop_duplicates("trade_date")
    return db.sort_values("trade_date").reset_index(drop=True)


def main():
    pro = get_pro_api(timeout=30)
    out = {}
    for code, name in PEERS:
        try:
            db = val_df(pro, code)
            pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
            ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
            pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
            mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
            close = pd.to_numeric(db["close"], errors="coerce").dropna()
            # 最新财务(2025年报+2026Q1)
            inc = pro.income(ts_code=code, start_date="20260101",
                             fields="ts_code,ann_date,end_date,total_revenue,n_income,n_income_attr_p")
            rows = {}
            for _, r in inc.iterrows():
                rows[str(r["end_date"])] = {
                    "rev_yi": round(float(r["total_revenue"]) / 1e8, 1),
                    "np_yi": round(float(r["n_income"]) / 1e8, 2),
                    "np_attr_yi": round(float(r["n_income_attr_p"]) / 1e8, 2),
                    "ann_date": str(r["ann_date"]),
                }
            fc = pro.forecast(ts_code=code, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
            fc_rows = []
            for _, r in fc.iterrows():
                fc_rows.append({
                    "ann_date": str(r["ann_date"]), "end_date": str(r["end_date"]),
                    "type": r["type"],
                    "p_min": r["p_change_min"], "p_max": r["p_change_max"],
                    "np_min_yi": round(float(r["net_profit_min"]) / 1e4, 1) if not pd.isna(r["net_profit_min"]) else None,
                    "np_max_yi": round(float(r["net_profit_max"]) / 1e4, 1) if not pd.isna(r["net_profit_max"]) else None,
                })
            out[code] = {
                "name": name, "trade_date": str(db["trade_date"].iloc[-1]),
                "close": float(close.iloc[-1]), "mv_yi": round(float(mv.iloc[-1]) / 1e4, 1),
                "pb": round(float(pb.iloc[-1]), 2),
                "pb_pct": round((pb < pb.iloc[-1]).sum() / len(pb) * 100, 1),
                "ps": round(float(ps.iloc[-1]), 2),
                "ps_pct": round((ps < ps.iloc[-1]).sum() / len(ps) * 100, 1),
                "pe": (round(float(pe.iloc[-1]), 1) if len(pe) and float(pe.iloc[-1]) > 0 else None),
                "points": len(db),
                "income": rows, "forecast": fc_rows[:3],
            }
            r = out[code]
            print(f"{name} {code}: {r['trade_date']} close={r['close']} mv={r['mv_yi']}亿 "
                  f"PB={r['pb']}({r['pb_pct']}%分位) PS={r['ps']}({r['ps_pct']}%) PE={r['pe']} "
                  f"2025归母={rows.get('20251231', {}).get('np_attr_yi')} 2026Q1归母={rows.get('20260331', {}).get('np_attr_yi')} "
                  f"预告={fc_rows[0]['type'] if fc_rows else '无'}")
        except Exception as e:
            print(f"{name} {code} 失败: {e}")
    Path("davis_analyzer/studies/output").mkdir(parents=True, exist_ok=True)
    with open("davis_analyzer/studies/output/pvduo_peers.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved → davis_analyzer/studies/output/pvduo_peers.json")


if __name__ == "__main__":
    main()
