"""Technical indicators — basic set (MA, support/resistance, volume-price).

Implements functions from the frozen contract
(:mod:`stockhot.technical_analyzer.contract`).  All functions consume
the standard OHLCV DataFrame (English columns, DatetimeIndex,
ascending).  Advanced indicators (RSI/MACD/KDJ/Bollinger) are appended
below by T12.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

try:
    import pandas_ta as ta

    _HAS_PANDAS_TA = True
except ImportError:
    _HAS_PANDAS_TA = False


# ── Basic indicators (T11) ─────────────────────────────────────────────────


def ma(df: pd.DataFrame, period: int) -> pd.Series:
    """Simple Moving Average of close prices."""
    return df["close"].rolling(window=period).mean()


def support_resistance(df: pd.DataFrame, lookback: int = 60) -> dict:
    """Identify key support and resistance levels via local extrema.

    Scans the last *lookback* rows (or all if fewer) for local minima
    (support) and local maxima (resistance) in the close-price series.
    A point is a local extremum if it is higher/lower than both
    neighbours.
    """
    window = df.tail(lookback)
    closes = window["close"].values
    n = len(closes)

    support: list[float] = []
    resistance: list[float] = []

    if n < 3:
        if n > 0:
            current = float(closes[-1])
            return {"support": [], "resistance": [], "current_price": current}
        return {"support": [], "resistance": [], "current_price": 0.0}

    for i in range(1, n - 1):
        if closes[i] < closes[i - 1] and closes[i] < closes[i + 1]:
            support.append(float(closes[i]))
        elif closes[i] > closes[i - 1] and closes[i] > closes[i + 1]:
            resistance.append(float(closes[i]))

    support = sorted(set(support), reverse=True)
    resistance = sorted(set(resistance))

    return {
        "support": support,
        "resistance": resistance,
        "current_price": float(closes[-1]),
    }


def volume_price_analysis(df: pd.DataFrame) -> dict:
    """Analyse volume-price relationship.

    Compares the most recent trading day's volume against the rolling
    mean of the preceding window and classifies the joint volume / price
    direction.

    Returns a dict matching the frozen contract:
      - ``volume_trend``: ``"increasing"`` / ``"decreasing"`` / ``"flat"``
      - ``price_volume_divergence``: bool
      - ``recent_avg_volume``: float
      - ``volume_ratio``: float
    """
    window = min(20, len(df))
    if window < 2:
        return {
            "volume_trend": "flat",
            "price_volume_divergence": False,
            "recent_avg_volume": 0.0,
            "volume_ratio": 0.0,
        }

    recent_vol = float(df["volume"].iloc[-1])
    avg_vol = float(df["volume"].iloc[:-1].tail(window - 1).mean())

    if avg_vol == 0 or recent_vol == 0:
        return {
            "volume_trend": "flat",
            "price_volume_divergence": False,
            "recent_avg_volume": avg_vol,
            "volume_ratio": 0.0,
        }

    ratio = recent_vol / avg_vol

    if ratio > 1.2:
        vol_trend = "increasing"
    elif ratio < 0.8:
        vol_trend = "decreasing"
    else:
        vol_trend = "flat"

    price_change = float(df["close"].iloc[-1]) - float(df["close"].iloc[-2])
    price_up = price_change > 0
    vol_up = ratio > 1.0

    divergence = price_up != vol_up

    return {
        "volume_trend": vol_trend,
        "price_volume_divergence": divergence,
        "recent_avg_volume": avg_vol,
        "volume_ratio": ratio,
    }


# ── Advanced indicators (T12) ──────────────────────────────────────────────


def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index (Wilder's smoothing).

    Args:
        df: OHLCV DataFrame with a ``close`` column.
        period: RSI window, default 14.

    Returns:
        pandas Series of RSI values in range [0, 100], same index as
        *df*.  Values are ``NaN`` during warm-up.
    """
    if _HAS_PANDAS_TA:
        try:
            result = ta.rsi(df["close"], length=period)
            result.name = "rsi"
            return result
        except Exception:
            pass

    return _rsi_manual(df["close"], period)


def _rsi_manual(close: pd.Series, period: int = 14) -> pd.Series:
    """Fallback Wilder's RSI when pandas-ta is unavailable."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi_val = 100 - (100 / (1 + rs))
    rsi_val.name = "rsi"
    return rsi_val


def macd(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate MACD (Moving Average Convergence Divergence).

    Uses standard parameters: fast EMA = 12, slow EMA = 26, signal = 9.

    Args:
        df: OHLCV DataFrame with a ``close`` column.

    Returns:
        DataFrame with three columns: ``macd_dif`` (DIF line),
        ``macd_dea`` (DEA signal line), ``macd_hist`` (histogram bar).
        Same index as *df*.
    """
    fast, slow, signal = 12, 26, 9

    if _HAS_PANDAS_TA:
        try:
            result = ta.macd(df["close"], fast=fast, slow=slow, signal=signal)
            col_dif = f"MACD_{fast}_{slow}_{signal}"
            col_hist = f"MACDh_{fast}_{slow}_{signal}"
            col_sig = f"MACDs_{fast}_{slow}_{signal}"
            return result.rename(
                columns={
                    col_dif: "macd_dif",
                    col_hist: "macd_hist",
                    col_sig: "macd_dea",
                }
            )
        except Exception:
            pass

    return _macd_manual(df["close"], fast, slow, signal)


def _macd_manual(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Fallback MACD when pandas-ta is unavailable."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return pd.DataFrame(
        {"macd_dif": dif, "macd_dea": dea, "macd_hist": hist},
        index=close.index,
    )


def kdj(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate KDJ stochastic oscillator.

    Uses standard parameters: RSV window = 9, K smooth = 3, D smooth = 3.

    Args:
        df: OHLCV DataFrame with ``high, low, close`` columns.

    Returns:
        DataFrame with three columns: ``k``, ``d``, ``j``.
        Same index as *df*.  Values are ``NaN`` during warm-up.
    """
    if _HAS_PANDAS_TA:
        try:
            result = ta.kdj(df["high"], df["low"], df["close"], length=9, signal=3)
            return result.rename(
                columns={
                    "K_9_3": "k",
                    "D_9_3": "d",
                    "J_9_3": "j",
                }
            )
        except Exception:
            pass

    return _kdj_manual(df["high"], df["low"], df["close"])


def _kdj_manual(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.DataFrame:
    """Fallback KDJ when pandas-ta is unavailable."""
    low_list = low.rolling(window=9, min_periods=1).min()
    high_list = high.rolling(window=9, min_periods=1).max()
    rsv = np.where(
        high_list == low_list,
        0,
        (close - low_list) / (high_list - low_list) * 100,
    )

    k = pd.Series(np.nan, index=close.index, dtype=float)
    d = pd.Series(np.nan, index=close.index, dtype=float)

    k_val = 50.0
    d_val = 50.0
    for i in range(len(close)):
        if np.isnan(rsv[i]):
            k.iloc[i] = np.nan
            d.iloc[i] = np.nan
            continue
        k_val = (2 / 3) * k_val + (1 / 3) * rsv[i]
        d_val = (2 / 3) * d_val + (1 / 3) * k_val
        k.iloc[i] = k_val
        d.iloc[i] = d_val

    j = 3 * k - 2 * d
    return pd.DataFrame({"k": k, "d": d, "j": j}, index=close.index)


def bollinger(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Calculate Bollinger Bands.

    Args:
        df: OHLCV DataFrame with a ``close`` column.
        period: Rolling window, default 20.

    Returns:
        DataFrame with three columns: ``boll_upper`` (upper band),
        ``boll_mid`` (middle band / SMA), ``boll_lower`` (lower band).
        Bands use 2 standard deviations.  Same index as *df*.
    """
    std_dev = 2

    if _HAS_PANDAS_TA:
        try:
            result = ta.bbands(df["close"], length=period, std=std_dev)
            upper_col = [c for c in result.columns if c.startswith("BBU")]
            mid_col = [c for c in result.columns if c.startswith("BBM")]
            lower_col = [c for c in result.columns if c.startswith("BBL")]

            if upper_col and mid_col and lower_col:
                return pd.DataFrame(
                    {
                        "boll_upper": result[upper_col[0]],
                        "boll_mid": result[mid_col[0]],
                        "boll_lower": result[lower_col[0]],
                    },
                    index=result.index,
                )
        except Exception:
            pass

    return _bollinger_manual(df["close"], period, std_dev)


def _bollinger_manual(close: pd.Series, period: int = 20, std_dev: int = 2) -> pd.DataFrame:
    """Fallback Bollinger Bands when pandas-ta is unavailable."""
    mid = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return pd.DataFrame(
        {"boll_upper": upper, "boll_mid": mid, "boll_lower": lower},
        index=close.index,
    )
