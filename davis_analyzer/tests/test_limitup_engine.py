"""engine.py 成交概率/主循环/跌停顺延测试。"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from davis_analyzer.limitup.engine import (
    LimitupBacktestConfig, TradeRecord, fill_probability, run_backtest,
)
from davis_analyzer.limitup.strategies import PRESETS, ExitRule, StrategyPreset


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
    """校准后档位（63k 排队模拟，2026-08-24）：早盘/午盘 45%，尾盘 35%."""
    hard = _cand().iloc[0]                          # 早盘 09:30 封板未炸
    mid = _cand(first_seal_time="133000").iloc[0]   # 午盘封板未炸
    late = _cand(first_seal_time="143000").iloc[0]  # 尾盘封板未炸
    yizi = _cand(open=11.0, low=11.0).iloc[0]       # 一字板
    broken = _cand(broken_count=2, first_seal_time="140000").iloc[0]
    assert fill_probability(hard) == 0.45   # 早盘→45%（旧 20%）
    assert fill_probability(mid) == 0.45    # 午盘→45%（旧 35%）
    assert fill_probability(late) == 0.35   # 尾盘→35%（新增档）
    assert fill_probability(yizi) == 0.05
    assert fill_probability(broken) == 0.70
    assert fill_probability(hard, "optimistic") == round(0.45 * 1.5, 10)
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


def test_fill_probability_early_board_unpadded_time() -> None:
    """9 点档封板时间无前导零（'95321'）归一后落早盘 45% 档."""
    row = _cand(first_seal_time="95321").iloc[0]
    assert fill_probability(row) == 0.45


# ── open_hold_locked 可观测卖出变体（规格 §3.2.1 第 4 条）──


def _hold_locked_preset() -> StrategyPreset:
    """研究脚本同款注入方式：dataclasses.replace 换 exit_rule，不动预设."""
    return replace(PRESETS["first_board"], exit_rule=ExitRule.OPEN_HOLD_LOCKED)


def test_open_hold_locked_open_at_limit_rides_to_break() -> None:
    # T+1 开盘=涨停价 12.1（=round(11.0*1.1,2)）→ 取消卖出转入 ride；
    # 0103 收盘仍涨停 → 持有；0104 断板（收盘 12.4 < 13.31）→ 0105 开盘卖
    prices = pd.DataFrame([
        ("600001.SH", "20240102", 10.2, 11.0, 10.0, 11.0, 10.0),
        ("600001.SH", "20240103", 12.1, 12.1, 12.0, 12.1, 11.0),
        ("600001.SH", "20240104", 12.5, 13.0, 12.0, 12.4, 12.1),
        ("600001.SH", "20240105", 12.2, 12.6, 12.0, 12.3, 12.4),
    ], columns=["ts_code", "trade_date", "open", "high", "low", "close", "pre_close"])
    trades, _ = run_backtest(
        _cand(), prices, _hold_locked_preset(), LimitupBacktestConfig(),
        scenario="always", seed=1,
    )
    assert trades[0].exit_date == "20240105"
    assert abs(trades[0].exit_price - 12.2 * (1 - 10 / 1e4)) < 1e-9


def test_open_hold_locked_open_below_limit_sells_t1() -> None:
    # T+1 开盘 10.8 远低于涨停价 12.1 → 正常 T+1 开盘卖（含滑点）
    trades, _ = run_backtest(
        _cand(), _prices(), _hold_locked_preset(), LimitupBacktestConfig(),
        scenario="always", seed=1,
    )
    assert trades[0].exit_date == "20240103"
    assert abs(trades[0].exit_price - 10.8 * (1 - 10 / 1e4)) < 1e-9


def test_open_hold_locked_limit_down_still_postpones() -> None:
    # T+1 一字跌停（open=low=9.9=round(11.0*0.9,2)）与涨停价 12.1 不符 →
    # 走既有跌停顺延：0103 无法卖，0104 开盘卖
    prices = pd.DataFrame([
        ("600001.SH", "20240102", 10.2, 11.0, 10.0, 11.0, 10.0),
        ("600001.SH", "20240103", 9.9, 9.9, 9.9, 9.9, 11.0),
        ("600001.SH", "20240104", 9.5, 9.8, 9.4, 9.7, 9.9),
    ], columns=["ts_code", "trade_date", "open", "high", "low", "close", "pre_close"])
    trades, _ = run_backtest(
        _cand(), prices, _hold_locked_preset(), LimitupBacktestConfig(),
        scenario="always", seed=1,
    )
    assert trades[0].exit_date == "20240104"
    assert abs(trades[0].exit_price - 9.5 * (1 - 10 / 1e4)) < 1e-9


def test_dynamic_slots_super_hot_day() -> None:
    """高潮增强仓位：dynamic_slots 覆盖指定日期的 max_positions."""
    from davis_analyzer.limitup.engine import LimitupBacktestConfig

    # 两候选同日（用 _cand 的 base dict 拼帧）
    base = {"trade_date": "20240102", "limit_price": 11.0,
            "first_seal_time": "093000", "broken_count": 0, "open": 10.2,
            "low": 10.0, "close": 11.0, "pre_close": 10.0, "seal_ratio": 0.05}
    cands = pd.DataFrame([
        {**base, "ts_code": "600001.SH"},
        {**base, "ts_code": "600002.SH", "seal_ratio": 0.08},  # 封单比更高
    ])
    prices = pd.DataFrame([
        ("600001.SH", "20240102", 10.2, 11.0, 10.0, 11.0, 10.0),
        ("600001.SH", "20240103", 10.8, 11.2, 10.5, 10.9, 11.0),
        ("600002.SH", "20240102", 10.2, 11.0, 10.0, 11.0, 10.0),
        ("600002.SH", "20240103", 10.8, 11.2, 10.5, 10.9, 11.0),
    ], columns=["ts_code", "trade_date", "open", "high", "low", "close", "pre_close"])

    cfg3 = LimitupBacktestConfig()
    t3, _ = run_backtest(cands, prices, PRESETS["first_board"], cfg3,
                          scenario="always", seed=42)
    cfg1 = LimitupBacktestConfig(dynamic_slots={"20240102": 1})
    t1, _ = run_backtest(cands, prices, PRESETS["first_board"], cfg1,
                          scenario="always", seed=42)
    assert len(t3) == 2  # 默认 max_pos=3 → 两只各买
    assert len(t1) == 1  # 仓位集中 max_pos=1 → 只买封单比最高的 600002
    assert cfg3.effective_max_positions("20240102") == 3
    assert cfg1.effective_max_positions("20240102") == 1
    assert cfg1.effective_max_positions("20240103") == 3  # 未指定日用默认
