#!/usr/bin/env python3
"""火电周期产业链批量数据采集脚本(6 家 A 股火电标的).

标的: 华能国际/华电国际/大唐发电/浙能电力/申能股份/皖能电力

采集内容:
  1. 最新交易日估值快照 (daily_basic 直连, 含 dv_ratio 股息率)
  2. 3 年估值历史分位 (client.get_daily_basic 缓存 + 行数校验, 不足分段直连兜底)
  3. 2020-2025 年报净利轨迹 (income, 周期历史复盘用)
  4. 2025 年报 + 2024 年报财务指标 (fina_indicator: ROE/负债率/毛利率)
  5. 2026Q1 财务 (景气度 ΔG)
  6. 2026H1 业绩预告 (forecast, 单位万元→亿)

用法:
    .venv/bin/python davis_analyzer/studies/thermal_power_chain_data.py
"""

from __future__ import annotations

import os
import sys
import json
from datetime import date, timedelta

# ── 环境设置 (必须在 import 前完成) ──
sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv
load_dotenv('/home/leo/Projects/CodeAgentDashboard/.env', override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd

from davis_analyzer.tushare_client import TushareClient
from stockhot.tushare_config import get_pro_api

# ── 标的清单 ──
THERMAL_TARGETS = [
    ("600011.SH", "华能国际", "全国火电龙头(央企)"),
    ("600027.SH", "华电国际", "全国火电(央企,山东为基本盘)"),
    ("601991.SH", "大唐发电", "全国火电(央企,京津冀+沿海)"),
    ("600023.SH", "浙能电力", "浙江区域火电(沿海进口煤)"),
    ("600642.SH", "申能股份", "上海区域火电(沿海负荷中心)"),
    ("000543.SZ", "皖能电力", "安徽区域火电(坑口煤+外送)"),
]

# 煤炭镜像参照(交叉引用煤炭研报, 直连取最新快照验证)
COAL_REF = [
    ("601088.SH", "中国神华"),
    ("601225.SH", "陕西煤业"),
]


def fmt(x, suffix=""):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "N/A"
    if isinstance(x, (int, float)):
        return f"{x:.2f}{suffix}"
    return str(x)


def fetch_db_history_direct(pro, ts_code: str, start: str, end: str) -> pd.DataFrame:
    """直连分段拉取 daily_basic(绕过缓存, 按年分段)."""
    parts = []
    s = date(int(start[:4]), int(start[4:6]), int(start[6:]))
    e = date(int(end[:4]), int(end[4:6]), int(end[6:]))
    cur = s
    while cur <= e:
        seg_end = min(date(cur.year, 12, 31), e)
        df = pro.daily_basic(
            ts_code=ts_code,
            start_date=cur.strftime("%Y%m%d"),
            end_date=seg_end.strftime("%Y%m%d"),
            fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv",
        )
        if df is not None and len(df) > 0:
            parts.append(df)
        cur = date(seg_end.year + 1, 1, 1)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def collect_one(client, pro, ts_code: str, name: str, tag: str) -> dict:
    out = {"ts_code": ts_code, "name": name, "tag": tag}
    print(f"\n{'='*70}", flush=True)
    print(f"# {name} ({ts_code}) — {tag}", flush=True)
    print(f"{'='*70}", flush=True)

    # ── 1. 最新估值快照(直连, 含股息率) ──
    try:
        db = pro.daily_basic(ts_code=ts_code, limit=1)
        if db is not None and len(db) > 0:
            row = db.iloc[0]
            mv_yi = float(row.get("total_mv", 0)) / 1e4 if pd.notna(row.get("total_mv")) else None
            out["snapshot"] = {
                "trade_date": str(row.get("trade_date", "")),
                "close": float(row["close"]) if pd.notna(row.get("close")) else None,
                "pe_ttm": float(row["pe_ttm"]) if pd.notna(row.get("pe_ttm")) else None,
                "pb": float(row["pb"]) if pd.notna(row.get("pb")) else None,
                "dv_ratio": float(row["dv_ratio"]) if pd.notna(row.get("dv_ratio")) else None,
                "dv_ttm": float(row["dv_ttm"]) if pd.notna(row.get("dv_ttm")) else None,
                "total_mv_yi": mv_yi,
            }
            d = out["snapshot"]
            print(f"[快照 {d['trade_date']}] close={d['close']} PE={fmt(d['pe_ttm'])} "
                  f"PB={fmt(d['pb'])} 股息率={fmt(d['dv_ratio'],'%')} 市值={fmt(mv_yi,'亿')}", flush=True)
    except Exception as e:
        out["db_error"] = str(e)
        print(f"- daily_basic 快照错误: {e}", flush=True)

    # ── 2. 3 年估值分位(缓存 + 行数校验兜底) ──
    try:
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
        db = client.get_daily_basic(ts_code, start, end)
        if db is None or len(db) < 600:  # 3年约730交易日, 缓存不足则直连
            print(f"- 缓存行数不足({0 if db is None else len(db)}), 分段直连兜底", flush=True)
            db = fetch_db_history_direct(pro, ts_code, start, end)
        db = db.sort_values("trade_date").reset_index(drop=True)
        pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
        pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
        out["valuation"] = {
            "data_points": len(db),
            "latest_trade_date": str(db["trade_date"].iloc[-1]),
            "pe_current": float(pe.iloc[-1]) if len(pe) else None,
            "pb_current": float(pb.iloc[-1]) if len(pb) else None,
            "pe_pct": float((pe < pe.iloc[-1]).sum() / len(pe) * 100) if len(pe) else None,
            "pb_pct": float((pb < pb.iloc[-1]).sum() / len(pb) * 100) if len(pb) else None,
            "pe_50": float(pe.quantile(0.50)) if len(pe) else None,
            "pb_50": float(pb.quantile(0.50)) if len(pb) else None,
            "pb_min": float(pb.min()) if len(pb) else None,
            "pb_max": float(pb.max()) if len(pb) else None,
        }
        v = out["valuation"]
        print(f"[3年分位] 数据点={v['data_points']} 截至{v['latest_trade_date']}", flush=True)
        print(f"  PE_TTM={fmt(v['pe_current'])} ({fmt(v['pe_pct'],'%分位')}, 中位{fmt(v['pe_50'])})", flush=True)
        print(f"  PB={fmt(v['pb_current'])} ({fmt(v['pb_pct'],'%分位')}, 中位{fmt(v['pb_50'])}, "
              f"范围{fmt(v['pb_min'])}-{fmt(v['pb_max'])})", flush=True)
    except Exception as e:
        out["val_error"] = str(e)
        print(f"- 估值分位错误: {e}", flush=True)

    # ── 3. 2020-2025 年报净利轨迹 ──
    try:
        hist = []
        for y in range(2020, 2026):
            inc = pro.income(ts_code=ts_code, period=f"{y}1231",
                             fields="ts_code,end_date,ann_date,total_revenue,n_income,n_income_attr_p")
            if inc is not None and len(inc) > 0:
                r = inc.iloc[0]
                rev = float(r["total_revenue"]) / 1e8 if pd.notna(r.get("total_revenue")) else None
                np_ = float(r["n_income_attr_p"]) / 1e8 if pd.notna(r.get("n_income_attr_p")) else None
                hist.append({"year": y, "revenue_yi": rev, "net_profit_yi": np_,
                             "ann_date": str(r.get("ann_date", ""))})
        out["profit_history"] = hist
        print("[年报净利轨迹] " + " | ".join(
            f"{h['year']}:{fmt(h['net_profit_yi'],'亿')}" for h in hist), flush=True)
    except Exception as e:
        out["hist_error"] = str(e)
        print(f"- 年报轨迹错误: {e}", flush=True)

    # ── 4. 2025/2024 年报指标 ──
    try:
        out["fina"] = {}
        for y in (2025, 2024):
            fina = pro.fina_indicator(ts_code=ts_code, period=f"{y}1231",
                                      fields="ts_code,end_date,roe,debt_to_assets,grossprofit_margin,netprofit_margin")
            if fina is not None and len(fina) > 0:
                r = fina.iloc[0]
                out["fina"][str(y)] = {
                    "roe": float(r["roe"]) if pd.notna(r.get("roe")) else None,
                    "debt_to_assets": float(r["debt_to_assets"]) if pd.notna(r.get("debt_to_assets")) else None,
                    "gpm": float(r["grossprofit_margin"]) if pd.notna(r.get("grossprofit_margin")) else None,
                    "npm": float(r["netprofit_margin"]) if pd.notna(r.get("netprofit_margin")) else None,
                }
        for y in ("2025", "2024"):
            f = out["fina"].get(y)
            if f:
                print(f"[{y}年报] ROE={fmt(f['roe'],'%')} 负债率={fmt(f['debt_to_assets'],'%')} "
                      f"毛利率={fmt(f['gpm'],'%')} 净利率={fmt(f['npm'],'%')}", flush=True)
    except Exception as e:
        out["fina_error"] = str(e)
        print(f"- 财务指标错误: {e}", flush=True)

    # ── 5. 2026Q1 ──
    try:
        inc_q1 = pro.income(ts_code=ts_code, period="20260331",
                            fields="ts_code,end_date,n_income_attr_p,total_revenue")
        if inc_q1 is not None and len(inc_q1) > 0:
            r = inc_q1.iloc[0]
            np_q1 = float(r["n_income_attr_p"]) / 1e8 if pd.notna(r.get("n_income_attr_p")) else None
            out["q1_2026_np_yi"] = np_q1
            print(f"[2026Q1] 归母净利={fmt(np_q1,'亿')}", flush=True)
    except Exception as e:
        print(f"- 2026Q1 错误: {e}", flush=True)

    # ── 6. 2026H1 预告(forecast 单位万元) ──
    try:
        fc = pro.forecast(ts_code=ts_code, period="20260630")
        if fc is not None and len(fc) > 0:
            r = fc.iloc[0]
            out["forecast_2026h1"] = {
                "ann_date": str(r.get("ann_date", "")),
                "type": str(r.get("type", "")),
                "p_change_min": float(r["p_change_min"]) if pd.notna(r.get("p_change_min")) else None,
                "p_change_max": float(r["p_change_max"]) if pd.notna(r.get("p_change_max")) else None,
                "net_profit_min_yi": float(r["net_profit_min"]) / 1e8 if pd.notna(r.get("net_profit_min")) else None,
                "net_profit_max_yi": float(r["net_profit_max"]) / 1e8 if pd.notna(r.get("net_profit_max")) else None,
                "summary": str(r.get("summary", "")),
            }
            f = out["forecast_2026h1"]
            print(f"[2026H1预告] {f['type']} 净利{fmt(f['net_profit_min_yi'],'亿')}~{fmt(f['net_profit_max_yi'],'亿')} "
                  f"同比{fmt(f['p_change_min'],'%')}~{fmt(f['p_change_max'],'%')}", flush=True)
            print(f"  摘要: {f['summary'][:160]}", flush=True)
        else:
            out["forecast_2026h1"] = None
            print("[2026H1预告] 无", flush=True)
    except Exception as e:
        out["forecast_error"] = str(e)
        print(f"- 预告错误: {e}", flush=True)

    return out


def main():
    client = TushareClient()
    pro = get_pro_api()
    results = {"thermal": [], "coal_ref": [], "fetch_date": date.today().isoformat()}

    for ts_code, name, tag in THERMAL_TARGETS:
        results["thermal"].append(collect_one(client, pro, ts_code, name, tag))

    print(f"\n{'#'*70}\n# 煤炭镜像参照(直连快照)\n{'#'*70}", flush=True)
    for ts_code, name in COAL_REF:
        try:
            db = pro.daily_basic(ts_code=ts_code, limit=1)
            if db is not None and len(db) > 0:
                row = db.iloc[0]
                results["coal_ref"].append({
                    "ts_code": ts_code, "name": name,
                    "trade_date": str(row.get("trade_date", "")),
                    "pb": float(row["pb"]) if pd.notna(row.get("pb")) else None,
                    "pe_ttm": float(row["pe_ttm"]) if pd.notna(row.get("pe_ttm")) else None,
                    "dv_ratio": float(row["dv_ratio"]) if pd.notna(row.get("dv_ratio")) else None,
                })
                r = results["coal_ref"][-1]
                print(f"{name} {r['trade_date']} PB={fmt(r['pb'])} PE={fmt(r['pe_ttm'])} "
                      f"股息率={fmt(r['dv_ratio'],'%')}", flush=True)
        except Exception as e:
            print(f"- {name} 错误: {e}", flush=True)

    out_path = "/tmp/thermal_chain_data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n[DONE] JSON → {out_path}", flush=True)


if __name__ == "__main__":
    main()
