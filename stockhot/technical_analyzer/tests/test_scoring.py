"""TDD tests for composite technical state scoring."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockhot.technical_analyzer.scoring import composite_technical_score


def _make_bullish_df(days: int = 70) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=days)
    rng = np.random.default_rng(42)
    closes = 10.0 + np.cumsum(np.abs(rng.standard_normal(days)) * 0.3)
    highs = closes + rng.uniform(0.1, 0.5, days)
    lows = closes - rng.uniform(0.1, 0.5, days)
    volumes = np.maximum(1_000_000, rng.integers(1_000_000, 5_000_000, days))
    volumes[-1] = int(volumes[-1] * 1.5)
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=dates,
    )


def _make_bearish_df(days: int = 70) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=days)
    rng = np.random.default_rng(99)
    closes = 50.0 - np.cumsum(np.abs(rng.standard_normal(days)) * 0.3)
    closes = np.maximum(closes, 1.0)
    highs = closes + rng.uniform(0.1, 0.5, days)
    lows = closes - rng.uniform(0.1, 0.5, days)
    volumes = np.maximum(1_000_000, rng.integers(1_000_000, 5_000_000, days))
    volumes[-1] = int(volumes[-1] * 0.5)
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=dates,
    )


def _make_sideways_df(days: int = 70) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=days)
    rng = np.random.default_rng(7)
    noise = rng.standard_normal(days) * 0.5
    closes = 20.0 + noise
    highs = closes + 0.3
    lows = closes - 0.3
    volumes = rng.integers(1_000_000, 2_000_000, days)
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=dates,
    )


class TestCompositeTechnicalScore:
    def test_bull_market_strong(self):
        df = _make_bullish_df()
        result = composite_technical_score(df)

        assert result["score"] > 65.0
        assert result["state"] == "强势"

    def test_bear_market_weak(self):
        df = _make_bearish_df()
        result = composite_technical_score(df)

        assert result["score"] < 35.0
        assert result["state"] == "弱势"

    def test_sideways_oscillating(self):
        df = _make_sideways_df()
        result = composite_technical_score(df)

        assert 35.0 <= result["score"] <= 65.0
        assert result["state"] == "震荡"

    def test_score_range_bull(self):
        df = _make_bullish_df()
        result = composite_technical_score(df)

        assert 0.0 <= result["score"] <= 100.0

    def test_score_range_bear(self):
        df = _make_bearish_df()
        result = composite_technical_score(df)

        assert 0.0 <= result["score"] <= 100.0

    def test_signals_list_has_six_entries(self):
        df = _make_bullish_df()
        result = composite_technical_score(df)

        assert len(result["signals"]) >= 6
        for sig in result["signals"]:
            assert "name" in sig
            assert "weight" in sig
            assert "hit" in sig

    def test_weights_sum_to_one(self):
        df = _make_sideways_df()
        result = composite_technical_score(df)

        total_weight = sum(s["weight"] for s in result["signals"])
        assert total_weight == pytest.approx(1.0, abs=0.001)

    def test_nan_handling_no_crash(self):
        dates = pd.bdate_range("2024-01-01", periods=5)
        closes = [10.0, 11.0, 12.0, 11.0, 10.0]
        volumes = [1_000_000] * 5
        df = pd.DataFrame(
            {"open": closes, "high": closes, "low": closes, "close": closes, "volume": volumes},
            index=dates,
        )
        result = composite_technical_score(df)

        assert 0.0 <= result["score"] <= 100.0
        assert result["state"] in ("强势", "震荡", "弱势")

    def test_nan_indicators_scored_neutral(self):
        dates = pd.bdate_range("2024-01-01", periods=10)
        closes = [10.0] * 10
        volumes = [1_000_000] * 10
        df = pd.DataFrame(
            {"open": closes, "high": closes, "low": closes, "close": closes, "volume": volumes},
            index=dates,
        )
        result = composite_technical_score(df)

        assert 0.0 <= result["score"] <= 100.0

    def test_signal_names_present(self):
        df = _make_bullish_df()
        result = composite_technical_score(df)

        names = {s["name"] for s in result["signals"]}
        assert "ma_arrangement" in names
        assert "rsi" in names
        assert "macd" in names
        assert "kdj" in names
        assert "bollinger" in names
        assert "volume_price" in names

    def test_signal_hit_is_bool(self):
        df = _make_bullish_df()
        result = composite_technical_score(df)

        for sig in result["signals"]:
            assert isinstance(sig["hit"], bool)

    def test_bull_signals_majority_hit(self):
        df = _make_bullish_df()
        result = composite_technical_score(df)

        hits = sum(1 for s in result["signals"] if s["hit"])
        assert hits >= 4

    def test_bear_signals_minority_hit(self):
        df = _make_bearish_df()
        result = composite_technical_score(df)

        hits = sum(1 for s in result["signals"] if s["hit"])
        assert hits <= 2

    def test_score_is_float(self):
        df = _make_sideways_df()
        result = composite_technical_score(df)

        assert isinstance(result["score"], float)

    def test_state_is_str(self):
        df = _make_sideways_df()
        result = composite_technical_score(df)

        assert isinstance(result["state"], str)

    def test_returns_three_keys(self):
        df = _make_sideways_df()
        result = composite_technical_score(df)

        assert "state" in result
        assert "score" in result
        assert "signals" in result

    def test_single_row_no_crash(self):
        dates = pd.bdate_range("2024-01-01", periods=1)
        df = pd.DataFrame(
            {"open": [10.0], "high": [10.0], "low": [10.0], "close": [10.0], "volume": [100]},
            index=dates,
        )
        result = composite_technical_score(df)

        assert 0.0 <= result["score"] <= 100.0
