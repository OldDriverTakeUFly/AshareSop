"""Full-A-share four-factor screening — find the top-20 buy candidates.

Two-stage funnel (reuses the pipeline's pre-filter optimisation):

  Stage 1: Valuation pre-filter on ~4500 stocks (1 API call/stock).
           Keep those with valuation_score > 50 → ~500-800 stocks.
  Stage 2: Full four-factor scoring on the survivors (~600 stocks).
           Each stock gets momentum + valuation + prosperity + distress.

As-of date: the most recent trading day (defaults to yesterday's close).

Output: a Markdown research report + JSON data file.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# Bootstrap project root so imports work when run from anywhere.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from loguru import logger

# Reduce log noise — keep only warnings+ from tushare_client, but our own prints.
logger.remove()
logger.add(sys.stderr, level="WARNING")

from davis_analyzer.backtest_factors import (
    FactorConfig,
    _blend,
    _count_consecutive_positive_delta_g,
    classify_stock,
    score_universe_at,
)
from davis_analyzer.constants import (
    CYCLICAL_FACTOR_WEIGHTS,
    CYCLICAL_INDUSTRIES,
    SUPER_CYCLE_INDUSTRIES,
    SUPER_CYCLE_MIN_POSITIVE_QUARTERS,
    SUPER_CYCLE_PERSISTENCE_BONUS,
)
from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.stock_universe import build_stock_universe
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo
from davis_analyzer.valuation import (
    calculate_valuation_score,
    fetch_valuation_history,
)

# ── Config ──
AS_OF = date(2026, 7, 12)          # most recent trading day close
VALUATION_PREFILTER = 50.0          # pipeline default
TOP_N = 20
OUTPUT_DIR = PROJECT_ROOT / "studies" / "output"
DOCS_DIR = PROJECT_ROOT / "docs" / "回测记录"


def main() -> None:
    client = TushareClient()
    cfg = FactorConfig()

    # ── 1. Build full-A universe ──
    print(f"[1/4] Building full-A stock universe...")
    stock_list = build_stock_universe(client)
    print(f"      Universe: {len(stock_list)} stocks (ST excluded)")
    stock_infos: dict[str, StockInfo] = {s.ts_code: s for s in stock_list}

    # ── 2. Stage 1: Valuation pre-filter ──
    print(f"\n[2/4] Stage 1: Valuation pre-filter (score > {VALUATION_PREFILTER})...")
    t0 = time.time()
    survivors: list[str] = []
    processed = 0
    for s in stock_list:
        try:
            history = fetch_valuation_history(client, s.ts_code, as_of=AS_OF)
            if not history:
                continue
            is_cyc = s.is_cyclical
            score, _, _ = calculate_valuation_score(history, is_cyc)
            if score > VALUATION_PREFILTER:
                survivors.append(s.ts_code)
        except Exception:
            pass
        processed += 1
        if processed % 200 == 0:
            elapsed = time.time() - t0
            rate = processed / elapsed if elapsed > 0 else 0
            eta = (len(stock_list) - processed) / rate / 60 if rate > 0 else 0
            print(
                f"      {processed}/{len(stock_list)} processed, "
                f"{len(survivors)} survived, {elapsed:.0f}s elapsed, "
                f"ETA {eta:.1f}min"
            )
    elapsed = time.time() - t0
    print(
        f"      Done: {len(survivors)}/{len(stock_list)} survived pre-filter "
        f"({elapsed:.0f}s)"
    )

    # ── 3. Stage 2: Full four-factor scoring ──
    print(f"\n[3/4] Stage 2: Full four-factor scoring on {len(survivors)} stocks...")
    survivor_infos = {code: stock_infos[code] for code in survivors if code in stock_infos}
    t0 = time.time()

    # Use score_universe_at but we also need per-factor breakdown for the report.
    # So we replicate the scoring loop here to capture all four sub-scores.
    results: list[dict] = []
    processed = 0
    for code, info in survivor_infos.items():
        try:
            # ── Classify stock into domain ──
            domain = classify_stock(info.industry)
            is_cyclical = domain == "classic_cyclical"
            is_super_cycle = domain == "super_cycle"

            # Momentum
            mom = analyze_momentum(client, code, today=AS_OF)
            momentum_score = None
            if mom is not None and mom.data_sufficient:
                momentum_score = mom.momentum_score

            # ── Defect 1 fix: short-term momentum guard ──
            if mom is not None and mom.window_returns:
                from davis_analyzer.constants import SHORT_TERM_MOMENTUM_WINDOW, SHORT_TERM_MOMENTUM_FLOOR_PCT, SHORT_TERM_MOMENTUM_PENALTY
                short_ret = mom.window_returns.get(SHORT_TERM_MOMENTUM_WINDOW)
                if short_ret is not None and short_ret < SHORT_TERM_MOMENTUM_FLOOR_PCT:
                    overshoot = abs(short_ret - SHORT_TERM_MOMENTUM_FLOOR_PCT)
                    penalty = min(SHORT_TERM_MOMENTUM_PENALTY, overshoot * 0.8)
                    if momentum_score is not None:
                        momentum_score = max(0.0, momentum_score - penalty)

            # Valuation
            history = fetch_valuation_history(client, code, as_of=AS_OF)
            valuation_score = pe_pct = pb_pct = None
            if history:
                valuation_score, pe_pct, pb_pct = calculate_valuation_score(history, is_cyclical)

                # ── Defect 2 fix: absolute PE cap ──
                from davis_analyzer.constants import ABSOLUTE_PE_CAP, ABSOLUTE_PE_PENALTY
                latest_pe = history[0].pe_ttm if history else None
                if latest_pe is not None and latest_pe > ABSOLUTE_PE_CAP:
                    overshoot_ratio = min(1.0, (latest_pe - ABSOLUTE_PE_CAP) / ABSOLUTE_PE_CAP)
                    penalty = ABSOLUTE_PE_PENALTY * overshoot_ratio
                    valuation_score = max(0.0, valuation_score - penalty)

            # Prosperity + Distress
            prosperity_score = distress_score = delta_g = None
            fin = fetch_financial_data(client, code, as_of=AS_OF)
            if len(fin) >= 2:
                ps = calculate_prosperity_score(fin, is_cyclical=is_cyclical)
                prosperity_score = ps.composite_score
                delta_g = ps.delta_g

                # ── Defect 3 fix: profit-direction check ──
                from davis_analyzer.constants import PROFIT_GROWTH_PENALTY_THRESHOLD, PROSPERITY_REVENUE_PROFIT_PENALTY
                latest_fin = fin[0]
                rev_g = latest_fin.yoy_revenue_growth
                prof_g = latest_fin.yoy_profit_growth
                if (rev_g is not None and rev_g > 0.2
                        and prof_g is not None
                        and prof_g < PROFIT_GROWTH_PENALTY_THRESHOLD):
                    prosperity_score = max(0.0, prosperity_score - PROSPERITY_REVENUE_PROFIT_PENALTY)

                if valuation_score is not None:
                    eps_hist = [fd.eps for fd in fin]
                    roe_hist = [fd.roe for fd in fin]
                    rev_hist = [fd.yoy_revenue_growth for fd in fin if fd.yoy_revenue_growth is not None]
                    prof_hist = [fd.yoy_profit_growth for fd in fin if fd.yoy_profit_growth is not None]
                    latest = fin[0]
                    td = latest.total_debt
                    ta = latest.total_assets
                    dr = td / ta if ta > 0 else 0.0
                    ds = calculate_distress_score(
                        eps_hist, pe_pct, pb_pct, dr,
                        latest.operating_cf, td, ta,
                        roe_hist, rev_hist, prof_hist,
                        ps.delta_g, code,
                    )
                    distress_score = ds.total_score

            composite = _blend(
                momentum_score, valuation_score, prosperity_score, distress_score, cfg,
                is_cyclical=is_cyclical,
            )

            # ── Super-cycle persistence bonus ──
            persistence_bonus = 0.0
            if is_super_cycle and fin and len(fin) >= 2:
                consecutive_pos = _count_consecutive_positive_delta_g(fin)
                if consecutive_pos >= SUPER_CYCLE_MIN_POSITIVE_QUARTERS:
                    persistence_bonus = SUPER_CYCLE_PERSISTENCE_BONUS
                    composite += persistence_bonus

            if all(s is None for s in (momentum_score, valuation_score, prosperity_score, distress_score)):
                continue

            results.append({
                "ts_code": code,
                "name": info.name,
                "industry": info.industry,
                "domain": domain,
                "composite": round(composite, 2),
                "momentum": round(momentum_score, 2) if momentum_score else None,
                "valuation": round(valuation_score, 2) if valuation_score else None,
                "prosperity": round(prosperity_score, 2) if prosperity_score else None,
                "distress": round(distress_score, 2) if distress_score else None,
                "delta_g": round(delta_g, 2) if delta_g else None,
                "persistence_bonus": persistence_bonus,
            })
        except Exception:
            pass

        processed += 1
        if processed % 100 == 0:
            elapsed = time.time() - t0
            rate = processed / elapsed if elapsed > 0 else 0
            eta = (len(survivors) - processed) / rate / 60 if rate > 0 else 0
            print(
                f"      {processed}/{len(survivors)} scored, "
                f"{len(results)} valid, {elapsed:.0f}s elapsed, "
                f"ETA {eta:.1f}min"
            )

    elapsed = time.time() - t0
    print(f"      Done: {len(results)} stocks scored ({elapsed:.0f}s)")

    # ── 4. Rank and select top-20 ──
    results.sort(key=lambda x: x["composite"], reverse=True)
    top20 = results[:TOP_N]

    print(f"\n[4/4] Top-{TOP_N} selected:")
    print(f"{'Rank':<5}{'Code':<12}{'Name':<10}{'Comp':>6}{'Mom':>6}{'Val':>6}{'Prosp':>6}{'Dist':>6}{'ΔG':>7}{'Domain':<18}{'Industry':<10}")
    print("-" * 100)
    for i, r in enumerate(top20, 1):
        print(
            f"{i:<5}{r['ts_code']:<12}{r['name']:<10}{r['composite']:>6.1f}"
            f"{(r['momentum'] or 0):>6.0f}{(r['valuation'] or 0):>6.0f}"
            f"{(r['prosperity'] or 0):>6.0f}{(r['distress'] or 0):>6.0f}"
            f"{(r['delta_g'] or 0):>+7.1f}{r.get('domain',''):>18}{r['industry']:<10}"
        )

    # ── Save JSON ──
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "as_of": AS_OF.isoformat(),
        "universe_size": len(stock_list),
        "prefilter_survivors": len(survivors),
        "scored": len(results),
        "top20": top20,
        "config": {
            "valuation_prefilter": VALUATION_PREFILTER,
            "buy_threshold": 60.0,
            "sell_threshold": 45.0,
            "factor_weights": {
                "momentum": cfg.momentum_weight,
                "valuation": cfg.valuation_weight,
                "prosperity": cfg.prosperity_weight,
                "distress": cfg.distress_weight,
            },
        },
    }
    json_path = OUTPUT_DIR / f"top20_screen_{AS_OF.isoformat()}.json"
    with open(json_path, "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nJSON saved: {json_path}")

    # ── Generate report ──
    report = generate_report(output)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = DOCS_DIR / f"全A股四因子top20筛选_{AS_OF.isoformat()}.md"
    with open(report_path, "w") as f:
        f.write(report)
    print(f"Report saved: {report_path}")


def generate_report(data: dict) -> str:
    """Generate Markdown research report from screening results."""
    as_of = data["as_of"]
    top20 = data["top20"]
    cfg = data["config"]

    lines = [
        f"# 全A股四因子截面筛选 — 当前最适合买入的 top-20 标的",
        "",
        f"> **筛选日期**：{as_of}（最近交易日收盘数据）",
        f"> **筛选范围**：全A股 {data['universe_size']} 只（已排除 ST/*ST）",
        f"> **筛选方法**：四因子加权打分（动量+估值+景气度+困境）",
        f"> **生成日期**：{date.today().isoformat()}",
        "",
        "---",
        "",
        "## 一、筛选方法论",
        "",
        "### 两阶段漏斗",
        "",
        f"| 阶段 | 范围 | 操作 | 结果 |",
        f"|------|------|------|------|",
        f"| 第一层 | 全A股 {data['universe_size']} 只 | 估值预筛（score > {cfg['valuation_prefilter']}） | {data['prefilter_survivors']} 只通过 |",
        f"| 第二层 | 预筛通过 {data['prefilter_survivors']} 只 | 完整四因子打分 | {data['scored']} 只有有效得分 |",
        f"| 最终 | top-20 | 综合分降序排名 | {len(top20)} 只 |",
        "",
        "### 四因子配置",
        "",
        f"| 因子 | 权重 | 点时间机制 |",
        f"|------|------|-----------|",
        f"| 动量 momentum | {cfg['factor_weights']['momentum']} | 60/120/250日多窗口收益率 |",
        f"| 估值 valuation | {cfg['factor_weights']['valuation']} | 3年PE/PB分位 |",
        f"| 景气度 prosperity | {cfg['factor_weights']['prosperity']} | G+ΔG四维（ann_date过滤） |",
        f"| 困境 distress | {cfg['factor_weights']['distress']} | EPS/财务健康/拐点三层 |",
        "",
        f"**买卖阈值参考**（基于单标的回测校准）：买入 ≥ {cfg['buy_threshold']}，卖出 ≤ {cfg['sell_threshold']}。",
        "",
        "---",
        "",
        "## 二、Top-20 排名总表",
        "",
        f"| 排名 | 代码 | 名称 | 综合分 | 动量 | 估值 | 景气度 | 困境 | ΔG | 行业 |",
        f"|------|------|------|--------|------|------|--------|------|-----|------|",
    ]

    for i, r in enumerate(top20, 1):
        lines.append(
            f"| {i} | {r['ts_code']} | {r['name']} | **{r['composite']:.1f}** "
            f"| {r['momentum'] or '—'} | {r['valuation'] or '—'} "
            f"| {r['prosperity'] or '—'} | {r['distress'] or '—'} "
            f"| {r['delta_g'] or '—'} | {r['industry']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 三、逐标的买入理由",
        "",
    ]

    for i, r in enumerate(top20, 1):
        lines.append(f"### {i}. {r['name']}（{r['ts_code']}）— 综合分 {r['composite']:.1f}")
        lines.append("")
        lines.append(f"| 因子 | 得分 | 评价 |")
        lines.append(f"|------|------|------|")

        # Factor commentary
        mom = r["momentum"]
        if mom is not None:
            mom_comment = "强势上涨趋势" if mom >= 80 else ("中等动量" if mom >= 60 else "动量偏弱")
            lines.append(f"| 动量 | {mom:.1f} | {mom_comment} |")
        else:
            lines.append(f"| 动量 | — | 数据不足 |")

        val = r["valuation"]
        if val is not None:
            val_comment = "低估值（安全边际高）" if val >= 70 else ("中等估值" if val >= 40 else "高估值（需警惕）")
            lines.append(f"| 估值 | {val:.1f} | {val_comment} |")
        else:
            lines.append(f"| 估值 | — | 数据不足 |")

        prosp = r["prosperity"]
        if prosp is not None:
            prosp_comment = "高景气度" if prosp >= 60 else ("中等景气" if prosp >= 40 else "景气度偏低")
            lines.append(f"| 景气度 | {prosp:.1f} | {prosp_comment} |")
        else:
            lines.append(f"| 景气度 | — | 数据不足 |")

        dist = r["distress"]
        if dist is not None:
            dist_comment = "财务健康" if dist >= 35 else ("轻度困境信号" if dist >= 25 else "困境风险较高")
            lines.append(f"| 困境 | {dist:.1f} | {dist_comment} |")
        else:
            lines.append(f"| 困境 | — | 数据不足 |")

        dg = r["delta_g"]
        if dg is not None:
            if dg > 10:
                dg_comment = "⚠️ **增速爆发加速**（ΔG=+{:.1f}，二次点火）"
            elif dg > 0:
                dg_comment = "增速加速（ΔG=+{:.1f}）"
            elif dg > -10:
                dg_comment = "增速微缓（ΔG={:.1f}）"
            else:
                dg_comment = "⚠️ 增速明显放缓（ΔG={:.1f}）"
            lines.append(f"| ΔG | {dg:+.1f} | {dg_comment.format(dg)} |")

        lines.append("")

        # Summary buy reason
        reasons = []
        if mom and mom >= 80:
            reasons.append("动量强劲")
        if val and val >= 70:
            reasons.append("估值低位")
        if prosp and prosp >= 60:
            reasons.append("景气度高")
        if dg and dg > 0:
            reasons.append("增速加速")
        if dist and dist >= 35:
            reasons.append("财务健康")

        buy_reason = "、".join(reasons) if reasons else "综合因子均衡"
        above_buy = r["composite"] >= cfg["buy_threshold"]
        signal = f"**✅ 超过买入阈值（≥{cfg['buy_threshold']}）**" if above_buy else f"接近买入阈值（{cfg['buy_threshold']}）"

        lines.append(f"**买入理由**：{buy_reason}。{signal}。")
        lines.append(f"**行业**：{r['industry']}")
        lines.append("")

    lines += [
        "---",
        "",
        "## 四、行业分布",
        "",
    ]
    industry_count = {}
    for r in top20:
        ind = r["industry"] or "未知"
        industry_count[ind] = industry_count.get(ind, 0) + 1
    lines.append("| 行业 | 数量 |")
    lines.append("|------|------|")
    for ind, cnt in sorted(industry_count.items(), key=lambda x: -x[1]):
        lines.append(f"| {ind} | {cnt} |")

    lines += [
        "",
        "---",
        "",
        "## 五、风险提示",
        "",
        "- 本筛选基于历史因子打分，不构成投资建议",
        "- 综合分高不等于必然上涨，需结合行业景气度和个股催化",
        "- ΔG 为正值（增速加速）是积极信号，但需确认其持续性",
        "- 估值分反映了PE/PB的历史分位，高成长股可能长期处于高估值区间",
        "- 建议对 top-20 中的标的做进一步基本面研究后再决策",
        "",
        f"---",
        "",
        f"*原始数据：`studies/output/top20_screen_{as_of}.json`*",
        "",
    ]

    return "\n".join(lines)


if __name__ == "__main__":
    main()
