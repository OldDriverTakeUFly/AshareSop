"""Three-layer distress signal system for Davis Double analysis.

Layer 1 — Distress Confirmation: Is the stock genuinely distressed?
Layer 2 — Reversal Possibility: Does the balance sheet support a turnaround?
Layer 3 — Reversal Activation: Are fundamentals starting to inflect?

All signal functions return continuous float values in [0.0, 1.0] to
maximise score differentiation across different stock profiles.
"""

from __future__ import annotations

from davis_analyzer.types import DistressSignal


# ── Layer 1: Distress Confirmation ──────────────────────────────────────────


def check_eps_decline(eps_history: list[float]) -> float:
    """Continuous signal [0, 1] for EPS decline severity.

    Uses same-quarter-prior-year comparison (index 0 vs 4) when 5+ periods
    are available, avoiding seasonal distortion from comparing a single
    quarter to a full-year figure. Falls back to adjacent-period (0 vs 1)
    comparison otherwise.

    A 30%+ EPS decline produces a full (1.0) signal. EPS improvements
    produce 0.0 (no distress).
    """
    if len(eps_history) < 2:
        return 0.0
    latest = eps_history[0]
    previous = eps_history[4] if len(eps_history) >= 5 else eps_history[1]
    if abs(previous) < 1e-9:
        return 0.0
    decline = (previous - latest) / abs(previous)
    return min(1.0, max(0.0, decline / 0.30))


def check_pe_pb_percentile(pe_pct: float, pb_pct: float) -> float:
    """Continuous signal [0, 1] — lower valuation percentile = stronger signal.

    Returns ``1.0 - (pe_pct + pb_pct) / 2`` clamped to [0, 1].
    A stock at the 0th percentile scores 1.0; at the 100th percentile, 0.0.
    """
    return max(0.0, min(1.0, 1.0 - (pe_pct + pb_pct) / 2.0))


def check_financial_health(debt_ratio: float, operating_cf: float) -> float:
    """Continuous signal [0, 1] for balance-sheet health.

    Returns 1.0 if debt_ratio < 0.5 **and** operating_cf > 0,
    0.5 if exactly one condition is met, 0.0 otherwise.
    """
    score = 0.0
    if debt_ratio < 0.5:
        score += 0.5
    if operating_cf > 0:
        score += 0.5
    return score


# ── Layer 2: Reversal Possibility ──────────────────────────────────────────


def check_balance_sheet(total_debt: float, total_assets: float) -> float:
    """Continuous signal [0, 1] based on leverage.

    Returns ``max(0.0, 1.0 - debt_ratio * 2)`` where
    ``debt_ratio = total_debt / total_assets``. A debt ratio of 0 gives
    1.0; 0.5+ gives 0.0.
    """
    if total_assets <= 0:
        return 0.0
    debt_ratio = total_debt / total_assets
    return max(0.0, 1.0 - debt_ratio * 2.0)


def check_operating_cf(operating_cf: float, total_assets: float = 0.0) -> float:
    """Continuous signal [0, 1] for operating cash-flow strength.

    When ``total_assets > 0`` returns ``operating_cf / total_assets``
    clamped to [0, 1]. Otherwise falls back to binary: 1.0 if positive,
    0.0 otherwise.
    """
    if total_assets > 0:
        return max(0.0, min(1.0, operating_cf / total_assets))
    return 1.0 if operating_cf > 0 else 0.0


def check_roe_trend(roe_history: list[float]) -> float:
    """Continuous signal [0, 1] for ROE improvement.

    Uses same-quarter-prior-year comparison (index 0 vs 4) when 5+ periods
    are available to avoid seasonal distortion. A 5+ percentage-point ROE
    improvement produces a full (1.0) signal. ROE decline gives 0.0.
    """
    if len(roe_history) < 2:
        return 0.0
    current = roe_history[0]
    prior = roe_history[4] if len(roe_history) >= 5 else roe_history[1]
    return min(1.0, max(0.0, (current - prior) / 5.0))


# ── Layer 3: Reversal Activation ───────────────────────────────────────────


def check_revenue_inflection(revenue_history: list[float]) -> float:
    """Continuous signal [0, 1] for revenue growth momentum swing.

    ``revenue_history`` contains YoY growth rates, most-recent-first.
    A 20 percentage-point positive swing (latest − previous) produces a
    full (1.0) signal. Negative swings give 0.0.
    """
    if len(revenue_history) < 2:
        return 0.0
    latest = revenue_history[0]
    previous = revenue_history[1]
    swing = latest - previous
    return min(1.0, max(0.0, swing / 20.0))


def check_profit_inflection(profit_history: list[float]) -> float:
    """Continuous signal [0, 1] for profit growth momentum swing.

    Same logic as :func:`check_revenue_inflection`.
    """
    if len(profit_history) < 2:
        return 0.0
    latest = profit_history[0]
    previous = profit_history[1]
    swing = latest - previous
    return min(1.0, max(0.0, swing / 20.0))


def check_delta_g_positive(delta_g: float) -> float:
    """Continuous signal [0, 1] — growth acceleration strength.

    A delta_g of 0.10 (10% acceleration) or more produces 1.0.
    Deceleration (delta_g ≤ 0) gives 0.0.
    """
    return min(1.0, max(0.0, delta_g / 0.10))


# ── Aggregation ─────────────────────────────────────────────────────────────


def calculate_distress_score(
    eps_history: list[float],
    pe_pct: float,
    pb_pct: float,
    debt_ratio: float,
    operating_cf: float,
    total_debt: float,
    total_assets: float,
    roe_history: list[float],
    revenue_history: list[float],
    profit_history: list[float],
    delta_g: float,
    ts_code: str = "",
) -> DistressSignal:
    """Compute three-layer distress score using continuous signals.

    Each layer score = average(continuous_signals) * 100.
    Total = layer1 * 0.3 + layer2 * 0.3 + layer3 * 0.4.
    """
    l1_signals = {
        "eps_decline": check_eps_decline(eps_history),
        "pe_pb_percentile": check_pe_pb_percentile(pe_pct, pb_pct),
        "financial_health": check_financial_health(debt_ratio, operating_cf),
    }
    layer1_score = _layer_score(l1_signals)

    l2_signals = {
        "balance_sheet": check_balance_sheet(total_debt, total_assets),
        "operating_cf": check_operating_cf(operating_cf, total_assets),
        "roe_trend": check_roe_trend(roe_history),
    }
    layer2_score = _layer_score(l2_signals)

    l3_signals = {
        "revenue_inflection": check_revenue_inflection(revenue_history),
        "profit_inflection": check_profit_inflection(profit_history),
        "delta_g_positive": check_delta_g_positive(delta_g),
    }
    layer3_score = _layer_score(l3_signals)

    total_score = round(
        layer1_score * 0.3 + layer2_score * 0.3 + layer3_score * 0.4,
        2,
    )

    signals_detail = {
        "layer1": l1_signals,
        "layer2": l2_signals,
        "layer3": l3_signals,
    }

    return DistressSignal(
        ts_code=ts_code,
        layer1_score=round(layer1_score, 2),
        layer2_score=round(layer2_score, 2),
        layer3_score=round(layer3_score, 2),
        total_score=total_score,
        signals_detail=signals_detail,
    )


def _layer_score(signals: dict[str, float]) -> float:
    """Average of continuous signal values * 100."""
    if not signals:
        return 0.0
    return (sum(signals.values()) / len(signals)) * 100.0
