"""工业互联网产业链标的引擎批量取数脚本。

跑 davis_analyzer 引擎：财务数据 + 景气度 G+ΔG + 估值分位（PE/PB/PS）+
相对市场锚定 + 股东户数趋势，输出结构化 JSON 供产业链研报引用。
遵循 engine-usage.md 模板（基于 wf6_scoring.py）。
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

# 工业互联网产业链核心标的池（按环节分类）
# 上游-工控硬件 / 中游-工业软件 / 中游-平台 / 中游-网络
TARGETS = [
    # ── 工控硬件层（DCS/PLC/伺服）──
    ("688777.SH", "中控技术", "DCS龙头"),
    ("300124.SZ", "汇川技术", "PLC/伺服"),
    ("603416.SH", "信捷电气", "小型PLC"),
    # ── 工业软件层（CAD/CAE/EDA/MES/ERP）──
    ("688083.SH", "中望软件", "CAD国产第一"),
    ("301269.SZ", "华大九天", "EDA国产龙头"),
    ("600845.SH", "宝信软件", "MES+IDC"),
    ("600588.SH", "用友网络", "ERP龙头"),
    ("300687.SZ", "赛意信息", "MES/ERP实施"),
    ("300378.SZ", "鼎捷软件", "亚太ERP"),
    # ── 工业互联网平台层 ──
    ("300166.SZ", "东方国信", "工业互联网平台"),
    ("300170.SZ", "汉得信息", "IT实施/平台"),
    # ── 状态监测/预测性维护 ──
    ("688768.SH", "容知日新", "设备健康监测"),
]


def fmt(v, d=2):
    if v is None or (isinstance(v, float) and v != v):
        return None
    return round(float(v), d)


def run_one(client, code, name, seg):
    out = {"code": code, "name": name, "seg": seg}
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
    out["gross_margin"] = fmt(latest.gross_margin, 2) if hasattr(latest, "gross_margin") and latest.gross_margin else None
    out["net_margin"] = fmt(latest.net_margin, 2) if hasattr(latest, "net_margin") and latest.net_margin else None
    out["yoy_revenue"] = fmt(latest.yoy_revenue_growth * 100, 1) if latest.yoy_revenue_growth is not None else None
    out["yoy_profit"] = fmt(latest.yoy_profit_growth * 100, 1) if latest.yoy_profit_growth is not None else None
    # 单季净利（取最近4期做 ΔG 判断的基础）
    out["recent4_periods"] = [f.report_period for f in fin[:4]]
    out["recent4_profit_yi"] = [fmt(f.net_profit / 1e8, 2) for f in fin[:4]]
    out["recent4_revenue_yi"] = [fmt(f.revenue / 1e8, 2) for f in fin[:4]]
    out["recent4_gross_margin"] = [fmt(f.gross_margin, 2) if hasattr(f, "gross_margin") else None for f in fin[:4]]

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
        out["latest_trade_date"] = str(db["trade_date"].iloc[-1])
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

    # ── 3. 景气度（G+ΔG + 山峰理论定位）──
    try:
        pscore = calculate_prosperity_score(fin)
        stage = classify_stock_stage(pscore)
        out["prosperity_composite"] = fmt(pscore.composite_score, 1)
        out["delta_g"] = fmt(pscore.delta_g, 2)
        out["revenue_score"] = fmt(pscore.revenue_score, 1)
        out["profit_score"] = fmt(pscore.profit_score, 1)
        out["stage"] = stage
        # G = 最新增速绝对值，ΔG 符号定山峰位置
        out["g_profit_abs"] = out["yoy_profit"]
        out["mountain_side"] = (
            "左山坡(ΔG>0 加速)" if (pscore.delta_g is not None and pscore.delta_g > 0)
            else ("右山坡(ΔG<0 减速)" if (pscore.delta_g is not None and pscore.delta_g < 0)
                  else "ΔG不可靠")
        )
    except Exception as e:
        out["prosperity_err"] = str(e)[:160]

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

    # ── 5. 股东户数趋势（筹码集中度领先信号）──
    try:
        df = pro.stk_holdernumber(ts_code=code, fields="ts_code,ann_date,end_date,holder_num")
        if df is not None and len(df) > 0:
            df = df.sort_values("end_date")
            recent = df.tail(6)  # 近 6 期
            nums = recent["holder_num"].tolist()
            dates = recent["end_date"].tolist()
            out["holder_num_periods"] = dates
            out["holder_num_values"] = nums
            # 趋势判断（近4期）
            if len(nums) >= 4:
                recent4 = nums[-4:]
                delta_pct = (recent4[-1] - recent4[0]) / recent4[0] * 100 if recent4[0] else None
                out["holder_num_change_pct"] = fmt(delta_pct, 1)
                out["chip_trend"] = (
                    "筹码集中(动能增强✓)" if recent4[-1] < recent4[0]
                    else "筹码分散(动能减弱⚠)"
                )
    except Exception as e:
        out["holder_err"] = str(e)[:120]

    return out


def main():
    client = TushareClient()
    results = []
    for code, name, seg in TARGETS:
        print(f"=== {name} ({code}) [{seg}] ===")
        try:
            r = run_one(client, code, name, seg)
        except Exception as e:
            r = {"code": code, "name": name, "seg": seg, "err": str(e)[:200]}
        results.append(r)
        # 精简打印
        summary = {k: v for k, v in r.items()
                   if k not in ("recent4_periods", "recent4_profit_yi", "recent4_revenue_yi",
                                "recent4_gross_margin", "holder_num_periods", "holder_num_values")}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print()

    out_path = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/industrial_internet_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nSaved {len(results)} results to {out_path}")


if __name__ == "__main__":
    main()
