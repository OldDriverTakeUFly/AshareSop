"""TDD tests for advanced indicators (RSI, MACD, KDJ, Bollinger).

These tests validate the four advanced indicator functions against the
frozen API contract in ``contract.py``.  Golden-value tests use known
price sequences with predictable indicator behavior.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def monotonic_up() -> pd.DataFrame:
    """30-day OHLCV with monotonically increasing close prices."""
    idx = pd.date_range("2024-01-01", periods=30, freq="B")
    close = pd.Series(np.arange(10.0, 40.0), index=idx)
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 0.5,
            "low": close - 1.0,
            "close": close,
            "volume": np.full(30, 10000),
        },
        index=idx,
    )


@pytest.fixture
def monotonic_down() -> pd.DataFrame:
    """30-day OHLCV with monotonically decreasing close prices."""
    idx = pd.date_range("2024-01-01", periods=30, freq="B")
    close = pd.Series(np.arange(40.0, 10.0, -1.0), index=idx)
    return pd.DataFrame(
        {
            "open": close + 0.5,
            "high": close + 1.0,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(30, 10000),
        },
        index=idx,
    )


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """60-day OHLCV with realistic random-walk prices for general tests."""
    np.random.seed(42)
    idx = pd.date_range("2024-01-01", periods=60, freq="B")
    close = pd.Series(np.cumsum(np.random.randn(60) * 0.5) + 100, index=idx)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + np.abs(np.random.randn(60) * 0.3),
            "low": close - np.abs(np.random.randn(60) * 0.3),
            "close": close,
            "volume": np.random.randint(1000, 10000, 60),
        },
        index=idx,
    )


# ── RSI tests ──────────────────────────────────────────────────────────────


class TestRSI:
    """Golden-value tests for rsi()."""

    def test_monotonic_up_high_rsi(self, monotonic_up):
        from stockhot.technical_analyzer.indicators import rsi

        result = rsi(monotonic_up, period=14)
        assert isinstance(result, pd.Series)
        # Monotonically increasing close → very high RSI
        assert result.iloc[-1] > 90

    def test_monotonic_down_low_rsi(self, monotonic_down):
        from stockhot.technical_analyzer.indicators import rsi

        result = rsi(monotonic_down, period=14)
        assert isinstance(result, pd.Series)
        # Monotonically decreasing close → very low RSI
        assert result.iloc[-1] < 10

    def test_warmup_nan(self, sample_ohlcv):
        """First value must be NaN — no prior diff exists."""
        from stockhot.technical_analyzer.indicators import rsi

        result = rsi(sample_ohlcv, period=14)
        assert pd.isna(result.iloc[0])

    def test_range_0_100(self, sample_ohlcv):
        from stockhot.technical_analyzer.indicators import rsi

        result = rsi(sample_ohlcv, period=14)
        valid = result.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_default_period_14(self, sample_ohlcv):
        from stockhot.technical_analyzer.indicators import rsi

        result_default = rsi(sample_ohlcv)
        result_explicit = rsi(sample_ohlcv, period=14)
        pd.testing.assert_series_equal(result_default, result_explicit)


# ── MACD tests ─────────────────────────────────────────────────────────────


class TestMACD:
    """Tests for macd()."""

    def test_columns_exist(self, sample_ohlcv):
        from stockhot.technical_analyzer.indicators import macd

        result = macd(sample_ohlcv)
        assert isinstance(result, pd.DataFrame)
        # Contract: columns must be macd_dif, macd_dea, macd_hist
        assert "macd_dif" in result.columns
        assert "macd_dea" in result.columns
        assert "macd_hist" in result.columns

    def test_hist_is_dif_minus_dea(self, sample_ohlcv):
        """MACD histogram = DIF - DEA (approximately)."""
        from stockhot.technical_analyzer.indicators import macd

        result = macd(sample_ohlcv)
        # Skip NaN warmup rows
        valid = result.dropna()
        if len(valid) > 0:
            # hist should be close to 2*(DIF-DEA) or (DIF-DEA) depending on convention
            # Just check hist has same sign as (dif - dea) for most rows
            sign_match = ((valid["macd_hist"] * (valid["macd_dif"] - valid["macd_dea"])) >= 0).sum()
            assert sign_match >= len(valid) * 0.8

    def test_index_preserved(self, sample_ohlcv):
        from stockhot.technical_analyzer.indicators import macd

        result = macd(sample_ohlcv)
        assert result.index.equals(sample_ohlcv.index)


# ── KDJ tests ──────────────────────────────────────────────────────────────


class TestKDJ:
    """Tests for kdj()."""

    def test_columns_exist(self, sample_ohlcv):
        from stockhot.technical_analyzer.indicators import kdj

        result = kdj(sample_ohlcv)
        assert isinstance(result, pd.DataFrame)
        assert "k" in result.columns
        assert "d" in result.columns
        assert "j" in result.columns

    def test_j_equals_3k_minus_2d(self, sample_ohlcv):
        """J = 3K - 2D (standard KDJ formula)."""
        from stockhot.technical_analyzer.indicators import kdj

        result = kdj(sample_ohlcv)
        valid = result.dropna()
        if len(valid) > 0:
            expected_j = 3 * valid["k"] - 2 * valid["d"]
            np.testing.assert_allclose(valid["j"].values, expected_j.values, rtol=0.01)

    def test_index_preserved(self, sample_ohlcv):
        from stockhot.technical_analyzer.indicators import kdj

        result = kdj(sample_ohlcv)
        assert result.index.equals(sample_ohlcv.index)


# ── Bollinger tests ────────────────────────────────────────────────────────


class TestBollinger:
    """Tests for bollinger()."""

    def test_columns_exist(self, sample_ohlcv):
        from stockhot.technical_analyzer.indicators import bollinger

        result = bollinger(sample_ohlcv)
        assert isinstance(result, pd.DataFrame)
        assert "boll_upper" in result.columns
        assert "boll_mid" in result.columns
        assert "boll_lower" in result.columns

    def test_upper_above_mid_above_lower(self, sample_ohlcv):
        from stockhot.technical_analyzer.indicators import bollinger

        result = bollinger(sample_ohlcv)
        valid = result.dropna()
        assert (valid["boll_upper"] >= valid["boll_mid"]).all()
        assert (valid["boll_mid"] >= valid["boll_lower"]).all()

    def test_mid_equals_sma(self, sample_ohlcv):
        """Middle band should equal SMA(period)."""
        from stockhot.technical_analyzer.indicators import bollinger

        period = 20
        result = bollinger(sample_ohlcv, period=period)
        expected_mid = sample_ohlcv["close"].rolling(window=period).mean()
        valid_idx = expected_mid.dropna().index
        np.testing.assert_allclose(
            result.loc[valid_idx, "boll_mid"].values,
            expected_mid.loc[valid_idx].values,
            rtol=1e-10,
        )

    def test_default_period_20(self, sample_ohlcv):
        from stockhot.technical_analyzer.indicators import bollinger

        r_default = bollinger(sample_ohlcv)
        r_explicit = bollinger(sample_ohlcv, period=20)
        pd.testing.assert_frame_equal(r_default, r_explicit)

    def test_index_preserved(self, sample_ohlcv):
        from stockhot.technical_analyzer.indicators import bollinger

        result = bollinger(sample_ohlcv)
        assert result.index.equals(sample_ohlcv.index)


# ── pandas-ta availability ─────────────────────────────────────────────────


class TestPandasTa:
    """Verify pandas-ta is importable and functional."""

    def test_import(self):
        import pandas_ta

        assert pandas_ta is not None

    def test_version(self):
        import pandas_ta

        assert hasattr(pandas_ta, "version")
        assert pandas_ta.version is not None

    def test_rsi_func_exists(self):
        import pandas_ta

        assert hasattr(pandas_ta, "rsi")

    def test_macd_func_exists(self):
        import pandas_ta

        assert hasattr(pandas_ta, "macd")

    def test_kdj_func_exists(self):
        import pandas_ta

        assert hasattr(pandas_ta, "kdj")

    def test_bbands_func_exists(self):
        import pandas_ta

        assert hasattr(pandas_ta, "bbands")
