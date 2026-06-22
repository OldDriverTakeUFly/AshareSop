"""PE/PB monthly trend calculation, slope/acceleration extraction, and scoring.

Implements the Davis Double Play trend analysis: declining PE/PB indicates
valuation normalization, which is a bullish signal. Monthly means are derived
from daily series, then linear regression slope and second-difference
acceleration are extracted to produce a 0–100 trend score.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.constants import MIN_TREND_MONTHS
from davis_analyzer.types import StockInfo


def calculate_monthly_trend(
    daily_pe_series: pd.Series,
    daily_pb_series: pd.Series,
) -> tuple[list[float], list[float]]:
    """Resample daily PE/PB series to monthly means, filtering negative PE.

    Args:
        daily_pe_series: Date-indexed pandas Series of daily PE values.
        daily_pb_series: Date-indexed pandas Series of daily PB values.

    Returns:
        Tuple ``(monthly_pe_means, monthly_pb_means)`` — two lists of floats.
        Months with negative PE are excluded from the PE list.  PB list
        contains all valid monthly means.
    """
    monthly_pe = daily_pe_series.resample("ME").mean()
    monthly_pb = daily_pb_series.resample("ME").mean()

    # Filter negative PE months — mark as NaN then drop
    monthly_pe = monthly_pe.where(monthly_pe >= 0, np.nan).dropna()
    monthly_pb = monthly_pb.dropna()

    return monthly_pe.tolist(), monthly_pb.tolist()


def calculate_trend_slope(monthly_values: list[float]) -> float:
    """Calculate linear regression slope of monthly values.

    Uses ``numpy.polyfit(degree=1)`` for ordinary least-squares regression.

    Args:
        monthly_values: List of monthly mean values.

    Returns:
        Linear slope (float). Returns 0.0 if fewer than 2 data points.
    """
    if len(monthly_values) < 2:
        return 0.0

    x = np.arange(len(monthly_values), dtype=np.float64)
    y = np.array(monthly_values, dtype=np.float64)
    slope = float(np.polyfit(x, y, 1)[0])
    return slope


def calculate_trend_acceleration(monthly_values: list[float]) -> float:
    """Calculate acceleration (mean of second differences) of monthly values.

    Positive acceleration means the trend is bending upward (decelerating
    decline or accelerating rise).  Negative acceleration means the trend
    is bending downward (accelerating decline or decelerating rise).

    Args:
        monthly_values: List of monthly mean values.

    Returns:
        Mean of second differences (float). Returns 0.0 if fewer than 3
        data points.
    """
    if len(monthly_values) < 3:
        return 0.0

    arr = np.array(monthly_values, dtype=np.float64)
    diff2 = np.diff(arr, n=2)
    return float(np.mean(diff2))


def calculate_trend_score(
    pe_slope: float,
    pb_slope: float,
    pe_acceleration: float,
    pb_acceleration: float,
    is_cyclical: bool,
) -> float:
    """Calculate Davis Double Play trend score from slopes and accelerations.

    For Davis Double Play: PE/PB declining = valuation normalizing =
    BULLISH → high score.

    Scoring logic:
        - Slope normalized to 0–100: slope in [-1, 0] → [100, 50],
          slope > 0 → [50, 0]. Clamped to [0, 100].
        - Negative acceleration (declining faster) adds bonus up to +10.
        - Positive acceleration (declining slowing) subtracts up to -10.
        - Non-cyclical: weighted PE 70% + PB 30%.
        - Cyclical: PB only (PE ignored).

    Args:
        pe_slope: PE linear regression slope.
        pb_slope: PB linear regression slope.
        pe_acceleration: PE mean second difference.
        pb_acceleration: PB mean second difference.
        is_cyclical: Whether the stock is in a cyclical industry.

    Returns:
        Trend score in [0.0, 100.0].
    """

    def _slope_to_score(slope: float) -> float:
        """Map slope to 0-100: declining (negative) → high, rising → low."""
        return max(0.0, min(100.0, 50.0 - slope * 50.0))

    def _accel_to_adjustment(accel: float) -> float:
        """Map acceleration to bonus/penalty in [-10, +10].

        Negative acceleration (declining faster) → positive bonus.
        Positive acceleration (declining slower / rising faster) → penalty.
        """
        return max(-10.0, min(10.0, -accel * 100.0))

    if is_cyclical:
        score = _slope_to_score(pb_slope) + _accel_to_adjustment(pb_acceleration)
    else:
        pe_component = _slope_to_score(pe_slope) + _accel_to_adjustment(pe_acceleration)
        pb_component = _slope_to_score(pb_slope) + _accel_to_adjustment(pb_acceleration)
        score = pe_component * 0.7 + pb_component * 0.3

    return max(0.0, min(100.0, score))


def batch_trend(
    valuation_history_map: dict[str, tuple[pd.Series, pd.Series]],
    stock_infos: dict[str, StockInfo],
) -> dict[str, float]:
    """Calculate trend scores for a batch of stocks.

    Args:
        valuation_history_map: Maps ts_code to ``(daily_pe_series,
            daily_pb_series)``.
        stock_infos: Maps ts_code to ``StockInfo``.

    Returns:
        Dict mapping ts_code → trend_score. Stocks with fewer than
        ``MIN_TREND_MONTHS`` monthly data points receive 50.0 (neutral).
    """
    results: dict[str, float] = {}

    for ts_code, (daily_pe, daily_pb) in valuation_history_map.items():
        try:
            stock_info = stock_infos.get(ts_code)
            is_cyclical = stock_info.is_cyclical if stock_info else False

            monthly_pe, monthly_pb = calculate_monthly_trend(daily_pe, daily_pb)

            # Insufficient data → neutral score
            if len(monthly_pe) < MIN_TREND_MONTHS or len(monthly_pb) < MIN_TREND_MONTHS:
                results[ts_code] = 50.0
                continue

            pe_slope = calculate_trend_slope(monthly_pe)
            pb_slope = calculate_trend_slope(monthly_pb)
            pe_accel = calculate_trend_acceleration(monthly_pe)
            pb_accel = calculate_trend_acceleration(monthly_pb)

            score = calculate_trend_score(pe_slope, pb_slope, pe_accel, pb_accel, is_cyclical)
            results[ts_code] = score
        except Exception:
            logger.exception("Trend calculation failed for {}", ts_code)
            results[ts_code] = 50.0

    return results
