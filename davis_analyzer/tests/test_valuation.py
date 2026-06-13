"""Tests for davis_analyzer.valuation — PE/PB percentile valuation engine."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from davis_analyzer.types import StockInfo, ValuationData
from davis_analyzer.valuation import (
    batch_valuation,
    calculate_percentile,
    calculate_valuation_score,
    detect_cyclical,
    fetch_valuation_history,
    handle_negative_eps,
)


class TestCalculatePercentile:
    def test_percentile_calculation(self) -> None:
        historical = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        result = calculate_percentile(50, historical)
        assert result == pytest.approx(0.5, abs=0.05)

    def test_percentile_at_min(self) -> None:
        historical = [10, 20, 30, 40, 50]
        result = calculate_percentile(10, historical)
        assert result == pytest.approx(0.2, abs=0.01)

    def test_percentile_at_max(self) -> None:
        historical = [10, 20, 30, 40, 50]
        result = calculate_percentile(50, historical)
        assert result == 1.0

    def test_percentile_above_max(self) -> None:
        historical = [10, 20, 30]
        result = calculate_percentile(999, historical)
        assert result == 1.0

    def test_percentile_below_min(self) -> None:
        historical = [10, 20, 30]
        result = calculate_percentile(1, historical)
        assert result == 0.0

    def test_percentile_empty_array(self) -> None:
        result = calculate_percentile(50.0, [])
        assert result == 0.5

    def test_percentile_single_value_match(self) -> None:
        result = calculate_percentile(42.0, [42.0])
        assert result == 1.0


class TestFetchValuationHistory:
    def test_fetch_parses_dataframe(self) -> None:
        mock_client = MagicMock()
        mock_client.get_daily_basic.return_value = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": ["20240101", "20240102"],
                "pe_ttm": [10.0, 12.0],
                "pb": [1.0, 1.2],
                "ps": [2.0, 2.5],
                "total_mv": [1000.0, 1100.0],
            }
        )

        result = fetch_valuation_history(mock_client, "000001.SZ")

        assert len(result) == 2
        assert isinstance(result[0], ValuationData)
        assert result[0].pe_ttm == 10.0
        assert result[1].pb == 1.2

    def test_fetch_skips_nan_pe(self) -> None:
        mock_client = MagicMock()
        mock_client.get_daily_basic.return_value = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "trade_date": ["20240101", "20240102", "20240103"],
                "pe_ttm": [10.0, np.nan, 12.0],
                "pb": [1.0, 1.2, np.nan],
                "ps": [2.0, 2.5, 3.0],
                "total_mv": [1000.0, 1100.0, 1200.0],
            }
        )

        result = fetch_valuation_history(mock_client, "000001.SZ")

        assert len(result) == 1
        assert result[0].pe_ttm == 10.0

    def test_fetch_empty_dataframe(self) -> None:
        mock_client = MagicMock()
        mock_client.get_daily_basic.return_value = pd.DataFrame()

        result = fetch_valuation_history(mock_client, "000001.SZ")

        assert result == []

    def test_fetch_none_dataframe(self) -> None:
        mock_client = MagicMock()
        mock_client.get_daily_basic.return_value = None

        result = fetch_valuation_history(mock_client, "000001.SZ")

        assert result == []


class TestDetectCyclical:
    def test_steel_is_cyclical(self) -> None:
        assert detect_cyclical("钢铁") is True

    def test_non_ferrous_is_cyclical(self) -> None:
        assert detect_cyclical("有色金属") is True

    def test_coal_is_cyclical(self) -> None:
        assert detect_cyclical("煤炭") is True

    def test_bank_not_cyclical(self) -> None:
        assert detect_cyclical("银行") is False

    def test_pharma_not_cyclical(self) -> None:
        assert detect_cyclical("医药生物") is False

    def test_empty_string_not_cyclical(self) -> None:
        assert detect_cyclical("") is False


class TestHandleNegativeEps:
    def test_negative_pe_triggers_fallback(self) -> None:
        assert handle_negative_eps([10.0, -5.0, 15.0]) is True

    def test_near_zero_pe_triggers_fallback(self) -> None:
        assert handle_negative_eps([10.0, 0.005, 15.0]) is True

    def test_all_positive_pe_no_fallback(self) -> None:
        assert handle_negative_eps([10.0, 15.0, 20.0]) is False

    def test_empty_list_no_fallback(self) -> None:
        assert handle_negative_eps([]) is False

    def test_zero_pe_triggers_fallback(self) -> None:
        assert handle_negative_eps([10.0, 0.0, 15.0]) is True


def _make_valuation_data(
    pe_values: list[float],
    pb_values: list[float],
) -> list[ValuationData]:
    return [
        ValuationData(
            ts_code="000001.SZ",
            trade_date=f"2024010{i+1}",
            pe_ttm=pe,
            pb=pb,
            ps=1.0,
            total_mv=1000.0,
        )
        for i, (pe, pb) in enumerate(zip(pe_values, pb_values))
    ]


class TestCalculateValuationScore:
    def test_valuation_score_normal(self) -> None:
        history = _make_valuation_data(
            pe_values=[20, 30, 40, 50, 60, 70, 80, 90, 100, 10],
            pb_values=[30, 40, 50, 60, 70, 80, 90, 100, 10, 20],
        )

        score, pe_pct, pb_pct = calculate_valuation_score(history, is_cyclical=False)

        expected_score = ((1.0 - pe_pct) * 0.6 + (1.0 - pb_pct) * 0.4) * 100.0
        assert score == pytest.approx(expected_score, abs=0.01)

    def test_valuation_score_cyclical_uses_pb_only(self) -> None:
        history = _make_valuation_data(
            pe_values=[20, 30, 40, 50, 60],
            pb_values=[10, 20, 30, 40, 50],
        )

        score, pe_pct, pb_pct = calculate_valuation_score(history, is_cyclical=True)

        expected_score = (1.0 - pb_pct) * 100.0
        assert score == pytest.approx(expected_score, abs=0.01)

    def test_valuation_score_negative_eps_fallback(self) -> None:
        history = _make_valuation_data(
            pe_values=[-10, 20, 30, 40, 50],
            pb_values=[10, 20, 30, 40, 50],
        )

        score, pe_pct, pb_pct = calculate_valuation_score(history, is_cyclical=False)

        expected_score = (1.0 - pb_pct) * 100.0
        assert score == pytest.approx(expected_score, abs=0.01)

    def test_valuation_score_empty_history(self) -> None:
        score, pe_pct, pb_pct = calculate_valuation_score([], is_cyclical=False)

        assert score == 50.0
        assert pe_pct == 0.5
        assert pb_pct == 0.5

    def test_score_range_0_to_100(self) -> None:
        history_high_pct = _make_valuation_data(
            pe_values=[100, 10, 20, 30, 40, 50],
            pb_values=[100, 10, 20, 30, 40, 50],
        )
        score, _, _ = calculate_valuation_score(history_high_pct, is_cyclical=False)
        assert 0.0 <= score <= 100.0


class TestScoreNormalization:
    def test_low_percentile_high_score(self) -> None:
        low_pb_history = _make_valuation_data(
            pe_values=[5, 20, 30, 40, 50, 60, 70, 80, 90, 100],
            pb_values=[0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0],
        )

        score, _, pb_pct = calculate_valuation_score(low_pb_history, is_cyclical=True)

        assert pb_pct < 0.2
        assert score > 80.0

    def test_high_percentile_low_score(self) -> None:
        high_pb_history = _make_valuation_data(
            pe_values=[100, 20, 30, 40, 50, 60, 70, 80, 90],
            pb_values=[5.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0],
        )

        score, _, pb_pct = calculate_valuation_score(high_pb_history, is_cyclical=True)

        assert pb_pct > 0.8
        assert score < 20.0


class TestBatchValuation:
    def test_batch_processes_multiple_stocks(self) -> None:
        mock_client = MagicMock()
        mock_client.get_daily_basic.return_value = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240101"],
                "pe_ttm": [15.0],
                "pb": [1.5],
                "ps": [2.0],
                "total_mv": [1000.0],
            }
        )

        stocks = [
            StockInfo(
                ts_code="000001.SZ",
                name="TestA",
                industry="银行",
                list_status="L",
                is_cyclical=False,
            ),
            StockInfo(
                ts_code="000002.SZ",
                name="TestB",
                industry="钢铁",
                list_status="L",
                is_cyclical=True,
            ),
        ]

        result = batch_valuation(mock_client, stocks)

        assert "000001.SZ" in result
        assert "000002.SZ" in result
        for ts_code, (score, pe_pct, pb_pct) in result.items():
            assert 0.0 <= score <= 100.0

    def test_batch_skips_failing_stocks(self) -> None:
        mock_client = MagicMock()
        mock_client.get_daily_basic.side_effect = [
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240101"],
                    "pe_ttm": [15.0],
                    "pb": [1.5],
                    "ps": [2.0],
                    "total_mv": [1000.0],
                }
            ),
            Exception("API error"),
        ]

        stocks = [
            StockInfo(
                ts_code="000001.SZ",
                name="Good",
                industry="银行",
                list_status="L",
                is_cyclical=False,
            ),
            StockInfo(
                ts_code="000002.SZ",
                name="Bad",
                industry="银行",
                list_status="L",
                is_cyclical=False,
            ),
        ]

        result = batch_valuation(mock_client, stocks)

        assert "000001.SZ" in result
        assert "000002.SZ" not in result

    def test_batch_empty_list(self) -> None:
        mock_client = MagicMock()
        result = batch_valuation(mock_client, [])
        assert result == {}
