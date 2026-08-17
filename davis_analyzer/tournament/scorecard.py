"""ScoreCard — frozen scoring formulas (spec §5.3, values in constants)."""

from __future__ import annotations

from dataclasses import dataclass

from davis_analyzer.backtest_report import PerformanceStats
from davis_analyzer.constants import (
    TOURNAMENT_COMPOSITE_WEIGHTS,
    TOURNAMENT_DRAWDOWN_PENALTY,
    TOURNAMENT_TRAILING_HALF_LIFE,
    TOURNAMENT_TRAILING_WINDOWS,
)
from davis_analyzer.tournament.judge import WindowReport


# ──────────────────────────── window-level formula ────────────────────────────


def window_performance(stats: PerformanceStats) -> float:
    """Sharpe − drawdown penalty (frozen v1 formula)."""
    return stats.sharpe_ratio - TOURNAMENT_DRAWDOWN_PENALTY * abs(stats.max_drawdown_pct)


# ──────────────────────────── aggregation formulas ────────────────────────────


def trailing_score(perfs: list[float]) -> float | None:
    """Half-life weighted mean of the most recent windows (chronological)."""
    recent = perfs[-TOURNAMENT_TRAILING_WINDOWS:]
    if len(recent) < 2:
        return None
    # NOTE: frozen direction (test 锁定 1.189340) — weight 0.5^(age/2) with age
    # counted from the OLDEST window of the recent slice; do NOT flip to
    # recency-weighted (0.5^((n-1-i)/2)), that yields 0.7071 and breaks the
    # frozen contract.
    ages = list(range(len(recent)))  # oldest gets age 0 (weight 1.0)
    weights = [0.5 ** (age / TOURNAMENT_TRAILING_HALF_LIFE) for age in ages]
    total_w = sum(weights)
    return sum(p * w for p, w in zip(recent, weights)) / total_w


def regime_match_score(
    perfs_by_regime: dict[str, list[float]], current_regime: str
) -> float | None:
    """Plain mean of realised window performances under the current regime."""
    hist = perfs_by_regime.get(current_regime)
    if not hist:
        return None
    return sum(hist) / len(hist)


def composite(trailing: float | None, regime_match: float | None) -> float | None:
    """Blend trailing + regime-match sub-scores with frozen weights."""
    if trailing is None or regime_match is None:
        return None
    w = TOURNAMENT_COMPOSITE_WEIGHTS
    return w["trailing"] * trailing + w["regime_match"] * regime_match


# ──────────────────────────── participant-level entry ────────────────────────────


@dataclass
class CompositeScore:
    """Final composite score for one participant (N/A legs stay None)."""

    total: float | None
    trailing: float | None
    regime_match: float | None
    valid_windows: int


def score_participant(reports: list[WindowReport], current_regime: str) -> CompositeScore:
    """Score one participant from its realised WindowReports (chronological)."""
    valid = [r for r in reports if r.stats is not None]
    perfs = [window_performance(r.stats) for r in valid]
    by_regime: dict[str, list[float]] = {}
    for r, p in zip(valid, perfs):
        if r.regime:
            by_regime.setdefault(r.regime, []).append(p)
    trailing = trailing_score(perfs)
    match = regime_match_score(by_regime, current_regime)
    return CompositeScore(
        total=composite(trailing, match), trailing=trailing,
        regime_match=match, valid_windows=len(valid),
    )
