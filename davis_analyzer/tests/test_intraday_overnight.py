"""隔夜退出校验模块单测：引擎对账 / T+1 定价 / 飞刀条件持有."""

from __future__ import annotations

import pandas as pd
import pytest

from davis_analyzer.intraday.engine import IntradayConfig, run_backtest
from davis_analyzer.intraday.overnight_study import (
    build_day_structures,
    lot_net_bps,
    resolve_exit,
    scan_entries,
)
from davis_analyzer.intraday.strategies import GapDownSmart

CFG = IntradayConfig(per_stock_notional=100_000, trade_fraction=0.3)
CODE = "600000.SH"


def _minute(code: str, date: str,
            bars: list[tuple[str, float, float, float]]) -> pd.DataFrame:
    """(time, open, close, low) → 分钟 DataFrame."""
    return pd.DataFrame([
        {"ts_code": code, "trade_date": date, "trade_time": t,
         "open": o, "high": max(o, c) + 0.2, "low": lo, "close": c}
        for t, o, c, lo in bars
    ])


def _daily(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    return pd.DataFrame([
        {"ts_code": CODE, "trade_date": d, "pre_close": pc, "close": c,
         "high": max(pc, c) + 0.5, "low": min(pc, c) - 0.5}
        for d, pc, c in rows
    ])


# 常规日:09:35 触发 3% 低开,09:40 成交,14:00 触发退出、14:05 开盘卖出
NORMAL_BARS = [
    ("09:35", 96.5, 96.6, 96.0), ("09:40", 96.2, 96.3, 96.0),
    ("14:00", 96.0, 95.5, 95.0), ("14:05", 95.5, 95.2, 94.8),
    ("14:10", 95.0, 94.9, 94.5),
]


def test_engine_calibration_d0_1400():
    """仿真 d0_1400 与引擎 GapDownSmart(0.03, x14:00) 净收益逐笔一致."""
    minute = _minute(CODE, "20260817", NORMAL_BARS)
    daily = _daily([("20260817", 100.0, 96.0)])
    days = build_day_structures(minute, daily)
    lots = scan_entries(days, None, 0.03, CFG)
    assert len(lots) == 1
    lot = lots[0]
    assert lot.fill_px == pytest.approx(96.2 * 1.001)
    assert lot.shares == 300  # 10万底仓/100元=1000股,30%=300股

    sell_raw, locked = resolve_exit("d0_1400", days[CODE], 0, lot.fill_px)
    assert (sell_raw, locked) == (95.5, False)
    _, bps = lot_net_bps(lot, sell_raw, CFG)

    res = run_backtest(minute, daily, [GapDownSmart(0.03, exit_time="14:00")], CFG)
    assert len(res) == 1
    assert abs(bps - res.iloc[0].net_bps) < 0.5


def test_t1_close_uses_next_trading_day_close():
    """t1_close 用 T+1 日线 close;样本末端无 T+2 时 t2_close 弃权."""
    minute = pd.concat([
        _minute(CODE, "20260817", NORMAL_BARS),
        _minute(CODE, "20260818", NORMAL_BARS[:2]),
    ], ignore_index=True)
    daily = _daily([("20260817", 100.0, 96.0), ("20260818", 96.0, 95.0)])
    days = build_day_structures(minute, daily)
    lots = scan_entries(days, None, 0.03, CFG)
    seq = days[CODE]
    assert len(lots) == 1 and lots[0].day_idx == 0

    px, locked = resolve_exit("t1_close", seq, 0, lots[0].fill_px)
    assert (px, locked) == (95.0, False)  # T+1=20260818 的 close
    assert resolve_exit("t2_close", seq, 0, lots[0].fill_px) is None


def test_cond3_holds_knife_to_t1_close():
    """cond3_t1: 14:00 检查点浮亏>3% → 扛到 T+1 收盘;否则当日 14:05 退出."""
    knife_bars = list(NORMAL_BARS)
    knife_bars[2] = ("14:00", 94.0, 93.0, 92.5)  # 检查点 close 93.0
    minute = pd.concat([
        _minute(CODE, "20260817", knife_bars),
        _minute(CODE, "20260818", NORMAL_BARS[:2]),
    ], ignore_index=True)
    daily = _daily([("20260817", 100.0, 93.5), ("20260818", 93.5, 95.0)])
    days = build_day_structures(minute, daily)
    lots = scan_entries(days, None, 0.03, CFG)
    seq = days[CODE]

    # 常规日(95.5 ≥ 96.296×0.97≈93.4):当日 14:05 开盘退出
    minute_n = _minute(CODE, "20260817", NORMAL_BARS)
    days_n = build_day_structures(minute_n, daily)
    lots_n = scan_entries(days_n, None, 0.03, CFG)
    px, _ = resolve_exit("cond3_t1", days_n[CODE], 0, lots_n[0].fill_px)
    assert px == 95.5

    # 飞刀日(93.0 < 93.4):T+1 收盘退出
    px, _ = resolve_exit("cond3_t1", seq, 0, lots[0].fill_px)
    assert px == 95.0
