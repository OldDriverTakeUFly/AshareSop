"""Allocator — risk-budget suggestion from composite scores (spec §5.4)."""

from __future__ import annotations

import math

from davis_analyzer.constants import (
    TOURNAMENT_ALLOCATOR_TAU,
    TOURNAMENT_WEIGHT_BOUNDS,
)


def allocate(scores: dict[str | None, float | None] | dict[str, float | None]) -> dict[str, float]:
    """Softmax(τ) over valid scores, clipped to bounds, N/A pinned to floor."""
    lo, hi = TOURNAMENT_WEIGHT_BOUNDS
    valid = {k: v for k, v in scores.items() if v is not None}
    n_na = len(scores) - len(valid)
    if not valid:
        n = max(len(scores), 1)
        return {k: 1.0 / n for k in scores}
    exps = {k: math.exp(v / TOURNAMENT_ALLOCATOR_TAU) for k, v in valid.items()}
    total = sum(exps.values())
    soft = {k: e / total for k, e in exps.items()}
    clipped = {k: min(max(v, lo), hi) for k, v in soft.items()}
    budget = 1.0 - n_na * lo
    clip_sum = sum(clipped.values())
    weights = {k: budget * v / clip_sum for k, v in clipped.items()}
    for k in scores:
        if k not in weights:
            weights[k] = lo
    return weights
