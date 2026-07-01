"""Tests for davis_analyzer.holder_concentration — chip-concentration engine."""

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from davis_analyzer.holder_concentration import analyze_holder_concentration

TODAY = date(2026, 7, 1)


def _client_returning(df: pd.DataFrame) -> MagicMock:
    c = MagicMock()
    c.get_stk_holdernumber.return_value = df
    return c


def _row(end_date: str, holder_num: int) -> dict:
    return {"ts_code": "x", "ann_date": end_date, "end_date": end_date, "holder_num": holder_num}


class TestAnalyzeHolderConcentration:
    def test_no_data_returns_none(self):
        c = _client_returning(pd.DataFrame())
        assert analyze_holder_concentration(c, "x", today=TODAY) is None

    def test_declining_counts_is_bullish(self):
        # 50000 → 42500 over 4 periods = -15% (FULL_DECLINE) → score 100
        df = pd.DataFrame(
            [
                _row("20250331", 50000),
                _row("20250630", 48000),
                _row("20250930", 45000),
                _row("20251231", 42500),
            ]
        )
        c = _client_returning(df)
        hc = analyze_holder_concentration(c, "x", today=TODAY)
        assert hc is not None
        assert hc.trend == "集中(动能增强)"
        assert hc.concentration_score == pytest.approx(100.0, abs=1.0)
        assert hc.latest_chg_pct is not None
        assert hc.latest_chg_pct < 0  # last period declined

    def test_rising_counts_is_bearish(self):
        df = pd.DataFrame(
            [
                _row("20250331", 40000),
                _row("20250630", 42000),
                _row("20250930", 45000),
                _row("20251231", 50000),
            ]
        )
        c = _client_returning(df)
        hc = analyze_holder_concentration(c, "x", today=TODAY)
        assert hc is not None
        assert hc.trend == "分散(动能减弱)"
        assert hc.concentration_score < 50.0

    def test_insufficient_periods(self):
        # only 1 valid period → 数据不足, neutral 50
        df = pd.DataFrame([_row("20251231", 50000)])
        c = _client_returning(df)
        hc = analyze_holder_concentration(c, "x", today=TODAY)
        assert hc is not None
        assert hc.trend == "数据不足"
        assert hc.concentration_score == 50.0
        assert hc.latest_chg_pct is None

    def test_dedups_same_period(self):
        df = pd.DataFrame(
            [
                _row("20250331", 50000),
                _row("20250331", 49000),  # dup end_date, keep last
                _row("20250630", 47000),
            ]
        )
        c = _client_returning(df)
        hc = analyze_holder_concentration(c, "x", today=TODAY)
        assert hc is not None
        assert hc.holder_counts[0] == 49000  # dedup keep="last"

    def test_client_error_returns_none(self):
        c = MagicMock()
        c.get_stk_holdernumber.side_effect = RuntimeError("api down")
        assert analyze_holder_concentration(c, "x", today=TODAY) is None

    def test_partial_decline_scores_between(self):
        # -7.5% over window (half of FULL_DECLINE) → score 75
        df = pd.DataFrame(
            [
                _row("20250331", 40000),
                _row("20250630", 38500),
                _row("20250930", 37500),
                _row("20251231", 37000),
            ]
        )
        c = _client_returning(df)
        hc = analyze_holder_concentration(c, "x", today=TODAY)
        assert hc is not None
        # (40000-37000)/40000 = 0.075 → score = 50 + (0.075/0.15)*50 = 75
        assert hc.concentration_score == pytest.approx(75.0, abs=1.0)
