"""GapDownSmart 增强策略与引擎扩展（on_fill/特征注入）的单测."""

from __future__ import annotations

import pandas as pd
import pytest

from davis_analyzer.intraday.engine import (
    Bar, DayCtx, IntradayConfig, Order, run_backtest, simulate_day,
)
from davis_analyzer.intraday.strategies import GapDownSmart


def make_ctx(base: int = 1000, trade: int = 300, pre_close: float = 100.0,
             daily_close: float = 100.0, features: dict | None = None) -> DayCtx:
    return DayCtx(
        ts_code="600000.SH", trade_date="20260818", pre_close=pre_close,
        daily_close=daily_close, limit_up=round(pre_close * 1.1, 2),
        limit_down=round(pre_close * 0.9, 2), base_shares=base,
        trade_shares=trade, prev_amplitude=-1.0, features=features or {},
    )


CFG = IntradayConfig(per_stock_notional=100_000, trade_fraction=0.3)


def _bars(seq: list[tuple[float, float, float, float]]) -> list[Bar]:
    return [
        Bar(f"{9 + (35 + 5 * i) // 60:02d}:{(35 + 5 * i) % 60:02d}", *seq[i])
        for i in range(len(seq))
    ]


def test_on_fill_tracks_entry_px():
    """成交回报：入场价=次bar开盘×(1+滑点)，止损以此为准."""
    s = GapDownSmart(0.03, stop_pct=0.02)
    ctx = make_ctx(pre_close=100.0, daily_close=97.0)
    bars = _bars([
        (96.5, 97.0, 96.0, 96.6),   # 低开 3.5% → bar0 触发
        (96.2, 96.8, 96.0, 96.3),   # bar1 开盘成交 96.2×1.001
        (96.0, 96.2, 95.0, 95.1),   # close 95.1 ≤ 96.3×0.98=94.4? 否
        (95.0, 95.6, 94.6, 94.8),   # close 94.8 ≤ 94.4? 否→ 检查数值
    ])
    res = simulate_day(s, ctx, bars, CFG)
    assert res is not None
    assert s._entry_px == pytest.approx(96.2 * 1.001, abs=1e-9)


def test_stop_loss_exits_before_close():
    """止损在盘中触发（close 跌破 entry×(1-stop)），不等收盘竞价."""
    s = GapDownSmart(0.03, stop_pct=0.02)
    ctx = make_ctx(pre_close=100.0, daily_close=90.0)  # 若扛到收盘会更惨
    bars = _bars([
        (96.5, 97.0, 96.0, 96.6),   # 触发
        (96.2, 96.8, 96.0, 96.3),   # 成交 entry≈96.30
        (95.0, 95.2, 94.6, 94.7),   # 94.7 ≤ 96.30×0.98≈94.37? 否（还差一点）
        (94.5, 94.8, 93.8, 94.0),   # 94.0 ≤ 94.37 → 止损信号
        (93.5, 94.0, 93.0, 93.2),   # bar4 开盘成交卖出
        (93.0, 93.5, 92.6, 93.0),
    ])
    res = simulate_day(s, ctx, bars, CFG)
    assert res is not None
    # 卖出发生在 bar4 开盘(93.5×0.999)而非收盘竞价(90)
    assert res.avg_sell == pytest.approx(93.5 * 0.999, abs=1e-9)


def test_tp_fill_exits_at_pre_close_recovery():
    s = GapDownSmart(0.03, tp_fill=True)
    ctx = make_ctx(pre_close=100.0, daily_close=103.0)
    bars = _bars([
        (96.5, 97.0, 96.0, 96.6),
        (96.2, 96.8, 96.0, 96.3),
        (98.0, 99.5, 97.8, 99.2),   # 未回昨收
        (100.1, 101.0, 99.8, 100.5),  # close ≥ 100 → 回补止盈
        (100.8, 102.0, 100.5, 101.5),  # 次bar开盘卖出
        (101.5, 103.0, 101.0, 102.5),
    ])
    res = simulate_day(s, ctx, bars, CFG)
    assert res is not None
    assert res.avg_sell == pytest.approx(100.8 * 0.999, abs=1e-9)


def test_require_filter_blocks_entry():
    s = GapDownSmart(0.03, require={"trend_up": True})
    ctx_bad = make_ctx(pre_close=100.0, features={"trend_up": False, "gap_pct": -0.035})
    bars = _bars([(96.5, 97.0, 96.0, 96.6), (96.2, 96.8, 96.0, 96.3)])
    assert simulate_day(s, ctx_bad, bars, CFG) is None

    ctx_nan = make_ctx(pre_close=100.0, features={})  # 特征缺失 → 保守不入场
    s.reset()
    assert simulate_day(s, ctx_nan, bars, CFG) is None

    ctx_ok = make_ctx(pre_close=100.0,
                      features={"trend_up": True, "idx_ret0940": -0.005})
    s2 = GapDownSmart(0.03, require={"trend_up": True, "idx_ret0940_min": -0.01})
    res = simulate_day(s2, ctx_ok, bars, CFG)
    assert res is not None and res.shares_bought == 300


def test_features_df_injected_into_ctx():
    """run_backtest 的 features_df 经 DayCtx.features 注入策略."""
    minute = pd.DataFrame([
        {"ts_code": "600000.SH", "trade_date": "20260818", "trade_time": "09:35",
         "open": 96.5, "high": 97.0, "low": 96.0, "close": 96.6},
        {"ts_code": "600000.SH", "trade_date": "20260818", "trade_time": "09:40",
         "open": 96.2, "high": 96.8, "low": 96.0, "close": 96.3},
    ])
    daily = pd.DataFrame([
        {"ts_code": "600000.SH", "trade_date": "20260818", "pre_close": 100.0,
         "close": 96.0, "high": 97.0, "low": 95.5},
    ])
    feats = pd.DataFrame([
        {"ts_code": "600000.SH", "trade_date": "20260818", "trend_up": True,
         "vol_ratio1": 1.2},
    ])
    seen: list[dict] = []
    base_on_bar = GapDownSmart.on_bar

    def spy(self, i, bar, ctx):
        seen.append(dict(ctx.features))
        return base_on_bar(self, i, bar, ctx)

    GapDownSmart.on_bar = spy  # type: ignore[method-assign]
    try:
        s = GapDownSmart(0.03, require={"trend_up": True})
        res = run_backtest(minute, daily, [s], CFG, features_df=feats)
    finally:
        GapDownSmart.on_bar = base_on_bar  # type: ignore[method-assign]
    assert seen and seen[0].get("trend_up") is True
    assert len(res) == 1 and res.iloc[0].shares_bought == 300
