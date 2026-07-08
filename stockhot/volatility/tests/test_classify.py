"""Tests for classify_panic_level() — panic-level threshold mapping."""

from __future__ import annotations

import pytest

from stockhot.volatility.analyzer import classify_panic_level


@pytest.mark.parametrize(
    "percentile, expected",
    [
        (0, "极度自满"),
        (5, "极度自满"),
        (9.9, "极度自满"),
        (10, "平静"),
        (24, "平静"),
        (25, "正常"),
        (50, "正常"),
        (74, "正常"),
        (75, "偏高"),
        (89, "偏高"),
        (89.9, "偏高"),
        (90, "明显恐慌"),
        (94, "明显恐慌"),
        (95, "极度恐慌"),
        (99, "极度恐慌"),
        (100, "极度恐慌"),
    ],
)
def test_classify_thresholds(percentile, expected):
    """验证 6 档恐慌等级阈值边界（对应研报 §2.2 刻度表）。"""
    assert classify_panic_level(percentile) == expected


def test_classify_nan_returns_unavailable():
    """NaN 分位返回"数据不可用"。"""
    result = classify_panic_level(float("nan"))
    assert result == "数据不可用"
    assert "不可用" in result
