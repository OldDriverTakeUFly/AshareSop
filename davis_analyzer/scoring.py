"""Davis Double final scoring model — weighted combination of valuation, prosperity, and distress."""

from __future__ import annotations

from davis_analyzer.constants import DAVIS_DOUBLE_WEIGHTS
from davis_analyzer.types import DavisDoubleScore


def calculate_davis_double_score(
    valuation_score: float,
    prosperity_score: float,
    distress_score: float,
    ts_code: str = "",
    name: str = "",
) -> DavisDoubleScore:
    """Combine three sub-scores using DAVIS_DOUBLE_WEIGHTS.

    Final = valuation * 0.35 + prosperity * 0.35 + distress * 0.30.
    """
    w = DAVIS_DOUBLE_WEIGHTS
    final_score = round(
        valuation_score * w["valuation"]
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
