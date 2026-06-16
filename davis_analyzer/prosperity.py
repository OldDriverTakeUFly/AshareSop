"""景气度 (Prosperity) composite scoring engine with ΔG and DuPont decomposition."""

from __future__ import annotations

import math

from davis_analyzer.constants import (
    DURATION_BONUS_GROWTH_FACTOR,
    DURATION_BONUS_MAX,
    PROSPERITY_WEIGHTS,
)
from davis_analyzer.types import FinancialData, ProsperityScore


def calculate_revenue_score(revenue_history: list[float]) -> float:
    """Score 0–100 based on YoY revenue growth level and trend.

    Args:
        revenue_history: YoY revenue growth rates for recent quarters,
                         most recent first.

    Scoring bands:
        growth > 30 %  → 80–100
        10–30 %        → 50–80
        0–10 %         → 25–50
        < 0 %          → 0–20
    Recent quarters are weighted more heavily via exponential decay.
    """
    revenue_history = [g for g in revenue_history if math.isfinite(g)]
    if not revenue_history:
        return 0.0

    decay = 0.8
    total_weight = 0.0
    weighted_sum = 0.0

    for i, g in enumerate(revenue_history):
        w = decay ** i
        total_weight += w
        raw = _growth_to_raw_score(g)
        weighted_sum += raw * w

    avg = weighted_sum / total_weight
    return _clamp(avg)


def calculate_profit_score(profit_history: list[float]) -> float:
    """Score 0–100 based on YoY net-profit growth using independent profit thresholds.

    利润波动更大，所以门槛高于营收:
        growth > 50 %  → 80–100
        20–50 %        → 50–80
        0–20 %         → 25–50
        < 0 %          → 0–20
    Recent quarters are weighted more heavily via exponential decay.
    """
    profit_history = [g for g in profit_history if math.isfinite(g)]
    if not profit_history:
        return 0.0

    decay = 0.8
    total_weight = 0.0
    weighted_sum = 0.0

    for i, g in enumerate(profit_history):
        w = decay ** i
        total_weight += w
        raw = _growth_to_raw_score_profit(g)
        weighted_sum += raw * w

    avg = weighted_sum / total_weight
    return _clamp(avg)


def calculate_slope_score(metrics_history: list[float]) -> float:
    """Score 0–100 based on linear-regression slope of the metric series.

    Strongly positive slope → 100, flat → 50, strongly negative → 0.
    Returns 50.0 when fewer than 3 data points are available.
    """
    metrics_history = [g for g in metrics_history if math.isfinite(g)]
    n = len(metrics_history)
    if n < 3:
        return 50.0

    xs = list(range(n))
    x_mean = (n - 1) / 2.0
    y_mean = sum(metrics_history) / n

    cov = 0.0
    var = 0.0
    for x, y in zip(xs, metrics_history):
        dx = x - x_mean
        dy = y - y_mean
        cov += dx * dy
        var += dx * dx

    slope = cov / var

    # normalise slope relative to |y_mean| to make scoring scale-invariant
    scale = max(abs(y_mean), 1e-9)
    normalised = slope / scale

    # map to 0–100 via sigmoid: score = 50 * (1 + tanh(normalised * k))
    k = 2.0
    score = 50.0 * (1.0 + math.tanh(normalised * k))
    return _clamp(score)


def calculate_duration_score(growth_history: list[float]) -> float:
    """Score based on consecutive quarters of positive growth + magnitude bonus.

    Base: 0 quarters → 0, 1 → 25, 2 → 50, 3 → 75, 4+ → 100.
    Bonus: min(25, avg_positive_growth * 0.5) added to base, capped at 100.
    """
    count = 0
    positive_growths: list[float] = []
    for g in growth_history:
        if g > 0:
            count += 1
            positive_growths.append(g)
        else:
            break

    if count == 0:
        return 0.0

    base_score = count * 25.0
    if base_score >= 100.0:
        base_score = 100.0

    avg_growth = (
        sum(positive_growths) / len(positive_growths) if positive_growths else 0.0
    )
    bonus = min(DURATION_BONUS_MAX, avg_growth * DURATION_BONUS_GROWTH_FACTOR)

    return min(100.0, base_score + bonus)


def calculate_delta_g(current_growth: float, previous_growth: float) -> float:
    """ΔG = current_growth − previous_growth (percentage points).

    Positive ΔG ⇒ growth accelerating (bullish).
    Negative ΔG ⇒ growth decelerating (bearish).
    """
    return current_growth - previous_growth


def dupont_decomposition(
    roe: float,
    net_margin: float,
    asset_turnover: float,
    leverage_ratio: float,
) -> str:
    """Classify the dominant ROE driver using DuPont identity.

    ROE = net_margin × asset_turnover × leverage_ratio

    Returns one of:
        "ROE为负（需警惕）"
        "净利率驱动（定价权强）"
        "周转率驱动（需求/效率提升）"
        "杠杆驱动（需警惕可持续性）"
    """
    if roe <= 0:
        return "ROE为负（需警惕）"

    abs_nm = abs(net_margin)
    abs_at = abs(asset_turnover)
    abs_lv = abs(leverage_ratio)

    if abs_nm >= abs_at and abs_nm >= abs_lv:
        return "净利率驱动（定价权强）"
    if abs_at >= abs_nm and abs_at >= abs_lv:
        return "周转率驱动（需求/效率提升）"
    return "杠杆驱动（需警惕可持续性）"


def calculate_prosperity_score(
    financial_data_list: list[FinancialData],
) -> ProsperityScore:
    """Build a ProsperityScore from at least 4 quarters of FinancialData.

    Uses the four sub-scores and the exact weights from PROSPERITY_WEIGHTS.
    """
    if not financial_data_list:
        raise ValueError("Need at least one FinancialData record")

    ts_code = financial_data_list[0].ts_code

    sorted_data = sorted(financial_data_list, key=lambda d: d.report_period)

    revenue_growth_history = _extract_yoy_series(sorted_data, "yoy_revenue_growth")
    profit_growth_history = _extract_yoy_series(sorted_data, "yoy_profit_growth")

    # reverse to most-recent-first for scoring functions
    rev_hist = list(reversed(revenue_growth_history))
    prof_hist = list(reversed(profit_growth_history))

    revenue_score = calculate_revenue_score(rev_hist)
    profit_score = calculate_profit_score(prof_hist)
    slope_score = calculate_slope_score(rev_hist)
    duration_score = calculate_duration_score(rev_hist)

    # A1: Cash flow quality adjustment
    latest = sorted_data[-1]
    if latest.net_profit > 0:
        cf_quality = min(1.0, latest.operating_cf / latest.net_profit)
        profit_score *= cf_quality

    # A3: 3-quarter moving average when available
    if len(rev_hist) >= 3:
        delta_g = (rev_hist[0] - rev_hist[2]) / 2
    elif len(rev_hist) == 2:
        delta_g = calculate_delta_g(rev_hist[0], rev_hist[1])
    elif len(rev_hist) == 1:
        delta_g = rev_hist[0]
    else:
        delta_g = 0.0

    w = PROSPERITY_WEIGHTS

    if math.isnan(revenue_score):
        revenue_score = 0.0
    if math.isnan(profit_score):
        profit_score = 0.0
    if math.isnan(slope_score):
        slope_score = 0.0
    if math.isnan(duration_score):
        duration_score = 0.0

    composite = (
        revenue_score * w["revenue"]
        + profit_score * w["profit"]
        + slope_score * w["slope"]
        + duration_score * w["duration"]
    )

    return ProsperityScore(
        ts_code=ts_code,
        revenue_score=round(revenue_score, 2),
        profit_score=round(profit_score, 2),
        slope_score=round(slope_score, 2),
        duration_score=round(duration_score, 2),
        composite_score=round(composite, 2),
        delta_g=round(delta_g, 2),
    )


def batch_prosperity(
    financial_data_map: dict[str, list[FinancialData]],
) -> dict[str, ProsperityScore]:
    """Calculate ProsperityScore for every ts_code with ≥ 2 quarters of data."""
    results: dict[str, ProsperityScore] = {}
    for ts_code, data_list in financial_data_map.items():
        if len(data_list) < 2:
            continue
        results[ts_code] = calculate_prosperity_score(data_list)
    return results


def _growth_to_raw_score(g: float) -> float:
    """Map a single YoY growth percentage to a 0–100 raw score."""
    if not math.isfinite(g):
        return 0.0
    if g > 30:
        return 80 + min((g - 30) / 70 * 20, 20)
    if g > 10:
        return 50 + (g - 10) / 20 * 30
    if g >= 0:
        return 25 + g / 10 * 25
    return max(0, 25 + g * 1.25)


def _growth_to_raw_score_profit(g: float) -> float:
    """Map a single YoY profit growth percentage to a 0–100 raw score.

    Higher thresholds than revenue (利润波动更大):
        >50% → 80-100, 20-50% → 50-80, 0-20% → 25-50, <0% → 0-20
    """
    if not math.isfinite(g):
        return 0.0
    if g > 50:
        return 80 + min((g - 50) / 50 * 20, 20)
    if g > 20:
        return 50 + (g - 20) / 30 * 30
    if g >= 0:
        return 25 + g / 20 * 25
    return max(0, 25 + g * 1.25)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _extract_yoy_series(
    data: list[FinancialData], attr: str
) -> list[float]:
    """Extract pre-computed YoY growth from FinancialData (not raw cumulative).

    Tushare returns cumulative financials — sequential differencing of raw
    revenue mixes seasonal resets with real growth.  ``financial_fetcher``
    already computes true YoY via ``shift(4)``; this surfaces those values
    as percentages, skipping periods where YoY is unavailable (None sentinel).
    """
    return [
        getattr(d, attr) * 100
        for d in data
        if getattr(d, attr) is not None
    ]
