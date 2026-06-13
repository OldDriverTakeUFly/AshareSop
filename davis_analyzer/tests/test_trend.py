"""Tests for davis_analyzer.trend — PE/PB monthly trend calculation and scoring."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from davis_analyzer.constants import MIN_TREND_MONTHS
from davis_analyzer.trend import (
    batch_trend,
    calculate_monthly_trend,
    calculate_trend_acceleration,
    calculate_trend_score,
    calculate_trend_slope,
)
from davis_analyzer.types import StockInfo


def _make_stock(ts_code: str, is_cyclical: bool = False) -> StockInfo:
    return StockInfo(
        ts_code=ts_code,
        name="Test",
        industry="钢铁" if is_cyclical else "银行",
        list_status="L",
        is_cyclical=is_cyclical,
    )


def _make_daily_series(
    start: str, months: int, pe_start: float, pe_step: float
) -> pd.Series:
    """Create a daily PE/PB series spanning *months* months with linear drift."""
    n_days = months * 30
    dates = pd.date_range(start, periods=n_days, freq="D")
    values = np.linspace(pe_start, pe_start + pe_step * n_days, n_days)
    return pd.Series(values, index=dates)


class TestCalculateMonthlyTrend:
    def test_monthly_resampling_basic(self) -> None:
        dates = pd.date_range("2024-01-01", "2024-02-29", freq="D")
        jan_mask = dates.month == 1

        pe_vals = np.where(jan_mask, 10.0, 20.0).astype(float)
        pb_vals = np.where(jan_mask, 1.0, 2.0).astype(float)

        daily_pe = pd.Series(pe_vals, index=dates)
        daily_pb = pd.Series(pb_vals, index=dates)

        monthly_pe, monthly_pb = calculate_monthly_trend(daily_pe, daily_pb)

        assert monthly_pe == [10.0, 20.0]
        assert monthly_pb == [1.0, 2.0]

    def test_monthly_mean_with_varying_daily(self) -> None:
        dates = pd.date_range("2024-01-01", "2024-01-31", freq="D")
        daily_pe = pd.Series(np.linspace(10.0, 30.0, 31), index=dates)
        daily_pb = pd.Series(np.linspace(1.0, 3.0, 31), index=dates)

        monthly_pe, monthly_pb = calculate_monthly_trend(daily_pe, daily_pb)

        assert len(monthly_pe) == 1
        assert monthly_pe[0] == pytest.approx(20.0, abs=0.5)
        assert monthly_pb[0] == pytest.approx(2.0, abs=0.1)

    def test_three_months_resampling(self) -> None:
        dates = pd.date_range("2024-01-01", "2024-03-31", freq="D")
        jan_mask = dates.month == 1
        feb_mask = dates.month == 2

        pe_vals = np.where(jan_mask, 15.0, np.where(feb_mask, 25.0, 35.0)).astype(float)
        daily_pe = pd.Series(pe_vals, index=dates)
        daily_pb = pd.Series(np.ones(len(dates)), index=dates)

        monthly_pe, monthly_pb = calculate_monthly_trend(daily_pe, daily_pb)

        assert len(monthly_pe) == 3
        assert monthly_pe == [15.0, 25.0, 35.0]

    def test_empty_series(self) -> None:
        daily_pe = pd.Series([], dtype=float)
        daily_pe.index = pd.DatetimeIndex([])
        daily_pb = pd.Series([], dtype=float)
        daily_pb.index = pd.DatetimeIndex([])

        monthly_pe, monthly_pb = calculate_monthly_trend(daily_pe, daily_pb)

        assert monthly_pe == []
        assert monthly_pb == []


class TestCalculateTrendSlope:
    def test_ascending_slope_positive(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        slope = calculate_trend_slope(values)
        assert slope > 0
        assert slope == pytest.approx(1.0, abs=0.01)

    def test_descending_slope_negative(self) -> None:
        values = [5.0, 4.0, 3.0, 2.0, 1.0]
        slope = calculate_trend_slope(values)
        assert slope < 0
        assert slope == pytest.approx(-1.0, abs=0.01)

    def test_flat_slope_zero(self) -> None:
        values = [10.0, 10.0, 10.0, 10.0]
        slope = calculate_trend_slope(values)
        assert slope == pytest.approx(0.0, abs=0.001)

    def test_slope_with_noise(self) -> None:
        values = [10.0, 12.0, 11.0, 14.0, 15.0]
        slope = calculate_trend_slope(values)
        assert slope > 0

    def test_single_value_returns_zero(self) -> None:
        assert calculate_trend_slope([42.0]) == 0.0

    def test_empty_list_returns_zero(self) -> None:
        assert calculate_trend_slope([]) == 0.0


class TestCalculateTrendAcceleration:
    def test_linear_data_zero_acceleration(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        accel = calculate_trend_acceleration(values)
        assert accel == pytest.approx(0.0, abs=0.001)

    def test_convex_positive_acceleration(self) -> None:
        values = [1.0, 2.0, 4.0, 7.0, 11.0]
        accel = calculate_trend_acceleration(values)
        assert accel > 0

    def test_accelerating_decline_negative_acceleration(self) -> None:
        values = [10.0, 8.0, 5.0, 1.0, -4.0]
        accel = calculate_trend_acceleration(values)
        assert accel < 0

    def test_short_list_returns_zero(self) -> None:
        assert calculate_trend_acceleration([1.0, 2.0]) == 0.0

    def test_three_values(self) -> None:
        values = [10.0, 20.0, 35.0]
        accel = calculate_trend_acceleration(values)
        expected = np.mean(np.diff(np.array(values), n=2))
        assert accel == pytest.approx(expected, abs=0.001)


class TestCalculateTrendScore:
    def test_declining_pe_high_score(self) -> None:
        score = calculate_trend_score(
            pe_slope=-0.5,
            pb_slope=-0.3,
            pe_acceleration=0.0,
            pb_acceleration=0.0,
            is_cyclical=False,
        )
        assert score > 70.0

    def test_rising_pe_low_score(self) -> None:
        score = calculate_trend_score(
            pe_slope=0.5,
            pb_slope=0.3,
            pe_acceleration=0.0,
            pb_acceleration=0.0,
            is_cyclical=False,
        )
        assert score < 30.0

    def test_flat_pe_moderate_score(self) -> None:
        score = calculate_trend_score(
            pe_slope=0.0,
            pb_slope=0.0,
            pe_acceleration=0.0,
            pb_acceleration=0.0,
            is_cyclical=False,
        )
        assert score == pytest.approx(50.0, abs=0.01)

    def test_score_clamped_to_range(self) -> None:
        score = calculate_trend_score(
            pe_slope=-100.0,
            pb_slope=-100.0,
            pe_acceleration=-100.0,
            pb_acceleration=-100.0,
            is_cyclical=False,
        )
        assert 0.0 <= score <= 100.0

    def test_negative_acceleration_adds_bonus(self) -> None:
        base = calculate_trend_score(
            pe_slope=-0.4,
            pb_slope=-0.4,
            pe_acceleration=0.0,
            pb_acceleration=0.0,
            is_cyclical=False,
        )
        with_accel = calculate_trend_score(
            pe_slope=-0.4,
            pb_slope=-0.4,
            pe_acceleration=-0.1,
            pb_acceleration=-0.1,
            is_cyclical=False,
        )
        assert with_accel > base

    def test_positive_acceleration_subtracts(self) -> None:
        base = calculate_trend_score(
            pe_slope=-0.4,
            pb_slope=-0.4,
            pe_acceleration=0.0,
            pb_acceleration=0.0,
            is_cyclical=False,
        )
        with_accel = calculate_trend_score(
            pe_slope=-0.4,
            pb_slope=-0.4,
            pe_acceleration=0.1,
            pb_acceleration=0.1,
            is_cyclical=False,
        )
        assert with_accel < base

    def test_weighting_pe_primary(self) -> None:
        score = calculate_trend_score(
            pe_slope=-0.5,
            pb_slope=0.5,
            pe_acceleration=0.0,
            pb_acceleration=0.0,
            is_cyclical=False,
        )
        pe_only = 50.0 - (-0.5) * 50.0
        pb_only = 50.0 - 0.5 * 50.0
        expected = pe_only * 0.7 + pb_only * 0.3
        assert score == pytest.approx(expected, abs=0.5)


class TestCyclicalStockScoring:
    def test_cyclical_uses_pb_only(self) -> None:
        score_bad_pe_good_pb = calculate_trend_score(
            pe_slope=1.0,
            pb_slope=-0.5,
            pe_acceleration=0.0,
            pb_acceleration=0.0,
            is_cyclical=True,
        )
        assert score_bad_pe_good_pb > 60.0

    def test_cyclical_ignores_pe(self) -> None:
        score_a = calculate_trend_score(
            pe_slope=1.0,
            pb_slope=-0.5,
            pe_acceleration=0.0,
            pb_acceleration=0.0,
            is_cyclical=True,
        )
        score_b = calculate_trend_score(
            pe_slope=-1.0,
            pb_slope=-0.5,
            pe_acceleration=0.0,
            pb_acceleration=0.0,
            is_cyclical=True,
        )
        assert score_a == pytest.approx(score_b, abs=0.001)

    def test_cyclical_bad_pb_low_score(self) -> None:
        score = calculate_trend_score(
            pe_slope=-1.0,
            pb_slope=0.5,
            pe_acceleration=0.0,
            pb_acceleration=0.0,
            is_cyclical=True,
        )
        assert score < 40.0


class TestNegativePEHandling:
    def test_negative_pe_months_excluded(self) -> None:
        dates = pd.date_range("2024-01-01", "2024-02-29", freq="D")
        jan_mask = dates.month == 1

        pe_vals = np.where(jan_mask, -5.0, 20.0).astype(float)
        pb_vals = np.ones(len(dates))

        daily_pe = pd.Series(pe_vals, index=dates)
        daily_pb = pd.Series(pb_vals, index=dates)

        monthly_pe, monthly_pb = calculate_monthly_trend(daily_pe, daily_pb)

        assert monthly_pe == [20.0]
        assert len(monthly_pb) == 2

    def test_all_negative_pe_returns_empty(self) -> None:
        dates = pd.date_range("2024-01-01", "2024-02-29", freq="D")
        daily_pe = pd.Series([-10.0] * len(dates), index=dates)
        daily_pb = pd.Series([1.0] * len(dates), index=dates)

        monthly_pe, monthly_pb = calculate_monthly_trend(daily_pe, daily_pb)

        assert monthly_pe == []
        assert len(monthly_pb) == 2

    def test_mixed_negative_positive_pe(self) -> None:
        dates = pd.date_range("2024-01-01", "2024-03-31", freq="D")
        jan_mask = dates.month == 1
        mar_mask = dates.month == 3

        pe_vals = np.ones(len(dates)) * 15.0
        pe_vals[jan_mask] = -5.0
        pe_vals[mar_mask] = -10.0

        daily_pe = pd.Series(pe_vals, index=dates)
        daily_pb = pd.Series([2.0] * len(dates), index=dates)

        monthly_pe, monthly_pb = calculate_monthly_trend(daily_pe, daily_pb)

        assert monthly_pe == [15.0]
        assert len(monthly_pb) == 3


class TestBatchTrend:
    def test_batch_multiple_stocks(self) -> None:
        n_months = 15
        declining_pe = _make_daily_series("2023-01-01", n_months, 30.0, -0.03)
        declining_pb = _make_daily_series("2023-01-01", n_months, 3.0, -0.003)
        rising_pe = _make_daily_series("2023-01-01", n_months, 10.0, 0.03)
        rising_pb = _make_daily_series("2023-01-01", n_months, 1.0, 0.003)

        valuation_map = {
            "000001.SZ": (declining_pe, declining_pb),
            "000002.SZ": (rising_pe, rising_pb),
        }
        stock_infos = {
            "000001.SZ": _make_stock("000001.SZ", is_cyclical=False),
            "000002.SZ": _make_stock("000002.SZ", is_cyclical=False),
        }

        result = batch_trend(valuation_map, stock_infos)

        assert len(result) == 2
        assert result["000001.SZ"] > 60.0
        assert result["000002.SZ"] < 40.0

    def test_batch_insufficient_data_returns_neutral(self) -> None:
        dates = pd.date_range("2024-01-01", "2024-06-30", freq="D")
        daily_pe = pd.Series([15.0] * len(dates), index=dates)
        daily_pb = pd.Series([1.5] * len(dates), index=dates)

        valuation_map = {"000001.SZ": (daily_pe, daily_pb)}
        stock_infos = {"000001.SZ": _make_stock("000001.SZ")}

        result = batch_trend(valuation_map, stock_infos)

        assert result["000001.SZ"] == 50.0

    def test_batch_insufficient_data_boundary(self) -> None:
        dates = pd.date_range("2023-01-01", periods=365, freq="D")
        daily_pe = pd.Series(np.linspace(20.0, 10.0, len(dates)), index=dates)
        daily_pb = pd.Series(np.linspace(2.0, 1.0, len(dates)), index=dates)

        valuation_map = {"000001.SZ": (daily_pe, daily_pb)}
        stock_infos = {"000001.SZ": _make_stock("000001.SZ")}

        result = batch_trend(valuation_map, stock_infos)

        monthly_pe, _ = calculate_monthly_trend(daily_pe, daily_pb)
        if len(monthly_pe) < MIN_TREND_MONTHS:
            assert result["000001.SZ"] == 50.0
        else:
            assert 0.0 <= result["000001.SZ"] <= 100.0

    def test_batch_cyclical_stock(self) -> None:
        n_months = 15
        declining_pb = _make_daily_series("2023-01-01", n_months, 3.0, -0.01)
        rising_pe = _make_daily_series("2023-01-01", n_months, 10.0, 0.03)

        valuation_map = {"000003.SZ": (rising_pe, declining_pb)}
        stock_infos = {"000003.SZ": _make_stock("000003.SZ", is_cyclical=True)}

        result = batch_trend(valuation_map, stock_infos)

        assert result["000003.SZ"] > 60.0

    def test_batch_empty_map(self) -> None:
        result = batch_trend({}, {})
        assert result == {}

    def test_batch_stock_info_missing(self) -> None:
        n_months = 15
        declining_pe = _make_daily_series("2023-01-01", n_months, 30.0, -0.5)
        declining_pb = _make_daily_series("2023-01-01", n_months, 3.0, -0.05)

        valuation_map = {"000001.SZ": (declining_pe, declining_pb)}
        stock_infos: dict[str, StockInfo] = {}

        result = batch_trend(valuation_map, stock_infos)

        assert "000001.SZ" in result
        assert 0.0 <= result["000001.SZ"] <= 100.0

    def test_batch_all_scores_in_range(self) -> None:
        n_months = 15
        pe = _make_daily_series("2023-01-01", n_months, 20.0, -0.3)
        pb = _make_daily_series("2023-01-01", n_months, 2.0, -0.03)

        valuation_map = {
            "000001.SZ": (pe, pb),
            "000002.SZ": (pe.copy(), pb.copy()),
        }
        stock_infos = {
            "000001.SZ": _make_stock("000001.SZ", is_cyclical=False),
            "000002.SZ": _make_stock("000002.SZ", is_cyclical=True),
        }

        result = batch_trend(valuation_map, stock_infos)

        for score in result.values():
            assert 0.0 <= score <= 100.0
