from unittest.mock import MagicMock

import pandas as pd
import pytest


@pytest.fixture
def mock_client():
    return MagicMock()


@pytest.fixture
def sample_income_df():
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 8,
            "end_date": [
                "20240331",
                "20231231",
                "20230930",
                "20230630",
                "20230331",
                "20221231",
                "20220930",
                "20220630",
            ],
            "total_revenue": [1000, 4000, 3000, 2000, 800, 3200, 2400, 1600],
            "n_income": [100, 400, 300, 200, 80, 320, 240, 160],
            "n_income_attr_p": [95, 380, 285, 190, 76, 304, 228, 152],
        }
    )


@pytest.fixture
def sample_balance_df():
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 8,
            "end_date": [
                "20240331",
                "20231231",
                "20230930",
                "20230630",
                "20230331",
                "20221231",
                "20220930",
                "20220630",
            ],
            "total_assets": [10000, 9500, 9000, 8500, 8000, 7500, 7000, 6500],
            "total_liab": [5000, 4750, 4500, 4250, 4000, 3750, 3500, 3250],
        }
    )


@pytest.fixture
def sample_cashflow_df():
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 8,
            "end_date": [
                "20240331",
                "20231231",
                "20230930",
                "20230630",
                "20230331",
                "20221231",
                "20220930",
                "20220630",
            ],
            "n_cashflow_act": [150, 600, 450, 300, 120, 480, 360, 240],
        }
    )


@pytest.fixture
def sample_fina_df():
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 8,
            "end_date": [
                "20240331",
                "20231231",
                "20230930",
                "20230630",
                "20230331",
                "20221231",
                "20220930",
                "20220630",
            ],
            "roe": [1.0, 4.0, 3.0, 2.0, 0.8, 3.2, 2.4, 1.6],
            "eps": [0.10, 0.40, 0.30, 0.20, 0.08, 0.32, 0.24, 0.16],
            "dt_eps": [0.09, 0.38, 0.28, 0.19, 0.07, 0.30, 0.22, 0.15],
            "revenue_ps": [1.0, 4.0, 3.0, 2.0, 0.8, 3.2, 2.4, 1.6],
            "grossprofit_margin": [40.0, 41.0, 42.0, 39.0, 38.0, 40.0, 41.0, 39.0],
            "rd_exp": [8.0, 32.0, 24.0, 16.0, 6.4, 25.6, 19.2, 12.8],
        }
    )


@pytest.fixture
def mock_client_with_data(
    mock_client, sample_income_df, sample_balance_df, sample_cashflow_df, sample_fina_df
):
    mock_client.get_income.return_value = sample_income_df
    mock_client.get_balancesheet.return_value = sample_balance_df
    mock_client.get_cashflow.return_value = sample_cashflow_df
    mock_client.get_fina_indicator.return_value = sample_fina_df
    return mock_client
