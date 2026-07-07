"""Tests for davis_analyzer.momentum — price-momentum + RS engine."""

from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from davis_analyzer.momentum import (
    _absolute_score,
    _annualised_return,
    _return_to_score,
    _window_returns,
    analyze_momentum,
    analyze_momentum_batch,
    compute_rs_percentile,
)
from davis_analyzer.types import StockInfo

TODAY = date(2026, 7, 1)


def _price_df(n_days: int, daily_growth: float = 0.0, base: float = 10.0) -> pd.DataFrame:
    """Build an ascending-date daily price series with constant adj_factor=1.

    ``daily_growth`` is the per-day fractional growth (e.g. 0.002 = 0.2%/day).
    """
    dates = pd.bdate_range(end=TODAY, periods=n_days)
    trade_dates = dates.strftime("%Y%m%d").astype(int)
    closes = [base * ((1 + daily_growth) ** i) for i in range(n_days)]
    return pd.DataFrame(
        {
            "ts_code": "x",
            "trade_date": trade_dates,
            "close": closes,
            "adj_factor": 1.0,
        }
    )


def _client_returning(df: pd.DataFrame) -> MagicMock:
    c = MagicMock()
    c.get_daily_prices.return_value = df
    return c


def _si(ts_code: str = "x", industry: str = "电子") -> StockInfo:
    return StockInfo(
        ts_code=ts_code, name="x", industry=industry, list_status="L", is_cyclical=False
    )


# ── _annualised_return / _return_to_score ─────────────────────────────


class TestAnnualisedReturn:
    def test_positive_growth(self):
        # 10 → 12.5 over 250 days = +25% over 0.685y → ~38.5% annualised
        r = _annualised_return(10.0, 12.5, 250)
        assert r == pytest.approx(38.5, abs=0.5)

    def test_zero_when_nonpositive_base(self):
        assert _annualised_return(0.0, 10.0, 250) == 0.0
        assert _annualised_return(10.0, 10.0, 0) == 0.0

    def test_total_loss_capped(self):
        # -100%+ should not blow up
        r = _annualised_return(10.0, 0.0, 250)
        assert r == -100.0


class TestReturnToScore:
    def test_zero_return_neutral(self):
        assert _return_to_score(0.0) == 50.0

    def test_high_return_caps_100(self):
        assert _return_to_score(120.0) == 100.0

    def test_sharp_drawdown_zero(self):
        assert _return_to_score(-120.0) == 0.0

    def test_symmetric(self):
        # ±FULL gives 100/0
        assert _return_to_score(60.0) == 100.0
        assert _return_to_score(-60.0) == 0.0


# ── _window_returns ───────────────────────────────────────────────────


class TestWindowReturns:
    def test_too_few_prices_returns_empty(self):
        s = pd.Series([10.0, 11.0])
        dates = pd.Series([20260101, 20260102])
        assert _window_returns(s, dates) == {}

    def test_uptrend_yields_positive_returns(self):
        df = _price_df(180, daily_growth=0.002)
        returns = _window_returns(df["close"], df["trade_date"])
        assert len(returns) > 0
        # 60d window should be present and positive
        if 60 in returns:
            assert returns[60] > 0

    def test_downtrend_yields_negative_returns(self):
        df = _price_df(180, daily_growth=-0.002, base=10.0)
        returns = _window_returns(df["close"], df["trade_date"])
        if 60 in returns:
            assert returns[60] < 0


# ── _absolute_score ───────────────────────────────────────────────────


class TestAbsoluteScore:
    def test_empty_returns_neutral(self):
        assert _absolute_score({}) == 50.0

    def test_positive_returns_score_above_50(self):
        wr = {60: 30.0, 120: 25.0, 250: 20.0}
        assert _absolute_score(wr) > 50.0

    def test_negative_returns_score_below_50(self):
        wr = {60: -30.0, 120: -25.0, 250: -20.0}
        assert _absolute_score(wr) < 50.0


# ── compute_rs_percentile ─────────────────────────────────────────────


class TestComputeRsPercentile:
    def test_ranks_within_industry(self):
        returns = {"a": 10.0, "b": 20.0, "c": 5.0}
        infos = {"a": _si("a"), "b": _si("b"), "c": _si("c")}
        rs = compute_rs_percentile(returns, infos)
        # b is strongest → 100, a middle, c weakest
        assert rs["b"] == 100.0
        assert rs["c"] < rs["a"] < rs["b"]

    def test_isolated_industry_returns_none(self):
        returns = {"a": 10.0}
        infos = {"a": _si("a")}
        rs = compute_rs_percentile(returns, infos)
        assert rs["a"] is None

    def test_missing_stock_info_returns_none(self):
        returns = {"a": 10.0}
        rs = compute_rs_percentile(returns, {})
        assert rs["a"] is None


# ── analyze_momentum ──────────────────────────────────────────────────


class TestAnalyzeMomentum:
    def test_no_data_returns_none(self):
        c = _client_returning(pd.DataFrame())
        assert analyze_momentum(c, "x", today=TODAY) is None

    def test_client_error_returns_none(self):
        c = MagicMock()
        c.get_daily_prices.side_effect = RuntimeError("api down")
        assert analyze_momentum(c, "x", today=TODAY) is None

    def test_uptrend_scores_high(self):
        df = _price_df(180, daily_growth=0.003)  # strong uptrend
        c = _client_returning(df)
        sig = analyze_momentum(c, "x", today=TODAY)
        assert sig is not None
        assert sig.data_sufficient is True
        assert sig.absolute_momentum_score > 50.0
        assert sig.rs_percentile is None  # not populated in single-stock path

    def test_downtrend_scores_low(self):
        df = _price_df(180, daily_growth=-0.003)
        c = _client_returning(df)
        sig = analyze_momentum(c, "x", today=TODAY)
        assert sig is not None
        assert sig.absolute_momentum_score < 50.0


# ── analyze_momentum_batch ────────────────────────────────────────────


class TestAnalyzeMomentumBatch:
    def test_rs_blended_in_batch(self):
        # Two stocks same industry, one uptrend one downtrend.
        up_df = _price_df(180, daily_growth=0.003)
        down_df = _price_df(180, daily_growth=-0.003)

        client = MagicMock()

        def fake_get(ts_code, start, end):
            return up_df if ts_code == "up" else down_df

        client.get_daily_prices.side_effect = fake_get
        infos = {"up": _si("up"), "down": _si("down")}
        signals = analyze_momentum_batch(client, infos, today=TODAY)

        assert "up" in signals and "down" in signals
        # With 2 peers the rank-percentile gives the strongest 100 and the
        # weaker 50 (1 of 2 values at-or-below itself). The RS still orders
        # them correctly: up > down.
        assert signals["up"].rs_percentile == 100.0
        assert signals["down"].rs_percentile == 50.0
        assert signals["up"].rs_percentile > signals["down"].rs_percentile
        # momentum_score should reflect the blend
        assert signals["up"].momentum_score > signals["down"].momentum_score

    def test_missing_stock_skipped(self):
        client = MagicMock()
        client.get_daily_prices.return_value = pd.DataFrame()
        infos = {"x": _si("x")}
        signals = analyze_momentum_batch(client, infos, today=TODAY)
        assert signals == {}
