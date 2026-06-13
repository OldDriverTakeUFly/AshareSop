"""景气度 (Prosperity) composite scoring engine with ΔG and DuPont decomposition."""

from __future__ import annotations

import math

from davis_analyzer.constants import PROSPERITY_WEIGHTS
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
    """Score 0–100 based on YoY net-profit growth (same logic as revenue)."""
    return calculate_revenue_score(profit_history)


def calculate_slope_score(metrics_history: list[float]) -> float:
    """Score 0–100 based on linear-regression slope of the metric series.

    Strongly positive slope → 100, flat → 50, strongly negative → 0.
    Returns 50.0 when fewer than 3 data points are available.
    """
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

    if var == 0:
        return 50.0

    slope = cov / var

    # normalise slope relative to |y_mean| to make scoring scale-invariant
    scale = max(abs(y_mean), 1e-9)
    normalised = slope / scale

    # map to 0–100 via sigmoid: score = 50 * (1 + tanh(normalised * k))
    k = 2.0
    score = 50.0 * (1.0 + math.tanh(normalised * k))
    return _clamp(score)


def calculate_duration_score(growth_history: list[float]) -> float:
    """Score based on consecutive quarters of positive growth (recent first).

    0 quarters → 0, 1 → 25, 2 → 50, 3 → 75, 4+ → 100.
    """
    count = 0
    for g in growth_history:
        if g > 0:
            count += 1
        else:
            break

    if count >= 4:
        return 100.0
    return count * 25.0


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
        "净利率驱动（定价权强）"
        "周转率驱动（需求/效率提升）"
        "杠杆驱动（需警惕可持续性）"
    """
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

    revenue_growth_history = _yoy_growth_series(
        [d.revenue for d in sorted_data]
    )
    profit_growth_history = _yoy_growth_series(
        [d.net_profit for d in sorted_data]
    )

    # reverse to most-recent-first for scoring functions
    rev_hist = list(reversed(revenue_growth_history))
    prof_hist = list(reversed(profit_growth_history))

    revenue_score = calculate_revenue_score(rev_hist)
    profit_score = calculate_profit_score(prof_hist)
    slope_score = calculate_slope_score(rev_hist)
    duration_score = calculate_duration_score(rev_hist)

    if len(rev_hist) >= 2:
        delta_g = calculate_delta_g(rev_hist[0], rev_hist[1])
    elif len(rev_hist) == 1:
        delta_g = rev_hist[0]
    else:
        delta_g = 0.0

    w = PROSPERITY_WEIGHTS
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
    if g > 30:
        return 80 + min((g - 30) / 70 * 20, 20)
    if g > 10:
        return 50 + (g - 10) / 20 * 30
    if g >= 0:
        return 25 + g / 10 * 25
    return max(0, 20 + g)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _yoy_growth_series(values: list[float]) -> list[float]:
    """Compute period-over-period growth rates for a chronologically ordered series.

    Returns len(values) - 1 growth rates.  Skips when the base period is
    zero or near-zero to avoid division-by-zero blow-ups.
    """
    rates: list[float] = []
    for i in range(1, len(values)):
        base = values[i - 1]
        if abs(base) < 1e-9:
            rates.append(0.0)
            continue
        rates.append((values[i] - base) / abs(base) * 100)
    return rates
