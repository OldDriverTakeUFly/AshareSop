"""RED-phase contract tests — verify all 9 API stubs raise NotImplementedError.

These tests lock the contract: every function in
``stockhot.technical_analyzer.contract`` must be importable and must
raise ``NotImplementedError`` until the implementation tasks (T7, T11,
T12) replace the stubs.

Once an indicator is implemented, its corresponding test here should be
removed or updated — the test serves as a tripwire for "not yet done".
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockhot.technical_analyzer.contract import (
    bollinger,
    composite_technical_score,
    fetch_ohlcv,
    kdj,
    ma,
    macd,
    rsi,
    support_resistance,
    volume_price_analysis,
)


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """Minimal valid OHLCV DataFrame (30 rows, English columns, ascending)."""
    dates = pd.bdate_range("2024-01-01", periods=30)
    rng = np.random.default_rng(42)
    close = 10.0 + rng.standard_normal(30).cumsum()
    high = close + rng.uniform(0.1, 0.5, size=30)
    low = close - rng.uniform(0.1, 0.5, size=30)
    op = close + rng.uniform(-0.3, 0.3, size=30)
    volume = rng.integers(1_000_000, 10_000_000, size=30)
    return pd.DataFrame(
        {"open": op, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


class TestContract:
    """One test per contract function — all must raise NotImplementedError."""

    def test_fetch_ohlcv_not_implemented(self):
        with pytest.raises(NotImplementedError):
            fetch_ohlcv("000001", "2024-01-01", "2024-06-01")

    def test_ma_not_implemented(self, sample_ohlcv):
        with pytest.raises(NotImplementedError):
            ma(sample_ohlcv, period=5)

    def test_rsi_not_implemented(self, sample_ohlcv):
        with pytest.raises(NotImplementedError):
            rsi(sample_ohlcv, period=14)

    def test_macd_not_implemented(self, sample_ohlcv):
        with pytest.raises(NotImplementedError):
            macd(sample_ohlcv)

    def test_kdj_not_implemented(self, sample_ohlcv):
        with pytest.raises(NotImplementedError):
            kdj(sample_ohlcv)

    def test_bollinger_not_implemented(self, sample_ohlcv):
        with pytest.raises(NotImplementedError):
            bollinger(sample_ohlcv, period=20)

    def test_support_resistance_not_implemented(self, sample_ohlcv):
        with pytest.raises(NotImplementedError):
            support_resistance(sample_ohlcv, lookback=60)

    def test_volume_price_analysis_not_implemented(self, sample_ohlcv):
        with pytest.raises(NotImplementedError):
            volume_price_analysis(sample_ohlcv)

    def test_composite_technical_score_not_implemented(self, sample_ohlcv):
        with pytest.raises(NotImplementedError):
            composite_technical_score(sample_ohlcv)
