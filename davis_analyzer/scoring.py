"""Davis Double final scoring model — 4-dimension weighted combination.

Dimensions: valuation (0.30), trend (0.15), prosperity (0.30), distress (0.25).
Weights are sourced from DAVIS_DOUBLE_WEIGHTS in constants.py.
"""

from __future__ import annotations

from davis_analyzer.constants import DAVIS_DOUBLE_WEIGHTS
from davis_analyzer.types import DavisDoubleScore


def calculate_davis_double_score(
    valuation_score: float,
    prosperity_score: float,
    distress_score: float,
    trend_score: float = 0.0,
    ts_code: str = "",
    name: str = "",
) -> DavisDoubleScore:
    """Combine four sub-scores using DAVIS_DOUBLE_WEIGHTS.

    Final = valuation*w_val + trend*w_trend + prosperity*w_pros + distress*w_dist.
    Weights sum to 1.0: 0.30 + 0.15 + 0.30 + 0.25 = 1.00.
    """
    w = DAVIS_DOUBLE_WEIGHTS
    final_score = round(
        valuation_score * w["valuation"]
        + trend_score * w["trend"]
        + prosperity_score * w["prosperity"]
        + distress_score * w["distress"],
        2,
    )

    return DavisDoubleScore(
        ts_code=ts_code,
        name=name,
        valuation_score=round(valuation_score, 2),
        prosperity_score=round(prosperity_score, 2),
        distress_score=round(distress_score, 2),
        trend_score=round(trend_score, 2),
        final_score=final_score,
        rank=0,
    )


def rank_stocks(
    scored_stocks: list[DavisDoubleScore],
    top_n: int = 30,
) -> list[DavisDoubleScore]:
    """Sort by final_score descending, assign rank (1-based), return top_n."""
    sorted_stocks = sorted(scored_stocks, key=lambda s: s.final_score, reverse=True)
    for i, stock in enumerate(sorted_stocks, start=1):
        stock.rank = i
    return sorted_stocks[:top_n]
