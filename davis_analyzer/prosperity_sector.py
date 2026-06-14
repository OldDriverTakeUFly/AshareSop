"""Sector-level 景气度 (prosperity) analysis — pure functions for industry aggregation, stage classification, ignition screening, and stock detail building."""

from __future__ import annotations

import statistics
from collections.abc import Iterable

from davis_analyzer.constants import (
    GROWTH_DECELERATION_THRESHOLD,
    SECTOR_MIN_STOCKS,
)
from davis_analyzer.prosperity_inflection import analyze_inflection
from davis_analyzer.types import (
    FinancialData,
    IndustryProsperityScore,
    ProsperityScore,
    ProsperityStockDetail,
    StockInfo,
)

_GROWTH_SCORE_HIGH = 80.0


def aggregate_industry_prosperity(
    prosperity_scores: dict[str, ProsperityScore],
    stock_infos: dict[str, StockInfo],
    min_stocks: int = SECTOR_MIN_STOCKS,
) -> list[IndustryProsperityScore]:
    """Group stocks by industry and compute aggregate prosperity metrics.

    Industries with fewer than *min_stocks* stocks are skipped.
    Results are sorted by avg_composite_score descending.
    """
    industry_buckets: dict[str, list[str]] = {}
    for ts_code, score in prosperity_scores.items():
        info = stock_infos.get(ts_code)
        if info is None:
            continue
        industry_buckets.setdefault(info.industry, []).append(ts_code)

    results: list[IndustryProsperityScore] = []
    for industry, codes in industry_buckets.items():
        if len(codes) < min_stocks:
            continue

        scores = [prosperity_scores[c] for c in codes]
        avg_composite = _mean(s.composite_score for s in scores)
        delta_values = [s.delta_g for s in scores]
        median_delta = statistics.median(delta_values)

        sorted_by_composite = sorted(
            scores, key=lambda s: s.composite_score, reverse=True
        )
        top_codes = [s.ts_code for s in sorted_by_composite[:10]]

        results.append(
            IndustryProsperityScore(
                industry=industry,
                stock_count=len(codes),
                avg_composite_score=round(avg_composite, 2),
                median_delta_g=round(median_delta, 2),
                avg_revenue_score=round(_mean(s.revenue_score for s in scores), 2),
                avg_profit_score=round(_mean(s.profit_score for s in scores), 2),
                avg_slope_score=round(_mean(s.slope_score for s in scores), 2),
                avg_duration_score=round(_mean(s.duration_score for s in scores), 2),
                stage="",
                ignition_count=0,
                top_stock_codes=top_codes,
            )
        )

    results.sort(key=lambda r: r.avg_composite_score, reverse=True)
    return results


def classify_stock_stage(
    prosperity_score: ProsperityScore,
    growth_deceleration_threshold: float = GROWTH_DECELERATION_THRESHOLD,
) -> str:
    """Classify a single stock into 加速期 / 减速期 / 上升拐点 / 下降拐点.

    Uses revenue_score as growth proxy (>80 ⇒ growth >30 %).
    """
    is_high_growth = prosperity_score.revenue_score > _GROWTH_SCORE_HIGH
    if is_high_growth and prosperity_score.delta_g > 0:
        return "加速期"
    if is_high_growth and prosperity_score.delta_g <= 0:
        return "减速期"
    if prosperity_score.delta_g > 0:
        return "上升拐点"
    return "下降拐点"


def classify_industry_stage(
    industry_score: IndustryProsperityScore,
    growth_deceleration_threshold: float = GROWTH_DECELERATION_THRESHOLD,
) -> str:
    """Classify an industry aggregate into 加速期 / 减速期 / 上升拐点 / 下降拐点.

    Uses avg_revenue_score as growth proxy (>80 ⇒ growth >30 %).
    """
    is_high_growth = industry_score.avg_revenue_score > _GROWTH_SCORE_HIGH
    if is_high_growth and industry_score.median_delta_g > 0:
        return "加速期"
    if is_high_growth and industry_score.median_delta_g <= 0:
        return "减速期"
    if industry_score.median_delta_g > 0:
        return "上升拐点"
    return "下降拐点"


def screen_g_delta_g_ignition(
    prosperity_scores: dict[str, ProsperityScore],
    growth_threshold: float = GROWTH_DECELERATION_THRESHOLD,
    delta_g_threshold: float = 0.0,
) -> set[str]:
    """Return ts_codes where revenue_score > 80 AND delta_g > threshold."""
    result: set[str] = set()
    for ts_code, score in prosperity_scores.items():
        if (
            score.revenue_score > _GROWTH_SCORE_HIGH
            and score.delta_g > delta_g_threshold
        ):
            result.add(ts_code)
    return result


def generate_ignition_reasons(score: ProsperityScore) -> list[str]:
    """Generate human-readable reasons explaining why a stock qualifies as ignition."""
    reasons: list[str] = []
    if score.revenue_score > _GROWTH_SCORE_HIGH:
        reasons.append(f"营收评分{score.revenue_score:.0f}（>80阈值）")
    if score.delta_g > 0:
        reasons.append(f"ΔG=+{score.delta_g:.1f}（增速加快）")
    if score.profit_score > _GROWTH_SCORE_HIGH:
        reasons.append(f"利润评分{score.profit_score:.0f}（高盈利增速）")
    if score.slope_score > 60:
        reasons.append(f"趋势评分{score.slope_score:.0f}（上行趋势确认）")
    return reasons


def generate_risk_warnings(
    prosperity_score: ProsperityScore,
    financial_data: list[FinancialData],
) -> list[str]:
    """Generate risk-warning labels for a single ProsperityScore."""
    warnings: list[str] = []
    if prosperity_score.delta_g < 0:
        warnings.append("增速放缓")
    if prosperity_score.revenue_score < 35:
        warnings.append("增速不足")
    if prosperity_score.slope_score < 40:
        warnings.append("趋势下行")
    if prosperity_score.duration_score < 25:
        warnings.append("景气持续性存疑")
    return warnings


def build_stock_details(
    prosperity_scores: dict[str, ProsperityScore],
    stock_infos: dict[str, StockInfo],
    financial_data: dict[str, list[FinancialData]],
    ignition_set: set[str],
    min_stocks: int = SECTOR_MIN_STOCKS,
) -> dict[str, ProsperityStockDetail]:
    """Build ProsperityStockDetail for every stock present in stock_infos.

    Stocks missing from stock_infos are skipped.  rank_in_industry is
    1-based, computed within each industry by composite_score descending.
    """
    industry_codes: dict[str, list[str]] = {}
    for ts_code in prosperity_scores:
        info = stock_infos.get(ts_code)
        if info is None:
            continue
        industry_codes.setdefault(info.industry, []).append(ts_code)

    industry_ranks: dict[str, int] = {}
    for industry, codes in industry_codes.items():
        sorted_codes = sorted(
            codes,
            key=lambda c: prosperity_scores[c].composite_score,
            reverse=True,
        )
        for rank, code in enumerate(sorted_codes, 1):
            industry_ranks[code] = rank

    details: dict[str, ProsperityStockDetail] = {}
    for ts_code, score in prosperity_scores.items():
        info = stock_infos.get(ts_code)
        if info is None:
            continue

        fd = financial_data.get(ts_code, [])
        details[ts_code] = ProsperityStockDetail(
            ts_code=ts_code,
            name=info.name,
            industry=info.industry,
            prosperity_score=score,
            stage=classify_stock_stage(score),
            is_ignition=ts_code in ignition_set,
            risk_warnings=generate_risk_warnings(score, fd),
            rank_in_industry=industry_ranks.get(ts_code, 0),
            ignition_reasons=generate_ignition_reasons(score)
            if ts_code in ignition_set
            else [],
        )
        details[ts_code].inflection = analyze_inflection(score, details[ts_code].stage, fd)

    return details


def _mean(values: Iterable[float]) -> float:
    seq = list(values)
    if not seq:
        return 0.0
    return sum(seq) / len(seq)
