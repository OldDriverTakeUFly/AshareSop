"""Profitability-quality engine: gross-margin trend + R&D intensity.

The prosperity engine scores *growth* (revenue/profit YoY) but not *quality*
of that growth. Two firms can post identical revenue acceleration while one
expands gross margin (real pricing power) and the other buys growth with
discounts. This module surfaces those quality dimensions so reports can
distinguish "growing better" from "growing cheaper".

Outputs (each 0–100, higher = better):

* ``gross_margin_score``   — trend of ``grossprofit_margin`` over recent
  periods. Expanding margin → high score (pricing power); contracting → low.
* ``rd_intensity_score``   — R&D / revenue level. Higher sustained R&D
  intensity → higher score (applies a soft cap so a firm spending 40% of
  revenue on R&D doesn't saturate trivially).
* ``quality_score``        — 60% margin + 40% R&D blend.

All functions are pure and take a chronological ``list[FinancialData]``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from davis_analyzer.types import FinancialData


@dataclass
class ProfitabilityQuality:
    """Profitability-quality result from margin + R&D analysis."""

    ts_code: str
    latest_gross_margin: float | None  # %
    gross_margin_score: float  # 0–100
    gross_margin_delta: float | None  # latest - first, percentage points
    latest_rd_intensity: float | None  # rd_exp / revenue, %
    rd_intensity_score: float  # 0–100
    quality_score: float  # 0–100 blend
    data_sufficient: bool  # False when too few periods / missing fields


def _valid_margin(fd: FinancialData) -> float | None:
    if fd.grossprofit_margin is None:
        return None
    if not math.isfinite(fd.grossprofit_margin):
        return None
    return fd.grossprofit_margin


def _rd_intensity_pct(fd: FinancialData) -> float | None:
    if fd.rd_exp is None or not fd.revenue:
        return None
    try:
        if not math.isfinite(fd.rd_exp) or not math.isfinite(fd.revenue):
            return None
    except TypeError:
        return None
    return fd.rd_exp / fd.revenue * 100.0


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def score_gross_margin_trend(margins: list[float]) -> tuple[float, float | None]:
    """Score a chronological gross-margin series → (score, delta_pp).

    Logic:
        - delta = last - first (percentage points)
        - score = 50 + delta * 5  (each +1pp → +5 points), clamped [0,100]
    Returns (50.0, None) when fewer than 2 valid points.
    """
    valid = [m for m in margins if m is not None and math.isfinite(m)]
    if len(valid) < 2:
        return 50.0, None
    delta = valid[-1] - valid[0]
    score = _clamp(50.0 + delta * 5.0)
    return score, delta


def score_rd_intensity(intensities: list[float]) -> tuple[float, float | None]:
    """Score R&D intensity (%) series → (score, latest).

    Logic (sustained innovation signal):
        - latest = most recent valid intensity
        - score maps [0, 15]% → [25, 100] with a soft ceiling above 15%.
    Returns (50.0, None) when no valid points.
    """
    valid = [x for x in intensities if x is not None and math.isfinite(x)]
    if not valid:
        return 50.0, None
    latest = valid[-1]
    # 15% intensity → 100; below that linear; above, capped at 100.
    score = _clamp(25.0 + min(latest, 15.0) / 15.0 * 75.0)
    return score, latest


def analyze_profitability_quality(
    financial_data: list[FinancialData],
    lookback: int = 4,
) -> ProfitabilityQuality:
    """Build a ProfitabilityQuality from a stock's FinancialData history.

    Parameters
    ----------
    financial_data : list[FinancialData]
        Chronologically unsorted; will be sorted by report_period internally.
    lookback : int
        Number of most-recent periods to score over (default 4 = ~1 year).
    """
    if not financial_data:
        return ProfitabilityQuality(
            ts_code="",
            latest_gross_margin=None,
            gross_margin_score=50.0,
            gross_margin_delta=None,
            latest_rd_intensity=None,
            rd_intensity_score=50.0,
            quality_score=50.0,
            data_sufficient=False,
        )

    ts_code = financial_data[0].ts_code
    ordered = sorted(financial_data, key=lambda d: d.report_period)
    window = ordered[-lookback:]

    margins = [_valid_margin(fd) for fd in window]
    intensities = [_rd_intensity_pct(fd) for fd in window]

    gm_score, gm_delta = score_gross_margin_trend(margins)  # type: ignore[arg-type]
    rd_score, latest_rd = score_rd_intensity(intensities)  # type: ignore[arg-type]
    latest_gm = next((m for m in reversed(margins) if m is not None), None)

    quality = _clamp(gm_score * 0.6 + rd_score * 0.4)

    sufficient = (
        sum(1 for m in margins if m is not None) >= 2
        or sum(1 for x in intensities if x is not None) >= 1
    )

    return ProfitabilityQuality(
        ts_code=ts_code,
        latest_gross_margin=round(latest_gm, 2) if latest_gm is not None else None,
        gross_margin_score=round(gm_score, 2),
        gross_margin_delta=round(gm_delta, 2) if gm_delta is not None else None,
        latest_rd_intensity=round(latest_rd, 2) if latest_rd is not None else None,
        rd_intensity_score=round(rd_score, 2),
        quality_score=round(quality, 2),
        data_sufficient=sufficient,
    )
