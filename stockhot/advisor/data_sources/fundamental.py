"""Fundamental (Davis Double) data-source wrappers for the advisor.

Wraps davis_analyzer scoring into :class:`UnifiedSignal` so the
aggregator (T4) can consume fundamental signals uniformly.

The ``polarity`` is always ``"higher_is_better"`` because the Davis
final_score already incorporates distress as a positive contribution
(higher distress sub-score = better reversal potential).  The raw
``distress_score`` is passed through in ``details`` for transparency
but must NOT be treated as the signal value.

**Caching:** ``run_screening_pipeline`` screens the whole A-share universe
(~4500 stocks) on every call. Without a per-stock entry point in
davis_analyzer, the advisor would re-run that scan once per stock — up to
40 times in a single ``daily`` run (once via fetch_davis_signal + once via
the thesis-broken check, per holding). The module-level cache below runs
the pipeline at most once per process, then serves every per-stock lookup
from the cached ``PipelineResult.scores``. Use ``clear_pipeline_cache()``
between independent runs in tests.
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

# Process-level cache for the full-market pipeline result. ``None`` means
# "not yet computed"; an empty PipelineResult is also cached so a failing
# first call does not trigger a re-scan on every subsequent per-stock lookup.
_pipeline_cache: PipelineResult | None = None


def clear_pipeline_cache() -> None:
    """Reset the pipeline cache. Intended for tests only."""
    global _pipeline_cache
    _pipeline_cache = None


def _get_pipeline_result() -> PipelineResult | None:
    """Return the cached pipeline result, running it once if needed.

    Returns ``None`` if the pipeline itself raised — callers fall back to
    ``_NO_DATA`` in that case. An *empty* result (no scores) is still cached
    and returned, so a dry_run with no Tushare data does not re-scan.
    """
    global _pipeline_cache
    if _pipeline_cache is not None:
        return _pipeline_cache
    try:
        _pipeline_cache = run_screening_pipeline(dry_run=True)
    except Exception:
        _pipeline_cache = None
    return _pipeline_cache


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

    result = _get_pipeline_result()
    if result is None or not result.scores:
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
