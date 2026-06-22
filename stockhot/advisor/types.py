"""Unified signal type for the AI trading advisor data-source layer."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UnifiedSignal:
    """Standardised signal envelope for all advisor data sources.

    The ``polarity`` field prevents the distress-score inversion trap:
    ``distress_score`` is lower-is-better, but the aggregator must not
    silently invert it.  Each signal tags its own direction.
    """

    name: str
    value: float | int | str
    polarity: str  # "higher_is_better" | "lower_is_better"
    data_timestamp: str | None
    data_age_days: int | None
    source: str
    details: dict = field(default_factory=dict)

    name: str
    value: float | int | str
    polarity: str
    data_timestamp: str | None
    data_age_days: int | None
    source: str
    details: dict = field(default_factory=dict)
