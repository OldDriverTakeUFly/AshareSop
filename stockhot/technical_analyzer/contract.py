"""Technical analysis engine — frozen API contract.

This module freezes the function signatures for the technical analysis
engine *before* implementation.  All functions ``raise NotImplementedError``
so that downstream tasks (data_loader, indicator implementations) can
proceed against a stable contract.

Indicator set is LOCKED to nine functions — no WR, CCI, OBV, ATR, or DMI
will be added.  No TA-Lib, no plotting, no matplotlib/HTML.

Data format contract (all indicator functions):
    * DataFrame columns are English: ``open, high, low, close, volume``.
    * Index is ``pandas.DatetimeIndex`` (daily frequency).
    * Rows are in ascending chronological order (oldest first).
"""

from __future__ import annotations

import pandas as pd


# ── Data access ────────────────────────────────────────────────────────────


def fetch_ohlcv(
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str = "qfq",
) -> pd.DataFrame:
    """Fetch OHLCV daily data for a stock symbol from AKShare.

    Args:
        symbol: Stock code, e.g. ``"000001"`` or ``"sh600519"``.
        start_date: Start date in ``"YYYY-MM-DD"`` format.
        end_date: End date in ``"YYYY-MM-DD"`` format (inclusive).
        adjust: Price adjustment type — ``"qfq"`` (前复权, default),
            ``"hfq"`` (后复权), or ``""`` (none/raw).

    Returns:
        DataFrame with English columns ``open, high, low, close, volume``
        and a ``DatetimeIndex``, sorted ascending.  Empty DataFrame if no
        data.

    Raises:
        NotImplementedError: Always — implementation deferred to T7.
    """
    raise NotImplementedError


# ── Trend indicators ───────────────────────────────────────────────────────


def ma(df: pd.DataFrame, period: int) -> pd.Series:
    """Calculate Simple Moving Average of close prices.

    Args:
        df: OHLCV DataFrame with a ``close`` column.
        period: Lookback window in trading days (e.g. 5, 10, 20, 60).

    Returns:
        pandas Series of moving average values, same index as *df*.
        The first ``period - 1`` entries are ``NaN`` (warm-up).

    Raises:
        NotImplementedError: Always — implementation deferred to T11.
    """
    raise NotImplementedError


def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index (Wilder's smoothing).

    Args:
        df: OHLCV DataFrame with a ``close`` column.
        period: RSI window, default 14.

    Returns:
        pandas Series of RSI values in range [0, 100], same index as
        *df*.  Values are ``NaN`` during warm-up.

    Raises:
        NotImplementedError: Always — implementation deferred to T11.
    """
    raise NotImplementedError


def macd(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate MACD (Moving Average Convergence Divergence).

    Uses standard parameters: fast EMA = 12, slow EMA = 26, signal = 9.

    Args:
        df: OHLCV DataFrame with a ``close`` column.

    Returns:
        DataFrame with three columns: ``macd_dif`` (DIF line),
        ``macd_dea`` (DEA signal line), ``macd_hist`` (histogram bar).
        Same index as *df*.

    Raises:
        NotImplementedError: Always — implementation deferred to T11.
    """
    raise NotImplementedError


# ── Oscillator indicators ──────────────────────────────────────────────────


def kdj(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate KDJ stochastic oscillator.

    Uses standard parameters: RSV window = 9, K smooth = 3, D smooth = 3.

    Args:
        df: OHLCV DataFrame with ``high, low, close`` columns.

    Returns:
        DataFrame with three columns: ``k``, ``d``, ``j``.
        Same index as *df*.  Values are ``NaN`` during warm-up.

    Raises:
        NotImplementedError: Always — implementation deferred to T11.
    """
    raise NotImplementedError


def bollinger(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Calculate Bollinger Bands.

    Args:
        df: OHLCV DataFrame with a ``close`` column.
        period: Rolling window, default 20.

    Returns:
        DataFrame with three columns: ``boll_upper`` (upper band),
        ``boll_mid`` (middle band / SMA), ``boll_lower`` (lower band).
        Bands use 2 standard deviations.  Same index as *df*.

    Raises:
        NotImplementedError: Always — implementation deferred to T11.
    """
    raise NotImplementedError


# ── Structural analysis ────────────────────────────────────────────────────


def support_resistance(df: pd.DataFrame, lookback: int = 60) -> dict:
    """Identify key support and resistance price levels.

    Args:
        df: OHLCV DataFrame with ``high, low, close`` columns.
        lookback: Number of recent trading days to scan, default 60.

    Returns:
        Dict with keys:
          - ``"support"``: list[float] — support price levels (descending).
          - ``"resistance"``: list[float] — resistance price levels
            (ascending).
          - ``"current_price"``: float — latest close.

    Raises:
        NotImplementedError: Always — implementation deferred to T12.
    """
    raise NotImplementedError


def volume_price_analysis(df: pd.DataFrame) -> dict:
    """Analyze volume-price relationship patterns.

    Args:
        df: OHLCV DataFrame with ``close, volume`` columns.

    Returns:
        Dict with keys:
          - ``"volume_trend"``: str — ``"increasing"`` / ``"decreasing"``
            / ``"flat"``.
          - ``"price_volume_divergence"``: bool — True if price and
            volume diverge.
          - ``"recent_avg_volume"``: float — mean volume over recent
            window.
          - ``"volume_ratio"``: float — latest volume / recent average.

    Raises:
        NotImplementedError: Always — implementation deferred to T12.
    """
    raise NotImplementedError


def composite_technical_score(df: pd.DataFrame) -> dict:
    """Calculate composite technical state score from all indicators.

    Aggregates MA trend, RSI, MACD, KDJ, Bollinger position,
    support/resistance proximity, and volume-price into a single
    state classification and numeric score.

    Args:
        df: OHLCV DataFrame with all required columns.

    Returns:
        Dict with keys:
          - ``"state"``: str — one of ``"bullish"``, ``"bearish"``,
            ``"neutral"``, ``"overbought"``, ``"oversold"``.
          - ``"score"``: float — composite score in range [0, 100]
            (50 = neutral, >50 bullish, <50 bearish).
          - ``"signals"``: list[str] — human-readable signal
            descriptions from individual indicators.

    Raises:
        NotImplementedError: Always — implementation deferred to T12.
    """
    raise NotImplementedError
