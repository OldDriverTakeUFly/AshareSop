"""日内做T引擎的关键语义单测（防假阳性优先）."""

from __future__ import annotations

import pytest

from davis_analyzer.intraday.engine import (
    Bar, DayCtx, IntradayConfig, Order, simulate_day,
)
from davis_analyzer.intraday.strategies import (
    AmplitudeGrid, GapDownLongT, SpikeFadeShortT,
)


def make_ctx(base: int = 1000, trade: int = 300, pre_close: float = 100.0,
             daily_close: float = 100.0, prev_amp: float = -1.0,
             code: str = "600000.SH") -> DayCtx:
    return DayCtx(
        ts_code=code, trade_date="20260818", pre_close=pre_close,
        daily_close=daily_close, limit_up=round(pre_close * 1.1, 2),
        limit_down=round(pre_close * 0.9, 2), base_shares=base,
        trade_shares=trade, prev_amplitude=prev_amp,
    )


CFG = IntradayConfig(per_stock_notional=100_000, trade_fraction=0.3)


# ── T+1 语义：当日买入不可卖 ──

def test_t1_same_day_buy_not_sellable():
    from davis_analyzer.intraday.engine import DayRunner

    ctx = make_ctx(base=1000)
    r = DayRunner(ctx, CFG)
    r.execute(Order("sell", 600), 100.0)          # 卖底仓 600
    r.execute(Order("buy", 600), 99.0)            # 当日买回 600（冻结）
    ok = r.execute(Order("sell", 600), 100.0)     # 再卖 → 只剩底仓池 400
    assert ok and r.sold_today == 1000            # 600 + clamp(400)
    assert r.bought_today == 600
    # 闭环轧平：diff = 600-1000 = -400 → 竞价买回 400
    r.flatten_eod(100.0)
    assert r.bought_today == r.sold_today == 1000


def test_t1_sell_beyond_base_clamped():
    from davis_analyzer.intraday.engine import DayRunner

    ctx = make_ctx(base=1000)
    r = DayRunner(ctx, CFG)
    r.execute(Order("buy", 1000), 99.0)           # 先买（当日冻结）
    r.execute(Order("sell", 1000), 100.0)         # 卖的是底仓池，不受买入影响
    assert (r.bought_today, r.sold_today) == (1000, 1000)
    ok = r.execute(Order("sell", 100), 100.0)     # 底仓池耗尽
    assert not ok


# ── 涨跌停拒单 ──

def test_limit_up_buy_rejected():
    from davis_analyzer.intraday.engine import DayRunner

    ctx = make_ctx(pre_close=100.0)
    r = DayRunner(ctx, CFG)
    assert not r.execute(Order("buy", 100), 110.0)   # 涨停价不追买
    assert r.execute(Order("buy", 100), 109.5)       # 临近但未涨停可成交
    assert r.n_rejected == 1 and r.bought_today == 100


def test_limit_down_sell_rejected():
    from davis_analyzer.intraday.engine import DayRunner

    ctx = make_ctx(pre_close=100.0)
    r = DayRunner(ctx, CFG)
    assert not r.execute(Order("sell", 100), 90.0)   # 跌停价卖不出
    assert r.execute(Order("sell", 100), 90.5)
    assert r.n_rejected == 1 and r.sold_today == 100


# ── 成本与收益口径 ──

def test_pnl_arithmetic_roundtrip():
    from davis_analyzer.intraday.engine import DayRunner

    ctx = make_ctx()
    r = DayRunner(ctx, CFG)
    r.execute(Order("buy", 1000), 100.0)     # 买 1000@100
    r.execute(Order("sell", 1000), 101.0)    # 卖 1000@101
    # 毛收益 1% = 100bps；拖累: 滑点双边20 + 佣金5 + 印花10 ≈ 35bps → 净 ≈ 65bps
    assert 60 <= r.result("t").net_bps <= 70


def test_eod_flatten_pairs_inventory():
    from davis_analyzer.intraday.engine import DayRunner

    ctx = make_ctx(daily_close=98.0)
    r = DayRunner(ctx, CFG)
    r.execute(Order("buy", 500), 100.0)      # 只买不卖
    r.flatten_eod(98.0)                      # 竞价卖出 500 恢复底仓
    assert r.bought_today == r.sold_today == 500
    assert r.pnl < 0                          # 100 买 98 卖必亏


# ── 策略触发 ──

def _bars(seq: list[tuple[float, float, float, float]]) -> list[Bar]:
    """seq: (open, high, low, close) 逐bar."""
    return [
        Bar(f"{9 + (35 + 5 * i) // 60:02d}:{(35 + 5 * i) % 60:02d}", *seq[i])
        for i in range(len(seq))
    ]


def test_gap_strategy_triggers_on_gap_only():
    s = GapDownLongT(0.02)
    s.reset()
    ctx = make_ctx(pre_close=100.0)
    # 低开 3% → 触发
    orders = s.on_bar(0, Bar("09:35", 97.0, 97.5, 96.8, 97.0), ctx)
    assert orders == [Order("buy", 300)]
    # 未低开 → 不触发
    s.reset()
    assert s.on_bar(0, Bar("09:35", 99.5, 100.2, 99.3, 99.9), ctx) is None


def test_fade_strategy_needs_spike_and_fade():
    s = SpikeFadeShortT(spike_pct=0.02, fade_pct=0.015)
    s.reset()
    ctx = make_ctx(pre_close=100.0)
    assert s.on_bar(0, Bar("09:35", 100.0, 100.5, 99.8, 100.2), ctx) is None  # 无冲高
    assert s.on_bar(1, Bar("09:40", 100.2, 103.0, 100.1, 102.8), ctx) is None  # 冲高未回落
    orders = s.on_bar(2, Bar("09:45", 102.8, 103.0, 101.2, 101.3), ctx)       # 回落 1.65%
    assert orders == [Order("sell", 300)]
    assert s.on_bar(3, Bar("09:50", 101.3, 101.8, 100.9, 101.0), ctx) is None  # 只触发一次


def test_grid_requires_prev_amplitude_and_rungs_single_use():
    s = AmplitudeGrid(prev_amp_th=0.05, step_pct=0.015, rungs=2)
    ctx = make_ctx(pre_close=100.0, prev_amp=0.03)
    s.reset()
    assert s.on_bar(0, Bar("09:35", 100.0, 100.4, 99.6, 99.9), ctx) is None   # 前日振幅不足

    ctx = make_ctx(pre_close=100.0, prev_amp=0.06)
    s.reset()
    assert s.on_bar(0, Bar("09:35", 100.0, 100.4, 99.6, 99.9), ctx) is None
    o1 = s.on_bar(1, Bar("09:40", 99.9, 100.0, 98.4, 98.5), ctx)             # 跌破 -1.5%
    assert o1 == [Order("buy", 300)]
    again = s.on_bar(2, Bar("09:45", 98.5, 98.8, 96.8, 96.9), ctx)           # 跌破第二档 -3%
    assert again == [Order("buy", 300)]
    third = s.on_bar(3, Bar("09:50", 96.9, 97.3, 96.4, 96.5), ctx)           # 档位用尽
    assert third is None


def test_simulate_day_executes_at_next_bar_open():
    """信号次bar开盘成交：低开买入的成交价应是 bar1.open（含滑点），非 bar0 价."""
    class Probe(GapDownLongT):
        pass

    s = Probe(0.02)
    ctx = make_ctx(pre_close=100.0, daily_close=99.0)
    bars = _bars([
        (97.0, 97.5, 96.8, 97.0),
        (96.5, 97.0, 96.2, 96.8),
        (97.0, 97.5, 96.9, 97.2),
    ])
    res = simulate_day(s, ctx, bars, CFG)
    assert res is not None
    # avg_buy ≈ bar1.open×(1+10bps) = 96.5×1.001
    assert res.avg_buy == pytest.approx(96.5 * 1.001, abs=1e-6)
    # EOD 卖出在收盘竞价 99.0（含滑点 99.0×0.999）
    assert res.avg_sell == pytest.approx(99.0 * 0.999, abs=1e-6)
    assert res.shares_bought == res.shares_sold == 300
