#!/usr/bin/env python3
"""补充采集: 股东户数趋势 (修 NaN) + 巨化景气度/盈利能力 + 周期股人工判定."""

from __future__ import annotations

import os
import sys
import json

os.environ.setdefault("PROJECT_ROOT", "/home/leo/Projects/CodeAgentDashboard")
sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv
load_dotenv('.env', override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.profitability import analyze_profitability_quality
from stockhot.tushare_config import get_pro_api

TARGETS = [
    ("600737.SH", "中粮糖业"),
    ("601118.SH", "海南橡胶"),
    ("002714.SZ", "牧原股份"),
    ("600160.SH", "巨化股份"),
    ("600330.SH", "天通股份"),
    ("002384.SZ", "东山精密"),
]

# 人工判定周期股 (覆盖 detect_cyclical 未覆盖的行业)
MANUAL_CYCLICAL = {
    "600737.SH": True,   # 糖周期
    "601118.SH": True,   # 胶周期
    "002714.SZ": True,   # 猪周期
    "600160.SH": True,   # 氟化工周期
    "600330.SH": False,  # 磁性材料/电子, 非典型周期
    "002384.SZ": False,  # PCB/元器件, 非典型周期
}


def holder_trend_clean(pro, ts_code: str, periods: int = 8) -> dict:
    """干净的股东户数趋势, 处理 NaN holder_num."""
    h = pro.stk_holdernumber(
        ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num"
    )
    # 过滤 NaN holder_num + 排序 + 取尾部
    h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(periods)
    rows = []
    prev = None
    for _, r in h.iterrows():
        num = int(r["holder_num"])
        chg = (num - prev) / prev * 100 if prev else None
        rows.append({
            "end_date": str(r["end_date"]),
            "ann_date": str(r["ann_date"]),
            "holder_num": num,
            "chg_pct": chg,
        })
        prev = num
    nums4 = [r["holder_num"] for r in rows[-4:]] if len(rows) >= 4 else [r["holder_num"] for r in rows]
    trend = "集中(动能增强✓)" if (len(nums4) >= 2 and nums4[-1] < nums4[0]) else "分散(动能减弱⚠)"
    return {"rows": rows, "trend": trend, "latest": nums4[-1] if nums4 else None, "base": nums4[0] if nums4 else None}


def top10_floatholders(pro, ts_code: str) -> dict:
    """十大流通股东持股合计 (近 2 期对比)."""
    try:
        t = pro.top10_floatholders(ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_name,hold_amount")
        if len(t) == 0:
            return {"available": False}
        # 按报告期聚合
        t = t.dropna(subset=["hold_amount"])
        by_date = t.groupby("end_date")["hold_amount"].sum().sort_index().tail(4)
        rows = []
        for end_date, total in by_date.items():
            rows.append({"end_date": str(end_date), "top10_total": float(total)})
        return {"available": True, "rows": rows}
    except Exception as e:
        return {"available": False, "error": str(e)}


def main():
    print("=" * 70, flush=True)
    print("# 补充数据采集: 股东户数 + 巨化景气度/盈利", flush=True)
    print("=" * 70, flush=True)

    client = TushareClient()
    pro = get_pro_api(timeout=30)

    # 加载既有数据
    out_path = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/six_cyclicals_data.json"
    with open(out_path, encoding="utf-8") as f:
        all_data = json.load(f)

    for ts_code, name in TARGETS:
        data = all_data.get(ts_code, {})
        print(f"\n{'='*60}", flush=True)
        print(f"# {name} ({ts_code})", flush=True)
        print(f"{'='*60}", flush=True)

        # ── 1. 股东户数趋势 (修 NaN) ──
        print(f"\n## 股东户数趋势 (clean)", flush=True)
        try:
            ht = holder_trend_clean(pro, ts_code)
            data["holder_number_trend"] = ht["rows"]
            data["holder_trend_label"] = ht["trend"]
            print(f"\n| 截止日 | 披露日 | 股东户数 | 环比 |", flush=True)
            print(f"|---|---|---|---|", flush=True)
            for r in ht["rows"]:
                chg = f"{'↑' if r['chg_pct']>0 else '↓'}{abs(r['chg_pct']):.1f}%" if r['chg_pct'] else "基期"
                print(f"| {r['end_date']} | {r['ann_date']} | {r['holder_num']:,} | {chg} |", flush=True)
            print(f"  → 趋势: {ht['trend']} (最新 {ht['latest']:,} / 基期 {ht['base']:,})", flush=True)
        except Exception as e:
            import traceback
            print(f"- 股东户数错误: {e}", flush=True)
            traceback.print_exc()

        # ── 2. 十大流通股东 ──
        print(f"\n## 十大流通股东持股合计", flush=True)
        t10 = top10_floatholders(pro, ts_code)
        data["top10_floatholders"] = t10
        if t10.get("available"):
            for r in t10["rows"]:
                print(f"  {r['end_date']}: top10 合计 {r['top10_total']/1e8:.2f}亿股", flush=True)
        else:
            print(f"  不可用: {t10.get('error','无数据')}", flush=True)

        # ── 3. 周期股人工判定 ──
        data["is_cyclical_manual"] = MANUAL_CYCLICAL.get(ts_code)

        # ── 4. 巨化景气度/盈利 (修 fin=[] bug) ──
        if ts_code == "600160.SH":
            print(f"\n## 巨化景气度/盈利 重采", flush=True)
            try:
                fin = fetch_financial_data(client, ts_code, periods=12)
                print(f"- fin 期数: {len(fin)}", flush=True)
                if fin and len(fin) >= 4:
                    p = calculate_prosperity_score(fin)
                    stage = classify_stock_stage(p)
                    data["prosperity"] = {
                        "composite_score": p.composite_score,
                        "revenue_score": p.revenue_score,
                        "profit_score": p.profit_score,
                        "slope_score": p.slope_score,
                        "duration_score": p.duration_score,
                        "delta_g": p.delta_g,
                        "relative_delta_g": p.relative_delta_g,
                        "stage": stage,
                    }
                    print(f"- prosperity: comp={p.composite_score:.2f} dG={p.delta_g:.2f} stage={stage}", flush=True)
                    print(f"  (营收={p.revenue_score:.2f} 利润={p.profit_score:.2f} 斜率={p.slope_score:.2f} 持续={p.duration_score:.2f})", flush=True)
                # profitability
                pq = analyze_profitability_quality(fin)
                pq_attrs = {k: getattr(pq, k, None) for k in dir(pq) if not k.startswith("_")}
                data["profitability_all"] = {k: v for k, v in pq_attrs.items() if not callable(v)}
                data["profitability"] = {
                    "quality_score": getattr(pq, "quality_score", None),
                    "gross_margin_score": getattr(pq, "gross_margin_score", None),
                    "rd_intensity_score": getattr(pq, "rd_intensity_score", None),
                    "latest_gross_margin": getattr(pq, "latest_gross_margin", None),
                    "latest_rd_intensity": getattr(pq, "latest_rd_intensity", None),
                    "gross_margin_delta": getattr(pq, "gross_margin_delta", None),
                    "data_sufficient": getattr(pq, "data_sufficient", None),
                }
                print(f"- profitability: {data['profitability']}", flush=True)
            except Exception as e:
                import traceback
                print(f"- 错误: {e}", flush=True)
                traceback.print_exc()

        all_data[ts_code] = data

    # 保存
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n{'='*60}", flush=True)
    print(f"# 补充数据已合并保存到 {out_path}", flush=True)


if __name__ == "__main__":
    main()
