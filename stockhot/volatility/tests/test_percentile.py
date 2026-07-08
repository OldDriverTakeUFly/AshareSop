"""Tests for percentile_rank() — historical percentile computation."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from stockhot.volatility.analyzer import percentile_rank


def test_percentile_basic():
    """中位数 → P50，最大值 → P~100，最小值 → P0。"""
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    assert percentile_rank(3.0, series) == pytest.approx(40.0, abs=1)  # 2/5 < 3
    assert percentile_rank(6.0, series) == pytest.approx(100.0, abs=1)
    assert percentile_rank(0.5, series) == pytest.approx(0.0, abs=1)


def test_percentile_with_nan_in_history():
    """历史序列含 NaN 时自动去 NaN。"""
    series = pd.Series([1.0, np.nan, 2.0, 3.0, 4.0])
    # 去 NaN 后 [1,2,3,4]，3.0 的分位 = 2/4 = 50%
    assert percentile_rank(3.0, series) == pytest.approx(50.0, abs=1)


def test_percentile_current_is_nan():
    """当前值为 NaN 返回 NaN。"""
    series = pd.Series([1.0, 2.0, 3.0])
    result = percentile_rank(float("nan"), series)
    assert math.isnan(result)


def test_percentile_empty_history():
    """历史序列空返回 NaN。"""
    result = percentile_rank(5.0, pd.Series([], dtype=float))
    assert math.isnan(result)


def test_percentile_all_nan_history():
    """历史全 NaN 返回 NaN。"""
    result = percentile_rank(5.0, pd.Series([np.nan, np.nan]))
    assert math.isnan(result)


def test_percentile_single_point_history():
    """历史单点：当前值 > 该点 → P100；< → P0。"""
    series = pd.Series([5.0])
    assert percentile_rank(6.0, series) == pytest.approx(100.0, abs=1)
    assert percentile_rank(4.0, series) == pytest.approx(0.0, abs=1)


def test_percentile_range_0_to_100():
    """分位输出始终在 [0, 100]。"""
    np.random.seed(0)
    series = pd.Series(np.random.randn(100) * 10 + 20)
    for val in [-100, 0, 50, 100, 1000]:
        pct = percentile_rank(val, series)
        assert 0 <= pct <= 100
