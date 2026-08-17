"""Parameter evolution engine (spec §5.6) — logic frozen, params only.

CPCV-lite validation: split history into N sequential blocks, randomly
hold out K as validation with an embargo gap at every boundary, repeat.
Mutation never leaves declared genome bounds (D8).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date

from davis_analyzer.constants import (
    TOURNAMENT_EMBARGO_DAYS,
    TOURNAMENT_FINALS_WINDOW_DAYS,
    TOURNAMENT_MUTATION_SIGMA,
    TOURNAMENT_SEGMENTS_K,
    TOURNAMENT_SEGMENTS_N,
    TOURNAMENT_SEGMENT_DRAWS,
)
from davis_analyzer.tournament.genome import Genome


@dataclass
class SegmentSplit:
    selection: list[tuple[date, date]] = field(default_factory=list)
    validation: list[tuple[date, date]] = field(default_factory=list)


def _blocks(calendar: list[date], n_segments: int) -> list[list[date]]:
    n = len(calendar)
    size = n // n_segments
    return [calendar[i * size:(i + 1) * size] for i in range(n_segments)]


def draw_segments(
    calendar: list[date],
    n_segments: int = TOURNAMENT_SEGMENTS_N,
    k_validation: int = TOURNAMENT_SEGMENTS_K,
    embargo_days: int = TOURNAMENT_EMBARGO_DAYS,
    n_draws: int = TOURNAMENT_SEGMENT_DRAWS,
    seed: int | None = None,
) -> list[SegmentSplit]:
    rng = random.Random(seed)
    blocks = _blocks(calendar, n_segments)
    splits: list[SegmentSplit] = []
    for _ in range(n_draws):
        val_idx = sorted(rng.sample(range(n_segments), k_validation))
        val_set = set(val_idx)
        validation: list[tuple[date, date]] = []
        for i in val_idx:
            block = blocks[i]
            kept = block[embargo_days:]  # embargo: drop the first days of the block
            if kept:
                validation.append((kept[0], kept[-1]))
        selection = [
            (blocks[i][0], blocks[i][-1]) for i in range(n_segments) if i not in val_set
        ]
        splits.append(SegmentSplit(selection=selection, validation=validation))
    return splits


def split_finals(
    calendar: list[date], finals_days: int = TOURNAMENT_FINALS_WINDOW_DAYS
) -> tuple[list[date], list[date]]:
    """Reserve the trailing trading days as the one-shot finals window."""
    if len(calendar) <= finals_days:
        raise ValueError("calendar too short for a finals window")
    return calendar[:-finals_days], calendar[-finals_days:]


def mutate(
    params: dict[str, float | int], genome: Genome, rng: random.Random
) -> dict[str, float | int]:
    """Gaussian perturbation, σ = 15% of range; choices snap back."""
    out: dict[str, float | int] = dict(params)
    for name in genome.names():
        if name not in out:
            continue
        spec = genome.spec(name)
        lo, hi = spec.lo, spec.hi
        if spec.kind == "choice":
            if rng.random() < 0.3:  # occasional discrete jump
                picks = [c for c in (spec.choices or []) if c != out[name]]
                if picks:
                    out[name] = rng.choice(picks)
            continue
        sigma = TOURNAMENT_MUTATION_SIGMA * (hi - lo)
        value = float(out[name]) + rng.gauss(0.0, sigma)
        out[name] = min(max(value, lo), hi)
    return out
