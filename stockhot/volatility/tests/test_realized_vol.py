"""Tests for realized_vol() — the RV computation core."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockhot.volatility.analyzer import TRADING_DAYS_PER_YEAR, realized_vol


def test_realized_vol_constant_series_is_zero():
    """常数序列无波动，RV 应为 0。"""
    close = pd.Series([100.0] * 30)
    rv = realized_vol(close, window=20)
    assert rv.iloc[-1] == pytest.approx(0.0, abs=0.01)


def test_realized_vol_known_input():
    """验证已知输入的 RV 计算（手算交叉验证）。

    构造：交替 ±1% 波动，对数收益率 ≈ ±0.00995，标准差 ≈ 0.00995
    年化 RV = 0.00995 × √242 × 100 ≈ 15.48%
    （每日恒定 +1% 的话标准差=0，需交替涨跌才有波动）
    """
    prices = [100.0]
    for i in range(30):
        prices.append(prices[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
    close = pd.Series(prices)
    rv = realized_vol(close, window=20)
    expected = np.log(1.01) * np.sqrt(TRADING_DAYS_PER_YEAR) * 100
    assert rv.iloc[-1] == pytest.approx(expected, rel=0.05)


def test_realized_vol_window_nan_prefix():
    """前 ``window`` 个值为 NaN（rolling 窗口未满）。

    diff() 消耗 1 个值，rolling(20) 再消耗 20 个 → 索引 0~19 为 NaN，
    索引 20 起（第 21 个元素）才有值。
    """
    close = pd.Series([100.0 + i for i in range(30)])
    rv = realized_vol(close, window=20)
    assert rv.iloc[:20].isna().all()
    assert rv.iloc[20:].notna().all()


def test_realized_vol_output_length_matches_input():
    """输出长度与输入一致。"""
    close = pd.Series(np.random.RandomState(42).randn(50) + 100)
    rv = realized_vol(close, window=10)
    assert len(rv) == len(close)


def test_realized_vol_uses_log_returns():
    """确认用对数收益率（非简单收益率）。

    对称涨跌（+10% / -9.09%）的对数收益率绝对值相等，
    简单收益率不等——RV 数值会不同。
    """
    # 交替涨跌，对数收益率序列 = [+ln(1.1), +ln(1/1.1), ...]
    prices = [100.0]
    for i in range(20):
        prices.append(prices[-1] * (1.10 if i % 2 == 0 else 1 / 1.10))
    close = pd.Series(prices)
    rv = realized_vol(close, window=20)
    # 对数收益率标准差 × √242 × 100
    logret = np.log(close).diff().dropna()
    expected = logret.std() * np.sqrt(TRADING_DAYS_PER_YEAR) * 100
    assert rv.iloc[-1] == pytest.approx(expected, rel=0.01)


def test_realized_vol_empty_series():
    """空序列返回空。"""
    rv = realized_vol(pd.Series([], dtype=float), window=20)
    assert len(rv) == 0


def test_realized_vol_window_default_is_20():
    """默认 window=20（对应 VIX 30 天窗口）。"""
    close = pd.Series([100.0 + i * 0.5 for i in range(30)])
    rv_default = realized_vol(close)
    rv_20 = realized_vol(close, window=20)
    pd.testing.assert_series_equal(rv_default, rv_20)
