from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from davis_analyzer.financial_fetcher import (
    _calculate_yoy_growth,
    _compute_date_range,
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

    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_nan_returns_zero(self):
        assert _safe_float(float("nan")) == 0.0

    def test_inf_returns_zero(self):
        assert _safe_float(float("inf")) == 0.0

    def test_string_number(self):
        assert _safe_float("3.14") == 3.14

    def test_invalid_string(self):
        # Unparseable garbage is NOT a "skip period" sentinel, so it falls
        # back to 0.0 rather than propagating None into downstream division.
        assert _safe_float("abc") == 0.0

    def test_garbage_never_returns_none(self):
        """Regression: only ``None`` input may return ``None``. Any other
        unparseable value must yield ``0.0`` so downstream code dividing by a
        coerced field never hits ``TypeError: NoneType``."""
        for bad in ["abc", object(), float("nan"), ""]:
            result = _safe_float(bad)
            assert result is not None, f"_safe_float({bad!r}) returned None"
            assert isinstance(result, float)


class TestCalculateYoyGrowth:
    def test_yoy_revenue_growth_25_percent(self):
        df = pd.DataFrame(
            {
                "report_period": [
                    "20220630",
                    "20220930",
                    "20221231",
                    "20230331",
                    "20230630",
                    "20230930",
                    "20231231",
                    "20240331",
                ],
                "total_revenue": [400, 500, 600, 80, 800, 900, 1000, 100],
            }
        )
        result = _calculate_yoy_growth(df, "total_revenue")
        growth_2024q1 = result.iloc[7]
        assert growth_2024q1 == pytest.approx(0.25, abs=0.001)

    def test_yoy_profit_growth(self):
        df = pd.DataFrame(
            {
                "report_period": [
                    "20220630",
                    "20220930",
                    "20221231",
                    "20230331",
                    "20230630",
                    "20230930",
                    "20231231",
                    "20240331",
                ],
                "n_income": [200, 250, 300, 80, 400, 450, 500, 120],
            }
        )
        result = _calculate_yoy_growth(df, "n_income")
        growth_2024q1 = result.iloc[7]
        assert growth_2024q1 == pytest.approx(0.5, abs=0.001)

    def test_yoy_with_zero_base(self):
        df = pd.DataFrame(
            {
                "report_period": [
                    "20220630",
                    "20220930",
                    "20221231",
                    "20230331",
                    "20240331",
                ],
                "total_revenue": [100, 200, 300, 0, 100],
            }
        )
        result = _calculate_yoy_growth(df, "total_revenue")
        assert result.iloc[4] == 0.0

    def test_yoy_fewer_than_5_periods(self):
        df = pd.DataFrame(
            {
                "report_period": ["20230331", "20230630", "20240331"],
                "total_revenue": [80, 90, 100],
            }
        )
        result = _calculate_yoy_growth(df, "total_revenue")
        assert result.iloc[2] is None

    def test_yoy_missing_column(self):
        df = pd.DataFrame(
            {
                "report_period": ["20240331"],
                "other_col": [100],
            }
        )
        result = _calculate_yoy_growth(df, "total_revenue")
        assert result.iloc[0] is None


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
        mock_client.get_income.return_value = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20240331"],
                "total_revenue": [np.nan],
                "n_income": [100],
                "n_income_attr_p": [95],
            }
        )
        mock_client.get_balancesheet.return_value = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20240331"],
                "total_assets": [10000],
                "total_liab": [np.nan],
            }
        )
        mock_client.get_cashflow.return_value = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20240331"],
                "n_cashflow_act": [150],
            }
        )
        mock_client.get_fina_indicator.return_value = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20240331"],
                "roe": [1.0],
                "eps": [np.nan],
                "dt_eps": [0.09],
                "revenue_ps": [1.0],
            }
        )

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
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20240331"],
                    "total_revenue": [1000],
                    "n_income": [100],
                    "n_income_attr_p": [95],
                }
            ),
            Exception("API error"),
        ]
        client.get_balancesheet.return_value = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20240331"],
                "total_assets": [10000],
                "total_liab": [5000],
            }
        )
        client.get_cashflow.return_value = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20240331"],
                "n_cashflow_act": [150],
            }
        )
        client.get_fina_indicator.return_value = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20240331"],
                "roe": [1.0],
                "eps": [0.10],
                "dt_eps": [0.09],
                "revenue_ps": [1.0],
            }
        )

        result = fetch_batch_financial(client, ["000001.SZ", "000002.SZ"])

        assert "000001.SZ" in result
        assert "000002.SZ" not in result

    def test_batch_empty_list(self, mock_client):
        result = fetch_batch_financial(mock_client, [])

        assert result == {}

    def test_batch_multiple_successes(self, mock_client):
        df_a = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20240331"],
                "total_revenue": [1000],
                "n_income": [100],
                "n_income_attr_p": [95],
            }
        )
        df_b = pd.DataFrame(
            {
                "ts_code": ["000002.SZ"],
                "end_date": ["20240331"],
                "total_revenue": [2000],
                "n_income": [200],
                "n_income_attr_p": [190],
            }
        )
        income_dfs = [df_a, df_b]
        balance_df = pd.DataFrame(
            {
                "ts_code": ["TEST"],
                "end_date": ["20240331"],
                "total_assets": [10000],
                "total_liab": [5000],
            }
        )
        cashflow_df = pd.DataFrame(
            {
                "ts_code": ["TEST"],
                "end_date": ["20240331"],
                "n_cashflow_act": [150],
            }
        )
        fina_df = pd.DataFrame(
            {
                "ts_code": ["TEST"],
                "end_date": ["20240331"],
                "roe": [1.0],
                "eps": [0.10],
                "dt_eps": [0.09],
                "revenue_ps": [1.0],
            }
        )

        mock_client.get_income.side_effect = income_dfs
        mock_client.get_balancesheet.return_value = balance_df
        mock_client.get_cashflow.return_value = cashflow_df
        mock_client.get_fina_indicator.return_value = fina_df

        result = fetch_batch_financial(mock_client, ["000001.SZ", "000002.SZ"])

        assert len(result) == 2
        assert "000001.SZ" in result
        assert "000002.SZ" in result


# ──────────────────────────── Point-in-time (ann_date) ────────────────────────────


def _client_with_ann_dates(ann_dates: list[str]) -> MagicMock:
    """Build a mock client whose financial endpoints return rows with ann_date.

    Each row spans 8 quarters (2022Q2 → 2024Q1); *ann_dates* (length 8, most
    recent first) supply the disclosure date for each quarter.
    """
    periods = [
        "20240331", "20231231", "20230930", "20230630",
        "20230331", "20221231", "20220930", "20220630",
    ]
    client = MagicMock()
    client.get_income.return_value = pd.DataFrame({
        "ts_code": ["000001.SZ"] * 8,
        "end_date": periods,
        "ann_date": ann_dates,
        "total_revenue": [1000, 4000, 3000, 2000, 800, 3200, 2400, 1600],
        "n_income": [100, 400, 300, 200, 80, 320, 240, 160],
        "n_income_attr_p": [95, 380, 285, 190, 76, 304, 228, 152],
    })
    client.get_balancesheet.return_value = pd.DataFrame({
        "ts_code": ["000001.SZ"] * 8,
        "end_date": periods,
        "ann_date": ann_dates,
        "total_assets": [10000, 9500, 9000, 8500, 8000, 7500, 7000, 6500],
        "total_liab": [5000, 4750, 4500, 4250, 4000, 3750, 3500, 3250],
    })
    client.get_cashflow.return_value = pd.DataFrame({
        "ts_code": ["000001.SZ"] * 8,
        "end_date": periods,
        "ann_date": ann_dates,
        "n_cashflow_act": [150, 600, 450, 300, 120, 480, 360, 240],
    })
    client.get_fina_indicator.return_value = pd.DataFrame({
        "ts_code": ["000001.SZ"] * 8,
        "end_date": periods,
        "ann_date": ann_dates,
        "roe": [1.0, 4.0, 3.0, 2.0, 0.8, 3.2, 2.4, 1.6],
        "eps": [0.10, 0.40, 0.30, 0.20, 0.08, 0.32, 0.24, 0.16],
        "dt_eps": [0.09, 0.38, 0.28, 0.19, 0.07, 0.30, 0.22, 0.15],
        "revenue_ps": [1.0, 4.0, 3.0, 2.0, 0.8, 3.2, 2.4, 1.6],
        "grossprofit_margin": [40.0] * 8,
        "rd_exp": [8.0] * 8,
    })
    return client


class TestPointInTimeFilter:
    """Verify that ``as_of`` correctly filters out not-yet-disclosed quarters."""

    def test_as_of_none_returns_all_quarters(self):
        """The live path (as_of=None) must return all 8 quarters — no filtering."""
        # Q1 2024 announced 20240430, all others earlier.
        ann_dates = [
            "20240430", "20240130", "20231028", "20230829",
            "20230428", "20230130", "20221028", "20220827",
        ]
        client = _client_with_ann_dates(ann_dates)
        results = fetch_financial_data(client, "000001.SZ")  # as_of=None
        assert len(results) == 8

    def test_as_of_filters_future_disclosure(self):
        """Quarters disclosed *after* as_of must be excluded.

        Setup: 8 quarters.  The most recent (2024Q1, ann_date=20240430) is
        disclosed *after* as_of=20240401, so it must be filtered out.  The
        next 7 are all disclosed before 20240401 and should survive.
        """
        ann_dates = [
            "20240430",  # 2024Q1 — disclosed Apr 30 → AFTER as_of
            "20240130",  # 2023年报 — Jan 30 → before
            "20231028",  # 2023Q3 — Oct 28 → before
            "20230829",  # 2023Q2 — Aug 29 → before
            "20230428",  # 2023Q1 — Apr 28 → before
            "20230130",  # 2022年报 — Jan 30 → before
            "20221028",  # 2022Q3 → before
            "20220827",  # 2022Q2 → before
        ]
        client = _client_with_ann_dates(ann_dates)
        results = fetch_financial_data(client, "000001.SZ", as_of=date(2024, 4, 1))

        assert len(results) == 7
        # The newest surviving quarter should be 2023Q4 (ann_date=20240130),
        # NOT 2024Q1 (which was filtered out).
        assert results[0].report_period == "20231231"
        assert results[0].ann_date == "20240130"
        # Ensure 2024Q1 is not present at all.
        assert "20240331" not in [r.report_period for r in results]

    def test_as_on_disclosure_day_included(self):
        """A quarter disclosed exactly on as_of should be included (<=)."""
        ann_dates = [
            "20240401",  # 2024Q1 — disclosed exactly on as_of → included
        ] + ["20240130", "20231028", "20230829", "20230428", "20230130", "20221028", "20220827"]
        client = _client_with_ann_dates(ann_dates)
        results = fetch_financial_data(client, "000001.SZ", as_of=date(2024, 4, 1))

        assert len(results) == 8
        assert results[0].report_period == "20240331"

    def test_financial_data_has_ann_date_field(self):
        """The returned FinancialData objects must carry ann_date."""
        ann_dates = [
            "20240430", "20240130", "20231028", "20230829",
            "20230428", "20230130", "20221028", "20220827",
        ]
        client = _client_with_ann_dates(ann_dates)
        results = fetch_financial_data(client, "000001.SZ")
        assert all(r.ann_date is not None for r in results)
        assert results[0].ann_date == "20240430"

    def test_sorted_by_ann_date_then_report_period(self):
        """Results must be sorted by (ann_date, report_period) descending."""
        # Deliberately scramble ann_dates so report_period order ≠ ann_date order.
        ann_dates = [
            "20240130",  # 2024Q1 disclosed early
            "20240430",  # 2023年报 disclosed late (amended?)
            "20231028", "20230829", "20230428", "20230130", "20221028", "20220827",
        ]
        client = _client_with_ann_dates(ann_dates)
        results = fetch_financial_data(client, "000001.SZ", as_of=date(2024, 5, 1))

        # The most recently disclosed is 2023年报 (ann_date=20240430), not 2024Q1.
        assert results[0].report_period == "20231231"
        assert results[0].ann_date == "20240430"

    def test_legacy_cache_no_ann_date_retained(self):
        """Rows without ann_date (legacy cache) must not be deleted by the filter."""
        client = MagicMock()
        # No ann_date column at all — simulates pre-migration cached data.
        periods = ["20240331", "20231231"]
        client.get_income.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"] * 2,
            "end_date": periods,
            "total_revenue": [1000, 800],
            "n_income": [100, 80],
            "n_income_attr_p": [95, 76],
        })
        client.get_balancesheet.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"] * 2,
            "end_date": periods,
            "total_assets": [10000, 9000],
            "total_liab": [5000, 4500],
        })
        client.get_cashflow.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"] * 2,
            "end_date": periods,
            "n_cashflow_act": [150, 120],
        })
        client.get_fina_indicator.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"] * 2,
            "end_date": periods,
            "roe": [1.0, 0.9],
            "eps": [0.10, 0.08],
            "dt_eps": [0.09, 0.07],
            "revenue_ps": [1.0, 0.9],
        })
        # Even with as_of set, legacy rows without ann_date survive.
        results = fetch_financial_data(client, "000001.SZ", as_of=date(2024, 4, 1))
        assert len(results) == 2


class TestComputeDateRange:
    def test_as_of_none_uses_today(self):
        from datetime import date as d

        start, end = _compute_date_range(8, as_of=None)
        today_str = d.today().strftime("%Y%m%d")
        assert end == today_str

    def test_as_of_anchors_window(self):
        start, end = _compute_date_range(8, as_of=date(2024, 4, 1))
        assert end == "20240401"
        # 8 quarters back = 24 months → 2022-04-01.
        assert start == "20220401"
