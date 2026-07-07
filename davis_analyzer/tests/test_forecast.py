"""Tests for davis_analyzer.forecast — leading-indicator engine."""

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from davis_analyzer.forecast import (
    _forecast_midpoint,
    _growth_to_forecast_score,
    _is_stale,
    _pick_most_relevant,
    _revision_score,
    _revisions_for_period,
    analyze_forecast,
    analyze_forecast_revision,
)
from davis_analyzer.types import ProsperityScore

TODAY = date(2026, 7, 1)


def _ps(delta_g: float = 10.0) -> ProsperityScore:
    return ProsperityScore(
        ts_code="x",
        revenue_score=60.0,
        profit_score=60.0,
        slope_score=60.0,
        duration_score=60.0,
        composite_score=60.0,
        delta_g=delta_g,
    )


def _client_returning(df: pd.DataFrame) -> MagicMock:
    c = MagicMock()
    c.get_forecast.return_value = df
    return c


# ── _forecast_midpoint ────────────────────────────────────────────────


class TestForecastMidpoint:
    def test_both_bounds(self):
        row = pd.Series({"p_change_min": 50.0, "p_change_max": 70.0})
        assert _forecast_midpoint(row) == 60.0

    def test_missing_max_treated_as_min(self):
        row = pd.Series({"p_change_min": 30.0, "p_change_max": None})
        assert _forecast_midpoint(row) == 30.0

    def test_both_missing(self):
        row = pd.Series({"p_change_min": None, "p_change_max": None})
        assert _forecast_midpoint(row) is None

    def test_nan_bounds(self):
        row = pd.Series({"p_change_min": float("nan"), "p_change_max": float("nan")})
        assert _forecast_midpoint(row) is None


# ── _growth_to_forecast_score bands ───────────────────────────────────


class TestForecastScoreBands:
    def test_high_band(self):
        # >50 → 80-100
        assert 80.0 <= _growth_to_forecast_score(60.0) <= 100.0
        assert _growth_to_forecast_score(60.0) == pytest.approx(84.0)

    def test_mid_band(self):
        # 20-50 → 50-80
        assert 50.0 <= _growth_to_forecast_score(35.0) <= 80.0

    def test_low_band_positive(self):
        # 0-20 → 25-50
        s = _growth_to_forecast_score(10.0)
        assert 25.0 <= s <= 50.0

    def test_negative_band(self):
        s = _growth_to_forecast_score(-10.0)
        assert 0.0 <= s <= 25.0

    def test_capped_at_100(self):
        assert _growth_to_forecast_score(500.0) == 100.0


# ── _is_stale ─────────────────────────────────────────────────────────


class TestIsStale:
    def test_recent_not_stale(self):
        assert _is_stale("20260601", today=TODAY) is False

    def test_old_is_stale(self):
        # > 400 days before 2026-07-01
        assert _is_stale("20250101", today=TODAY) is True

    def test_garbage_is_stale(self):
        assert _is_stale("", today=TODAY) is True
        assert _is_stale("bad", today=TODAY) is True


# ── _pick_most_relevant ───────────────────────────────────────────────


class TestPickMostRelevant:
    def test_prefers_newest_end_date(self):
        df = pd.DataFrame(
            {
                "ts_code": ["x", "x"],
                "ann_date": ["20260131", "20250131"],
                "end_date": ["20251231", "20241231"],
                "type": ["预增", "预增"],
                "p_change_min": [50, 30],
                "p_change_max": [70, 40],
            }
        )
        row = _pick_most_relevant(df)
        assert row["end_date"] == "20251231"

    def test_empty_returns_none(self):
        assert _pick_most_relevant(pd.DataFrame()) is None
        assert _pick_most_relevant(None) is None  # type: ignore[arg-type]

    def test_skips_malformed_end_dates(self):
        df = pd.DataFrame(
            {
                "ann_date": ["20260131", "20250131"],
                "end_date": ["", "20241231"],
                "type": ["预增", "预增"],
                "p_change_min": [50, 30],
                "p_change_max": [70, 40],
            }
        )
        row = _pick_most_relevant(df)
        assert row is not None
        assert row["end_date"] == "20241231"


# ── analyze_forecast end-to-end ───────────────────────────────────────


class TestAnalyzeForecast:
    def test_no_forecast_returns_none(self):
        c = _client_returning(pd.DataFrame())
        assert analyze_forecast(c, "x", today=TODAY) is None

    def test_basic_signal_fields(self):
        df = pd.DataFrame(
            [
                {
                    "ts_code": "x",
                    "ann_date": "20260131",
                    "end_date": "20251231",
                    "type": "预增",
                    "p_change_min": 50.0,
                    "p_change_max": 70.0,
                }
            ]
        )
        c = _client_returning(df)
        sig = analyze_forecast(c, "x", prosperity_score=_ps(delta_g=10.0), today=TODAY)
        assert sig is not None
        assert sig.type == "预增"
        assert sig.p_change_mid == 60.0
        assert sig.is_stale is False
        # midpoint 60 → base 84; delta_g 10 → adj = (60-10)*0.3 = +15 (capped)
        assert sig.leading_score == pytest.approx(99.0, abs=1.0)

    def test_stale_marked(self):
        df = pd.DataFrame(
            [
                {
                    "ts_code": "x",
                    "ann_date": "20240101",
                    "end_date": "20231231",
                    "type": "预增",
                    "p_change_min": 50.0,
                    "p_change_max": 70.0,
                }
            ]
        )
        c = _client_returning(df)
        sig = analyze_forecast(c, "x", today=TODAY)
        assert sig is not None
        assert sig.is_stale is True

    def test_negative_forecast_scores_low(self):
        df = pd.DataFrame(
            [
                {
                    "ts_code": "x",
                    "ann_date": "20260131",
                    "end_date": "20251231",
                    "type": "首亏",
                    "p_change_min": -299.0,
                    "p_change_max": -239.0,
                }
            ]
        )
        c = _client_returning(df)
        sig = analyze_forecast(c, "x", prosperity_score=_ps(delta_g=-10.0), today=TODAY)
        assert sig is not None
        assert sig.type == "首亏"
        # midpoint -269 → base clamped near 0; delta_g -10 → adj=( -269 - -10)*0.3 ~ -77 → clamped
        assert sig.leading_score <= 25.0

    def test_client_error_returns_none(self):
        c = MagicMock()
        c.get_forecast.side_effect = RuntimeError("api down")
        assert analyze_forecast(c, "x", today=TODAY) is None

    def test_decile_midpoint_uses_single_bound(self):
        # only min bound present
        row = pd.Series({"p_change_min": 40.0, "p_change_max": None})
        assert _forecast_midpoint(row) == 40.0


# ── Forecast-revision engine ──────────────────────────────────────────


def _fc_row(ann_date: str, end_date: str, lo: float, hi: float, type_: str = "预增") -> dict:
    return {
        "ts_code": "x",
        "ann_date": ann_date,
        "end_date": end_date,
        "type": type_,
        "p_change_min": lo,
        "p_change_max": hi,
    }


class TestRevisionScore:
    def test_no_revision_is_neutral(self):
        assert _revision_score(None) == 50.0
        assert _revision_score(0.0) == 50.0

    def test_upward_revision_scores_high(self):
        assert _revision_score(20.0) == 100.0

    def test_downward_revision_scores_low(self):
        assert _revision_score(-20.0) == 0.0

    def test_symmetric(self):
        assert _revision_score(10.0) == 75.0
        assert _revision_score(-10.0) == 25.0


class TestRevisionsForPeriod:
    def test_filters_by_end_date(self):
        df = pd.DataFrame(
            [
                _fc_row("20250131", "20241231", 50, 70),
                _fc_row("20250131", "20231231", 30, 40),  # different period
            ]
        )
        revs = _revisions_for_period(df, "20241231")
        assert len(revs) == 1
        assert revs[0]["end_date"] == "20241231"

    def test_collapses_same_announcement_cycle(self):
        # Two rows on the same day (different types) → collapsed to one
        df = pd.DataFrame(
            [
                _fc_row("20250131", "20241231", 50, 70, "预增"),
                _fc_row("20250131", "20241231", 50, 70, "略增"),
            ]
        )
        revs = _revisions_for_period(df, "20241231")
        assert len(revs) == 1

    def test_keeps_genuine_revisions(self):
        # Same period, two announcement dates 60 days apart → both kept
        df = pd.DataFrame(
            [
                _fc_row("20250131", "20241231", 50, 70),
                _fc_row("20250401", "20241231", 80, 100),
            ]
        )
        revs = _revisions_for_period(df, "20241231")
        assert len(revs) == 2


class TestAnalyzeForecastRevision:
    def test_no_forecast_returns_none(self):
        c = _client_returning(pd.DataFrame())
        assert analyze_forecast_revision(c, "x", today=TODAY) is None

    def test_client_error_returns_none(self):
        c = MagicMock()
        c.get_forecast.side_effect = RuntimeError("api down")
        assert analyze_forecast_revision(c, "x", today=TODAY) is None

    def test_single_announcement_no_revision(self):
        df = pd.DataFrame([_fc_row("20260131", "20251231", 50, 70)])
        c = _client_returning(df)
        rev = analyze_forecast_revision(c, "x", today=TODAY)
        assert rev is not None
        assert rev.revision_direction == "无修正"
        assert rev.revision_score == 50.0
        assert rev.initial_mid == 60.0
        assert rev.revised_mid == 60.0

    def test_upward_revision_detected(self):
        df = pd.DataFrame(
            [
                _fc_row("20250131", "20241231", 50, 70),  # mid 60
                _fc_row("20250401", "20241231", 80, 100),  # mid 90, +30pp
            ]
        )
        c = _client_returning(df)
        rev = analyze_forecast_revision(c, "x", end_date="20241231", today=TODAY)
        assert rev is not None
        assert rev.revision_direction == "上调"
        assert rev.revision_pp == 30.0
        assert rev.revision_score == 100.0

    def test_downward_revision_detected(self):
        df = pd.DataFrame(
            [
                _fc_row("20250131", "20241231", 50, 70),  # mid 60
                _fc_row("20250401", "20241231", 20, 40),  # mid 30, -30pp
            ]
        )
        c = _client_returning(df)
        rev = analyze_forecast_revision(c, "x", end_date="20241231", today=TODAY)
        assert rev is not None
        assert rev.revision_direction == "下调"
        assert rev.revision_pp == -30.0
        assert rev.revision_score == 0.0

    def test_auto_picks_most_recent_period(self):
        # Two periods; should resolve to the newest (20251231) without end_date arg
        df = pd.DataFrame(
            [
                _fc_row("20250131", "20241231", 50, 70),
                _fc_row("20260131", "20251231", 80, 100),
            ]
        )
        c = _client_returning(df)
        rev = analyze_forecast_revision(c, "x", today=TODAY)
        assert rev is not None
        assert rev.end_date == "20251231"

