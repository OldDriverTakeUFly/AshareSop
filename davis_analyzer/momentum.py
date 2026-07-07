"""Price-momentum + relative-strength (RS) engine.

The 4-dimension Davis Double model has no true price-momentum leg —
``trend.py`` measures the *valuation* (PE/PB) trend, not price action. This
module fills the CANSLIM "M" (market direction / momentum) and "R" (relative
strength vs peers) legs that the methodology docs call for but the engine
never implemented.

Two sub-scores (each 0–100):

* ``absolute_momentum_score`` — multi-window (60/120/250d) blended adjusted
  return. Uses ``adj_close = close × adj_factor`` so returns are correct
  across ex-dividend days (naïve ``pct_chg`` compounding is biased there).
  Each window's annualised return is mapped to 0–100 and weighted by
  ``MOMENTUM_WINDOW_WEIGHTS``.

* ``rs_percentile`` — the stock's longest-window return rank within its
  industry peer set (0–100). This is "how strong vs peers", independent of
  absolute level.

``momentum_score`` = 0.5 × absolute + 0.5 × RS.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

from davis_analyzer.constants import (
    MOMENTUM_FULL_ANNUAL_RETURN,
    MOMENTUM_MIN_PRICES,
    MOMENTUM_WINDOW_WEIGHTS,
    MOMENTUM_WINDOWS_DAYS,
)
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import MomentumSignal

if TYPE_CHECKING:
    from davis_analyzer.types import StockInfo

# We fetch ~1 year more than the longest window so the longest-window return
# has a real base price, and to support RS over a stable window.
_LOOKBACK_DAYS = max(MOMENTUM_WINDOWS_DAYS) + 30


def _annualised_return(first_adj: float, last_adj: float, days: int) -> float:
    """Annualised % return between two adjusted prices over *days* calendar days."""
    if first_adj <= 0 or days <= 0:
        return 0.0
    total_return = last_adj / first_adj - 1.0
    if total_return <= -1.0:
        return -100.0
    years = days / 365.0
    ann = math.pow(1.0 + total_return, 1.0 / years) - 1.0
    return ann * 100.0


def _return_to_score(ann_return_pct: float) -> float:
    """Map an annualised return (%) to 0–100.

    0% annualised → 50; +FULL_ANNUAL_RETURN → 100; -FULL → 0. Symmetric so
    sharp drawdowns score near 0 and strong rallies near 100.
    """
    score = 50.0 + (ann_return_pct / MOMENTUM_FULL_ANNUAL_RETURN) * 50.0
    return max(0.0, min(100.0, score))


def _window_returns(adj_series: pd.Series, dates: pd.Series) -> dict[int, float]:
    """Compute annualised return for each window in MOMENTUM_WINDOWS_DAYS.

    ``adj_series`` / ``dates`` are ascending by trade_date. Returns {} when
    fewer than MOMENTUM_MIN_PRICES rows exist (insufficient data).
    """
    if len(adj_series) < MOMENTUM_MIN_PRICES:
        return {}

    # Map trade_date (YYYYMMDD) → datetime for window slicing.
    dt = pd.to_datetime(dates.astype(str), format="%Y%m%d", errors="coerce")
    frame = pd.DataFrame({"adj": adj_series.values}, index=dt)

    out: dict[int, float] = {}
    latest_dt = frame.index.max()
    for window in MOMENTUM_WINDOWS_DAYS:
        cutoff = latest_dt - pd.Timedelta(days=window)
        window_frame = frame[frame.index >= cutoff]
        if len(window_frame) < 2:
            continue
        first = float(window_frame["adj"].iloc[0])
        last = float(window_frame["adj"].iloc[-1])
        out[window] = round(_annualised_return(first, last, window), 2)
    return out


def _absolute_score(window_returns: dict[int, float]) -> float:
    """Blend per-window annualised returns into a 0–100 score.

    Falls back to 50.0 (neutral) when no window has data.
    """
    if not window_returns:
        return 50.0
    total_w = 0.0
    weighted = 0.0
    for window, weight in zip(MOMENTUM_WINDOWS_DAYS, MOMENTUM_WINDOW_WEIGHTS):
        if window in window_returns:
            weighted += _return_to_score(window_returns[window]) * weight
            total_w += weight
    if total_w == 0:
        return 50.0
    return max(0.0, min(100.0, weighted / total_w))


def compute_rs_percentile(
    stock_returns: dict[str, float],
    stock_infos: dict[str, StockInfo],
) -> dict[str, float | None]:
    """Rank each stock's longest-window return within its industry.

    Parameters
    ----------
    stock_returns : dict[str, float]
        ts_code → longest-window annualised return (%). Missing stocks get None.
    stock_infos : dict[str, StockInfo]
        Used to group by industry.

    Returns
    -------
    dict[str, float | None]
        ts_code → RS percentile (0–100, higher = stronger vs peers). Stocks in
        industries with < 2 ranked peers get None.
    """
    # Group returns by industry.
    industry_returns: dict[str, list[float]] = {}
    code_industry: dict[str, str] = {}
    for ts_code, ret in stock_returns.items():
        info = stock_infos.get(ts_code)
        if info is None:
            continue
        industry_returns.setdefault(info.industry, []).append(ret)
        code_industry[ts_code] = info.industry

    rs: dict[str, float | None] = {}
    for ts_code, ret in stock_returns.items():
        industry = code_industry.get(ts_code)
        if industry is None:
            rs[ts_code] = None
            continue
        peers = industry_returns.get(industry, [])
        if len(peers) < 2:
            rs[ts_code] = None
            continue
        # Percentile = fraction of peers at or below this stock's return.
        count_le = sum(1 for p in peers if p <= ret)
        rs[ts_code] = round(count_le / len(peers) * 100.0, 2)
    return rs


def analyze_momentum(
    client: TushareClient,
    ts_code: str,
    today: date | None = None,
) -> MomentumSignal | None:
    """Build a MomentumSignal for *ts_code*, or None when price data is empty.

    RS is NOT computed here (it needs the peer set); call
    :func:`compute_rs_percentile` separately over the full universe and fold
    the result in, or use :func:`analyze_momentum_batch`.
    """
    ref = today or date.today()
    end = ref.strftime("%Y%m%d")
    start = (ref - pd.Timedelta(days=_LOOKBACK_DAYS)).strftime("%Y%m%d")
    try:
        df = client.get_daily_prices(ts_code, start, end)
    except Exception:
        logger.exception("daily price fetch failed for {}", ts_code)
        return None

    if df is None or df.empty:
        return None

    df = df.copy()
    df = df.sort_values("trade_date")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["adj_factor"] = pd.to_numeric(df["adj_factor"], errors="coerce").fillna(1.0)
    df = df.dropna(subset=["close"])
    if df.empty:
        return None

    df["adj_close"] = df["close"] * df["adj_factor"]

    window_returns = _window_returns(df["adj_close"], df["trade_date"])
    abs_score = _absolute_score(window_returns)
    sufficient = len(window_returns) >= 1

    return MomentumSignal(
        ts_code=ts_code,
        window_returns=window_returns,
        absolute_momentum_score=round(abs_score, 2),
        rs_percentile=None,  # filled in by analyze_momentum_batch
        momentum_score=round(abs_score, 2),  # RS blended in batch path
        data_sufficient=sufficient,
    )


def analyze_momentum_batch(
    client: TushareClient,
    stock_infos: dict[str, StockInfo],
    today: date | None = None,
) -> dict[str, MomentumSignal]:
    """Compute momentum + RS for a universe of stocks.

    Returns one MomentumSignal per stock that had usable price data, with
    ``rs_percentile`` and the RS-blended ``momentum_score`` populated.
    """
    signals: dict[str, MomentumSignal] = {}
    longest_window = max(MOMENTUM_WINDOWS_DAYS)

    for ts_code in stock_infos:
        sig = analyze_momentum(client, ts_code, today=today)
        if sig is None:
            continue
        signals[ts_code] = sig

    # Build longest-window return map for RS ranking.
    longest_returns = {
        code: sig.window_returns[longest_window]
        for code, sig in signals.items()
        if longest_window in sig.window_returns
    }
    rs_map = compute_rs_percentile(longest_returns, stock_infos)

    for code, sig in signals.items():
        rs = rs_map.get(code)
        sig.rs_percentile = rs
        # Blend: absolute 50% + RS 50% when RS available, else absolute only.
        if rs is not None:
            sig.momentum_score = round(0.5 * sig.absolute_momentum_score + 0.5 * rs, 2)
        else:
            sig.momentum_score = sig.absolute_momentum_score

    return signals
