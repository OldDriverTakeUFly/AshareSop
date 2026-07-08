"""Integration tests for run_volatility_analysis() — entry point with DB persistence."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from stockhot.volatility.analyzer import run_volatility_analysis


@pytest.fixture
def mock_index_data():
    """模拟 60 天的指数日线（带波动）。"""
    import numpy as np

    dates = pd.date_range("2026-05-01", periods=60, freq="B")
    np.random.seed(42)
    close = 3000 + np.cumsum(np.random.randn(60) * 20)
    return pd.DataFrame({"close": close}, index=dates)


@pytest.fixture
def mock_ivix_data():
    """模拟 60 天的 iVIX 序列。"""
    import numpy as np

    dates = pd.date_range("2026-05-01", periods=60, freq="B")
    np.random.seed(7)
    values = 20 + np.random.randn(60) * 3
    return pd.Series(values, index=dates, name="ivix")


def test_run_analysis_success_persists(mock_index_data, mock_ivix_data):
    """成功时持久化到 DB（save_daily_data 被调用）。"""
    with (
        patch("stockhot.volatility.analyzer.fetch_index_history", return_value=mock_index_data),
        patch("stockhot.volatility.analyzer.fetch_ivix_history", return_value=mock_ivix_data),
        patch("stockhot.volatility.analyzer.save_daily_data") as mock_save,
    ):
        result = run_volatility_analysis(date="2026-07-08", days=60)

        assert result["status"] == "success"
        assert result["date"] == "2026-07-08"
        assert len(result["indices"]) == 5  # DEFAULT_INDICES
        mock_save.assert_called_once()
        # 验证 save_daily_data 收到正确的 date 和 volatility key
        saved = mock_save.call_args[0][0]
        assert saved["date"] == "2026-07-08"
        assert "volatility" in saved


def test_run_analysis_index_isolation(mock_index_data, mock_ivix_data):
    """单指数失败不影响其他（遵循 daily-market-scan try/except 隔离原则）。"""

    def fake_fetch(ts_code, days=60):
        if ts_code == "399006.SZ":  # 创业板模拟失败
            return pd.DataFrame()
        return mock_index_data

    with (
        patch("stockhot.volatility.analyzer.fetch_index_history", side_effect=fake_fetch),
        patch("stockhot.volatility.analyzer.fetch_ivix_history", return_value=mock_ivix_data),
        patch("stockhot.volatility.analyzer.save_daily_data"),
    ):
        result = run_volatility_analysis(date="2026-07-08", days=60)

        # 总体仍 success（4/5 指数成功）
        assert result["status"] == "success"
        # 创业板标记为数据不可用
        assert result["indices"]["399006.SZ"]["status"] == "数据不可用"
        # 其他指数正常
        assert result["indices"]["000001.SH"]["status"] == "success"


def test_run_analysis_all_fail_returns_no_data():
    """所有指数都失败时返回 no_data 且不持久化。"""
    with (
        patch(
            "stockhot.volatility.analyzer.fetch_index_history",
            return_value=pd.DataFrame(),
        ),
        patch(
            "stockhot.volatility.analyzer.fetch_ivix_history", return_value=pd.Series(dtype=float)
        ),
        patch("stockhot.volatility.analyzer.save_daily_data") as mock_save,
    ):
        result = run_volatility_analysis(date="2026-07-08", days=60)

        assert result["status"] == "no_data"
        mock_save.assert_not_called()


def test_run_analysis_result_structure(mock_index_data, mock_ivix_data):
    """验证返回结构符合约定（indices/market/summary/date/status）。"""
    with (
        patch("stockhot.volatility.analyzer.fetch_index_history", return_value=mock_index_data),
        patch("stockhot.volatility.analyzer.fetch_ivix_history", return_value=mock_ivix_data),
        patch("stockhot.volatility.analyzer.save_daily_data"),
    ):
        result = run_volatility_analysis(date="2026-07-08", days=60)

        assert set(result.keys()) >= {"date", "status", "indices", "market", "summary"}

        # 单指数结果结构
        idx = result["indices"]["000001.SH"]
        assert set(idx.keys()) >= {
            "ts_code",
            "name",
            "status",
            "rv20",
            "rv60",
            "rv20_pct",
            "rv60_pct",
            "panic_level",
        }

        # 市场层结构
        market = result["market"]
        assert set(market.keys()) >= {"status", "ivix_current", "ivix_pct", "vr_ratio"}


def test_run_analysis_market_unavailable_when_ivix_empty(mock_index_data):
    """iVIX 空时 market 标记数据不可用，但指数仍成功。"""
    with (
        patch("stockhot.volatility.analyzer.fetch_index_history", return_value=mock_index_data),
        patch(
            "stockhot.volatility.analyzer.fetch_ivix_history",
            return_value=pd.Series(dtype=float),
        ),
        patch("stockhot.volatility.analyzer.save_daily_data"),
    ):
        result = run_volatility_analysis(date="2026-07-08", days=60)

        assert result["status"] == "success"  # 指数成功
        assert result["market"]["status"] == "数据不可用"  # 但市场层不可用
