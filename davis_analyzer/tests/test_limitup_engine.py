"""engine.py 成交概率/主循环/跌停顺延测试。"""

from __future__ import annotations

import pandas as pd

from davis_analyzer.limitup.engine import (
    LimitupBacktestConfig, TradeRecord, fill_probability, run_backtest,
)
from davis_analyzer.limitup.strategies import PRESETS


def _cand(**kw) -> pd.DataFrame:
    base = {
        "ts_code": "600001.SH", "trade_date": "20240102", "limit_price": 11.0,
        "first_seal_time": "093000", "broken_count": 0, "open": 10.2, "low": 10.0,
        "close": 11.0, "pre_close": 10.0, "seal_ratio": 0.05,
    }
    base.update(kw)
    return pd.DataFrame([base])


def _prices() -> pd.DataFrame:
    return pd.DataFrame([
        # 0102 打板日：open 10.2 low 10.0 close 11.0（涨停）
        ("600001.SH", "20240102", 10.2, 11.0, 10.0, 11.0, 10.0),
        # 0103 正常回落：open 10.8 → open_next 以 10.8 卖
        ("600001.SH", "20240103", 10.8, 11.2, 10.5, 10.9, 11.0),
        ("600001.SH", "20240104", 10.9, 11.0, 10.6, 10.7, 10.9),
    ], columns=["ts_code", "trade_date", "open", "high", "low", "close", "pre_close"])


def test_fill_probability_bands() -> None:
    hard = _cand().iloc[0]                          # 早盘 09:30 封板未炸
    mid = _cand(first_seal_time="133000").iloc[0]   # 午盘封板未炸
    yizi = _cand(open=11.0, low=11.0).iloc[0]       # 一字板
    broken = _cand(broken_count=2, first_seal_time="140000").iloc[0]
    assert fill_probability(hard) == 0.20
    assert fill_probability(mid) == 0.35
    assert fill_probability(yizi) == 0.05
    assert fill_probability(broken) == 0.70
    assert fill_probability(hard, "optimistic") == 0.30
    assert fill_probability(hard, "always") == 1.0


def test_open_next_roundtrip_with_fees() -> None:
    cfg = LimitupBacktestConfig()
    trades, nav = run_backtest(
        _cand(), _prices(), PRESETS["first_board"], cfg, scenario="always", seed=1
    )
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_date == "20240102" and t.exit_date == "20240103"
    assert t.shares % 100 == 0 and t.shares > 0
    # 卖出价含滑点 10bps：10.8 * (1 - 1e-3)
    assert abs(t.exit_price - 10.8 * (1 - 10 / 1e4)) < 1e-9
    assert t.ret_pct < (10.8 / 11.0 - 1)  # 费用+滑点拖累
    assert list(nav.columns) == ["date", "cash", "equity"]
    assert nav["equity"].iloc[-1] > 0


def test_limit_down_postpones_sell() -> None:
    prices = pd.DataFrame([
        ("600001.SH", "20240102", 10.2, 11.0, 10.0, 11.0, 10.0),
        # 0103 一字跌停：open=low=9.9 = round(11.0*0.9,2)
        ("600001.SH", "20240103", 9.9, 9.9, 9.9, 9.9, 11.0),
        ("600001.SH", "20240104", 9.5, 9.8, 9.4, 9.7, 9.9),
    ], columns=["ts_code", "trade_date", "open", "high", "low", "close", "pre_close"])
    trades, _ = run_backtest(
        _cand(), prices, PRESETS["first_board"], LimitupBacktestConfig(),
        scenario="always", seed=1,
    )
    assert trades[0].exit_date == "20240104"  # 0103 无法卖 → 顺延
    assert abs(trades[0].exit_price - 9.5 * (1 - 10 / 1e4)) < 1e-9


def test_ride_board_holds_through_boards() -> None:
    cand = _cand(consecutive_boards=2)
    prices = pd.DataFrame([
        ("600001.SH", "20240102", 10.2, 11.0, 10.0, 11.0, 10.0),
        # 0103 继续涨停（close = 12.1 = 11.0*1.1）
        ("600001.SH", "20240103", 11.5, 12.1, 11.4, 12.1, 11.0),
        # 0104 断板 → 0105 开盘卖
        ("600001.SH", "20240104", 12.5, 13.0, 12.0, 12.4, 12.1),
        ("600001.SH", "20240105", 12.2, 12.6, 12.0, 12.3, 12.4),
    ], columns=["ts_code", "trade_date", "open", "high", "low", "close", "pre_close"])
    trades, _ = run_backtest(
        cand, prices, PRESETS["relay_2"], LimitupBacktestConfig(),
        scenario="always", seed=1,
    )
    assert trades[0].exit_date == "20240105"
    assert abs(trades[0].exit_price - 12.2 * (1 - 10 / 1e4)) < 1e-9


def test_pessimistic_scenario_reduces_fills() -> None:
    # 弱成交场景下大概率买不进：多 seed 统计成交次数不高于乐观场景
    cfg = LimitupBacktestConfig()
    n_pess = n_opt = 0
    for seed in range(20):
        tp, _ = run_backtest(_cand(), _prices(), PRESETS["first_board"], cfg,
                             scenario="pessimistic", seed=seed)
        to, _ = run_backtest(_cand(), _prices(), PRESETS["first_board"], cfg,
                             scenario="optimistic", seed=seed)
        n_pess += len(tp)
        n_opt += len(to)
    assert n_pess <= n_opt


def test_compute_limitup_performance() -> None:
    import pytest

    from davis_analyzer.backtest_report import PerformanceStats
    from davis_analyzer.limitup.engine import compute_limitup_performance

    nav = pd.DataFrame({
        "date": ["20240102", "20240103", "20240104"],
        "cash": [1e6, 1e6, 1e6], "equity": [1e6, 1.01e6, 1.02e6],
    })
    trades = [
        TradeRecord("A", "20240102", 10.0, 100, "20240103", 10.5, "规则卖出",
                    "base", 40.0, 6.0, 0.04),
        TradeRecord("B", "20240102", 10.0, 100, "20240103", 9.5, "规则卖出",
                    "base", -60.0, 6.0, -0.06),
    ]
    stats = compute_limitup_performance(nav, trades, n_signal_days=2)
    assert isinstance(stats, PerformanceStats)
    assert stats.num_trades == 2
    assert stats.win_rate_pct == 50.0
    assert stats.total_return_pct == pytest.approx(2.0)
    assert stats.num_rebalances == 2
