"""Comprehensive TDD tests for check_trailing_stop signal."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockhot.sell_monitor.signals import check_trailing_stop


def _make_ohlcv(days: int = 30, base_close: float = 10.0) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=days)
    rng = np.random.default_rng(42)
    close = base_close + rng.standard_normal(days).cumsum() * 0.1
    low = close - rng.uniform(0.1, 0.3, size=days)
    high = close + rng.uniform(0.1, 0.3, size=days)
    op = close + rng.uniform(-0.1, 0.1, size=days)
    volume = rng.integers(1_000_000, 5_000_000, size=days)
    return pd.DataFrame(
        {"open": op, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


def _make_holding(current_price: float = 10.0) -> dict:
    return {
        "code": "000001",
        "name": "TestStock",
        "current_price": current_price,
    }


class TestTrailingStop:
    def test_triggered_when_below_trailing_stop(self):
        ohlcv = _make_ohlcv(30, base_close=10.0)
        ma20 = float(ohlcv["close"].rolling(20).mean().iloc[-1])
        recent_low = float(ohlcv["low"].tail(20).min())
        trailing_stop = max(ma20, recent_low) * 0.98

        holding = _make_holding(current_price=trailing_stop - 1.0)
        result = check_trailing_stop(holding, ohlcv)

        assert result["triggered"] is True

    def test_not_triggered_when_above_trailing_stop(self):
        ohlcv = _make_ohlcv(30, base_close=10.0)
        ma20 = float(ohlcv["close"].rolling(20).mean().iloc[-1])
        recent_low = float(ohlcv["low"].tail(20).min())
        trailing_stop = max(ma20, recent_low) * 0.98

        holding = _make_holding(current_price=trailing_stop + 5.0)
        result = check_trailing_stop(holding, ohlcv)

        assert result["triggered"] is False

    def test_ma20_dominant_trails_up(self):
        dates = pd.bdate_range("2024-01-01", periods=30)
        closes = list(range(20, 50))
        lows = [c - 0.1 for c in closes]
        highs = [c + 0.5 for c in closes]
        ohlcv = pd.DataFrame(
            {
                "open": closes,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": [1_000_000] * 30,
            },
            index=dates,
        )

        ma20 = float(ohlcv["close"].rolling(20).mean().iloc[-1])
        recent_low = float(ohlcv["low"].tail(20).min())
        trailing_stop = max(ma20, recent_low) * 0.98

        holding = _make_holding(current_price=trailing_stop + 1.0)
        result = check_trailing_stop(holding, ohlcv)

        assert result["details"]["trailing_stop"] == pytest.approx(trailing_stop, abs=0.01)
        assert ma20 > recent_low

    def test_recent_low_fallback(self):
        dates = pd.bdate_range("2024-01-01", periods=30)
        closes = [50.0] * 10 + [5.0] + [50.0] * 19
        lows = [c - 0.1 for c in closes]
        highs = [c + 0.5 for c in closes]
        ohlcv = pd.DataFrame(
            {
                "open": closes,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": [1_000_000] * 30,
            },
            index=dates,
        )

        ma20 = float(ohlcv["close"].rolling(20).mean().iloc[-1])
        recent_low = float(ohlcv["low"].tail(20).min())
        trailing_stop = max(ma20, recent_low) * 0.98

        holding = _make_holding(current_price=trailing_stop + 1.0)
        result = check_trailing_stop(holding, ohlcv)

        assert recent_low < ma20
        assert result["details"]["trailing_stop"] == pytest.approx(ma20 * 0.98, abs=0.01)

    def test_2pct_buffer_applied(self):
        ohlcv = _make_ohlcv(30, base_close=10.0)
        ma20 = float(ohlcv["close"].rolling(20).mean().iloc[-1])
        recent_low = float(ohlcv["low"].tail(20).min())
        expected_stop = max(ma20, recent_low) * 0.98

        holding = _make_holding(current_price=100.0)
        result = check_trailing_stop(holding, ohlcv)

        assert result["details"]["trailing_stop"] == pytest.approx(expected_stop, abs=0.05)

    def test_insufficient_data_returns_not_triggered(self):
        dates = pd.bdate_range("2024-01-01", periods=10)
        closes = [10.0] * 10
        ohlcv = pd.DataFrame(
            {
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [1_000_000] * 10,
            },
            index=dates,
        )
        holding = _make_holding(current_price=5.0)
        result = check_trailing_stop(holding, ohlcv)

        assert result["triggered"] is False
        assert result["details"]["error"] == "insufficient_data"

    def test_signal_type_always_trailing_stop(self):
        ohlcv = _make_ohlcv(30, base_close=10.0)
        holding = _make_holding(current_price=100.0)
        result = check_trailing_stop(holding, ohlcv)

        assert result["signal_type"] == "trailing_stop"

    def test_details_has_all_keys(self):
        ohlcv = _make_ohlcv(30, base_close=10.0)
        holding = _make_holding(current_price=100.0)
        result = check_trailing_stop(holding, ohlcv)

        details = result["details"]
        assert "ma20" in details
        assert "recent_low" in details
        assert "trailing_stop" in details

    def test_returned_types_are_native(self):
        ohlcv = _make_ohlcv(30, base_close=10.0)
        holding = _make_holding(current_price=100.0)
        result = check_trailing_stop(holding, ohlcv)

        assert type(result["triggered"]) is bool

    def test_triggered_at_boundary(self):
        ohlcv = _make_ohlcv(30, base_close=10.0)
        ma20 = float(ohlcv["close"].rolling(20).mean().iloc[-1])
        recent_low = float(ohlcv["low"].tail(20).min())
        trailing_stop = max(ma20, recent_low) * 0.98

        holding = _make_holding(current_price=trailing_stop)
        result = check_trailing_stop(holding, ohlcv)

        assert result["triggered"] is True
