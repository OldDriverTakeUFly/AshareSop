"""Signal aggregator — assembles all advisor signals into one schema.

Pure data assembly: calls T3 data sources + all 4 sell_monitor signals,
wraps into :class:`AggregatedSignals`.  No conflict arbitration, no LLM.

The critical fix: ``check_thesis_broken`` receives the REAL davis score
dict from ``get_current_davis_score()``, not ``{}``, so the thesis-broken
check actually runs instead of always SKIPping.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from stockhot.advisor.data_sources.fundamental import (
    fetch_davis_signal,
    get_current_davis_score,
)
from stockhot.advisor.data_sources.technical import (
    fetch_realtime_price,
    fetch_technical_signal,
)
from stockhot.advisor.types import UnifiedSignal
from stockhot.sell_monitor.signals import (
    check_hard_stop_loss,
    check_target_reached,
    check_thesis_broken,
    check_trailing_stop,
)


@dataclass
class AggregatedSignals:
    code: str
    technical: UnifiedSignal
    davis: UnifiedSignal
    realtime_price: dict
    sell_signals: list[dict] = field(default_factory=list)
    data_freshness: dict = field(default_factory=dict)


def aggregate_signals(
    code: str,
    holding: dict | None = None,
    ohlcv_df: pd.DataFrame | None = None,
) -> AggregatedSignals:
    technical = fetch_technical_signal(code, ohlcv_df)
    realtime = fetch_realtime_price(code)
    davis = fetch_davis_signal(code)

    sell_signals: list[dict] = []

    if holding is not None:
        current_price = realtime.get("current_price") or 0.0

        sell_signals.append(check_hard_stop_loss(holding, current_price))
        trailing_df = ohlcv_df if ohlcv_df is not None else pd.DataFrame()
        sell_signals.append(check_trailing_stop(holding, trailing_df))

        sell_signals.append(check_target_reached(holding, current_price))

        davis_score = get_current_davis_score(code)
        sell_signals.append(check_thesis_broken(holding, davis_score))

    return AggregatedSignals(
        code=code,
        technical=technical,
        davis=davis,
        realtime_price=realtime,
        sell_signals=sell_signals,
        data_freshness={
            "technical": technical.data_age_days,
            "davis": davis.data_age_days,
        },
    )


def compute_confidence_multiplier(aggregated: AggregatedSignals) -> float:
    multiplier = 1.0
    for age in aggregated.data_freshness.values():
        if age is None:
            continue
        if age > 30:
            multiplier *= 0.3
            break
        elif age > 7:
            multiplier *= 0.5
            break
    return multiplier
