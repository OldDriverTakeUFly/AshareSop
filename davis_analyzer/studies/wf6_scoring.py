"""WF6 产业链 5 标的引擎横评脚本。

跑 davis_analyzer 引擎：财务数据 + 景气度 G+ΔG + 估值分位 + 相对市场锚定，
输出结构化结果供研报引用。遵循 engine-usage.md 模板。
"""
import os
from dotenv import load_dotenv
# override=True: shell may export a STALE token (old api.waditu.com token) that
# shadows the fresh one in .env. Force the .env token to win.
load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
# Re-pin PROJECT_ROOT: .env ships a Docker value (/app) which breaks local
# stockhot.core.config mkdir. Must override AFTER load_dotenv.
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

from datetime import date, timedelta
import json

import pandas as pd
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage

from stockhot.valuation import analyze_relative_valuation
from stockhot.tushare_config import get_pro_api

TARGETS = [
    ("688146.SH", "中船特气", "科创"),
    ("688549.SH", "中巨芯", "科创"),
    ("300346.SZ", "南大光电", "创业板"),
    ("002971.SZ", "和远气体", "主板"),
    ("600378.SH", "昊华科技", "主板"),
]


def fmt(v, d=2):
    if v is None or (isinstance(v, float) and v != v):
        return None
    return round(float(v), d)


def run_one(client, code, name, board):
    out = {"code": code, "name": name, "board": board}
    # ── 1. 财务 ──
    fin = fetch_financial_data(client, code, periods=12)
    out["fin_periods"] = len(fin)
    if not fin:
        out["err"] = "no financial data"
        return out
    # code 张冠李戴核对
    out["fin_ts_code"] = fin[0].ts_code
    latest = fin[0]
    out["latest_period"] = latest.report_period
    out["revenue_yi"] = fmt(latest.revenue / 1e8, 2) if latest.revenue else None
    out["net_profit_yi"] = fmt(latest.net_profit / 1e8, 2) if latest.net_profit else None
    out["roe"] = fmt(latest.roe, 2)
    out["yoy_revenue"] = fmt(latest.yoy_revenue_growth * 100, 1) if latest.yoy_revenue_growth is not None else None
    out["yoy_profit"] = fmt(latest.yoy_profit_growth * 100, 1) if latest.yoy_profit_growth is not None else None
    # 单季净利（取最近4期做 ΔG 判断的基础）
    out["recent4_periods"] = [f.report_period for f in fin[:4]]
    out["recent4_profit_yi"] = [fmt(f.net_profit / 1e8, 2) for f in fin[:4]]

    # ── 2. 估值（get_daily_basic 更可靠）──
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
    db = client.get_daily_basic(code, start, end)
    if db is not None and len(db) > 0:
        db = db.sort_values("trade_date")
        pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
        pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
        ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
        mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
        out["pe_ttm"] = fmt(pe.iloc[-1], 1) if len(pe) else None
        out["pe_pct"] = round((pe < pe.iloc[-1]).sum() / len(pe) * 100, 1) if len(pe) else None
        out["pb"] = fmt(pb.iloc[-1], 2) if len(pb) else None
        out["pb_pct"] = round((pb < pb.iloc[-1]).sum() / len(pb) * 100, 1) if len(pb) else None
        out["ps"] = fmt(ps.iloc[-1], 2) if len(ps) else None
        out["ps_pct"] = round((ps < ps.iloc[-1]).sum() / len(ps) * 100, 1) if len(ps) else None
        out["mv_yi"] = fmt(mv.iloc[-1] / 1e4, 1) if len(mv) else None
        out["pe_valid_pts"] = int(len(pe))
    else:
        out["pe_ttm"] = None

    # ── 3. 景气度 ──
    try:
        pscore = calculate_prosperity_score(fin)
        stage = classify_stock_stage(pscore)
        out["prosperity_composite"] = fmt(pscore.composite_score, 1)
        out["delta_g"] = fmt(pscore.delta_g, 2)
        out["revenue_score"] = fmt(pscore.revenue_score, 1)
        out["profit_score"] = fmt(pscore.profit_score, 1)
        out["stage"] = stage
    except Exception as e:
        out["prosperity_err"] = str(e)

    # ── 4. 相对市场估值锚定 ──
    try:
        pro = get_pro_api(timeout=30)
        rv = analyze_relative_valuation(pro, code, stock_name=name)
        out["benchmark"] = rv.benchmark
        out["pe_ratio"] = fmt(rv.pe_ratio, 2)
        out["pe_ratio_pct"] = fmt(rv.pe_ratio_pct, 1)
        out["pe_ratio_label"] = rv.pe_ratio_label
        out["erp"] = fmt(rv.erp, 2)
        out["erp_label"] = rv.erp_label
        out["stock_pe_pct"] = fmt(rv.stock_pe_pct, 1)
        out["index_pe_pct"] = fmt(rv.index_pe_pct, 1)
        out["pe_band_quadrant"] = rv.quadrant_label
        out["rv_verdict"] = rv.composite_verdict
    except Exception as e:
        out["rv_err"] = str(e)[:160]

    return out


def main():
    client = TushareClient()
    results = []
    for code, name, board in TARGETS:
        print(f"=== {name} ({code}) ===")
        try:
            r = run_one(client, code, name, board)
        except Exception as e:
            r = {"code": code, "name": name, "board": board, "err": str(e)[:200]}
        results.append(r)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        print()

    out_path = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/wf6_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nSaved {len(results)} results to {out_path}")


if __name__ == "__main__":
    main()
