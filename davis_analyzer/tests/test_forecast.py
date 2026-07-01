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
    analyze_forecast,
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
