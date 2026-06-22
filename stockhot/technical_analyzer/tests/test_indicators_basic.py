"""TDD golden tests for basic indicators (MA, support/resistance, volume-price)."""

from __future__ import annotations

import pandas as pd
import pytest

from stockhot.technical_analyzer.indicators import (
    ma,
    support_resistance,
    volume_price_analysis,
)


def _make_ohlcv(
    closes: list[float],
    volumes: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> pd.DataFrame:
    n = len(closes)
    dates = pd.bdate_range("2024-01-01", periods=n)
    if volumes is None:
        volumes = [1_000_000] * n
    if highs is None:
        highs = [c + 0.5 for c in closes]
    if lows is None:
        lows = [c - 0.5 for c in closes]
    opens = closes
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=dates,
    )


class TestMa:
    def test_golden_five_period(self):
        df = _make_ohlcv([10, 11, 12, 13, 14])
        result = ma(df, period=5)

        assert result.iloc[-1] == pytest.approx(12.0)
        assert len(result) == 5
        assert result.index.equals(df.index)

    def test_first_four_nan(self):
        df = _make_ohlcv([10, 11, 12, 13, 14])
        result = ma(df, period=5)

        assert result.iloc[:4].isna().all()
        assert not result.iloc[4:].isna().any()

    def test_window_insufficient(self):
        df = _make_ohlcv([10, 11, 12])
        result = ma(df, period=5)

        assert result.isna().all()

    def test_golden_ten_period(self):
        closes = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        df = _make_ohlcv(closes)
        result = ma(df, period=10)

        assert result.iloc[-1] == pytest.approx(5.5)
        assert result.iloc[:9].isna().all()

    def test_returns_series(self):
        df = _make_ohlcv([10, 11, 12, 13, 14])
        result = ma(df, period=3)

        assert isinstance(result, pd.Series)

    def test_ma20_last_value(self):
        closes = list(range(1, 21))
        df = _make_ohlcv([float(c) for c in closes])
        result = ma(df, period=20)

        assert result.iloc[-1] == pytest.approx(10.5)


class TestSupportResistance:
    def test_double_bottom_finds_support(self):
        closes = [10, 8, 10, 12, 10, 8, 10, 12]
        df = _make_ohlcv(closes)
        result = support_resistance(df)

        assert 8.0 in result["support"]
        assert len(result["support"]) > 0

    def test_resistance_found(self):
        closes = [10, 12, 10, 8, 10, 12, 10, 8]
        df = _make_ohlcv(closes)
        result = support_resistance(df)

        assert 12.0 in result["resistance"]
        assert len(result["resistance"]) > 0

    def test_current_price_returned(self):
        closes = [10, 11, 12, 11, 10]
        df = _make_ohlcv(closes)
        result = support_resistance(df)

        assert result["current_price"] == pytest.approx(10.0)

    def test_support_descending_order(self):
        closes = [10, 7, 10, 12, 10, 8, 10, 12]
        df = _make_ohlcv(closes)
        result = support_resistance(df)

        if len(result["support"]) >= 2:
            assert result["support"][0] >= result["support"][1]

    def test_resistance_ascending_order(self):
        closes = [10, 12, 10, 8, 10, 14, 10, 8]
        df = _make_ohlcv(closes)
        result = support_resistance(df)

        if len(result["resistance"]) >= 2:
            assert result["resistance"][0] <= result["resistance"][1]

    def test_lookback_limits_window(self):
        full_closes = [10] * 50 + [10, 5, 10, 15, 10, 5, 10, 15, 10]
        df = _make_ohlcv(full_closes)
        result = support_resistance(df, lookback=10)

        assert 5.0 in result["support"]
        assert 15.0 in result["resistance"]

    def test_lookback_exceeds_len(self):
        closes = [10, 8, 10, 12, 10]
        df = _make_ohlcv(closes)
        result = support_resistance(df, lookback=60)

        assert 8.0 in result["support"]
        assert 12.0 in result["resistance"]
        assert result["current_price"] == pytest.approx(10.0)

    def test_monotonic_no_extrema(self):
        closes = [1, 2, 3, 4, 5, 6, 7, 8]
        df = _make_ohlcv(closes)
        result = support_resistance(df)

        assert result["support"] == []
        assert result["resistance"] == []

    def test_too_few_rows(self):
        df = _make_ohlcv([10])
        result = support_resistance(df)

        assert result["support"] == []
        assert result["resistance"] == []
        assert result["current_price"] == pytest.approx(10.0)

    def test_deduplicated_levels(self):
        closes = [10, 8, 10, 8, 10, 8, 10]
        df = _make_ohlcv(closes)
        result = support_resistance(df)

        assert result["support"].count(8.0) == 1


class TestVolumePriceAnalysis:
    def test_volume_increasing_price_up_no_divergence(self):
        closes = [10.0, 10.0, 10.0, 10.0, 11.0]
        volumes = [1_000_000, 1_000_000, 1_000_000, 1_000_000, 1_500_000]
        df = _make_ohlcv(closes, volumes)
        result = volume_price_analysis(df)

        assert result["volume_trend"] == "increasing"
        assert result["price_volume_divergence"] is False
        assert result["volume_ratio"] > 1.0

    def test_volume_decreasing_price_down_no_divergence(self):
        closes = [10.0, 10.0, 10.0, 10.0, 9.0]
        volumes = [1_000_000, 1_000_000, 1_000_000, 1_000_000, 500_000]
        df = _make_ohlcv(closes, volumes)
        result = volume_price_analysis(df)

        assert result["volume_trend"] == "decreasing"
        assert result["price_volume_divergence"] is False

    def test_divergence_volume_up_price_down(self):
        closes = [10.0, 10.0, 10.0, 10.0, 9.0]
        volumes = [1_000_000, 1_000_000, 1_000_000, 1_000_000, 1_500_000]
        df = _make_ohlcv(closes, volumes)
        result = volume_price_analysis(df)

        assert result["price_volume_divergence"] is True

    def test_divergence_volume_down_price_up(self):
        closes = [10.0, 10.0, 10.0, 10.0, 11.0]
        volumes = [1_000_000, 1_000_000, 1_000_000, 1_000_000, 500_000]
        df = _make_ohlcv(closes, volumes)
        result = volume_price_analysis(df)

        assert result["price_volume_divergence"] is True

    def test_flat_volume(self):
        closes = [10.0, 10.0, 10.0, 10.0, 10.0]
        volumes = [1_000_000] * 5
        df = _make_ohlcv(closes, volumes)
        result = volume_price_analysis(df)

        assert result["volume_trend"] == "flat"
        assert result["volume_ratio"] == pytest.approx(1.0)

    def test_volume_zero_handling(self):
        closes = [10.0, 10.0, 10.0, 10.0, 10.0]
        volumes = [1_000_000, 1_000_000, 1_000_000, 1_000_000, 0]
        df = _make_ohlcv(closes, volumes)
        result = volume_price_analysis(df)

        assert result["volume_trend"] == "flat"
        assert result["volume_ratio"] == 0.0

    def test_all_zero_volume(self):
        closes = [10.0, 10.0, 10.0, 10.0, 10.0]
        volumes = [0, 0, 0, 0, 0]
        df = _make_ohlcv(closes, volumes)
        result = volume_price_analysis(df)

        assert result["volume_trend"] == "flat"
        assert result["recent_avg_volume"] == 0.0

    def test_avg_volume_calculated(self):
        closes = [10.0, 10.0, 10.0, 10.0, 10.0]
        volumes = [100, 200, 300, 400, 500]
        df = _make_ohlcv(closes, volumes)
        result = volume_price_analysis(df)

        assert result["recent_avg_volume"] == pytest.approx(250.0)

    def test_single_row(self):
        df = _make_ohlcv([10.0], [1_000_000])
        result = volume_price_analysis(df)

        assert result["volume_trend"] == "flat"
        assert result["volume_ratio"] == 0.0

    def test_returns_contract_keys(self):
        df = _make_ohlcv([10.0, 11.0], [1_000_000, 1_200_000])
        result = volume_price_analysis(df)

        assert "volume_trend" in result
        assert "price_volume_divergence" in result
        assert "recent_avg_volume" in result
        assert "volume_ratio" in result
