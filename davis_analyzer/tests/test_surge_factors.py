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


# ── 5.3 资金组 ──

from davis_analyzer.surge.factors import compute_moneyflow  # noqa: E402


def _mf(elg_nets: list[float], net_mf=None):
    n = len(elg_nets)
    return pd.DataFrame({
        "trade_date": [f"d{i:03d}" for i in range(n)],
        "buy_lg_amount": [0.0] * n,
        "sell_lg_amount": [0.0] * n,
        "buy_elg_amount": [abs(x) for x in elg_nets],
        "sell_elg_amount": [0.0] * n,
        "net_mf_amount": net_mf if net_mf is not None else [float(x) for x in elg_nets],
    })


def test_moneyflow_units_and_streak():
    # 近6日,末3日超大单净额连续正;今日净流入500万,成交额1000千元=1万元? 不:1000千元=1000*10=10000万元
    mf = _mf([100.0, -50.0, 200.0, 300.0, 400.0, 500.0])
    r = compute_moneyflow(mf, amount_today_k=1000.0)
    assert r["elg_net_d0"] == 500.0
    assert r["lg_net_5d"] == 500.0 + 400.0 + 300.0 + 200.0 - 50.0  # tail(5)=-50,200,300,400,500
    # net_ratio: 500万元 / (1000千元×10=10000万元) = 0.05
    assert abs(r["net_ratio_d0"] - 0.05) < 1e-9
    assert r["consec_net_days"] == 3


def test_moneyflow_empty_nan():
    r = compute_moneyflow(pd.DataFrame(), amount_today_k=1000.0)
    assert all(np.isnan(v) for v in r.values())


def test_moneyflow_missing_net_mf():
    mf = _mf([100.0], net_mf=[np.nan])
    r = compute_moneyflow(mf, amount_today_k=100.0)
    assert np.isnan(r["net_ratio_d0"])
    assert r["elg_net_d0"] == 100.0
