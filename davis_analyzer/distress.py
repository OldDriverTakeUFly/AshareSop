"""Three-layer distress signal system for Davis Double analysis.

Layer 1 — Distress Confirmation: Is the stock genuinely distressed?
Layer 2 — Reversal Possibility: Does the balance sheet support a turnaround?
Layer 3 — Reversal Activation: Are fundamentals starting to inflect?
"""

from __future__ import annotations

from davis_analyzer.types import DistressSignal


# ── Layer 1: Distress Confirmation ──────────────────────────────────────────

def check_eps_decline(eps_history: list[float]) -> bool:
    """True if latest EPS YoY decline > 30%.

    eps_history is ordered most-recent-first. Compares eps_history[0] to eps_history[1].
    """
    if len(eps_history) < 2:
        return False
    latest = eps_history[0]
    previous = eps_history[1]
    if abs(previous) < 1e-9:
        return False
    decline = (previous - latest) / abs(previous)
    return decline > 0.30


def check_pe_pb_percentile(pe_pct: float, pb_pct: float) -> bool:
    """True if PE OR PB percentile < 10% (deeply undervalued signal)."""
    return pe_pct < 0.10 or pb_pct < 0.10


def check_financial_health(debt_ratio: float, operating_cf: float) -> bool:
    """True if debt_ratio < 0.5 AND operating_cf > 0 (healthy balance sheet).

    debt_ratio = total_debt / total_assets.
    """
    return debt_ratio < 0.5 and operating_cf > 0


# ── Layer 2: Reversal Possibility ──────────────────────────────────────────

def check_balance_sheet(total_debt: float, total_assets: float) -> bool:
    """True if total_debt / total_assets < 0.5."""
    if total_assets <= 0:
        return False
    return (total_debt / total_assets) < 0.5


def check_operating_cf(operating_cf: float) -> bool:
    """True if operating_cf > 0."""
    return operating_cf > 0


def check_roe_trend(roe_history: list[float]) -> bool:
    """True if ROE is stabilizing or improving (latest >= previous).

    roe_history is ordered most-recent-first.
    """
    if len(roe_history) < 2:
        return False
    return roe_history[0] >= roe_history[1]


# ── Layer 3: Reversal Activation ───────────────────────────────────────────

def check_revenue_inflection(revenue_history: list[float]) -> bool:
    """True if revenue went from declining to stabilizing/growing.

    revenue_history is YoY growth rates, most-recent-first.
    Detects: earlier period negative, latest period positive or flat.
    """
    if len(revenue_history) < 2:
        return False
    latest = revenue_history[0]
    previous = revenue_history[1]
    return previous < 0 and latest >= 0


def check_profit_inflection(profit_history: list[float]) -> bool:
    """True if profit growth turning positive.

    profit_history is YoY growth rates, most-recent-first.
    """
    if len(profit_history) < 2:
        return False
    latest = profit_history[0]
    previous = profit_history[1]
    return previous < 0 and latest >= 0


def check_delta_g_positive(delta_g: float) -> bool:
    """True if delta_g > 0 (growth accelerating)."""
    return delta_g > 0


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
    """Compute three-layer distress score.

    Each layer score = (true_count / total_signals) * 100.
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
        "operating_cf": check_operating_cf(operating_cf),
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


def _layer_score(signals: dict[str, bool]) -> float:
    """(true_count / total) * 100."""
    if not signals:
        return 0.0
    true_count = sum(1 for v in signals.values() if v)
    return (true_count / len(signals)) * 100.0
