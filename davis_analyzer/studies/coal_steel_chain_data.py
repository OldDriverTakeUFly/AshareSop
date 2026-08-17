#!/usr/bin/env python3
"""煤炭周期 + 钢铁/铁矿石周期产业链批量数据采集脚本.

采集 18 家 A 股标的:
  - 煤炭 8 家: 中国神华/陕西煤业/中煤能源/兖矿能源/山西焦煤/平煤股份/潞安环能/昊华能源
  - 钢铁 8 家: 宝钢/河钢/包钢/鞍钢/华菱/首钢/太钢不锈/永兴材料
  - 铁矿石 2 家: 海南矿业/金岭矿业

采集内容:
  1. 最新交易日估值快照 (daily_basic) — PE/PB/PS/市值/股息率
  2. 3 年估值历史分位 (PE/PB/PS percentile)
  3. 2025 年报财务 (income/fina_indicator) — 营收/净利/ROE/毛利率/负债率
  4. 2026H1 业绩预告 (forecast)

用法:
    .venv/bin/python davis_analyzer/studies/coal_steel_chain_data.py
输出:
    打印每个标的完整数据到 stdout + 写入 JSON
"""

from __future__ import annotations

import os
import sys
import json
from datetime import date, timedelta

# ── 环境设置 (必须在 import 前完成) ──
os.environ.setdefault("PROJECT_ROOT", "/home/leo/Projects/CodeAgentDashboard")
sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv
load_dotenv('.env', override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.valuation import detect_cyclical
from stockhot.tushare_config import get_pro_api

# ── 标的清单 ──
COAL_TARGETS = [
    ("601088.SH", "中国神华", "煤炭开采"),
    ("601225.SH", "陕西煤业", "煤炭开采"),
    ("601898.SH", "中煤能源", "煤炭开采"),
    ("600188.SH", "兖矿能源", "煤炭开采"),
    ("000983.SZ", "山西焦煤", "煤炭开采"),
    ("601666.SH", "平煤股份", "煤炭开采"),
    ("601699.SH", "潞安环能", "煤炭开采"),
    ("601101.SH", "昊华能源", "煤炭开采"),
]

STEEL_TARGETS = [
    ("600019.SH", "宝钢股份", "普钢"),
    ("000709.SZ", "河钢股份", "普钢"),
    ("600010.SH", "包钢股份", "普钢"),
    ("000898.SZ", "鞍钢股份", "普钢"),
    ("000932.SZ", "华菱钢铁", "普钢"),
    ("000959.SZ", "首钢股份", "普钢"),
    ("000825.SZ", "太钢不锈", "特钢"),
    ("002756.SZ", "永兴材料", "特钢"),
]

IRON_ORE_TARGETS = [
    ("601969.SH", "海南矿业", "普钢"),
    ("000655.SZ", "金岭矿业", "普钢"),
]

ALL_TARGETS = [
    ("coal", t) for t in COAL_TARGETS
] + [
    ("steel", t) for t in STEEL_TARGETS
] + [
    ("iron_ore", t) for t in IRON_ORE_TARGETS
]


def fmt(x, suffix=""):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "N/A"
    if isinstance(x, (int, float)):
        return f"{x:.2f}{suffix}"
    return str(x)


def collect_one(client, pro, ts_code: str, name: str, industry: str, sector: str) -> dict:
    """采集单只标的的全部数据."""
    out = {"ts_code": ts_code, "name": name, "industry": industry, "sector": sector}
    print(f"\n{'='*70}", flush=True)
    print(f"# [{sector}] {name} ({ts_code}) — 行业: {industry}", flush=True)
    print(f"{'='*70}", flush=True)

    # ── 0. 周期判定 ──
    is_cyc = detect_cyclical(industry)
    out["is_cyclical"] = bool(is_cyc)
    print(f"\n## 行业判定\n- 行业: {industry}\n- 周期股: {is_cyc}", flush=True)

    # ── 1. 最新交易日估值快照 ──
    print(f"\n## 最新交易日估值快照 (daily_basic)", flush=True)
    try:
        db = pro.daily_basic(ts_code=ts_code, limit=1)
        if db is not None and len(db) > 0:
            row = db.iloc[0]
            mv_yi = float(row.get("total_mv", 0)) / 1e4 if pd.notna(row.get("total_mv")) else None
            out["daily_basic_latest"] = {
                "trade_date": str(row.get("trade_date", "")),
                "close": float(row.get("close")) if pd.notna(row.get("close")) else None,
                "pe_ttm": float(row.get("pe_ttm")) if pd.notna(row.get("pe_ttm")) else None,
                "pb": float(row.get("pb")) if pd.notna(row.get("pb")) else None,
                "ps": float(row.get("ps")) if pd.notna(row.get("ps")) else None,
                "dv_ratio": float(row.get("dv_ratio")) if pd.notna(row.get("dv_ratio")) else None,
                "dv_ttm": float(row.get("dv_ttm")) if pd.notna(row.get("dv_ttm")) else None,
                "total_mv_yi": mv_yi,
            }
            d = out["daily_basic_latest"]
            print(f"  日期={d['trade_date']} close={d['close']} "
                  f"PE={fmt(d['pe_ttm'])} PB={fmt(d['pb'])} PS={fmt(d['ps'])} "
                  f"股息率={fmt(d['dv_ratio'],'%')} 市值={fmt(mv_yi,'亿')}", flush=True)
    except Exception as e:
        out["db_error"] = str(e)
        print(f"- daily_basic 错误: {e}", flush=True)

    # ── 2. 3 年估值历史分位 ──
    print(f"\n## 估值历史分位 (3 年 daily_basic)", flush=True)
    try:
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
        db = client.get_daily_basic(ts_code, start, end)
        db = db.sort_values("trade_date")
        pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
        pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
        ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
        mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
        out["valuation"] = {
            "data_points": len(db),
            "pe_current": float(pe.iloc[-1]) if len(pe) else None,
            "pb_current": float(pb.iloc[-1]) if len(pb) else None,
            "ps_current": float(ps.iloc[-1]) if len(ps) else None,
            "pe_pct": float((pe < pe.iloc[-1]).sum() / len(pe) * 100) if len(pe) else None,
            "pb_pct": float((pb < pb.iloc[-1]).sum() / len(pb) * 100) if len(pb) else None,
            "ps_pct": float((ps < ps.iloc[-1]).sum() / len(ps) * 100) if len(ps) else None,
            "mv_pct": float((mv < mv.iloc[-1]).sum() / len(mv) * 100) if len(mv) else None,
            "pe_50": float(pe.quantile(0.50)) if len(pe) else None,
            "pb_50": float(pb.quantile(0.50)) if len(pb) else None,
            "pb_min": float(pb.min()) if len(pb) else None,
            "pb_max": float(pb.max()) if len(pb) else None,
            "latest_trade_date": str(db["trade_date"].iloc[-1]) if len(db) else None,
        }
        v = out["valuation"]
        print(f"- 数据点: {v['data_points']}, 最新交易日: {v['latest_trade_date']}", flush=True)
        if v['pe_current']:
            print(f"- PE_TTM: {v['pe_current']:.2f} ({v['pe_pct']:.1f}%分位, 中位{v['pe_50']:.2f})", flush=True)
        if v['pb_current']:
            print(f"- PB: {v['pb_current']:.2f} ({v['pb_pct']:.1f}%分位, 中位{v['pb_50']:.2f}, 范围{v['pb_min']:.2f}-{v['pb_max']:.2f})", flush=True)
        if v['ps_current']:
            print(f"- PS: {v['ps_current']:.2f} ({v['ps_pct']:.1f}%分位)", flush=True)
    except Exception as e:
        out["val_error"] = str(e)
        print(f"- 估值分位错误: {e}", flush=True)

    # ── 3. 2025 年报财务 (income + fina_indicator) ──
    print(f"\n## 2025 年报财务", flush=True)
    try:
        inc = pro.income(ts_code=ts_code, period="20251231")
        fina = pro.fina_indicator(ts_code=ts_code, period="20251231")
        out["fin_2025"] = {}
        if inc is not None and len(inc) > 0:
            r = inc.iloc[0]
            rev = float(r.get("total_revenue", 0)) / 1e8 if pd.notna(r.get("total_revenue")) else None
            np_ = float(r.get("n_income_attr_p", 0)) / 1e8 if pd.notna(r.get("n_income_attr_p")) else None
            # 同比需要 2024 数据
            inc_prev = pro.income(ts_code=ts_code, period="20241231")
            np_prev = None
            rev_prev = None
            if inc_prev is not None and len(inc_prev) > 0:
                np_prev = float(inc_prev.iloc[0].get("n_income_attr_p", 0)) / 1e8 if pd.notna(inc_prev.iloc[0].get("n_income_attr_p")) else None
                rev_prev = float(inc_prev.iloc[0].get("total_revenue", 0)) / 1e8 if pd.notna(inc_prev.iloc[0].get("total_revenue")) else None
            np_yoy = None
            if np_ is not None and np_prev is not None and abs(np_prev) > 0.01:
                np_yoy = (np_ - np_prev) / abs(np_prev) * 100
            rev_yoy = None
            if rev is not None and rev_prev is not None and abs(rev_prev) > 0.01:
                rev_yoy = (rev - rev_prev) / abs(rev_prev) * 100
            out["fin_2025"].update({
                "revenue_yi": rev,
                "net_profit_yi": np_,
                "net_profit_yoy_pct": np_yoy,
                "revenue_yoy_pct": rev_yoy,
            })
            print(f"- 2025 营收: {fmt(rev,'亿')} ({fmt(rev_yoy,'%')}) | 净利: {fmt(np_,'亿')} | 净利同比: {fmt(np_yoy,'%')}", flush=True)
        if fina is not None and len(fina) > 0:
            r = fina.iloc[0]
            roe = float(r.get("roe")) if pd.notna(r.get("roe")) else None
            debt = float(r.get("debt_to_assets")) if pd.notna(r.get("debt_to_assets")) else None
            gpr = float(r.get("grossprofit_margin")) if pd.notna(r.get("grossprofit_margin")) else None
            npm = float(r.get("netprofit_margin")) if pd.notna(r.get("netprofit_margin")) else None
            out["fin_2025"].update({
                "roe_pct": roe,
                "debt_to_assets_pct": debt,
                "grossprofit_margin_pct": gpr,
                "netprofit_margin_pct": npm,
            })
            print(f"- ROE: {fmt(roe,'%')} | 负债率: {fmt(debt,'%')} | 毛利率: {fmt(gpr,'%')} | 净利率: {fmt(npm,'%')}", flush=True)
    except Exception as e:
        out["fin_error"] = str(e)
        print(f"- 财务错误: {e}", flush=True)

    # ── 4. 2026H1 业绩预告 (forecast) ──
    print(f"\n## 2026H1 业绩预告 (forecast)", flush=True)
    try:
        fc = pro.forecast(ts_code=ts_code, period="20260630")
        if fc is not None and len(fc) > 0:
            r = fc.iloc[0]
            out["forecast_2026h1"] = {
                "ann_date": str(r.get("ann_date", "")),
                "type": str(r.get("type", "")),
                "p_change_min": float(r.get("p_change_min")) if pd.notna(r.get("p_change_min")) else None,
                "p_change_max": float(r.get("p_change_max")) if pd.notna(r.get("p_change_max")) else None,
                "net_profit_min": float(r.get("net_profit_min", 0)) / 1e8 if pd.notna(r.get("net_profit_min")) else None,
                "net_profit_max": float(r.get("net_profit_max", 0)) / 1e8 if pd.notna(r.get("net_profit_max")) else None,
                "summary": str(r.get("summary", "")),
            }
            f = out["forecast_2026h1"]
            print(f"- 类型: {f['type']} | 净利: {fmt(f['net_profit_min'],'亿')}~{fmt(f['net_profit_max'],'亿')} | "
                  f"同比: {fmt(f['p_change_min'],'%')}~{fmt(f['p_change_max'],'%')}", flush=True)
            print(f"- 摘要: {f['summary'][:200]}", flush=True)
        else:
            out["forecast_2026h1"] = None
            print(f"- 无 2026H1 预告", flush=True)
    except Exception as e:
        out["forecast_error"] = str(e)
        print(f"- 预告错误: {e}", flush=True)

    # ── 5. 2026Q1 财务 (用于景气度 ΔG 判断) ──
    print(f"\n## 2026Q1 财务 (景气度参考)", flush=True)
    try:
        inc_q1 = pro.income(ts_code=ts_code, period="20260331")
        if inc_q1 is not None and len(inc_q1) > 0:
            r = inc_q1.iloc[0]
            np_q1 = float(r.get("n_income_attr_p", 0)) / 1e8 if pd.notna(r.get("n_income_attr_p")) else None
            inc_q1_prev = pro.income(ts_code=ts_code, period="20250331")
            np_q1_prev = None
            if inc_q1_prev is not None and len(inc_q1_prev) > 0:
                np_q1_prev = float(inc_q1_prev.iloc[0].get("n_income_attr_p", 0)) / 1e8 if pd.notna(inc_q1_prev.iloc[0].get("n_income_attr_p")) else None
            np_q1_yoy = None
            if np_q1 is not None and np_q1_prev is not None and abs(np_q1_prev) > 0.01:
                np_q1_yoy = (np_q1 - np_q1_prev) / abs(np_q1_prev) * 100
            out["fin_2026q1"] = {
                "net_profit_yi": np_q1,
                "net_profit_yoy_pct": np_q1_yoy,
            }
            print(f"- 2026Q1 净利: {fmt(np_q1,'亿')} | 同比: {fmt(np_q1_yoy,'%')}", flush=True)
    except Exception as e:
        out["q1_error"] = str(e)
        print(f"- Q1 错误: {e}", flush=True)

    return out


def summary_table(all_results, sector_filter, title):
    print(f"\n\n{'='*70}", flush=True)
    print(f"# 汇总表 ({title})", flush=True)
    print("=" * 70, flush=True)
    print(f"\n| 公司 | 代码 | 市值(亿) | PE_TTM | PB | PB分位% | PS | PS分位% | 股息率% | 2025营收(亿) | 2025净利(亿) | 净利同比% | ROE% | 负债率% | 毛利率% | 2026H1预告 |", flush=True)
    print(f"|------|------|---------|--------|-----|---------|-----|---------|---------|-------------|-------------|----------|------|---------|---------|------------|", flush=True)
    for r in all_results:
        if r["sector"] != sector_filter:
            continue
        name = r["name"]
        code = r["ts_code"]
        db = r.get("daily_basic_latest", {}) or {}
        val = r.get("valuation", {}) or {}
        fin = r.get("fin_2025", {}) or {}
        fc = r.get("forecast_2026h1", {}) or {}
        mv = db.get("total_mv_yi")
        pe = db.get("pe_ttm")
        pb = db.get("pb")
        pb_pct = val.get("pb_pct")
        ps = db.get("ps")
        ps_pct = val.get("ps_pct")
        dv = db.get("dv_ratio")
        rev = fin.get("revenue_yi")
        np_ = fin.get("net_profit_yi")
        np_yoy = fin.get("net_profit_yoy_pct")
        roe = fin.get("roe_pct")
        debt = fin.get("debt_to_assets_pct")
        gpr = fin.get("grossprofit_margin_pct")
        fc_str = ""
        if fc:
            fc_str = f"{fc.get('type','')} {fmt(fc.get('net_profit_min'),'亿')}~{fmt(fc.get('net_profit_max'),'亿')}"
        def f2(x, s=""):
            if x is None or (isinstance(x, float) and pd.isna(x)):
                return "N/A"
            return f"{x:.2f}{s}" if isinstance(x, float) else str(x)
        print(f"| {name} | {code} | {f2(mv)} | {f2(pe)} | {f2(pb)} | {f2(pb_pct)} | {f2(ps)} | {f2(ps_pct)} | {f2(dv)} | {f2(rev)} | {f2(np_)} | {f2(np_yoy)} | {f2(roe)} | {f2(debt)} | {f2(gpr)} | {fc_str} |", flush=True)


def main() -> None:
    print("=" * 70, flush=True)
    print("# 煤炭 + 钢铁/铁矿石周期产业链 18 家标的批量数据采集", flush=True)
    print(f"# 采集日期: {date.today()}", flush=True)
    print("=" * 70, flush=True)

    client = TushareClient()
    pro = get_pro_api()

    all_results = []
    for sector, (ts_code, name, industry) in ALL_TARGETS:
        try:
            r = collect_one(client, pro, ts_code, name, industry, sector)
            all_results.append(r)
        except Exception as e:
            print(f"\n!!! {name} ({ts_code}) 采集失败: {e}", flush=True)
            import traceback
            traceback.print_exc()

    summary_table(all_results, "coal", "煤炭 8 家")
    summary_table(all_results, "steel", "钢铁 8 家")
    summary_table(all_results, "iron_ore", "铁矿石 2 家")

    # JSON 落盘
    out_path = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/coal_steel_chain_data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n\nJSON 已写入: {out_path}", flush=True)


if __name__ == "__main__":
    main()
