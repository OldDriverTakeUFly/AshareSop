"""六脉神剑共振策略 — 同花顺指标市场「浩坚六脉神剑」参赛者复刻.

六脉 = MACD(8,13,5) / KDJ(8,3,1) / RSI(5,13) / LWR(13,负值) /
BBI(3,5,8,13) / ZLMM(5,3,13,8)，全部由收盘价族派生（通达信公式口径）。
六脉全真 → 共振买入（信号收盘的次日开盘成交）；任一脉断剑 → 次日开盘卖出。

Engine rules:
* signals on adjusted prices (close/high/low × adj_factor), execution on
  raw open prices (除权日信号连续、成交价真实);
* T+1 by construction — both legs fill at the *next* trading day's open,
  so no same-day round trip can occur;
* 涨跌停拒单: open beyond the board-specific limit band (±10%/20%/30% by
  code suffix, 0.5% tolerance) is unfillable — buys are dropped (不追),
  sells retry on subsequent days (跌停板必须等到能卖);
* equal-weight slots (equity/max_positions), A-share 100-share lots;
* commission both sides + stamp duty on sells (project convention).
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd

from davis_analyzer.backtest import EquitySnapshot, Trade


# ── data loading ──


def load_window_prices(
    conn: sqlite3.Connection, codes: list[str] | None, start: str, end: str,
) -> pd.DataFrame:
    """Read OHLC + adj_factor for *codes* (``None`` = 全缓存) in one pass.

    Reads the shared ``daily_price`` cache directly (same pattern as
    :func:`davis_analyzer.tournament.adapters.liquidity_universe`) because
    ``TushareClient.get_daily_prices`` does not return high/low — and KDJ /
    LWR need the intraday extremes.
    """
    base = (
        "SELECT ts_code, trade_date, open, high, low, close, pre_close, "
        "amount, adj_factor FROM daily_price "
        "WHERE trade_date>=? AND trade_date<=? AND open IS NOT NULL "
        "AND close IS NOT NULL"
    )
    frames: list[pd.DataFrame] = []
    if codes is None:
        frames.append(pd.read_sql_query(base, conn, params=(start, end)))
    else:
        for i in range(0, len(codes), 500):
            chunk = codes[i : i + 500]
            q = base + f" AND ts_code IN ({','.join('?' * len(chunk))})"
            frames.append(
                pd.read_sql_query(q, conn, params=(start, end, *chunk))
            )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ── signal computation (TDX formula semantics) ──


def _sma_tdx(x: pd.Series, n: int, m: int = 1) -> pd.Series:
    """TDX ``SMA(X,N,M)``: ``y = (m·x + (N−m)·y′)/N``, seeded with first x.

    ``ewm(alpha=m/n, adjust=False)`` reproduces the recursion exactly
    (both seed ``y₀ = x₀`` and skip NaN samples by carrying state).
    """
    return x.ewm(alpha=m / n, adjust=False).mean()


def compute_signals(g: pd.DataFrame) -> pd.DataFrame:
    """Compute the six pulses + resonance for ONE stock (ascending trade_date).

    Input columns: trade_date, open, high, low, close, pre_close, amount,
    adj_factor (raw, unadjusted).  Output keeps the execution-side fields
    and adds six boolean pulse columns + ``resonance`` (six-way AND).
    NaN anywhere (warm-up head, flat HHV/LLV windows) compares False.
    """
    g = g.sort_values("trade_date").set_index("trade_date")
    adj = g["adj_factor"].astype(float).fillna(1.0)
    c = g["close"].astype(float) * adj
    h = g["high"].astype(float) * adj
    l = g["low"].astype(float) * adj
    chg = c - c.shift(1)

    # 1) MACD: DIFF = EMA(c,8) − EMA(c,13), DEA = EMA(DIFF,5)
    diff = c.ewm(span=8, adjust=False).mean() - c.ewm(span=13, adjust=False).mean()
    dea = diff.ewm(span=5, adjust=False).mean()
    p_macd = diff > dea

    # 2) KDJ(8,3,1): RSV over the 8-day high/low envelope
    hhv8 = h.rolling(8, min_periods=1).max()
    llv8 = l.rolling(8, min_periods=1).min()
    rsv = (c - llv8) / (hhv8 - llv8).replace(0.0, np.nan) * 100.0
    k = _sma_tdx(rsv, 3, 1)
    d = _sma_tdx(k, 3, 1)
    p_kdj = k > d

    # 3) RSI(5) vs RSI(13)
    up, ab = chg.clip(lower=0.0), chg.abs()
    rsi_fast = _sma_tdx(up, 5, 1) / _sma_tdx(ab, 5, 1) * 100.0
    rsi_slow = _sma_tdx(up, 13, 1) / _sma_tdx(ab, 13, 1) * 100.0
    p_rsi = rsi_fast > rsi_slow

    # 4) LWR(13): 负值威廉 — 0 = 贴近 13 日高点(强), −100 = 贴低(弱)
    hhv13 = h.rolling(13, min_periods=1).max()
    llv13 = l.rolling(13, min_periods=1).min()
    w = -(hhv13 - c) / (hhv13 - llv13).replace(0.0, np.nan) * 100.0
    lwr1 = _sma_tdx(w, 3, 1)
    lwr2 = _sma_tdx(lwr1, 3, 1)
    p_lwr = lwr1 > lwr2

    # 5) BBI(3,5,8,13): 多空均线
    bbi = sum(c.rolling(n, min_periods=n).mean() for n in (3, 5, 8, 13)) / 4.0
    p_bbi = c > bbi

    # 6) ZLMM(主力买卖): MTM 双平滑 MMS(5,3) vs MMM(13,8)
    mms = _sma_tdx(chg, 5, 1).rolling(3, min_periods=3).mean()
    mmm = _sma_tdx(chg, 13, 1).rolling(8, min_periods=8).mean()
    p_zlmm = mms > mmm

    pre_close = (
        g["pre_close"].astype(float) if "pre_close" in g.columns
        else g["close"].astype(float).shift(1)
    )
    return pd.DataFrame(
        {
            "open": g["open"].astype(float),
            "close": g["close"].astype(float),
            "pre_close": pre_close,
            "amount": g["amount"].astype(float),
            "p_macd": p_macd,
            "p_kdj": p_kdj,
            "p_rsi": p_rsi,
            "p_lwr": p_lwr,
            "p_bbi": p_bbi,
            "p_zlmm": p_zlmm,
            "resonance": p_macd & p_kdj & p_rsi & p_lwr & p_bbi & p_zlmm,
        }
    )


# ── resonance engine ──


@dataclass
class SixVeinConfig:
    """Engine knobs (only max_positions is genome-reachable)."""

    max_positions: int = 3
    initial_capital: float = 1_000_000.0
    commission_bps: float = 2.5
    stamp_tax_bps: float = 10.0


def _limit_ratio(ts_code: str) -> float:
    """Board-specific daily price limit (approximation; ST 5% not modelled)."""
    if ts_code.startswith(("688", "300", "301", "302")):
        return 0.20  # 科创板/创业板
    if ts_code.startswith(("83", "87", "92", "43")):
        return 0.30  # 北交所
    return 0.10


def _trade_cost(
    gross: float, commission_bps: float, stamp_tax_bps: float, is_sell: bool,
) -> float:
    """Commission applies both sides; stamp duty only on sells (A-share rule)."""
    commission = gross * commission_bps / 1e4
    stamp = gross * stamp_tax_bps / 1e4 if is_sell else 0.0
    return commission + stamp


def _to_date(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def run_six_vein(
    prices: pd.DataFrame, cfg: SixVeinConfig, start: date, end: date,
) -> tuple[list[Trade], list[EquitySnapshot]]:
    """Run the 六脉神剑 closed loop over one evaluation window.

    *prices* spans [warm-up start … end]; only dates inside [start, end]
    trade and mark (warm-up bars feed the indicator chains only).
    """
    signals: dict[str, pd.DataFrame] = {}
    flips: dict[str, set[str]] = {}
    for code, g in prices.groupby("ts_code", sort=True):
        sig = compute_signals(g)
        code = str(code)
        signals[code] = sig
        res = sig["resonance"].to_numpy()
        idx = sig.index.to_numpy()
        flips[code] = {
            idx[i] for i in range(1, len(idx)) if res[i] and not res[i - 1]
        }
    if not signals:
        return [], []

    calendar = sorted(
        {d for sig in signals.values() for d in sig.index
         if start <= _to_date(str(d)) <= end}
    )
    dates_sorted = {code: list(sig.index) for code, sig in signals.items()}

    def _last_before(code: str, d: str) -> str | None:
        arr = dates_sorted[code]
        i = bisect_left(arr, d)
        return arr[i - 1] if i >= 1 else None

    cash = cfg.initial_capital
    shares_held: dict[str, int] = {}
    last_close: dict[str, float] = {}
    trades: list[Trade] = []
    curve: list[EquitySnapshot] = []

    for d in calendar:
        # — 1) sells at open: held & resonance broken at last data date —
        for code in sorted(shares_held):
            sig = signals[code]
            t = _last_before(code, d)
            if t is None or bool(sig.at[t, "resonance"]):
                continue  # still resonant — hold
            if d not in sig.index:
                continue  # suspended today — retry next trading day
            o, pc = float(sig.at[d, "open"]), float(sig.at[d, "pre_close"])
            if not (o > 0 and pc > 0):
                continue
            if o <= pc * (1 - _limit_ratio(code) + 0.005):
                continue  # 跌停开口卖不出 — 次日再试
            n = shares_held.pop(code)
            gross = o * n
            cost = _trade_cost(gross, cfg.commission_bps, cfg.stamp_tax_bps, True)
            cash += gross - cost
            trades.append(Trade(
                signal_date=_to_date(t), exec_date=_to_date(d), ts_code=code,
                action="SELL", price=o, shares=n, amount=gross, cost=cost,
            ))

        # — 2) buys at open: fresh flips, liquidity-ranked, one-shot —
        slots = cfg.max_positions - len(shares_held)
        if slots > 0:
            candidates: list[tuple[float, str]] = []
            for code, sig in signals.items():
                if code in shares_held:
                    continue
                t = _last_before(code, d)
                if t is None or t not in flips[code]:
                    continue  # no fresh flip pending
                if d not in sig.index:
                    continue  # suspended — flip stays pending (one-shot lives on)
                o, pc = float(sig.at[d, "open"]), float(sig.at[d, "pre_close"])
                if not (o > 0 and pc > 0):
                    continue
                if o >= pc * (1 + _limit_ratio(code) - 0.005):
                    continue  # 涨停开口买不进 — 放弃,不追
                candidates.append((float(sig.at[t, "amount"]), code))
            candidates.sort(key=lambda x: (-x[0], x[1]))
            equity_now = cash + sum(
                n * last_close.get(code, 0.0) for code, n in shares_held.items()
            )
            target = equity_now / cfg.max_positions
            for _, code in candidates[:slots]:
                o = float(signals[code].at[d, "open"])
                budget = min(target, cash)
                n = int(budget / (o * (1 + cfg.commission_bps / 1e4)) // 100) * 100
                if n < 100:
                    continue
                gross = o * n
                cost = _trade_cost(gross, cfg.commission_bps, cfg.stamp_tax_bps, False)
                if gross + cost > cash:
                    continue
                cash -= gross + cost
                shares_held[code] = n
                trades.append(Trade(
                    signal_date=_to_date(_last_before(code, d)), exec_date=_to_date(d),
                    ts_code=code, action="BUY", price=o, shares=n,
                    amount=gross, cost=cost,
                ))

        # — 3) mark to market at close —
        for code, sig in signals.items():
            if d in sig.index:
                cl = float(sig.at[d, "close"])
                if cl == cl and cl > 0:  # NaN guard
                    last_close[code] = cl
        positions_value = sum(
            n * last_close.get(code, 0.0) for code, n in shares_held.items()
        )
        curve.append(EquitySnapshot(
            date=_to_date(d), equity=cash + positions_value,
            cash=cash, positions_value=positions_value,
        ))

    return trades, curve
