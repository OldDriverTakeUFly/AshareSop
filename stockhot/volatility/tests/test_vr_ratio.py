"""Tests for vr_ratio() — IV/RV ratio with divide-by-zero protection."""

from __future__ import annotations

import math

import pytest

from stockhot.volatility.analyzer import vr_ratio


def test_vr_ratio_basic():
    """V/R = IV / RV，标准计算。"""
    assert vr_ratio(20.0, 10.0) == pytest.approx(2.0)
    assert vr_ratio(15.0, 20.0) == pytest.approx(0.75)


def test_vr_ratio_equal():
    """IV = RV → V/R = 1.0（定价合理）。"""
    assert vr_ratio(20.0, 20.0) == pytest.approx(1.0)


def test_vr_ratio_zero_rv_returns_nan():
    """RV=0 除零保护返回 NaN。"""
    result = vr_ratio(20.0, 0.0)
    assert math.isnan(result)


def test_vr_ratio_nan_iv_returns_nan():
    """IV=NaN 返回 NaN。"""
    result = vr_ratio(float("nan"), 20.0)
    assert math.isnan(result)


def test_vr_ratio_nan_rv_returns_nan():
    """RV=NaN 返回 NaN。"""
    result = vr_ratio(20.0, float("nan"))
    assert math.isnan(result)


def test_vr_ratio_rounding():
    """结果四舍五入到小数点后 2 位。"""
    assert vr_ratio(22.8, 16.9) == pytest.approx(1.35, abs=0.01)
