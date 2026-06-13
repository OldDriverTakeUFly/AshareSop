from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from davis_analyzer.financial_fetcher import (
    _calculate_yoy_growth,
    _safe_float,
    fetch_batch_financial,
    fetch_financial_data,
)
from davis_analyzer.types import FinancialData


class TestSafeFloat:
    def test_normal_float(self):
        assert _safe_float(3.14) == 3.14

    def test_integer(self):
        assert _safe_float(42) == 42.0

    def test_none_returns_zero(self):
        assert _safe_float(None) == 0.0

    def test_nan_returns_zero(self):
        assert _safe_float(float("nan")) == 0.0

    def test_inf_returns_zero(self):
        assert _safe_float(float("inf")) == 0.0

    def test_string_number(self):
        assert _safe_float("3.14") == 3.14

    def test_invalid_string(self):
        assert _safe_float("abc") == 0.0


class TestCalculateYoyGrowth:
    def test_yoy_revenue_growth_25_percent(self):
        df = pd.DataFrame({
            "report_period": [
                "20220630", "20220930", "20221231", "20230331",
                "20230630", "20230930", "20231231", "20240331",
            ],
            "total_revenue": [400, 500, 600, 80, 800, 900, 1000, 100],
        })
        result = _calculate_yoy_growth(df, "total_revenue")
        growth_2024q1 = result.iloc[7]
        assert growth_2024q1 == pytest.approx(0.25, abs=0.001)

    def test_yoy_profit_growth(self):
        df = pd.DataFrame({
            "report_period": [
                "20220630", "20220930", "20221231", "20230331",
                "20230630", "20230930", "20231231", "20240331",
            ],
            "n_income": [200, 250, 300, 80, 400, 450, 500, 120],
        })
        result = _calculate_yoy_growth(df, "n_income")
        growth_2024q1 = result.iloc[7]
        assert growth_2024q1 == pytest.approx(0.5, abs=0.001)

    def test_yoy_with_zero_base(self):
        df = pd.DataFrame({
            "report_period": [
                "20220630", "20220930", "20221231", "20230331",
                "20240331",
            ],
            "total_revenue": [100, 200, 300, 0, 100],
        })
        result = _calculate_yoy_growth(df, "total_revenue")
        assert result.iloc[4] == 0.0

    def test_yoy_fewer_than_5_periods(self):
        df = pd.DataFrame({
            "report_period": ["20230331", "20230630", "20240331"],
            "total_revenue": [80, 90, 100],
        })
        result = _calculate_yoy_growth(df, "total_revenue")
        assert result.iloc[2] == 0.0

    def test_yoy_missing_column(self):
        df = pd.DataFrame({
            "report_period": ["20240331"],
            "other_col": [100],
        })
        result = _calculate_yoy_growth(df, "total_revenue")
        assert result.iloc[0] == 0.0


class TestFetchFinancialData:
    def test_returns_financial_data_objects(self, mock_client_with_data):
        results = fetch_financial_data(mock_client_with_data, "000001.SZ")

        assert len(results) == 8
        assert all(isinstance(r, FinancialData) for r in results)

    def test_sorted_descending_by_period(self, mock_client_with_data):
        results = fetch_financial_data(mock_client_with_data, "000001.SZ")

        periods = [r.report_period for r in results]
        assert periods == sorted(periods, reverse=True)

    def test_fields_populated_correctly(self, mock_client_with_data):
        results = fetch_financial_data(mock_client_with_data, "000001.SZ")

        latest = results[0]
        assert latest.ts_code == "000001.SZ"
        assert latest.report_period == "20240331"
        assert latest.revenue == 1000.0
        assert latest.net_profit == 100.0
        assert latest.eps == 0.10
        assert latest.roe == 1.0
        assert latest.operating_cf == 150.0
        assert latest.total_debt == 5000.0
        assert latest.total_assets == 10000.0

    def test_yoy_revenue_growth_calculated(self, mock_client_with_data):
        results = fetch_financial_data(mock_client_with_data, "000001.SZ")

        latest = results[0]
        assert latest.report_period == "20240331"
        expected_growth = (1000 - 800) / 800
        assert latest.yoy_revenue_growth == pytest.approx(expected_growth, abs=0.001)

    def test_yoy_profit_growth_calculated(self, mock_client_with_data):
        results = fetch_financial_data(mock_client_with_data, "000001.SZ")

        latest = results[0]
        expected_growth = (100 - 80) / 80
        assert latest.yoy_profit_growth == pytest.approx(expected_growth, abs=0.001)

    def test_empty_data_returns_empty_list(self, mock_client):
        mock_client.get_income.return_value = pd.DataFrame()
        mock_client.get_balancesheet.return_value = pd.DataFrame()
        mock_client.get_cashflow.return_value = pd.DataFrame()
        mock_client.get_fina_indicator.return_value = pd.DataFrame()

        results = fetch_financial_data(mock_client, "000001.SZ")

        assert results == []

    def test_nan_values_replaced_with_zero(self, mock_client):
        mock_client.get_income.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "end_date": ["20240331"],
            "total_revenue": [np.nan],
            "n_income": [100],
            "n_income_attr_p": [95],
        })
        mock_client.get_balancesheet.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "end_date": ["20240331"],
            "total_assets": [10000],
            "total_liab": [np.nan],
        })
        mock_client.get_cashflow.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "end_date": ["20240331"],
            "n_cashflow_act": [150],
        })
        mock_client.get_fina_indicator.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "end_date": ["20240331"],
            "roe": [1.0],
            "eps": [np.nan],
            "dt_eps": [0.09],
            "revenue_ps": [1.0],
        })

        results = fetch_financial_data(mock_client, "000001.SZ")

        assert len(results) == 1
        assert results[0].revenue == 0.0
        assert results[0].total_debt == 0.0
        assert results[0].eps == 0.0
        assert results[0].net_profit == 100.0

    def test_calls_client_with_correct_params(self, mock_client_with_data):
        fetch_financial_data(mock_client_with_data, "000001.SZ", periods=4)

        mock_client_with_data.get_income.assert_called_once()
        call_args = mock_client_with_data.get_income.call_args
        assert call_args[0][0] == "000001.SZ"


class TestFetchBatchFinancial:
    def test_batch_returns_dict(self, mock_client_with_data):
        result = fetch_batch_financial(mock_client_with_data, ["000001.SZ"])

        assert "000001.SZ" in result
        assert len(result["000001.SZ"]) == 8

    def test_batch_skips_failures(self):
        client = MagicMock()
        client.get_income.side_effect = [
            pd.DataFrame({
                "ts_code": ["000001.SZ"],
                "end_date": ["20240331"],
                "total_revenue": [1000],
                "n_income": [100],
                "n_income_attr_p": [95],
            }),
            Exception("API error"),
        ]
        client.get_balancesheet.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "end_date": ["20240331"],
            "total_assets": [10000],
            "total_liab": [5000],
        })
        client.get_cashflow.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "end_date": ["20240331"],
            "n_cashflow_act": [150],
        })
        client.get_fina_indicator.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "end_date": ["20240331"],
            "roe": [1.0],
            "eps": [0.10],
            "dt_eps": [0.09],
            "revenue_ps": [1.0],
        })

        result = fetch_batch_financial(client, ["000001.SZ", "000002.SZ"])

        assert "000001.SZ" in result
        assert "000002.SZ" not in result

    def test_batch_empty_list(self, mock_client):
        result = fetch_batch_financial(mock_client, [])

        assert result == {}

    def test_batch_multiple_successes(self, mock_client):
        df_a = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "end_date": ["20240331"],
            "total_revenue": [1000],
            "n_income": [100],
            "n_income_attr_p": [95],
        })
        df_b = pd.DataFrame({
            "ts_code": ["000002.SZ"],
            "end_date": ["20240331"],
            "total_revenue": [2000],
            "n_income": [200],
            "n_income_attr_p": [190],
        })
        income_dfs = [df_a, df_b]
        balance_df = pd.DataFrame({
            "ts_code": ["TEST"],
            "end_date": ["20240331"],
            "total_assets": [10000],
            "total_liab": [5000],
        })
        cashflow_df = pd.DataFrame({
            "ts_code": ["TEST"],
            "end_date": ["20240331"],
            "n_cashflow_act": [150],
        })
        fina_df = pd.DataFrame({
            "ts_code": ["TEST"],
            "end_date": ["20240331"],
            "roe": [1.0],
            "eps": [0.10],
            "dt_eps": [0.09],
            "revenue_ps": [1.0],
        })

        mock_client.get_income.side_effect = income_dfs
        mock_client.get_balancesheet.return_value = balance_df
        mock_client.get_cashflow.return_value = cashflow_df
        mock_client.get_fina_indicator.return_value = fina_df

        result = fetch_batch_financial(mock_client, ["000001.SZ", "000002.SZ"])

        assert len(result) == 2
        assert "000001.SZ" in result
        assert "000002.SZ" in result
