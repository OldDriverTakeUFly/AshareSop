"""factors 纯函数测试（合成日线fixture）."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from davis_analyzer.systems.surge.factors import compute_position


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

from davis_analyzer.systems.surge.factors import compute_moneyflow  # noqa: E402


def _mf(elg_nets: list[float], net_mf=None):
    n = len(elg_nets)
    return pd.DataFrame({
        "trade_date": [f"d{i:03d}" for i in range(n)],
        "buy_lg_amount": [0.0] * n,
        "sell_lg_amount": [0.0] * n,
        "buy_elg_amount": [max(x, 0.0) for x in elg_nets],
        "sell_elg_amount": [max(-x, 0.0) for x in elg_nets],
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
    assert r["consec_net_days"] == 4  # 500,400,300,200 连续正,-50 截断


def test_moneyflow_empty_nan():
    r = compute_moneyflow(pd.DataFrame(), amount_today_k=1000.0)
    assert all(np.isnan(v) for v in r.values())


def test_moneyflow_missing_net_mf():
    mf = _mf([100.0], net_mf=[np.nan])
    r = compute_moneyflow(mf, amount_today_k=100.0)
    assert np.isnan(r["net_ratio_d0"])
    assert r["elg_net_d0"] == 100.0


# ── 5.6/5.7 压力支撑 ──

from davis_analyzer.systems.surge.factors import compute_resistance_support  # noqa: E402


def test_resistance_picks_nearest_above():
    # 130日: 箱体 high=11 low=9 close=10;今日拉到10.2
    px = _px([10.0] * 130, highs=[11.0] * 130, lows=[9.0] * 130)
    px.iloc[-1, px.columns.get_loc("close")] = 10.2
    px.iloc[-1, px.columns.get_loc("high")] = 10.4
    cyq = pd.Series({"cost_5pct": 9.5, "cost_15pct": 9.8, "cost_50pct": 10.0,
                     "cost_85pct": 10.5, "cost_95pct": 10.8, "weight_avg": 10.1,
                     "his_high": 12.0})
    r = compute_resistance_support(px, cyq)
    # 候选>10.2*1.005=10.251: cost_85=10.5, cost_95=10.8, his_high=12, high_120=11 → 最近=10.5
    assert abs(r["resistance_price"] - 10.5) < 1e-9
    # 候选<10.2*0.995=10.149: cost_15=9.8, cost_5=9.5, weight_avg=10.1, low_120=9 → 最近=10.1
    assert abs(r["support_price"] - 10.1) < 1e-9
    assert "cost_85pct" in r["resistance_ladder"]


def test_resistance_all_below_nan():
    px = _px([10.0] * 130, highs=[10.0] * 130, lows=[9.5] * 130)
    r = compute_resistance_support(px, None)
    assert np.isnan(r["resistance_price"])  # 无高于现价的档位
    assert abs(r["support_price"] - 9.5) < 1e-9


def test_weight_avg_flips_to_resistance_when_lost():
    # 现价跌破成本中枢 → weight_avg 从支撑变阻力
    px = _px([10.0] * 130, highs=[11.0] * 130, lows=[8.0] * 130)
    px.iloc[-1, px.columns.get_loc("close")] = 9.0  # 跌破 weight_avg=10.1
    cyq = pd.Series({"cost_5pct": 8.5, "cost_15pct": 8.8, "cost_50pct": 9.2,
                     "cost_85pct": 10.5, "cost_95pct": 10.8, "weight_avg": 10.1,
                     "his_high": 12.0})
    r = compute_resistance_support(px, cyq)
    # 阻力最近档=MA20=(19×10+9)/20=9.95(现价9.0上方均线压);weight_avg 转压在梯队中
    assert abs(r["resistance_price"] - 9.95) < 1e-6
    assert "weight_avg" in r["resistance_ladder"]
    assert "cost_85pct" in r["resistance_ladder"]


def test_gap_down_becomes_resistance():
    # 构造近端向下缺口: 昨日 [low=11,high=11.5] → 今日 [low=9.5,high=10] 缺口;收盘10.5回补中
    closes = [10.0] * 128
    px = _px(closes, highs=[10.5] * 128, lows=[9.8] * 128)
    px = pd.concat([px, pd.DataFrame([{
        "ts_code": "000001.SZ", "trade_date": "d128", "open": 11.2, "high": 11.5,
        "low": 11.0, "close": 11.2, "vol": 1000.0, "adj_factor": 1.0}]),
        pd.DataFrame([{
        "ts_code": "000001.SZ", "trade_date": "d129", "open": 9.8, "high": 10.0,
        "low": 9.5, "close": 9.8, "vol": 1000.0, "adj_factor": 1.0}])],
        ignore_index=True)
    r = compute_resistance_support(px, None)
    # 收盘9.8;最近阻力=MA120=(118×10+11.2+9.8)/120≈10.0083(MA120比MA20均值更低更近);
    # 缺口下沿11.0在梯队(先于high_120=11.5)
    assert abs(r["resistance_price"] - 10.008333333333) < 1e-6
    assert "gap_down" in r["resistance_ladder"]
    lad = r["resistance_ladder"]
    assert lad.index("gap_down") < lad.index("high_120d")  # 缺口档位先于滚动高点


def test_resistance_support_none_adjfactor():
    # 历史数据个别日 adj_factor NULL——不崩,均线换算退化(20260205 回放实锤)
    px = _px([10.0] * 130, highs=[11.0] * 130, lows=[9.0] * 130)
    px.iloc[-1, px.columns.get_loc("adj_factor")] = None
    r = compute_resistance_support(px, None)
    assert r["resistance_price"] == r["resistance_price"]  # 有值不崩
