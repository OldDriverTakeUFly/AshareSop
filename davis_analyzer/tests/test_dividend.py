"""Tests for davis_analyzer.dividend — 红利 factor engine."""

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from davis_analyzer.dividend import (
    _consecutive_trailing_years,
    _continuity_score,
    _executed_cash_per_year,
    _yield_score,
    analyze_dividend,
)

TODAY = date(2026, 7, 1)


def _div_row(end_date: str, cash_div: float, div_proc: str = "实施", stk_div: float = 0.0) -> dict:
    return {
        "ts_code": "x",
        "end_date": end_date,
        "ann_date": end_date,
        "div_proc": div_proc,
        "cash_div": cash_div,
        "stk_div": stk_div,
        "ex_date": end_date,
    }


# ── _executed_cash_per_year ───────────────────────────────────────────


class TestExecutedCashPerYear:
    def test_groups_by_year(self):
        df = pd.DataFrame([_div_row("20231231", 0.5), _div_row("20241231", 0.6)])
        out = _executed_cash_per_year(df)
        assert out == {2023: 0.5, 2024: 0.6}

    def test_sums_multiple_rows_same_year(self):
        # interim + final in same year
        df = pd.DataFrame(
            [_div_row("20240630", 0.3), _div_row("20241231", 0.3)]
        )
        out = _executed_cash_per_year(df)
        assert out[2024] == pytest.approx(0.6)

    def test_skips_unexecuted_plans(self):
        df = pd.DataFrame(
            [_div_row("20241231", 0.5, div_proc="预案"), _div_row("20231231", 0.4, div_proc="实施")]
        )
        out = _executed_cash_per_year(df)
        assert out == {2023: 0.4}  # 预案 dropped

    def test_skips_zero_or_negative(self):
        df = pd.DataFrame([_div_row("20241231", 0.0), _div_row("20231231", -0.1)])
        assert _executed_cash_per_year(df) == {}

    def test_empty(self):
        assert _executed_cash_per_year(pd.DataFrame()) == {}


# ── _consecutive_trailing_years ───────────────────────────────────────


class TestConsecutiveTrailingYears:
    def test_full_continuous(self):
        assert _consecutive_trailing_years([2022, 2023, 2024], as_of_year=2024) == 3

    def test_gap_breaks_chain(self):
        # 2024, 2022 (gap at 2023) → only 1 trailing
        assert _consecutive_trailing_years([2022, 2024], as_of_year=2024) == 1

    def test_future_years_ignored(self):
        assert _consecutive_trailing_years([2024, 2025], as_of_year=2024) == 1

    def test_empty(self):
        assert _consecutive_trailing_years([], as_of_year=2024) == 0


# ── _continuity_score / _yield_score ──────────────────────────────────


class TestScores:
    def test_continuity_zero_years_floor(self):
        assert _continuity_score(0, 3) == 10.0

    def test_continuity_saturates_at_lookback(self):
        assert _continuity_score(3, 3) == 100.0

    def test_continuity_partial(self):
        # 2 of 3 years → 10 + 2/3*90 = 70
        assert _continuity_score(2, 3) == pytest.approx(70.0)

    def test_yield_none_floor(self):
        assert _yield_score(None) == 10.0

    def test_yield_saturates(self):
        assert _yield_score(5.0) == 100.0

    def test_yield_partial(self):
        # 2.5% → 10 + 2.5/5*90 = 55
        assert _yield_score(2.5) == pytest.approx(55.0)


# ── analyze_dividend ──────────────────────────────────────────────────


class TestAnalyzeDividend:
    def test_non_payer_returns_floor_signal(self):
        client = MagicMock()
        client.get_dividend.return_value = pd.DataFrame()
        client.get_daily_prices.return_value = pd.DataFrame()
        sig = analyze_dividend(client, "x", today=TODAY)
        assert sig.data_sufficient is False
        assert sig.dividend_score == 10.0
        assert sig.consecutive_years == 0
        assert sig.latest_yield_pct is None

    def test_stable_payer_scores_high(self):
        # 3 consecutive years, 5% yield
        div_df = pd.DataFrame(
            [
                _div_row("20231231", 0.5),
                _div_row("20241231", 0.5),
                _div_row("20251231", 0.5),
            ]
        )
        price_df = pd.DataFrame(
            [{"ts_code": "x", "trade_date": "20260701", "close": 10.0, "adj_factor": 1.0}]
        )
        client = MagicMock()
        client.get_dividend.return_value = div_df
        client.get_daily_prices.return_value = price_df
        sig = analyze_dividend(client, "x", today=TODAY)
        # consecutive 3 → continuity 100; yield 0.5/10 = 5% → yield 100
        assert sig.consecutive_years == 3
        assert sig.latest_yield_pct == pytest.approx(5.0)
        assert sig.dividend_score == 100.0

    def test_yield_calculation_uses_latest_payout_year(self):
        # Only 2024 payout, price 10 → 0.5/10 = 5%
        div_df = pd.DataFrame([_div_row("20241231", 0.5)])
        price_df = pd.DataFrame(
            [{"ts_code": "x", "trade_date": "20260701", "close": 10.0, "adj_factor": 1.0}]
        )
        client = MagicMock()
        client.get_dividend.return_value = div_df
        client.get_daily_prices.return_value = price_df
        sig = analyze_dividend(client, "x", today=TODAY)
        assert sig.latest_yield_pct == pytest.approx(5.0)

    def test_dividend_fetch_error_floors_score(self):
        client = MagicMock()
        client.get_dividend.side_effect = RuntimeError("api down")
        client.get_daily_prices.return_value = pd.DataFrame()
        sig = analyze_dividend(client, "x", today=TODAY)
        # error → treated as non-payer → floor
        assert sig.dividend_score == 10.0

    def test_skips_plans_only_counts_executed(self):
        div_df = pd.DataFrame(
            [
                _div_row("20241231", 0.5, div_proc="预案"),
                _div_row("20231231", 0.4, div_proc="实施"),
            ]
        )
        client = MagicMock()
        client.get_dividend.return_value = div_df
        client.get_daily_prices.return_value = pd.DataFrame(
            [{"ts_code": "x", "trade_date": "20260701", "close": 10.0, "adj_factor": 1.0}]
        )
        sig = analyze_dividend(client, "x", today=TODAY)
        # only 2023 executed → 1 trailing year as of 2026 (gap 2024,2025)
        assert sig.consecutive_years == 1
