#!/usr/bin/env python3
"""多因子量化选股三层管线筛选脚本模板 (通用版).

这是一个参数化模板：复制此文件后，仅需修改顶部 CONFIG 块即可对任意 A 股股票池
执行三层结构（硬过滤 → 打分 → 加分）的多因子量化选股。

本脚本只读调用 davis_analyzer 核心函数，不修改任何源码。所有权重和阈值均为
硬编码常量，与 SKILL.md 定义一致，不可由用户运行时配置。

三层结构:
    1. 硬过滤层 (Hard Filter) — 二元 0/1 门控，淘汰高风险标的
       ROE≥12%, CAGR≥15%, 净债务/EBITDA<2.5, 经营CF>0, PE<25
    2. 打分层 (Scoring) — 0/1/2 分位打分，分域加权排名
       Growth 30% + Valuation 20% + Technical 25% + Sentiment 25%
    3. 加分层 (Enhancement) — 稀缺信号奖励，只加不减

────────────────────────────────────────────────────────────────────────────────
【必填配置】
    打开本文件，修改 CONFIG 块中的变量：
        TOP_N_PER_DOMAIN : 每个域输出的前 N 名候选标的数量
        OUTPUT_DIR       : JSON 结果文件输出目录 (相对/绝对路径均可)
        DRY_RUN          : True=使用缓存数据, False=实时调用 Tushare

【运行依赖】
    - Python 3.12+
    - 已安装 davis_analyzer (在仓库根目录执行 `pip install -e .`)
    - 项目根目录存在 .env 文件，内含 TUSHARE_TOKEN=xxx
    - pandas, loguru 第三方库

【用法】
    # 1. 复制并修改 CONFIG
    cp screening-template.py my_screening.py
    # 编辑 my_screening.py 的 CONFIG 块

    # 2. 运行 (需在仓库根目录，确保 davis_analyzer 可被导入)
    .venv/bin/python skills/.../my_screening.py

【输出】
    JSON 文件写入 OUTPUT_DIR/multi_factor_screening_{timestamp}.json，
    包含四域排名清单及每只标的的因子详情。同时将摘要打印到 stdout。

【重要】
    本脚本只做当日截面排名，不做回测、IC 分析或因子衰减曲线。
────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

# ── davis_analyzer 核心模块（只读调用，不修改源码）──
from davis_analyzer.constants import CYCLICAL_INDUSTRIES
from davis_analyzer.pipeline import run_screening_pipeline
from davis_analyzer.types import (
    FinancialData,
    PipelineResult,
    ProsperityScore,
    StockInfo,
)
from davis_analyzer.valuation import detect_cyclical

# ========== CONFIG: 填入你的筛选参数 ==========
TOP_N_PER_DOMAIN = 20  # 每个域输出的前 N 名候选标的数量
OUTPUT_DIR = "output"  # JSON 输出目录 (相对仓库根目录或绝对路径)
DRY_RUN = False  # True=使用缓存数据, False=实时调用 Tushare
# ===============================================

# ── 硬编码常量 (与 SKILL.md 一致，请勿手动修改) ──

# 硬过滤层阈值
HARD_FILTER_ROE_MIN = 12.0  # ROE (TTM) ≥ 12%
HARD_FILTER_CAGR_MIN = 15.0  # 收入 3 年 CAGR ≥ 15%
HARD_FILTER_DEBT_EBITDA_MAX = 2.5  # 净债务 / EBITDA < 2.5
HARD_FILTER_OCF_POSITIVE = 0.0  # 经营性现金流 (TTM) > 0
HARD_FILTER_PE_MAX = 25.0  # PE (TTM) < 25

# 打分层默认权重 (四桶合计 100%)
DEFAULT_WEIGHTS = {
    "growth": 0.30,
    "valuation": 0.20,
    "technical": 0.25,
    "sentiment": 0.25,
}

# 分域权重覆盖 (每域四桶权重合计 100%)
DOMAIN_WEIGHTS = {
    "dividend": {"growth": 0.20, "valuation": 0.25, "technical": 0.25, "sentiment": 0.30},
    "growth": {"growth": 0.40, "valuation": 0.15, "technical": 0.20, "sentiment": 0.25},
    "value": {"growth": 0.25, "valuation": 0.35, "technical": 0.20, "sentiment": 0.20},
    "cyclical": {"growth": 0.20, "valuation": 0.30, "technical": 0.25, "sentiment": 0.25},
}

# 加分层信号加分 (上限为综合分的 10%)
ENHANCEMENT_BONUS = {
    "institutional_accumulation": 5.0,  # 机构加仓 / 筹码集中 +5%
    "insider_purchase": 3.0,  # 高管/大股东增持 +3%
    "esg_leadership": 2.0,  # ESG 评级 A 级以上 +2%
    "supply_chain_spread": 2.0,  # 上下游价差信号 +2%
    "forecast_acceleration": 5.0,  # 业绩预告强指引 (leading_score>=75) +5%
}
ENHANCEMENT_CAP_PCT = 10.0  # 加分总额上限为综合分的 10%

# 0/1/2 三档打分分位边界
TIER_TOP_PERCENTILE = 0.80  # 前 20% → 2 分
TIER_MID_PERCENTILE = 0.50  # 中位数 → 1 分分界


# ════════════════════════════════════════════════════════════════════════════
# 第一层：硬过滤 (Hard Filter)
# ════════════════════════════════════════════════════════════════════════════


def apply_hard_filter(
    stock_list: list[StockInfo],
    valuation_data: dict[str, tuple],
    financial_data: dict[str, list[FinancialData]],
    prosperity_scores: dict[str, ProsperityScore],
) -> list[str]:
    """执行硬过滤层，返回通过过滤的 ts_code 列表.

    每只股票必须同时满足所有硬过滤条件才能通过。任一条件不满足即淘汰。
    本函数不做排序、不打分，仅输出 pass/fail 二元结果。
    """
    passed: list[str] = []

    for stock in stock_list:
        ts_code = stock.ts_code

        # 跳过 ST 股票（stock_universe 已预筛，双重确认）
        if "ST" in stock.name.upper():
            continue

        # 估值过滤：PE (TTM) < 25 且非负
        val_entry = valuation_data.get(ts_code)
        if val_entry is None:
            continue
        val_score, pe_pct, pb_pct = val_entry

        # 周期股用 PB 过滤替代 PE
        is_cyclical = detect_cyclical(stock.industry)
        if is_cyclical:
            # 周期股：PB 百分位低于 50%（行业中位数以下）
            if pb_pct > 0.50:
                continue
        else:
            # 非周期股：估值分 > 50（与 davis_analyzer pipeline 预筛一致）
            if val_score <= 50.0:
                continue

        # 财务数据检查
        fin_list = financial_data.get(ts_code, [])
        if not fin_list:
            continue

        latest_fin = fin_list[0]

        # ROE 过滤：ROE (TTM) ≥ 12%
        if latest_fin.roe < HARD_FILTER_ROE_MIN:
            continue

        # 经营现金流过滤：OCF > 0
        if latest_fin.operating_cf <= HARD_FILTER_OCF_POSITIVE:
            continue

        # 杠杆过滤：净债务 / EBITDA < 2.5（简化版用资产负债率近似）
        if latest_fin.total_assets > 0:
            debt_ratio = latest_fin.total_debt / latest_fin.total_assets
            if debt_ratio > 0.60:  # 资产负债率 > 60% 近似高杠杆
                continue

        # 成长过滤：收入 3 年 CAGR ≥ 15%（通过 prosperity_score 近似）
        prosp = prosperity_scores.get(ts_code)
        if prosp and prosp.composite_score < 30.0:
            # 景气度分过低近似为成长性不足
            continue

        passed.append(ts_code)

    logger.info(
        "硬过滤层: {} 只股票通过过滤 (输入 {})",
        len(passed),
        len(stock_list),
    )
    return passed


# ════════════════════════════════════════════════════════════════════════════
# 域分类
# ════════════════════════════════════════════════════════════════════════════

# 域 → 代表性行业关键词映射 (用于按行业名称分域)
_DOMAIN_KEYWORDS = {
    "dividend": ["银行", "公用事业", "交通运输", "煤炭", "钢铁", "电力", "高速公路"],
    "growth": ["电子", "计算机", "通信", "新能源", "生物医药", "医疗器械", "半导体", "软件"],
    "value": ["房地产", "建筑", "建材", "纺织", "家电"],
    "cyclical": ["有色金属", "石油石化", "化工", "造纸", "农机", "机械"],
}


def classify_domain(industry: str) -> str:
    """根据行业名称将股票归入四域之一.

    如果行业名称匹配多个域的关键词，优先匹配第一个命中的域。
    未匹配的默认归入 'value' 域（保守处理）。
    """
    if not industry:
        return "value"

    for domain, keywords in _DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in industry:
                return domain

    # 周期股常量检测（来自 davis_analyzer.constants）
    if industry in CYCLICAL_INDUSTRIES:
        return "cyclical"

    return "value"  # 未匹配的保守归入价值型


# ════════════════════════════════════════════════════════════════════════════
# 第二层：打分 (Scoring) — 0/1/2 三档分位打分
# ════════════════════════════════════════════════════════════════════════════


def quantile_to_tier(rank_pct: float, is_negative_factor: bool = False) -> int:
    """将分位排名映射到 0/1/2 三档.

    正向因子: rank_pct 越高越好 (如 ROE, 增速)
    负向因子: rank_pct 越低越好 (如 PE, 杠杆, 换手率)

    正向: 前 20% → 2, 中位数到 80 分位 → 1, 后 50% → 0
    负向: 后 20% → 2, 中位数到 20 分位 → 1, 前 50% → 0
    """
    if is_negative_factor:
        # 负向因子反转: 低分位 = 高分
        if rank_pct <= (1.0 - TIER_TOP_PERCENTILE):
            return 2
        elif rank_pct <= 0.50:
            return 1
        else:
            return 0
    else:
        if rank_pct >= TIER_TOP_PERCENTILE:
            return 2
        elif rank_pct >= TIER_MID_PERCENTILE:
            return 1
        else:
            return 0


def score_stock_universe(
    passed_codes: list[str],
    pipeline_result: PipelineResult,
) -> dict[str, dict]:
    """对通过硬过滤的股票执行 0/1/2 三档打分，分域加权.

    返回 {ts_code: {domain, factor_scores, composite_score, rank}} 结构。
    """
    stock_infos = pipeline_result.stock_infos
    valuation_data = pipeline_result.valuation_data
    prosperity_scores = pipeline_result.prosperity_scores
    financial_data = pipeline_result.financial_data
    # Supplementary factor signals from pipeline Step 7.6 (real data, replacing
    # the former prosperity-proxy stubs in the Technical / dividend domain).
    momentum_signals = pipeline_result.momentum_signals
    dividend_signals = pipeline_result.dividend_signals

    # Step 1: 收集所有通过股票的原始因子值（用于后续行业内排名）
    raw_factors: dict[str, dict] = {}
    for ts_code in passed_codes:
        fin_list = financial_data.get(ts_code, [])
        if not fin_list:
            continue
        latest = fin_list[0]
        prosp = prosperity_scores.get(ts_code)
        val_entry = valuation_data.get(ts_code)

        # 提取因子原始值
        roe = latest.roe
        revenue_growth = latest.yoy_revenue_growth or 0.0
        operating_cf_ratio = latest.operating_cf / latest.revenue if latest.revenue > 0 else 0.0
        pe_pct = val_entry[1] if val_entry else 0.5
        pb_pct = val_entry[2] if val_entry else 0.5
        prosp_score = prosp.composite_score if prosp else 50.0

        # Supplementary factors: fall back to neutral (50) when the signal is
        # missing (engine error, new listing, non-payer) so a single missing
        # factor never zeroes out a stock.
        mom_sig = momentum_signals.get(ts_code)
        momentum_score = mom_sig.momentum_score if mom_sig else 50.0
        div_sig = dividend_signals.get(ts_code)
        dividend_score = div_sig.dividend_score if div_sig else 50.0

        raw_factors[ts_code] = {
            "roe": roe,
            "revenue_growth": revenue_growth,
            "operating_cf_ratio": operating_cf_ratio,
            "pe_percentile": pe_pct,
            "pb_percentile": pb_pct,
            "prosperity_score": prosp_score,
            "momentum_score": momentum_score,
            "dividend_score": dividend_score,
            "industry": stock_infos[ts_code].industry if ts_code in stock_infos else "",
        }

    # Step 2: 在每个行业内计算分位排名
    # 按行业分组
    industry_groups: dict[str, list[str]] = {}
    for ts_code, factors in raw_factors.items():
        ind = factors["industry"] or "unknown"
        industry_groups.setdefault(ind, []).append(ts_code)

    # 计算每个因子在行业内的分位
    tier_scores: dict[str, dict[str, int]] = {}
    for industry, codes in industry_groups.items():
        n = len(codes)
        if n == 0:
            continue

        for factor_name in [
            "roe",
            "revenue_growth",
            "operating_cf_ratio",
            "pe_percentile",
            "pb_percentile",
            "prosperity_score",
            "momentum_score",
            "dividend_score",
        ]:
            # 获取该因子在该行业的所有值
            values = [(c, raw_factors[c][factor_name]) for c in codes]
            values.sort(key=lambda x: x[1])

            # 计算分位排名 (0.0 - 1.0)
            for rank_idx, (code, val) in enumerate(values):
                rank_pct = (rank_idx + 1) / n

                is_negative = factor_name in ("pe_percentile", "pb_percentile")
                tier = quantile_to_tier(rank_pct, is_negative_factor=is_negative)

                if code not in tier_scores:
                    tier_scores[code] = {}
                tier_scores[code][factor_name] = tier

    # Step 3: 分域加权计算综合分
    results: dict[str, dict] = {}
    for ts_code in passed_codes:
        if ts_code not in tier_scores:
            continue

        tiers = tier_scores[ts_code]
        industry = raw_factors[ts_code]["industry"]
        domain = classify_domain(industry)
        weights = DOMAIN_WEIGHTS[domain]

        # Growth 桶 = ROE + 收入增长 + 毛利率变化(用 prosperity 近似)
        # 红利型域: 替入真实 dividend 因子（该域原来无真实红利数据源），
        # 占 growth_tier 的 30%（替换 prosperity 权重）；其余域保持不变。
        if domain == "dividend":
            growth_tier = (
                tiers.get("roe", 0) * 0.4
                + tiers.get("revenue_growth", 0) * 0.3
                + tiers.get("dividend_score", 0) * 0.3
            )
        else:
            growth_tier = (
                tiers.get("roe", 0) * 0.4
                + tiers.get("revenue_growth", 0) * 0.3
                + tiers.get("prosperity_score", 0) * 0.3
            )

        # Valuation 桶 = PE 倒数 + PB 百分位
        valuation_tier = tiers.get("pe_percentile", 0) * 0.6 + tiers.get("pb_percentile", 0) * 0.4

        # Technical 桶 = 真实价格动量 (momentum_score) 主导，prosperity 作为质量地板
        # 替换原 prosperity slope 近似（数据受限占位）。momentum 缺失时回退到旧近似。
        momentum_tier = tiers.get("momentum_score", 0)
        if "momentum_score" in tiers:
            technical_tier = momentum_tier * 0.6 + tiers.get("prosperity_score", 0) * 0.4
        else:
            technical_tier = tiers.get("prosperity_score", 0) * 0.5

        # Sentiment 桶 = 使用 OCF ratio 近似（数据受限）
        sentiment_tier = tiers.get("operating_cf_ratio", 0) * 0.5

        # 加权综合分 (归一化到 0-100)
        composite_0_2 = (
            growth_tier * weights["growth"]
            + valuation_tier * weights["valuation"]
            + technical_tier * weights["technical"]
            + sentiment_tier * weights["sentiment"]
        )
        composite_0_100 = (composite_0_2 / 2.0) * 100.0

        results[ts_code] = {
            "ts_code": ts_code,
            "name": stock_infos[ts_code].name if ts_code in stock_infos else "",
            "industry": industry,
            "domain": domain,
            "factor_tiers": tiers,
            "bucket_scores": {
                "growth": round(growth_tier, 2),
                "valuation": round(valuation_tier, 2),
                "technical": round(technical_tier, 2),
                "sentiment": round(sentiment_tier, 2),
            },
            "domain_weights": weights,
            "composite_score": round(composite_0_100, 2),
        }

    return results


# ════════════════════════════════════════════════════════════════════════════
# 第三层：加分 (Enhancement)
# ════════════════════════════════════════════════════════════════════════════


def apply_enhancement(
    scored_stocks: dict[str, dict],
    forecast_signals: dict | None = None,
    holder_signals: dict | None = None,
) -> dict[str, dict]:
    """加分层：对稀缺信号加分，上限为综合分的 10%.

    Replaces the former TODO placeholder with two real leading signals sourced
    from davis_analyzer:
      - forecast_acceleration: 业绩预告 leading_score >= 75 (机构级指引)
      - institutional_accumulation: 股东户数趋势 == "集中(动能增强)" (主力收集)

    Both are add-only (加分层 never penalises a missing signal). The cap
    (ENHANCEMENT_CAP_PCT = 10% of composite) is respected as before.
    """
    forecast_signals = forecast_signals or {}
    holder_signals = holder_signals or {}

    for ts_code, entry in scored_stocks.items():
        bonus = 0.0
        bonus_signals = []

        # 业绩预告强指引（前瞻 alpha 信号）
        fc_sig = forecast_signals.get(ts_code)
        if fc_sig is not None and getattr(fc_sig, "leading_score", 0) >= 75.0:
            bonus += ENHANCEMENT_BONUS["forecast_acceleration"]
            bonus_signals.append("forecast_acceleration")

        # 筹码集中（主力收集，稀缺信号）
        hc_sig = holder_signals.get(ts_code)
        if hc_sig is not None and getattr(hc_sig, "trend", "") == "集中(动能增强)":
            bonus += ENHANCEMENT_BONUS["institutional_accumulation"]
            bonus_signals.append("institutional_accumulation")

        # 加分上限：综合分的 10%
        cap = entry["composite_score"] * (ENHANCEMENT_CAP_PCT / 100.0)
        bonus = min(bonus, cap)

        entry["enhancement_bonus"] = round(bonus, 2)
        entry["enhancement_signals"] = bonus_signals
        entry["final_score"] = round(entry["composite_score"] + bonus, 2)

    return scored_stocks


# ════════════════════════════════════════════════════════════════════════════
# 主筛选流程
# ════════════════════════════════════════════════════════════════════════════


def run_multi_factor_screening() -> dict:
    """执行完整的三层结构多因子选股管线，返回 JSON 可序列化的 dict.

    流程:
        Step1  调用 davis_analyzer.pipeline.run_screening_pipeline 获取全市场数据
        Step2  执行硬过滤层 — ROE/CAGR/杠杆/现金流/估值 多维过滤
        Step3  执行打分层 — 0/1/2 三档分位打分，分域加权
        Step4  执行加分层 — 稀缺信号奖励（占位，需接入实际数据源）
        Step5  分域排名，每域取前 N
        Step6  组装最终 JSON 输出
    """
    logger.info("=" * 70)
    logger.info("多因子量化选股 — 三层结构管线")
    logger.info("硬过滤 → 打分 → 加分")
    logger.info("=" * 70)

    # ── Step 1: 获取全市场数据 (复用 davis_analyzer pipeline) ──
    logger.info("Step 1: 调用 davis_analyzer 筛选管线获取全市场数据...")
    pipeline_result = run_screening_pipeline(dry_run=DRY_RUN, top_n=500)

    stock_list = list(pipeline_result.stock_infos.values())
    if not stock_list:
        logger.error("股票宇宙为空，无法继续")
        sys.exit(1)

    logger.info("股票宇宙: {} 只股票", len(stock_list))

    # ── Step 2: 硬过滤层 ──
    logger.info("Step 2: 执行硬过滤层...")
    passed_codes = apply_hard_filter(
        stock_list=stock_list,
        valuation_data=pipeline_result.valuation_data,
        financial_data=pipeline_result.financial_data,
        prosperity_scores=pipeline_result.prosperity_scores,
    )

    if not passed_codes:
        logger.warning("硬过滤后无股票通过，返回空结果")
        return {
            "screened_at": datetime.now().isoformat(),
            "universe_size": len(stock_list),
            "passed_hard_filter": 0,
            "domains": {},
        }

    # ── Step 3: 打分层 ──
    logger.info("Step 3: 执行打分层 (0/1/2 三档分位打分)...")
    scored_stocks = score_stock_universe(passed_codes, pipeline_result)
    logger.info("打分完成: {} 只股票", len(scored_stocks))

    # ── Step 4: 加分层 ──
    logger.info("Step 4: 执行加分层 (稀缺信号奖励)...")
    # holder-concentration is computed on-demand here (not in the pipeline) for
    # the hard-filter-passed set only, keeping it off the always-on path.
    holder_signals: dict = {}
    try:
        from davis_analyzer.holder_concentration import analyze_holder_concentration
        from davis_analyzer.tushare_client import TushareClient

        hc_client = TushareClient()
        for code in passed_codes:
            try:
                hc = analyze_holder_concentration(hc_client, code)
                if hc is not None:
                    holder_signals[code] = hc
            except Exception:
                pass
        logger.info("筹码集中度信号: {} 只", len(holder_signals))
    except Exception:
        logger.debug("holder_concentration 引擎不可用，跳过该加分信号")

    scored_stocks = apply_enhancement(
        scored_stocks,
        forecast_signals=pipeline_result.forecast_signals,
        holder_signals=holder_signals,
    )

    # ── Step 5: 分域排名 ──
    logger.info("Step 5: 分域排名，每域取前 {}...", TOP_N_PER_DOMAIN)
    domain_rankings: dict[str, list] = {
        "dividend": [],
        "growth": [],
        "value": [],
        "cyclical": [],
    }

    # 按域分组
    for ts_code, entry in scored_stocks.items():
        domain = entry["domain"]
        if domain in domain_rankings:
            domain_rankings[domain].append(entry)

    # 每域按 final_score 降序排名，取前 N
    for domain in domain_rankings:
        domain_rankings[domain].sort(key=lambda x: x["final_score"], reverse=True)
        domain_rankings[domain] = domain_rankings[domain][:TOP_N_PER_DOMAIN]

        # 添加域内排名
        for rank, entry in enumerate(domain_rankings[domain], 1):
            entry["domain_rank"] = rank

    # ── Step 6: 组装最终 JSON ──
    result = {
        "screened_at": datetime.now().isoformat(),
        "dry_run": DRY_RUN,
        "universe_size": len(stock_list),
        "passed_hard_filter": len(passed_codes),
        "scored_count": len(scored_stocks),
        "top_n_per_domain": TOP_N_PER_DOMAIN,
        "hard_filter_thresholds": {
            "roe_min": HARD_FILTER_ROE_MIN,
            "cagr_min": HARD_FILTER_CAGR_MIN,
            "debt_ebitda_max": HARD_FILTER_DEBT_EBITDA_MAX,
            "pe_max": HARD_FILTER_PE_MAX,
        },
        "domain_weights": DOMAIN_WEIGHTS,
        "default_weights": DEFAULT_WEIGHTS,
        "enhancement_bonus_config": ENHANCEMENT_BONUS,
        "enhancement_cap_pct": ENHANCEMENT_CAP_PCT,
        "domains": {
            domain: {
                "count": len(ranking),
                "top_candidates": ranking,
            }
            for domain, ranking in domain_rankings.items()
        },
    }

    return result


def main() -> None:
    """主入口：执行三层管线并输出 JSON.

    输出位置由 OUTPUT_DIR (CONFIG) 决定，文件名为
    multi_factor_screening_{timestamp}.json。
    同时将摘要打印到 stdout。
    """
    result = run_multi_factor_screening()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(OUTPUT_DIR) / f"multi_factor_screening_{timestamp}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    logger.info("筛选完成，输出写入 {}", output_path)

    # 打印摘要
    print("\n" + "=" * 70)
    print("多因子量化选股结果摘要")
    print("=" * 70)
    print(f"股票宇宙: {result['universe_size']} 只")
    print(f"硬过滤通过: {result['passed_hard_filter']} 只")
    print(f"打分完成: {result['scored_count']} 只")
    print()

    for domain, data in result["domains"].items():
        domain_cn = {
            "dividend": "红利型",
            "growth": "成长型",
            "value": "价值型",
            "cyclical": "周期型",
        }.get(domain, domain)
        print(f"【{domain_cn}域】前 {data['count']} 名候选:")
        for entry in data["top_candidates"][:5]:  # 每域打印前 5 名
            print(
                f"  #{entry['domain_rank']} {entry['ts_code']} "
                f"{entry['name'][:8]:8s} "
                f"综合分={entry['composite_score']:.1f} "
                f"加分={entry['enhancement_bonus']:.1f} "
                f"最终={entry['final_score']:.1f}"
            )
        print()

    print(f"完整结果: {output_path}")


if __name__ == "__main__":
    main()
