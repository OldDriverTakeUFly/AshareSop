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
