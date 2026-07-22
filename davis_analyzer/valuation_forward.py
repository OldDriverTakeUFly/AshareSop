"""Forward overlay + PS cross-check — bounded forward-looking valuation adjustment.

The backward-looking valuation engine (``valuation.py``) is purely
self-referential: current PE/PB vs its own 3-year history. That is *by design*
an honest anchor — it cannot be talked out of "expensive" by a good story.
But it has a blind spot: it assumes the future equals the past, which is
exactly where growth-stock investing loses money.

This module layers a **bounded** forward-looking adjustment on top of that
anchor, drawing on signals the pipeline already computes but currently fences
off from valuation:

  * **Cycle stage** (加速期/减速期/上升拐点/下降拐点) from ``classify_stock_stage``
  * **relative_delta_g** (industry-relative growth acceleration)
  * **Earnings pre-announcement** (``ForecastSignal.leading_score``)
  * **Forecast revision** (``ForecastRevision.revision_score``)
  * **Secondary ignition** (``screen_g_delta_g_ignition``)

Architecture — why this is bounded and parallel, not a replacement:

  Layer 1  historical percentile (honest anchor, untouched)
  Layer 2  forward overlay    ← this module, clipped to [+15, −20]
  Layer 3  Triangle Framework (PS + scenario DCF, future work)

The overlay is asymmetric (−20 vs +15) because the two growth forces —
forward-E relief (trailing PE overstates cost when earnings are rising) and
growth premium (high multiples are *fair* for growth) — **cancel on the upside
but reinforce on the downside**. A decelerating stock has both rising
forward-E *and* a contracting multiple; that double hit is high-confidence, so
downside gets more room. Upside is the opposite: relief and premium partially
offset, so a single "accelerating" signal should not move the needle far.

The overlay never feeds ``valuation_score`` or ``final_score`` — it is a
parallel signal (same tier as momentum/dividend/forecast), so the 4-dimension
model's calibration stays intact. Callers see *both* the raw historical
percentile and the "effective" percentile, so they can judge how much of the
cheapness is forward-earnings relief.
"""

from __future__ import annotations

from davis_analyzer.constants import (
    BASE_ACCELERATING_AHEAD,
    BASE_ACCELERATING_DECEL,
    BASE_DECLINING,
    BASE_DECELERATING,
    BASE_TURNING_UNCONFIRMED,
    BASE_TURNING_UP,
    FCST_RESONANCE,
    FCST_WEAKENING,
    FCST_WEAK_CONFIRM,
    FORWARD_DELTA_G_MIN_QUARTERS,
    FORWARD_FORECAST_LEADING_HIGH,
    FORWARD_FORECAST_LEADING_LOW,
    FORWARD_OVERLAY_MAX,
    FORWARD_OVERLAY_MIN,
    FORWARD_PRICEIN_HALF_CAP,
    FORWARD_PRICEIN_PE_PERCENTILE,
    FORWARD_PROFIT_GROWTH_THRESHOLD,
    IGNITION_BONUS,
    PS_DIVERGENCE_THRESHOLD_PP,
    REV_DOWNGRADE,
    REV_UPGRADE,
)
from davis_analyzer.types import (
    ForecastRevision,
    ForecastSignal,
    ForwardOverlay,
    PsCrossCheck,
    ValuationData,
)
from davis_analyzer.valuation import calculate_percentile

# ── Sub-signal 1: cycle-stage base adjustment ─────────────────────────

# Maps (stage, relative_delta_g sign) → base value. Relative-delta-g sign is
# the industry-relative acceleration: a stock out-accelerating its sector is a
# stronger signal than absolute ΔG alone.
_BASE_TABLE: dict[tuple[str, str], float] = {
    ("加速期", "positive"): BASE_ACCELERATING_AHEAD,
    ("加速期", "nonpositive"): BASE_ACCELERATING_DECEL,
    ("上升拐点", "positive"): BASE_TURNING_UP,
    ("上升拐点", "nonpositive"): BASE_TURNING_UNCONFIRMED,
    ("减速期", "positive"): BASE_DECELERATING,
    ("减速期", "nonpositive"): BASE_DECELERATING,
    ("下降拐点", "positive"): BASE_DECLINING,
    ("下降拐点", "nonpositive"): BASE_DECLINING,
}


def _base_adjustment(stage: str, relative_delta_g: float) -> float:
    """Look up the cycle-stage base adjustment.

    加速期/上升拐点 split on relative_delta_g sign (leading vs lagging the
    sector); 减速期/下降拐点 are unconditional — a confirmed downtrend is
    bearish regardless of relative position.
    """
    rdg_key = "positive" if relative_delta_g > 0 else "nonpositive"
    return _BASE_TABLE.get((stage, rdg_key), 0.0)


# ── Sub-signal 2: forecast confirmation + revision ────────────────────


def _forecast_adjustment(
    forecast: ForecastSignal | None,
    revision: ForecastRevision | None,
) -> float:
    """Forecast leading-score band + stackable revision adjustment.

    A stale or missing forecast contributes 0 (neither reward nor penalty).
    A revision (up or down) is *additional* — it stacks on the band score so a
    management downgrade on top of a weak leading score compounds.
    """
    adj = 0.0

    if forecast is not None and not forecast.is_stale:
        leading = forecast.leading_score
        if leading > FORWARD_FORECAST_LEADING_HIGH:
            # Resonance requires realised ΔG confirmation, checked by caller
            # via the delta_g>0 gate passed implicitly (see _has_realised_accel).
            adj += FCST_RESONANCE
        elif leading > FORWARD_FORECAST_LEADING_LOW:
            adj += FCST_WEAK_CONFIRM
        else:
            adj += FCST_WEAKENING

    if revision is not None and revision.revision_score <= 0.0 + 1e-9:
        # revision_score == 0 ⇒ pure downward revision (±20pp → 0/100 scale)
        adj += REV_DOWNGRADE
    elif revision is not None and revision.revision_score >= 100.0 - 1e-9:
        adj += REV_UPGRADE

    return adj


# ── Sub-signal 3: secondary ignition ──────────────────────────────────


def _ignition_adjustment(is_ignition: bool, relative_delta_g: float) -> float:
    """Ignition bonus, but zeroed when ΔG contradicts it (conflict rule 4).

    Ignition is a single-quarter speculative signal. If relative_delta_g is
    already negative, the ignition flag is treated as noise and ignored — the
    trend signal wins.
    """
    if not is_ignition:
        return 0.0
    if relative_delta_g < 0:
        return 0.0  # conflict rule 4: trend overrides ignition
    return IGNITION_BONUS


# ── Correction layer ──────────────────────────────────────────────────


def _apply_corrections(
    raw: float,
    delta_g_quarters: int,
    profit_growth: float | None,
    relative_delta_g: float,
    revision: ForecastRevision | None,
    stage: str,
    historical_pe_percentile: float,
) -> tuple[float, list[str], bool, str]:
    """Apply the five correction rules in order. Returns (adjusted, tags, sufficient, note).

    Rules:
      1. data-insufficient  — ΔG < min quarters → overlay zeroed
      2. 30% threshold      — profit growth < 30% → kill upside (min(·, 0))
      3. conflict forecast  — ΔG>0 but management downgraded → min(·, 0)
      4. conflict ignition  — handled in _ignition_adjustment
      5. price-in risk      — accelerating + high PE → halve upside cap
    """
    tags: list[str] = []
    sufficient = delta_g_quarters >= FORWARD_DELTA_G_MIN_QUARTERS

    # Rule 1: data insufficiency zeroes everything (no guesswork).
    if not sufficient:
        return 0.0, ["data_insufficient"], False, "低（ΔG 数据不足 2 季度，调整不可靠）"

    adjusted = raw

    # Rule 3 (checked before rule 2 so the conflict is logged even when the
    # 30% rule would also bind): forward-looking downgrade overrides a positive
    # ΔG — management knows more than the realised trend.
    if (
        relative_delta_g > 0
        and revision is not None
        and revision.revision_score <= 0.0 + 1e-9
    ):
        adjusted = min(adjusted, 0.0)
        tags.append("conflict_forecast_down")

    # Rule 2: 30% profit-growth red line kills *upside only*. The downside
    # channel stays open so "减速 + 低分位" value traps are still detected.
    if profit_growth is not None and profit_growth < FORWARD_PROFIT_GROWTH_THRESHOLD:
        if adjusted > 0.0:
            adjusted = 0.0
            tags.append("30pct_kills_upside")

    # Rule 5: price-in risk. An accelerating stock already expensive vs its
    # history has likely already been bid up for the growth.
    if (
        stage == "加速期"
        and historical_pe_percentile > FORWARD_PRICEIN_PE_PERCENTILE
    ):
        if adjusted > FORWARD_PRICEIN_HALF_CAP:
            adjusted = FORWARD_PRICEIN_HALF_CAP
            tags.append("pricein_halve")

    # Confidence note
    if adjusted < 0:
        note = "高（下调方向：趋势确认，可信度高）"
    elif adjusted >= FORWARD_OVERLAY_MAX - 1e-9:
        note = "中（达到上调上限：前景信号共振，但 price-in 风险存在）"
    elif adjusted > 0:
        note = "中（上调：前景信号正向，需结合分位判断）"
    else:
        note = "中（前景信号中性）"

    return adjusted, tags, sufficient, note


# ── Public API ────────────────────────────────────────────────────────


def calculate_forward_overlay(
    stage: str,
    relative_delta_g: float,
    forecast: ForecastSignal | None,
    revision: ForecastRevision | None,
    is_ignition: bool,
    delta_g_quarters: int,
    profit_growth: float | None,
    historical_pe_percentile: float,
) -> ForwardOverlay:
    """Compute the bounded forward-looking overlay for a single stock.

    Parameters
    ----------
    stage : str
        Cycle position from ``classify_stock_stage`` — one of
        加速期/减速期/上升拐点/下降拐点.
    relative_delta_g : float
        Industry-relative growth acceleration (delta_g − industry median).
    forecast : ForecastSignal | None
        Earnings pre-announcement leading signal (None or stale → no contribution).
    revision : ForecastRevision | None
        Forecast revision (None → no revision adjustment).
    is_ignition : bool
        Whether the stock passed the 二次点火 screen.
    delta_g_quarters : int
        Number of quarters of financial data available (gates rule 1).
    profit_growth : float | None
        Latest YoY net-profit growth (%) for the 30% threshold (None → skip rule 2).
    historical_pe_percentile : float
        Current PE percentile vs own 3-yr history, in 0.0–1.0 (from valuation_data).

    Returns
    -------
    ForwardOverlay
        With ``final_overlay`` clipped to [+15, −20] and
        ``effective_percentile`` in [0, 100].
    """
    base = _base_adjustment(stage, relative_delta_g)
    forecast_adj = _forecast_adjustment(forecast, revision)

    # Resonance gate: the +7 FCST_RESONANCE requires realised ΔG confirmation.
    # If forecast says high but the realised trend is not accelerating, demote
    # to weak-confirmation magnitude.
    if (
        forecast is not None
        and not forecast.is_stale
        and forecast.leading_score > FORWARD_FORECAST_LEADING_HIGH
        and relative_delta_g <= 0
    ):
        # demote resonance → weak confirm delta
        forecast_adj += FCST_WEAK_CONFIRM - FCST_RESONANCE

    ignition_adj = _ignition_adjustment(is_ignition, relative_delta_g)
    raw = base + forecast_adj + ignition_adj

    corrected, tags, sufficient, note = _apply_corrections(
        raw=raw,
        delta_g_quarters=delta_g_quarters,
        profit_growth=profit_growth,
        relative_delta_g=relative_delta_g,
        revision=revision,
        stage=stage,
        historical_pe_percentile=historical_pe_percentile,
    )

    final = max(FORWARD_OVERLAY_MIN, min(FORWARD_OVERLAY_MAX, corrected))

    # effective_percentile: overlay > 0 (bullish) → cheaper effective percentile
    hist_pct_100 = historical_pe_percentile * 100.0
    effective = max(0.0, min(100.0, hist_pct_100 - final))

    return ForwardOverlay(
        base_adjustment=round(base, 2),
        forecast_adjustment=round(forecast_adj, 2),
        ignition_adjustment=round(ignition_adj, 2),
        raw_overlay=round(raw, 2),
        final_overlay=round(final, 2),
        effective_percentile=round(effective, 2),
        adjustments_applied=tags,
        data_sufficient=sufficient,
        confidence_note=note,
    )


def calculate_ps_crosscheck(
    valuation_history: list[ValuationData],
) -> PsCrossCheck | None:
    """Compute the PS-vs-PE percentile divergence flag.

    Returns None when PS data is absent (all zeros / empty) — PS is optional
    in the daily_basic payload and missing for some names.
    """
    if not valuation_history:
        return None

    latest = valuation_history[0]
    ps_series = [v.ps for v in valuation_history]
    pe_series = [v.pe_ttm for v in valuation_history]

    # PS = 0.0 is the sentinel for "missing" set in fetch_valuation_history.
    if latest.ps <= 0.0 or all(p <= 0.0 for p in ps_series):
        return None

    ps_pct = calculate_percentile(latest.ps, ps_series)
    pe_pct = calculate_percentile(latest.pe_ttm, pe_series)
    divergence_pp = (ps_pct - pe_pct) * 100.0

    if divergence_pp < -PS_DIVERGENCE_THRESHOLD_PP:
        flag = "PS_DISCOUNT"
    elif divergence_pp > PS_DIVERGENCE_THRESHOLD_PP:
        flag = "PS_PREMIUM"
    else:
        flag = "CONSISTENT"

    return PsCrossCheck(
        ps_percentile=round(ps_pct, 4),
        pe_percentile=round(pe_pct, 4),
        divergence_flag=flag,
        divergence_pp=round(divergence_pp, 2),
    )
