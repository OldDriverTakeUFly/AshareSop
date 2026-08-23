#!/usr/bin/env python3
"""六只周期/材料股的统一数据采集脚本.

采集内容:
  1. 财务数据 (fetch_financial_data) - 12 期
  2. 估值历史分位 (daily_basic 3 年)
  3. 景气度评分 (calculate_prosperity_score)
  4. 五补充因子 (momentum/dividend/forecast/holder/profitability)
  5. 股东户数趋势 (pro.stk_holdernumber)
  6. 相对市场估值 (stockhot.valuation.analyze_relative_valuation)
  7. 时效性校验 (daily_basic 最新交易日 + income 最新 + forecast)
  8. 行业分类 (周期股判定)

用法:
    .venv/bin/python davis_analyzer/studies/six_cyclicals_data.py
输出:
    打印每个标的的完整数据到 stdout (用 rtk 包装)
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
load_dotenv('/home/leo/Projects/CodeAgentDashboard/.env', override=True)
# load_dotenv 后重新 pin PROJECT_ROOT, 防 .env 的 /app 值破坏 stockhot mkdir
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.valuation import detect_cyclical
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality

# stockhot 相对估值
from stockhot.tushare_config import get_pro_api
from stockhot.valuation import analyze_relative_valuation

# ── 标的清单 ──
TARGETS = [
    ("600737.SH", "中粮糖业", "食品"),
    ("601118.SH", "海南橡胶", "橡胶"),
    ("002714.SZ", "牧原股份", "农业综合"),
    ("600160.SH", "巨化股份", "化工原料"),
    ("600330.SH", "天通股份", "专用机械"),
    ("002384.SZ", "东山精密", "元器件"),
]


def fmt_pct(x):
    """格式化百分比, None 返回 N/A."""
    if x is None:
        return "N/A"
    return f"{x*100:.1f}%"


def collect_one(client, pro, ts_code: str, name: str, industry: str) -> dict:
    """采集单只标的的全部数据."""
    out = {"ts_code": ts_code, "name": name, "industry": industry}
    print(f"\n{'='*70}", flush=True)
    print(f"# {name} ({ts_code}) — 行业: {industry}", flush=True)
    print(f"{'='*70}", flush=True)

    # ── 0. 行业/周期判定 ──
    is_cyc = detect_cyclical(industry)
    out["is_cyclical"] = bool(is_cyc)
    print(f"\n## 行业判定\n- 行业: {industry}\n- 周期股: {is_cyc}", flush=True)

    # ── 1. 时效性校验 ──
    print(f"\n## 时效性校验", flush=True)
    try:
        db = pro.daily_basic(ts_code=ts_code, limit=1)
        latest_trade = db.iloc[0]["trade_date"] if len(db) else "none"
        out["latest_trade_date"] = str(latest_trade)
        if len(db):
            row = db.iloc[0]
            out["daily_basic_latest"] = {
                "trade_date": str(row.get("trade_date")),
                "close": float(row.get("close")) if pd.notna(row.get("close")) else None,
                "pe_ttm": float(row.get("pe_ttm")) if pd.notna(row.get("pe_ttm")) else None,
                "pb": float(row.get("pb")) if pd.notna(row.get("pb")) else None,
                "ps": float(row.get("ps")) if pd.notna(row.get("ps")) else None,
                "total_mv": float(row.get("total_mv")) if pd.notna(row.get("total_mv")) else None,
                "circ_mv": float(row.get("circ_mv")) if pd.notna(row.get("circ_mv")) else None,
                "turnover_rate": float(row.get("turnover_rate")) if pd.notna(row.get("turnover_rate")) else None,
            }
        print(f"- 最新交易日: {latest_trade}", flush=True)
        if "daily_basic_latest" in out:
            d = out["daily_basic_latest"]
            mv_yi = d["total_mv"]/1e4 if d["total_mv"] else None
            print(f"  close={d['close']} pe={d['pe_ttm']} pb={d['pb']} ps={d['ps']} mv={mv_yi:.1f}亿" if mv_yi else "  mv=N/A", flush=True)
    except Exception as e:
        out["latest_trade_date"] = f"ERR:{e}"
        print(f"- daily_basic 错误: {e}", flush=True)

    try:
        inc = pro.income(ts_code=ts_code, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
        if len(inc):
            out["latest_income"] = {
                "end_date": str(inc.iloc[0]["end_date"]),
                "ann_date": str(inc.iloc[0].get("ann_date")),
            }
            print(f"- 最新报告期: {out['latest_income']['end_date']} (披露 {out['latest_income']['ann_date']})", flush=True)
    except Exception as e:
        print(f"- income 时效错误: {e}", flush=True)

    try:
        fc = pro.forecast(ts_code=ts_code, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,summary", limit=1)
        if len(fc):
            r = fc.iloc[0]
            out["latest_forecast"] = {
                "ann_date": str(r.get("ann_date")),
                "end_date": str(r.get("end_date")),
                "type": str(r.get("type")),
                "p_change_min": float(r["p_change_min"]) if pd.notna(r.get("p_change_min")) else None,
                "p_change_max": float(r["p_change_max"]) if pd.notna(r.get("p_change_max")) else None,
                "summary": str(r.get("summary"))[:120] if pd.notna(r.get("summary")) else "",
            }
            print(f"- 业绩预告: {out['latest_forecast']['type']} {out['latest_forecast']['p_change_min']}~{out['latest_forecast']['p_change_max']}% (ann {out['latest_forecast']['ann_date']}, end {out['latest_forecast']['end_date']})", flush=True)
        else:
            out["latest_forecast"] = None
            print(f"- 业绩预告: 无", flush=True)
    except Exception as e:
        print(f"- forecast 错误: {e}", flush=True)

    # ── 2. 财务数据 ──
    print(f"\n## 财务数据 (12 期)", flush=True)
    try:
        fin = fetch_financial_data(client, ts_code, periods=12)
        out["fin_periods"] = len(fin)
        if fin:
            fin_rows = []
            for fd in fin[:12]:
                debt_ratio = (fd.total_debt / fd.total_assets) if (fd.total_assets and fd.total_assets > 0) else None
                row = {
                    "report_period": fd.report_period,
                    "revenue_yi": (fd.revenue or 0) / 1e8,
                    "net_profit_yi": (float(fd.net_profit) if fd.net_profit else 0) / 1e8,
                    "eps": fd.eps,
                    "roe_pct": fd.roe,
                    "operating_cf_yi": (fd.operating_cf or 0) / 1e8,
                    "debt_ratio_pct": debt_ratio * 100 if debt_ratio else None,
                    "total_assets_yi": (fd.total_assets or 0) / 1e8,
                    "total_debt_yi": (fd.total_debt or 0) / 1e8,
                    "yoy_revenue": fmt_pct(fd.yoy_revenue_growth),
                    "yoy_profit": fmt_pct(fd.yoy_profit_growth),
                    "grossprofit_margin": fd.grossprofit_margin,
                    "rd_exp": fd.rd_exp,
                }
                fin_rows.append(row)
            out["financials"] = fin_rows
            # 打印表格
            print(f"\n| 报告期 | 营收(亿) | 归母净利(亿) | EPS | ROE% | 经营CF(亿) | 资产负债率% | 营收同比 | 净利同比 | 毛利率% |", flush=True)
            print(f"|---|---|---|---|---|---|---|---|---|---|", flush=True)
            for r in fin_rows:
                gm = f"{r['grossprofit_margin']:.2f}" if r['grossprofit_margin'] else "N/A"
                print(f"| {r['report_period']} | {r['revenue_yi']:.2f} | {r['net_profit_yi']:.2f} | {r['eps']:.4f} | {r['roe_pct']:.2f} | {r['operating_cf_yi']:.2f} | {r['debt_ratio_pct']:.1f} | {r['yoy_revenue']} | {r['yoy_profit']} | {gm} |", flush=True)
    except Exception as e:
        out["fin_error"] = str(e)
        print(f"- 财务错误: {e}", flush=True)
        fin = []

    # ── 3. 估值历史分位 ──
    print(f"\n## 估值历史分位 (3 年 daily_basic)", flush=True)
    try:
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
        db = client.get_daily_basic(ts_code, start, end)
        db = db.sort_values("trade_date")  # 升序! 坑点
        pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
        pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
        ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
        mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
        out["valuation_points"] = len(db)
        out["valuation"] = {
            "data_points": len(db),
            "pe_current": float(pe.iloc[-1]) if len(pe) else None,
            "pb_current": float(pb.iloc[-1]) if len(pb) else None,
            "ps_current": float(ps.iloc[-1]) if len(ps) else None,
            "mv_current_yi": float(mv.iloc[-1]/1e4) if len(mv) else None,
            "pe_pct": float((pe < pe.iloc[-1]).sum() / len(pe) * 100) if len(pe) else None,
            "pb_pct": float((pb < pb.iloc[-1]).sum() / len(pb) * 100) if len(pb) else None,
            "ps_pct": float((ps < ps.iloc[-1]).sum() / len(ps) * 100) if len(ps) else None,
            "mv_pct": float((mv < mv.iloc[-1]).sum() / len(mv) * 100) if len(mv) else None,
            "pe_10": float(pe.quantile(0.10)) if len(pe) else None,
            "pe_25": float(pe.quantile(0.25)) if len(pe) else None,
            "pe_50": float(pe.quantile(0.50)) if len(pe) else None,
            "pe_75": float(pe.quantile(0.75)) if len(pe) else None,
            "pe_90": float(pe.quantile(0.90)) if len(pe) else None,
            "pe_95": float(pe.quantile(0.95)) if len(pe) else None,
            "pb_10": float(pb.quantile(0.10)) if len(pb) else None,
            "pb_25": float(pb.quantile(0.25)) if len(pb) else None,
            "pb_50": float(pb.quantile(0.50)) if len(pb) else None,
            "pb_75": float(pb.quantile(0.75)) if len(pb) else None,
            "pb_90": float(pb.quantile(0.90)) if len(pb) else None,
            "pb_95": float(pb.quantile(0.95)) if len(pb) else None,
            "ps_10": float(ps.quantile(0.10)) if len(ps) else None,
            "ps_50": float(ps.quantile(0.50)) if len(ps) else None,
            "ps_90": float(ps.quantile(0.90)) if len(ps) else None,
            "latest_trade_date": str(db["trade_date"].iloc[-1]) if len(db) else None,
        }
        v = out["valuation"]
        print(f"- 数据点: {v['data_points']}, 最新交易日: {v['latest_trade_date']}", flush=True)
        print(f"- PE_TTM: {v['pe_current']:.2f} ({v['pe_pct']:.1f}%分位) | 分位: 10%={v['pe_10']:.2f} 25%={v['pe_25']:.2f} 50%={v['pe_50']:.2f} 75%={v['pe_75']:.2f} 90%={v['pe_90']:.2f}" if v['pe_current'] else "- PE: 无有效点", flush=True)
        print(f"- PB: {v['pb_current']:.2f} ({v['pb_pct']:.1f}%分位) | 分位: 10%={v['pb_10']:.2f} 25%={v['pb_25']:.2f} 50%={v['pb_50']:.2f} 75%={v['pb_75']:.2f} 90%={v['pb_90']:.2f}", flush=True)
        print(f"- PS: {v['ps_current']:.2f} ({v['ps_pct']:.1f}%分位) | 10%={v['ps_10']:.2f} 50%={v['ps_50']:.2f} 90%={v['ps_90']:.2f}" if v['ps_current'] else "- PS: 无有效点", flush=True)
        print(f"- 总市值: {v['mv_current_yi']:.1f}亿 ({v['mv_pct']:.1f}%分位)", flush=True)
    except Exception as e:
        out["val_error"] = str(e)
        print(f"- 估值错误: {e}", flush=True)

    # ── 4. 景气度 ──
    print(f"\n## 景气度评分", flush=True)
    if fin and len(fin) >= 4:
        try:
            pscore = calculate_prosperity_score(fin)
            stage = classify_stock_stage(pscore)
            out["prosperity"] = {
                "composite_score": pscore.composite_score,
                "revenue_score": pscore.revenue_score,
                "profit_score": pscore.profit_score,
                "slope_score": pscore.slope_score,
                "duration_score": pscore.duration_score,
                "delta_g": pscore.delta_g,
                "relative_delta_g": pscore.relative_delta_g,
                "stage": stage,
            }
            p = out["prosperity"]
            print(f"- composite={p['composite_score']:.2f} (营收={p['revenue_score']:.2f}×0.30 + 利润={p['profit_score']:.2f}×0.30 + 斜率={p['slope_score']:.2f}×0.25 + 持续={p['duration_score']:.2f}×0.15)", flush=True)
            print(f"- ΔG={p['delta_g']:.2f} (相对={p['relative_delta_g']:.2f}), 阶段={p['stage']}", flush=True)
        except Exception as e:
            out["prosperity_error"] = str(e)
            print(f"- 景气度错误: {e}", flush=True)
            pscore = None
    else:
        out["prosperity"] = None
        print(f"- 财务数据不足 {len(fin) if fin else 0} 期, 跳过景气度", flush=True)
        pscore = None

    # ── 5. 五补充因子 ──
    print(f"\n## 五补充因子", flush=True)
    # 5.1 momentum
    try:
        mom = analyze_momentum(client, ts_code)
        if mom:
            out["momentum"] = {
                "momentum_score": mom.momentum_score,
                "absolute_momentum_score": mom.absolute_momentum_score,
                "rs_percentile": mom.rs_percentile,
                "window_returns": dict(mom.window_returns) if mom.window_returns else {},
            }
            m = out["momentum"]
            print(f"- 动量: 综合={m['momentum_score']:.2f} 绝对={m['absolute_momentum_score']:.2f} RS={m['rs_percentile']}", flush=True)
            for w, r in m["window_returns"].items():
                print(f"    {w}d: {r:.2f}%", flush=True)
        else:
            out["momentum"] = None
            print(f"- 动量: None", flush=True)
    except Exception as e:
        out["momentum_error"] = str(e)
        print(f"- 动量错误: {e}", flush=True)

    # 5.2 dividend
    try:
        div = analyze_dividend(client, ts_code)
        if div:
            out["dividend"] = {
                "dividend_score": div.dividend_score,
                "consecutive_years": div.consecutive_years,
                "latest_yield_pct": div.latest_yield_pct,
                "payout_years": getattr(div, "payout_years", None),
            }
            d = out["dividend"]
            print(f"- 分红: 得分={d['dividend_score']:.2f} 连续{d['consecutive_years']}年 股息率={d['latest_yield_pct']}%", flush=True)
    except Exception as e:
        out["dividend_error"] = str(e)
        print(f"- 分红错误: {e}", flush=True)

    # 5.3 forecast (需要 pscore)
    try:
        if pscore:
            fc = analyze_forecast(client, ts_code, pscore)
            if fc:
                out["forecast_signal"] = {
                    "leading_score": fc.leading_score,
                    "p_change_mid": fc.p_change_mid,
                    "type": getattr(fc, "type", None),
                    "is_stale": getattr(fc, "is_stale", None),
                }
                f = out["forecast_signal"]
                print(f"- 业绩预告信号: 得分={f['leading_score']:.2f} 类型={f['type']} 中值={f['p_change_mid']}%", flush=True)
            else:
                out["forecast_signal"] = None
                print(f"- 业绩预告信号: None", flush=True)
        else:
            out["forecast_signal"] = None
            print(f"- 业绩预告信号: 跳过 (无 pscore)", flush=True)
    except Exception as e:
        out["forecast_error"] = str(e)
        print(f"- 业绩预告信号错误: {e}", flush=True)

    # 5.4 holder concentration
    try:
        hc = analyze_holder_concentration(client, ts_code)
        if hc:
            out["holder_concentration"] = {
                "score": getattr(hc, "score", None) or getattr(hc, "concentration_score", None),
                "trend": getattr(hc, "trend", None) or getattr(hc, "trend_label", None),
                "latest_holder_num": getattr(hc, "latest_holder_num", None),
                "prev_holder_num": getattr(hc, "prev_holder_num", None),
            }
            h = out["holder_concentration"]
            print(f"- 筹码集中度: 得分={h['score']} 趋势={h['trend']} 最新户数={h['latest_holder_num']}", flush=True)
        else:
            out["holder_concentration"] = None
            print(f"- 筹码集中度: None", flush=True)
    except Exception as e:
        out["holder_error"] = str(e)
        print(f"- 筹码集中度错误: {e}", flush=True)

    # 5.5 profitability quality
    try:
        if fin:
            pq = analyze_profitability_quality(fin)
            out["profitability"] = {
                "quality_score": getattr(pq, "quality_score", None),
                "gm_score": getattr(pq, "gm_score", None),
                "gmd_score": getattr(pq, "gmd_score", None),
                "gross_margin_trend": getattr(pq, "gross_margin_trend", None),
                "rd_intensity": getattr(pq, "rd_intensity", None),
            }
            # 打印所有属性用于排查
            pq_attrs = {k: getattr(pq, k, None) for k in dir(pq) if not k.startswith("_")}
            out["profitability_all"] = {k: v for k, v in pq_attrs.items() if not callable(v)}
            print(f"- 成长质量: {out['profitability']}", flush=True)
            print(f"  全部属性: {out['profitability_all']}", flush=True)
    except Exception as e:
        out["profitability_error"] = str(e)
        print(f"- 成长质量错误: {e}", flush=True)

    # ── 6. 股东户数趋势 (pro.stk_holdernumber) ──
    print(f"\n## 股东户数趋势 (stk_holdernumber, 近 8 期)", flush=True)
    try:
        h = pro.stk_holdernumber(ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num").sort_values("end_date").tail(8)
        rows = []
        prev = None
        for _, r in h.iterrows():
            num = int(r["holder_num"])
            chg = (num - prev) / prev * 100 if prev else None
            rows.append({"end_date": str(r["end_date"]), "ann_date": str(r["ann_date"]), "holder_num": num, "chg_pct": chg})
            prev = num
        out["holder_number_trend"] = rows
        if rows:
            print(f"\n| 截止日 | 披露日 | 股东户数 | 环比 |", flush=True)
            print(f"|---|---|---|---|", flush=True)
            for r in rows:
                chg = f"{'↑' if r['chg_pct']>0 else '↓'}{abs(r['chg_pct']):.1f}%" if r['chg_pct'] else "基期"
                print(f"| {r['end_date']} | {r['ann_date']} | {r['holder_num']:,} | {chg} |", flush=True)
            nums4 = [r["holder_num"] for r in rows[-4:]]
            trend = "集中(动能增强✓)" if nums4[-1] < nums4[0] else "分散(动能减弱⚠)"
            out["holder_trend_label"] = trend
            print(f"  → 趋势: {trend}", flush=True)
    except Exception as e:
        out["holder_number_error"] = str(e)
        print(f"- 股东户数错误: {e}", flush=True)

    # ── 7. 相对市场估值 ──
    print(f"\n## 相对市场估值锚定 (stockhot.valuation)", flush=True)
    try:
        rv = analyze_relative_valuation(pro, ts_code, name)
        out["relative_valuation"] = {
            "pe_ratio": getattr(rv, "pe_ratio", None),
            "pe_ratio_pct": getattr(rv, "pe_ratio_pct", None),
            "erp": getattr(rv, "erp", None),
            "quadrant": getattr(rv, "quadrant", None),
            "quadrant_label": getattr(rv, "quadrant_label", None),
            "composite_verdict": getattr(rv, "composite_verdict", None),
            "signals": list(getattr(rv, "signals", []) or []),
            "benchmark_pe": getattr(rv, "benchmark_pe", None),
            "benchmark_pct": getattr(rv, "benchmark_pct", None),
            "risk_free_rate": getattr(rv, "risk_free_rate", None),
        }
        # 打印全部属性
        rv_attrs = {k: getattr(rv, k, None) for k in dir(rv) if not k.startswith("_")}
        out["relative_valuation_all"] = {k: v for k, v in rv_attrs.items() if not callable(v)}
        rv_o = out["relative_valuation"]
        print(f"- 相对PE: {rv_o['pe_ratio']}x ({rv_o['pe_ratio_pct']}%分位)", flush=True)
        print(f"- ERP: {rv_o['erp']}%", flush=True)
        print(f"- 象限: Q{rv_o['quadrant']} ({rv_o['quadrant_label']})", flush=True)
        print(f"- 综合: {rv_o['composite_verdict']}", flush=True)
        print(f"  全部属性: {out['relative_valuation_all']}", flush=True)
    except Exception as e:
        out["relative_valuation_error"] = str(e)
        import traceback
        out["relative_valuation_tb"] = traceback.format_exc()
        print(f"- 相对估值错误: {e}", flush=True)
        print(traceback.format_exc(), flush=True)

    return out


def main():
    print("=" * 70, flush=True)
    print("# 六只周期/材料股统一数据采集", flush=True)
    print(f"# 运行日期: {date.today()}", flush=True)
    print("=" * 70, flush=True)

    client = TushareClient()
    pro = get_pro_api(timeout=30)

    all_data = {}
    for ts_code, name, industry in TARGETS:
        try:
            data = collect_one(client, pro, ts_code, name, industry)
            all_data[ts_code] = data
        except Exception as e:
            import traceback
            print(f"\n!!! {name} ({ts_code}) 采集失败: {e}", flush=True)
            print(traceback.format_exc(), flush=True)
            all_data[ts_code] = {"ts_code": ts_code, "name": name, "error": str(e), "tb": traceback.format_exc()}

    # 保存 JSON
    out_path = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/six_cyclicals_data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n\n{'='*70}", flush=True)
    print(f"# 全部数据已保存到 {out_path}", flush=True)
    print(f"{'='*70}", flush=True)


if __name__ == "__main__":
    main()
