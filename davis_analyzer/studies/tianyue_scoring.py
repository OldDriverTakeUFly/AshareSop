#!/usr/bin/env python3
"""天岳先进 (688234.SH) 单股困境反转评分脚本.

调用 davis_analyzer 核心函数（不改任何源码），对天岳先进执行完整四维评分：
  1. 估值层 — PE/PB 3年历史百分位 (valuation.py)
  2. 趋势层 — PE/PB 月度趋势斜率/加速度 (trend.py)
  3. 景气层 — 营收/利润/斜率/持续时间四维复合 (prosperity.py)
  4. 困境层 — 三层信号：困境确认 + 反转可能 + 反转激活 (distress.py)

最终通过 scoring.py 的 calculate_davis_double_score() 合成戴维斯双击综合评分。

用法:
    .venv/bin/python davis_analyzer/studies/tianyue_scoring.py

输出:
    .sisyphus/evidence/tianyue/t2-davis-score.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from loguru import logger

# ── davis_analyzer 核心模块（只读调用，不修改源码）──
from davis_analyzer.distress import (
    check_balance_sheet,
    check_delta_g_positive,
    check_eps_decline,
    check_financial_health,
    check_operating_cf,
    check_pe_pb_percentile,
    check_profit_inflection,
    check_revenue_inflection,
    check_roe_trend,
    calculate_distress_score,
)
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import (
    batch_trend,
    calculate_monthly_trend,
    calculate_trend_acceleration,
    calculate_trend_score,
    calculate_trend_slope,
)
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo
from davis_analyzer.valuation import (
    calculate_percentile,
    calculate_valuation_score,
    detect_cyclical,
    fetch_valuation_history,
)

# ── 常量 ──
TS_CODE = "688234.SH"
STOCK_NAME = "天岳先进"
PERIODS = 12  # 3年季度数据（12个季度）
OUTPUT_PATH = Path(".sisyphus/evidence/tianyue/t2-davis-score.json")


# ════════════════════════════════════════════════════════════════════════════
# 信号得分依据生成器 — 解释每个信号为什么得这个分
# ════════════════════════════════════════════════════════════════════════════


def _explain_eps_decline(eps_history: list[float]) -> str:
    """解释 eps_decline 信号得分依据."""
    score = check_eps_decline(eps_history)
    if len(eps_history) < 2:
        return f"得分={score:.2f}，数据不足（EPS历史少于2期），信号=N/A"
    latest = eps_history[0]
    if len(eps_history) >= 5:
        previous = eps_history[4]
        cmp_desc = f"最新EPS={latest:.4f}，同比( index[0] vs index[4] )={previous:.4f}"
    else:
        previous = eps_history[1]
        cmp_desc = f"最新EPS={latest:.4f}，上期EPS={previous:.4f}"
    if abs(previous) < 1e-9:
        return f"得分={score:.2f}，{cmp_desc}，基准EPS≈0，无法计算降幅"
    decline = (previous - latest) / abs(previous)
    pct = decline * 100
    if decline >= 0.30:
        return (
            f"得分={score:.2f}，{cmp_desc}，降幅{pct:.1f}%≥30%阈值，"
            f"困境确认信号满档（min(1.0, {pct:.1f}%/30%)={score:.2f}）"
        )
    elif decline > 0:
        return (
            f"得分={score:.2f}，{cmp_desc}，降幅{pct:.1f}%<30%阈值，"
            f"部分确认（{pct:.1f}%/30%={score:.2f}）"
        )
    else:
        return (
            f"得分={score:.2f}，{cmp_desc}，EPS增长{-pct:.1f}%，无困境信号"
        )


def _explain_pe_pb_percentile(pe_pct: float, pb_pct: float) -> str:
    """解释 pe_pb_percentile 信号得分依据."""
    score = check_pe_pb_percentile(pe_pct, pb_pct)
    avg_pct = (pe_pct + pb_pct) / 2.0 * 100
    return (
        f"得分={score:.2f}，PE百分位={pe_pct*100:.1f}%，PB百分位={pb_pct*100:.1f}%，"
        f"均值={avg_pct:.1f}%。"
        f"{'深度低估' if avg_pct < 10 else '偏低估' if avg_pct < 30 else '合理' if avg_pct < 70 else '偏高'}"
        f"（公式: 1.0 - 均值/100 = {score:.2f}）"
    )


def _explain_financial_health(debt_ratio: float, operating_cf: float) -> str:
    """解释 financial_health 信号得分依据."""
    score = check_financial_health(debt_ratio, operating_cf)
    parts = []
    if debt_ratio < 0.5:
        parts.append(f"资产负债率={debt_ratio*100:.1f}%<50% ✓(+0.5)")
    else:
        parts.append(f"资产负债率={debt_ratio*100:.1f}%≥50% ✗(+0)")
    if operating_cf > 0:
        parts.append(f"经营现金流={operating_cf/1e8:.2f}亿>0 ✓(+0.5)")
    else:
        parts.append(f"经营现金流={operating_cf/1e8:.2f}亿≤0 ✗(+0)")
    return f"得分={score:.2f}，" + "，".join(parts)


def _explain_balance_sheet(total_debt: float, total_assets: float) -> str:
    """解释 balance_sheet 信号得分依据."""
    score = check_balance_sheet(total_debt, total_assets)
    if total_assets <= 0:
        return f"得分={score:.2f}，总资产≤0，无法计算"
    debt_ratio = total_debt / total_assets
    return (
        f"得分={score:.2f}，总负债={total_debt/1e8:.2f}亿，总资产={total_assets/1e8:.2f}亿，"
        f"负债率={debt_ratio*100:.1f}%（公式: max(0, 1-{debt_ratio:.3f}×2)={score:.2f}）"
    )


def _explain_operating_cf(operating_cf: float, total_assets: float) -> str:
    """解释 operating_cf 信号得分依据."""
    score = check_operating_cf(operating_cf, total_assets)
    if total_assets > 0:
        ratio = operating_cf / total_assets
        return (
            f"得分={score:.2f}，经营现金流={operating_cf/1e8:.2f}亿，"
            f"总资产={total_assets/1e8:.2f}亿，"
            f"CF/资产={ratio*100:.2f}%（公式: clamp(CF/资产, 0, 1)={score:.2f}）"
        )
    return f"得分={score:.2f}，经营现金流={operating_cf/1e8:.2f}亿，总资产未知，" \
           f"{'>0计1.0' if operating_cf > 0 else '≤0计0.0'}"


def _explain_roe_trend(roe_history: list[float]) -> str:
    """解释 roe_trend 信号得分依据."""
    score = check_roe_trend(roe_history)
    if len(roe_history) < 2:
        return f"得分={score:.2f}，数据不足（ROE历史少于2期），信号=N/A"
    current = roe_history[0]
    if len(roe_history) >= 5:
        prior = roe_history[4]
        cmp = f"最新ROE={current:.2f}%，同比( index[0] vs index[4] )={prior:.2f}%"
    else:
        prior = roe_history[1]
        cmp = f"最新ROE={current:.2f}%，上期ROE={prior:.2f}%"
    delta = current - prior
    if delta >= 5:
        return f"得分={score:.2f}，{cmp}，改善{delta:.2f}个百分点≥5阈值，满档"
    elif delta > 0:
        return f"得分={score:.2f}，{cmp}，改善{delta:.2f}个百分点（{delta:.2f}/5={score:.2f}）"
    else:
        return f"得分={score:.2f}，{cmp}，ROE下降{-delta:.2f}个百分点，无改善信号"


def _explain_revenue_inflection(revenue_history: list[float]) -> str:
    """解释 revenue_inflection 信号得分依据."""
    score = check_revenue_inflection(revenue_history)
    if len(revenue_history) < 2:
        return f"得分={score:.2f}，数据不足（营收增速历史少于2期），信号=N/A"
    latest = revenue_history[0]
    previous = revenue_history[1]
    swing = latest - previous
    return (
        f"得分={score:.4f}，最新营收增速={latest*100:.1f}%，上期={previous*100:.1f}%，"
        f"动量变化={swing*100:.1f}个百分点"
        f"（公式: clamp({swing:.4f}/20.0, 0, 1)={score:.4f}）"
    )


def _explain_profit_inflection(profit_history: list[float]) -> str:
    """解释 profit_inflection 信号得分依据."""
    score = check_profit_inflection(profit_history)
    if len(profit_history) < 2:
        return f"得分={score:.2f}，数据不足（利润增速历史少于2期），信号=N/A"
    latest = profit_history[0]
    previous = profit_history[1]
    swing = latest - previous
    return (
        f"得分={score:.4f}，最新利润增速={latest*100:.1f}%，上期={previous*100:.1f}%，"
        f"动量变化={swing*100:.1f}个百分点"
        f"（公式: clamp({swing:.4f}/20.0, 0, 1)={score:.4f}）"
    )


def _explain_delta_g_positive(delta_g: float) -> str:
    """解释 delta_g_positive 信号得分依据."""
    score = check_delta_g_positive(delta_g)
    return (
        f"得分={score:.4f}，delta_G={delta_g:.2f}"
        f"（3季度均值增速变化，百分点），"
        f"{'增速加速' if delta_g > 0 else '增速减速'}"
        f"（公式: clamp({delta_g:.2f}/0.10, 0, 1)={score:.4f}）"
    )


# ════════════════════════════════════════════════════════════════════════════
# 主评分流程
# ════════════════════════════════════════════════════════════════════════════


def _build_stock_info(client: TushareClient, ts_code: str, name: str) -> StockInfo:
    """从 Tushare 获取股票行业信息，构造 StockInfo."""
    try:
        stock_df = client.get_stock_list()
        row = stock_df[stock_df["ts_code"] == ts_code]
        if not row.empty:
            industry = str(row.iloc[0].get("industry", "") or "")
            real_name = str(row.iloc[0].get("name", name) or name)
        else:
            industry = ""
            real_name = name
    except Exception:
        logger.warning("无法从 stock_list 获取 {} 的行业信息，使用默认值", ts_code)
        industry = ""
        real_name = name

    return StockInfo(
        ts_code=ts_code,
        name=real_name,
        industry=industry,
        list_status="L",
        is_cyclical=detect_cyclical(industry),
    )


def score_tianyue() -> dict:
    """执行天岳先进完整四维评分，返回 JSON 可序列化的 dict."""

    logger.info("=" * 70)
    logger.info("天岳先进 (688234.SH) 戴维斯双击困境反转评分")
    logger.info("=" * 70)

    # ── Step 1: 创建 TushareClient ──
    logger.info("Step 1: 初始化 TushareClient...")
    client = TushareClient()

    # ── Step 2: 获取财务数据（3年=12季度）──
    logger.info("Step 2: 获取 {} 财务数据 (periods={})...", TS_CODE, PERIODS)
    fin_data = fetch_financial_data(client, TS_CODE, periods=PERIODS)
    if not fin_data:
        logger.error("财务数据为空，无法继续评分")
        sys.exit(1)
    logger.info("获取到 {} 期财务数据", len(fin_data))
    for fd in fin_data:
        logger.debug(
            "  {} rev={:.0f} np={:.0f} eps={:.4f} roe={:.2f} "
            "yoy_rev={} yoy_prof={}",
            fd.report_period,
            fd.revenue or 0,
            fd.net_profit or 0,
            fd.eps or 0,
            fd.roe or 0,
            f"{fd.yoy_revenue_growth*100:.1f}%" if fd.yoy_revenue_growth else "N/A",
            f"{fd.yoy_profit_growth*100:.1f}%" if fd.yoy_profit_growth else "N/A",
        )

    # ── Step 3: 提取历史序列（照抄 pipeline.py:171-174 模式）──
    eps_history = [fd.eps for fd in fin_data]
    roe_history = [fd.roe for fd in fin_data]
    revenue_growth = [fd.yoy_revenue_growth or 0.0 for fd in fin_data]
    profit_growth = [fd.yoy_profit_growth or 0.0 for fd in fin_data]

    latest = fin_data[0]  # 最新报告期（列表已降序排列）
    total_debt = latest.total_debt or 0.0
    total_assets = latest.total_assets or 0.0
    operating_cf = latest.operating_cf or 0.0
    debt_ratio = total_debt / total_assets if total_assets > 0 else 0.0

    logger.info(
        "最新报告期 {}: 负债率={:.1f}% 经营CF={:.2f}亿 总资产={:.2f}亿",
        latest.report_period,
        debt_ratio * 100,
        operating_cf / 1e8,
        total_assets / 1e8,
    )

    # ── Step 4: 获取估值历史 + 计算 PE/PB 百分位 ──
    logger.info("Step 4: 获取 {} 估值历史 (3年 daily_basic)...", TS_CODE)
    val_history = fetch_valuation_history(client, TS_CODE)
    if not val_history:
        logger.error("估值历史为空，使用中性百分位 0.5")
        pe_pct, pb_pct = 0.5, 0.5
        val_score = 50.0
    else:
        logger.info("获取到 {} 天估值数据", len(val_history))
        latest_val = val_history[0]
        pe_series = [v.pe_ttm for v in val_history]
        pb_series = [v.pb for v in val_history]
        pe_pct = calculate_percentile(latest_val.pe_ttm, pe_series)
        pb_pct = calculate_percentile(latest_val.pb, pb_series)
        stock_info = _build_stock_info(client, TS_CODE, STOCK_NAME)
        val_score, pe_pct, pb_pct = calculate_valuation_score(
            val_history, stock_info.is_cyclical
        )
        logger.info(
            "估值评分={:.2f} PE百分位={:.1f}% PB百分位={:.1f}% (行业={} 周期={})",
            val_score,
            pe_pct * 100,
            pb_pct * 100,
            stock_info.industry,
            stock_info.is_cyclical,
        )

    # ── Step 5: 计算景气度评分 ──
    logger.info("Step 5: 计算景气度评分...")
    prosp_score = calculate_prosperity_score(fin_data)
    logger.info(
        "景气度: composite={:.2f} revenue={:.2f} profit={:.2f} "
        "slope={:.2f} duration={:.2f} delta_g={:.2f}",
        prosp_score.composite_score,
        prosp_score.revenue_score,
        prosp_score.profit_score,
        prosp_score.slope_score,
        prosp_score.duration_score,
        prosp_score.delta_g,
    )

    # ── Step 6: 计算趋势评分 ──
    logger.info("Step 6: 计算PE/PB月度趋势评分...")
    trend_score = 50.0  # 默认中性
    trend_detail: dict = {"score": 50.0, "reason": "数据不足，趋势评分=N/A(50.0)"}
    if val_history and len(val_history) >= 3:
        try:
            dates = pd.to_datetime(
                [v.trade_date for v in val_history], format="%Y%m%d"
            )
            daily_pe = pd.Series([v.pe_ttm for v in val_history], index=dates)
            daily_pb = pd.Series([v.pb for v in val_history], index=dates)

            stock_info = _build_stock_info(client, TS_CODE, STOCK_NAME)

            # 使用 batch_trend（与 pipeline 一致的方式）
            trend_map = batch_trend(
                {TS_CODE: (daily_pe, daily_pb)},
                {TS_CODE: stock_info},
            )
            trend_score = trend_map.get(TS_CODE, 50.0)

            # 额外提取趋势细节用于解释
            monthly_pe, monthly_pb = calculate_monthly_trend(daily_pe, daily_pb)
            pe_slope = calculate_trend_slope(monthly_pe)
            pb_slope = calculate_trend_slope(monthly_pb)
            pe_accel = calculate_trend_acceleration(monthly_pe)
            pb_accel = calculate_trend_acceleration(monthly_pb)

            trend_detail = {
                "score": round(trend_score, 2),
                "pe_slope": round(pe_slope, 4),
                "pb_slope": round(pb_slope, 4),
                "pe_acceleration": round(pe_accel, 4),
                "pb_acceleration": round(pb_accel, 4),
                "monthly_pe_points": len(monthly_pe),
                "monthly_pb_points": len(monthly_pb),
                "is_cyclical": stock_info.is_cyclical,
                "reason": (
                    f"得分={trend_score:.2f}，"
                    f"PE斜率={pe_slope:.4f} PB斜率={pb_slope:.4f}，"
                    f"PE加速度={pe_accel:.4f} PB加速度={pb_accel:.4f}，"
                    f"月度数据点: PE={len(monthly_pe)} PB={len(monthly_pb)}，"
                    f"{'周期股(PB-only)' if stock_info.is_cyclical else '非周期(PE70%+PB30%)'}"
                ),
            }
            logger.info("趋势评分={:.2f}", trend_score)
        except Exception:
            logger.exception("趋势评分计算失败，使用中性50.0")
    else:
        logger.warning("估值历史不足3个数据点，趋势评分使用中性50.0")

    # ── Step 7: 计算困境反转三层评分 ──
    logger.info("Step 7: 计算困境反转三层评分...")
    distress = calculate_distress_score(
        eps_history=eps_history,
        pe_pct=pe_pct,
        pb_pct=pb_pct,
        debt_ratio=debt_ratio,
        operating_cf=operating_cf,
        total_debt=total_debt,
        total_assets=total_assets,
        roe_history=roe_history,
        revenue_history=revenue_growth,
        profit_history=profit_growth,
        delta_g=prosp_score.delta_g,
        ts_code=TS_CODE,
    )
    logger.info(
        "困境评分: total={:.2f} L1={:.2f} L2={:.2f} L3={:.2f}",
        distress.total_score,
        distress.layer1_score,
        distress.layer2_score,
        distress.layer3_score,
    )

    # ── Step 8: 计算戴维斯双击综合评分 ──
    logger.info("Step 8: 计算戴维斯双击综合评分...")
    davis = calculate_davis_double_score(
        valuation_score=val_score,
        prosperity_score=prosp_score.composite_score,
        distress_score=distress.total_score,
        trend_score=trend_score,
        ts_code=TS_CODE,
        name=STOCK_NAME,
    )
    logger.info(
        "戴维斯双击: final={:.2f} (估值={:.2f}×0.30 + 趋势={:.2f}×0.15 "
        "+ 景气={:.2f}×0.30 + 困境={:.2f}×0.25)",
        davis.final_score,
        val_score,
        trend_score,
        prosp_score.composite_score,
        distress.total_score,
    )

    # ── Step 9: 构建带详细依据的 signals_detail ──
    signals_with_reasons = {
        "layer1": {
            "eps_decline": {
                "score": round(distress.signals_detail["layer1"]["eps_decline"], 4),
                "reason": _explain_eps_decline(eps_history),
            },
            "pe_pb_percentile": {
                "score": round(
                    distress.signals_detail["layer1"]["pe_pb_percentile"], 4
                ),
                "reason": _explain_pe_pb_percentile(pe_pct, pb_pct),
            },
            "financial_health": {
                "score": round(
                    distress.signals_detail["layer1"]["financial_health"], 4
                ),
                "reason": _explain_financial_health(debt_ratio, operating_cf),
            },
        },
        "layer2": {
            "balance_sheet": {
                "score": round(
                    distress.signals_detail["layer2"]["balance_sheet"], 4
                ),
                "reason": _explain_balance_sheet(total_debt, total_assets),
            },
            "operating_cf": {
                "score": round(distress.signals_detail["layer2"]["operating_cf"], 4),
                "reason": _explain_operating_cf(operating_cf, total_assets),
            },
            "roe_trend": {
                "score": round(distress.signals_detail["layer2"]["roe_trend"], 4),
                "reason": _explain_roe_trend(roe_history),
            },
        },
        "layer3": {
            "revenue_inflection": {
                "score": round(
                    distress.signals_detail["layer3"]["revenue_inflection"], 4
                ),
                "reason": _explain_revenue_inflection(revenue_growth),
            },
            "profit_inflection": {
                "score": round(
                    distress.signals_detail["layer3"]["profit_inflection"], 4
                ),
                "reason": _explain_profit_inflection(profit_growth),
            },
            "delta_g_positive": {
                "score": round(
                    distress.signals_detail["layer3"]["delta_g_positive"], 4
                ),
                "reason": _explain_delta_g_positive(prosp_score.delta_g),
            },
        },
    }

    # ── Step 10: 组装最终 JSON 输出 ──
    result = {
        "ts_code": TS_CODE,
        "name": STOCK_NAME,
        "scored_at": datetime.now().isoformat(),
        "latest_report_period": latest.report_period,
        "financial_data_periods": len(fin_data),
        "valuation_data_points": len(val_history) if val_history else 0,
        "distress": {
            "total_score": distress.total_score,
            "layer1_score": distress.layer1_score,
            "layer2_score": distress.layer2_score,
            "layer3_score": distress.layer3_score,
            "layer_weights": {"layer1": 0.3, "layer2": 0.3, "layer3": 0.4},
            "signals_detail": signals_with_reasons,
        },
        "prosperity": {
            "composite_score": prosp_score.composite_score,
            "revenue_score": prosp_score.revenue_score,
            "profit_score": prosp_score.profit_score,
            "slope_score": prosp_score.slope_score,
            "duration_score": prosp_score.duration_score,
            "delta_g": prosp_score.delta_g,
            "weights": {
                "revenue": 0.30,
                "profit": 0.30,
                "slope": 0.25,
                "duration": 0.15,
            },
        },
        "valuation": {
            "score": round(val_score, 2),
            "pe_percentile": round(pe_pct, 4),
            "pb_percentile": round(pb_pct, 4),
            "latest_pe_ttm": val_history[0].pe_ttm if val_history else None,
            "latest_pb": val_history[0].pb if val_history else None,
        },
        "trend": trend_detail,
        "davis_double": {
            "final_score": davis.final_score,
            "valuation_score": davis.valuation_score,
            "trend_score": davis.trend_score,
            "prosperity_score": davis.prosperity_score,
            "distress_score": davis.distress_score,
            "weights": {
                "valuation": 0.30,
                "trend": 0.15,
                "prosperity": 0.30,
                "distress": 0.25,
            },
        },
    }

    return result


def main() -> None:
    """主入口：执行评分并输出 JSON."""
    result = score_tianyue()

    # 确保输出目录存在
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 写入 JSON
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    logger.info("✅ 评分完成，输出写入 {}", OUTPUT_PATH)
    logger.info(
        "最终评分: distress={:.2f} prosperity={:.2f} valuation={:.2f} "
        "trend={:.2f} → davis_final={:.2f}",
        result["distress"]["total_score"],
        result["prosperity"]["composite_score"],
        result["valuation"]["score"],
        result["trend"]["score"],
        result["davis_double"]["final_score"],
    )

    # 同时打印 JSON 到 stdout
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
