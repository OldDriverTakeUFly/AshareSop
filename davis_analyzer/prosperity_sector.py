"""Sector-level 景气度 (prosperity) analysis — pure functions for industry aggregation, stage classification, ignition screening, and stock detail building."""

from __future__ import annotations

import statistics
from collections.abc import Iterable

from davis_analyzer.constants import (
    HIGH_GROWTH_CONFIRMED_THRESHOLD,
    HIGH_GROWTH_LOWER_BOUND,
    IGNITION_SLOPE_THRESHOLD,
    INDUSTRY_TOP_STOCK_COUNT,
    RISK_DURATION_SCORE_LOW,
    RISK_REVENUE_SCORE_LOW,
    RISK_SLOPE_SCORE_LOW,
    SECTOR_MIN_STOCKS,
    TRANSITION_DELTA_G_NEGATIVE,
    TRANSITION_DELTA_G_POSITIVE,
)
from davis_analyzer.prosperity import dupont_decomposition
from davis_analyzer.prosperity_inflection import analyze_inflection
from davis_analyzer.types import (
    FinancialData,
    IndustryProsperityScore,
    ProsperityScore,
    ProsperityStockDetail,
    StockInfo,
)


def aggregate_industry_prosperity(
    prosperity_scores: dict[str, ProsperityScore],
    stock_infos: dict[str, StockInfo],
    min_stocks: int = SECTOR_MIN_STOCKS,
    market_cap_map: dict[str, float] | None = None,
) -> list[IndustryProsperityScore]:
    """Group stocks by industry and compute aggregate prosperity metrics.

    Industries with fewer than *min_stocks* stocks are skipped.
    Results are sorted by avg_composite_score descending.
    When *market_cap_map* is provided, uses market-cap weighted averages;
    otherwise falls back to simple averages.
    """

    def _weighted_avg(codes: list[str], attr: str) -> float:
        """Compute market-cap weighted average of a ProsperityScore attribute."""
        if market_cap_map:
            weights = {c: market_cap_map.get(c, 0) for c in codes}
            total_w = sum(w for w in weights.values() if w > 0)
            if total_w > 0:
                return sum(
                    getattr(prosperity_scores[c], attr) * weights[c]
                    for c in codes if weights[c] > 0
                ) / total_w
        return _mean(getattr(prosperity_scores[c], attr) for c in codes)

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
        delta_values = [s.delta_g for s in scores]
        median_delta = statistics.median(delta_values)

        sorted_by_composite = sorted(
            scores, key=lambda s: s.composite_score, reverse=True
        )
        top_codes = [s.ts_code for s in sorted_by_composite[:INDUSTRY_TOP_STOCK_COUNT]]

        results.append(
            IndustryProsperityScore(
                industry=industry,
                stock_count=len(codes),
                avg_composite_score=round(_weighted_avg(codes, "composite_score"), 2),
                median_delta_g=round(median_delta, 2),
                avg_revenue_score=round(_weighted_avg(codes, "revenue_score"), 2),
                avg_profit_score=round(_weighted_avg(codes, "profit_score"), 2),
                avg_slope_score=round(_weighted_avg(codes, "slope_score"), 2),
                avg_duration_score=round(_weighted_avg(codes, "duration_score"), 2),
                stage="",
                ignition_count=0,
                top_stock_codes=top_codes,
            )
        )

    results.sort(key=lambda r: r.avg_composite_score, reverse=True)
    return results


def classify_stock_stage(
    prosperity_score: ProsperityScore,
) -> str:
    """Classify a single stock into 加速期 / 减速期 / 上升拐点 / 下降拐点.

    Uses max(revenue_score, profit_score) for growth assessment with
    a transition zone (75-85) resolved by relative_delta_g direction.
    """
    max_score = max(
        prosperity_score.revenue_score, prosperity_score.profit_score
    )

    if max_score > HIGH_GROWTH_CONFIRMED_THRESHOLD:
        is_high_growth = True
    elif max_score < HIGH_GROWTH_LOWER_BOUND:
        is_high_growth = False
    else:
        # B2: transition zone (75-85) — use relative_delta_g direction
        rdg = prosperity_score.relative_delta_g
        if rdg > TRANSITION_DELTA_G_POSITIVE:
            is_high_growth = True
        elif rdg < TRANSITION_DELTA_G_NEGATIVE:
            is_high_growth = False
        else:
            is_high_growth = False

    if is_high_growth and prosperity_score.delta_g > 0:
        return "加速期"
    if is_high_growth and prosperity_score.delta_g <= 0:
        return "减速期"
    if prosperity_score.delta_g > 0:
        return "上升拐点"
    return "下降拐点"


def classify_industry_stage(
    industry_score: IndustryProsperityScore,
) -> str:
    """Classify an industry aggregate into 加速期 / 减速期 / 上升拐点 / 下降拐点.

    Uses max(avg_revenue_score, avg_profit_score) with the same transition
    zone logic as classify_stock_stage, using median_delta_g direction.
    """
    max_score = max(
        industry_score.avg_revenue_score, industry_score.avg_profit_score
    )

    if max_score > HIGH_GROWTH_CONFIRMED_THRESHOLD:
        is_high_growth = True
    elif max_score < HIGH_GROWTH_LOWER_BOUND:
        is_high_growth = False
    else:
        if industry_score.median_delta_g > TRANSITION_DELTA_G_POSITIVE:
            is_high_growth = True
        elif industry_score.median_delta_g < TRANSITION_DELTA_G_NEGATIVE:
            is_high_growth = False
        else:
            is_high_growth = False

    if is_high_growth and industry_score.median_delta_g > 0:
        return "加速期"
    if is_high_growth and industry_score.median_delta_g <= 0:
        return "减速期"
    if industry_score.median_delta_g > 0:
        return "上升拐点"
    return "下降拐点"


def screen_g_delta_g_ignition(
    prosperity_scores: dict[str, ProsperityScore],
    stock_infos: dict[str, StockInfo] | None = None,
    financial_data: dict[str, list[FinancialData]] | None = None,
    industry_median_delta_g: dict[str, float] | None = None,
    delta_g_threshold: float = 0.0,
) -> set[str]:
    """Return ts_codes where growth is high AND relative delta_g > 0 AND operating_cf > 0.

    Uses the same transition-zone (85/75) logic as classify_stock_stage
    and reads ``score.relative_delta_g`` directly (pre-computed by
    :func:`compute_relative_delta_g`).
    """
    result: set[str] = set()
    for ts_code, score in prosperity_scores.items():
        max_score = max(score.revenue_score, score.profit_score)
        if max_score > HIGH_GROWTH_CONFIRMED_THRESHOLD:
            is_high_growth = True
        elif max_score < HIGH_GROWTH_LOWER_BOUND:
            is_high_growth = False
        else:
            # transition zone: needs relative_delta_g > 0
            is_high_growth = score.relative_delta_g > TRANSITION_DELTA_G_POSITIVE
        if not is_high_growth:
            continue

        rel_dg = score.relative_delta_g
        if rel_dg <= delta_g_threshold:
            continue

        if financial_data:
            fd_list = financial_data.get(ts_code, [])
            if fd_list:
                latest_fd = sorted(
                    fd_list, key=lambda d: d.report_period
                )[-1]
                if latest_fd.operating_cf <= 0:
                    continue

        result.add(ts_code)
    return result


def generate_ignition_reasons(score: ProsperityScore) -> list[str]:
    """Generate human-readable reasons explaining why a stock qualifies as ignition."""
    reasons: list[str] = []
    if score.revenue_score > HIGH_GROWTH_CONFIRMED_THRESHOLD:
        reasons.append(f"营收评分{score.revenue_score:.0f}（高增长确认）")
    if score.profit_score > HIGH_GROWTH_CONFIRMED_THRESHOLD:
        reasons.append(f"利润评分{score.profit_score:.0f}（高增长确认）")
    if score.relative_delta_g > 0:
        reasons.append(f"相对ΔG=+{score.relative_delta_g:.1f}（行业内加速）")
    if score.slope_score > IGNITION_SLOPE_THRESHOLD:
        reasons.append(f"趋势评分{score.slope_score:.0f}（上行趋势确认）")
    return reasons


def generate_risk_warnings(
    prosperity_score: ProsperityScore,
    financial_data: list[FinancialData],
) -> list[str]:
    """Generate risk-warning labels for a single ProsperityScore."""
    warnings: list[str] = []
    if prosperity_score.relative_delta_g < 0:
        warnings.append("增速放缓")
    if prosperity_score.revenue_score < RISK_REVENUE_SCORE_LOW:
        warnings.append("增速不足")
    if prosperity_score.slope_score < RISK_SLOPE_SCORE_LOW:
        warnings.append("趋势下行")
    if prosperity_score.duration_score < RISK_DURATION_SCORE_LOW:
        warnings.append("景气持续性存疑")
    if financial_data:
        latest = sorted(financial_data, key=lambda d: d.report_period)[-1]
        if latest.operating_cf < 0:
            warnings.append("经营性现金流为负")
    return warnings


def compute_relative_delta_g(
    prosperity_scores: dict[str, ProsperityScore],
    stock_infos: dict[str, StockInfo],
) -> dict[str, float]:
    """Compute relative_delta_g for each stock (delta_g - industry_median_delta_g).

    Mutates each ProsperityScore in-place, setting ``relative_delta_g``.
    Returns a dict mapping ts_code → relative_delta_g for convenience.
    """
    industry_delta_g_map: dict[str, list[float]] = {}
    for ts_code, score in prosperity_scores.items():
        info = stock_infos.get(ts_code)
        if info is None:
            continue
        industry_delta_g_map.setdefault(info.industry, []).append(score.delta_g)

    industry_median_delta_g: dict[str, float] = {}
    for industry, deltas in industry_delta_g_map.items():
        if len(deltas) >= SECTOR_MIN_STOCKS:
            industry_median_delta_g[industry] = statistics.median(deltas)
        else:
            industry_median_delta_g[industry] = 0.0

    result: dict[str, float] = {}
    for ts_code, score in prosperity_scores.items():
        info = stock_infos.get(ts_code)
        if info is None:
            continue
        median = industry_median_delta_g.get(info.industry, 0.0)
        score.relative_delta_g = round(score.delta_g - median, 2)
        result[ts_code] = score.relative_delta_g
    return result


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
    compute_relative_delta_g(prosperity_scores, stock_infos)

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

        if fd:
            latest_fd = sorted(fd, key=lambda d: d.report_period)[-1]
            net_margin = latest_fd.net_profit / latest_fd.revenue if latest_fd.revenue else 0.0
            asset_turnover = latest_fd.revenue / latest_fd.total_assets if latest_fd.total_assets else 0.0
            leverage_ratio = latest_fd.total_debt / latest_fd.total_assets if latest_fd.total_assets else 0.0
            details[ts_code].dupont_driver = dupont_decomposition(
                latest_fd.roe, net_margin, asset_turnover, leverage_ratio
            )

    return details


def _mean(values: Iterable[float]) -> float:
    seq = list(values)
    if not seq:
        return 0.0
    return sum(seq) / len(seq)
