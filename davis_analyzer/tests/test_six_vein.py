"""六脉神剑参赛者:六脉信号计算与共振引擎闭环测试。

合成行情三段式(下跌 → 加速拉升 → 下跌)驱动:
* 信号层——牛市尾段六脉全真、熊市共振恒灭(BBI 脉在单调下跌中恒 False);
* 引擎层——共振翻转次日开盘买入、断剑次日开盘卖出、成本/整手/T+1 口径;
* 拒单层——涨停开口买入放弃(one-shot)、跌停开口卖出顺延重试。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from davis_analyzer.tournament.adapters import SixVeinAdapter, default_participants
from davis_analyzer.tournament.genome import SIX_VEIN_GENOME
from davis_analyzer.tournament.six_vein import (
    SixVeinConfig,
    compute_signals,
    run_six_vein,
)

_D0 = date(2023, 1, 2)


# ── synthetic market helpers ──


def _decline(days: int, start: float = 10.0) -> list[float]:
    closes = [start]
    for i in range(days):
        r = -0.006 if i % 2 == 0 else -0.014
        closes.append(closes[-1] * (1 + r))
    return closes


def _bull(days: int, start: float = 10.0, pure_tail: int = 0) -> list[float]:
    """Accelerating rise with a dip every 3rd day (keeps RSI two-line alive)."""
    closes = [start]
    for i in range(days):
        r = -0.004 if i % 3 == 2 else 0.010 + 0.0002 * i
        closes.append(closes[-1] * (1 + r))
    for _ in range(pure_tail):
        closes.append(closes[-1] * 1.025)
    return closes


def _ohlc(closes: list[float], code: str = "000001.SZ") -> pd.DataFrame:
    rows, prev = [], closes[0]
    for i, c in enumerate(closes):
        rows.append({
            "ts_code": code,
            "trade_date": (_D0 + timedelta(days=i)).strftime("%Y%m%d"),
            "open": prev,  # 平开——避开涨跌停拒单干扰主路径断言
            "high": c * 1.005,
            "low": c * 0.995,
            "close": c,
            "pre_close": prev,
            "amount": 1e8,
            "adj_factor": 1.0,
        })
        prev = c
    return pd.DataFrame(rows)


def _run(closes: list[float], cfg: SixVeinConfig | None = None):
    df = _ohlc(closes)
    end = _D0 + timedelta(days=len(closes) - 1)
    return run_six_vein(df, cfg or SixVeinConfig(), _D0, end)


# ── signal layer ──


def test_signals_bull_tail_all_six_pulses_on() -> None:
    closes = _decline(60)
    closes.extend(_bull(60, start=closes[-1], pure_tail=8)[1:])
    sig = compute_signals(_ohlc(closes))
    for col in ("p_macd", "p_kdj", "p_rsi", "p_lwr", "p_bbi", "p_zlmm"):
        assert bool(sig[col].iloc[-1]), f"{col} 未在牛市尾段点亮"
    assert bool(sig["resonance"].iloc[-1])


def test_signals_bear_market_resonance_never_fires() -> None:
    sig = compute_signals(_ohlc(_decline(120)))
    assert not sig["resonance"].any()


# ── engine layer ──


def test_engine_round_trip_buys_flip_sells_break() -> None:
    closes = _decline(60)
    closes.extend(_bull(45, start=closes[-1])[1:])
    closes.extend(_decline(30, start=closes[-1])[1:])
    df = _ohlc(closes)
    sig = compute_signals(df)
    res, dates = sig["resonance"], list(sig.index)

    trades, curve = run_six_vein(
        df, SixVeinConfig(), _D0, _D0 + timedelta(days=len(closes) - 1),
    )
    buys = [t for t in trades if t.action == "BUY"]
    sells = [t for t in trades if t.action == "SELL"]
    assert buys and sells
    assert trades[0].action == "BUY"
    assert trades[-1].action == "SELL"  # 尾部下跌段清仓离场

    first_flip = next(i for i in range(1, len(dates)) if res.iloc[i] and not res.iloc[i - 1])
    assert trades[0].exec_date.strftime("%Y%m%d") == dates[first_flip + 1]

    for t in trades:
        i = dates.index(t.exec_date.strftime("%Y%m%d"))
        if t.action == "BUY":
            assert i >= 2 and res.iloc[i - 1] and not res.iloc[i - 2]
        else:
            assert not res.iloc[i - 1]
        assert t.shares % 100 == 0 and t.shares > 0
        rate = 2.5e-4 if t.action == "BUY" else 12.5e-4  # 佣金双向 + 印花税卖出
        assert t.cost == pytest.approx(t.amount * rate)
        assert t.amount == pytest.approx(t.price * t.shares)

    assert len(curve) == len(dates)
    assert curve[0].equity == pytest.approx(1_000_000.0)
    # 等权槽位:首笔买入名义 ≈ 初始权益 / max_positions
    assert buys[0].amount == pytest.approx(1_000_000.0 / 3, rel=0.02)


def test_engine_limit_up_open_drops_buy_one_shot() -> None:
    closes = _decline(60)
    closes.extend(_bull(45, start=closes[-1])[1:])
    df = _ohlc(closes)
    sig = compute_signals(df)
    res, dates = sig["resonance"], list(sig.index)
    fp = next(i for i in range(1, len(dates)) if res.iloc[i] and not res.iloc[i - 1])
    blocked = dates[fp + 1]
    pc = float(df.loc[df["trade_date"] == blocked, "pre_close"].iloc[0])
    df.loc[df["trade_date"] == blocked, "open"] = round(pc * 1.096, 2)  # 主板 +9.6% 开口

    trades, _ = run_six_vein(
        df, SixVeinConfig(), _D0, _D0 + timedelta(days=len(closes) - 1),
    )
    assert all(t.signal_date.strftime("%Y%m%d") != dates[fp] for t in trades), \
        "涨停开口被拒的翻转信号不应成交(one-shot 放弃,不追)"


def test_engine_limit_down_open_defers_sell_retry() -> None:
    closes = _decline(60)
    closes.extend(_bull(45, start=closes[-1])[1:])
    closes.extend(_decline(30, start=closes[-1])[1:])
    df = _ohlc(closes)
    sig = compute_signals(df)
    res, dates = sig["resonance"], list(sig.index)
    last_true = max(i for i in range(len(dates)) if res.iloc[i])
    blocked = dates[last_true + 1]
    pc = float(df.loc[df["trade_date"] == blocked, "pre_close"].iloc[0])
    df.loc[df["trade_date"] == blocked, "open"] = round(pc * 0.900, 2)  # 跌停开口

    trades, _ = run_six_vein(
        df, SixVeinConfig(), _D0, _D0 + timedelta(days=len(closes) - 1),
    )
    assert any(
        t.action == "SELL" and t.exec_date.strftime("%Y%m%d") == dates[last_true + 2]
        for t in trades
    ), "跌停拒单后应顺延至次一交易日开盘卖出"


# ── genome / registration ──


def test_genome_declares_only_max_positions() -> None:
    assert SIX_VEIN_GENOME.names() == ["max_positions"]
    SIX_VEIN_GENOME.validate({"max_positions": 4})
    with pytest.raises(KeyError):
        SIX_VEIN_GENOME.validate({"rsi_period": 5})


def test_adapter_rejects_undeclared_params(mock_client) -> None:
    adapter = SixVeinAdapter(params={"foo": 1})
    with pytest.raises(KeyError):
        adapter.run_window(mock_client, date(2024, 1, 2), date(2024, 3, 1))
    assert SixVeinAdapter().horizon == "event"
    assert SixVeinAdapter().name == "six_vein"


def test_default_participants_registers_six_vein() -> None:
    names = [p.name for p in default_participants(["000001.SZ"])]
    assert "six_vein" in names
