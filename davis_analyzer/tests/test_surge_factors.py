"""factors 纯函数测试（合成日线fixture）."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from davis_analyzer.surge.factors import compute_position


def _px(closes: list[float], highs=None, lows=None, vol=1000.0, adj=None):
    n = len(closes)
    return pd.DataFrame({
        "ts_code": ["000001.SZ"] * n,
        "trade_date": [f"d{i:03d}" for i in range(n)],
        "open": [c * 0.99 for c in closes],
        "high": highs or [c * 1.02 for c in closes],
        "low": lows or [c * 0.98 for c in closes],
        "close": closes,
        "vol": [vol] * n,
        "adj_factor": adj or [1.0] * n,
    })


def test_position_mid_range():
    closes = [10 + 0.01 * i for i in range(260)]
    r = compute_position(_px(closes))
    assert 0.0 < r["pos_250d"] <= 1.0
    assert r["dist_ma20"] > 0  # 连涨序列现价在均线上
    assert r["dd_high_250"] <= 0


def test_position_short_history_nan():
    r = compute_position(_px([10, 11, 12]))
    assert math.isnan(r["pos_250d"])
    assert math.isnan(r["dist_ma20"])


def test_position_uses_adjusted():
    # 除权: adj_factor 前段2.0 后段1.0,后复权价连续——位置不应失真为深跌
    closes = [10.0] * 130 + [10.0] * 130
    adj = [2.0] * 130 + [1.0] * 130
    r = compute_position(_px(closes, adj=adj))
    assert not math.isnan(r["pos_250d"])
    assert r["dist_ma20"] == 0.0  # 后复权价恒定→现价=均线
