"""Fundamental (Davis Double) data-source wrappers for the advisor.

Wraps davis_analyzer scoring into :class:`UnifiedSignal` so the
aggregator (T4) can consume fundamental signals uniformly.

The ``polarity`` is always ``"higher_is_better"`` because the Davis
final_score already incorporates distress as a positive contribution
(higher distress sub-score = better reversal potential).  The raw
``distress_score`` is passed through in ``details`` for transparency
but must NOT be treated as the signal value.
"""

from __future__ import annotations

from datetime import date

from davis_analyzer.pipeline import run_screening_pipeline
from davis_analyzer.types import PipelineResult

from stockhot.advisor.data_sources.technical import _compute_data_age
from stockhot.advisor.types import UnifiedSignal

_NO_DATA = {
    "final_score": 50.0,
    "percentile_rank": 50.0,
    "distress_score": 0.0,
    "data_date": None,
    "error": "no_data",
}


def _to_ts_code(code: str) -> str:
    if "." in code:
        return code
    first = code[0] if code else ""
    if first == "6":
        return f"{code}.SH"
    if first in ("0", "3"):
        return f"{code}.SZ"
    if first in ("8", "4"):
        return f"{code}.BJ"
    return code


def get_current_davis_score(code: str) -> dict:
    ts_code = _to_ts_code(code)

    try:
        result: PipelineResult = run_screening_pipeline(dry_run=True)
    except Exception:
        return dict(_NO_DATA)

    if not result.scores:
        return dict(_NO_DATA)

    total = len(result.scores)
    for score in result.scores:
        if score.ts_code == ts_code:
            percentile = round(
                (total - score.rank + 1) / total * 100.0, 2
            ) if total > 0 else 50.0
            return {
                "final_score": score.final_score,
                "percentile_rank": percentile,
                "distress_score": score.distress_score,
                "data_date": date.today().isoformat(),
            }

    return dict(_NO_DATA)


def fetch_davis_signal(code: str) -> UnifiedSignal:
    score_data = get_current_davis_score(code)

    return UnifiedSignal(
        name="davis",
        value=score_data["final_score"],
        polarity="higher_is_better",
        data_timestamp=score_data.get("data_date"),
        data_age_days=_compute_data_age(score_data.get("data_date")),
        source="davis_analyzer",
        details={
            "percentile_rank": score_data["percentile_rank"],
            "distress_score": score_data["distress_score"],
            **({"error": score_data["error"]} if "error" in score_data else {}),
        },
    )
