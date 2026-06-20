"""Composite technical state scoring — weighted multi-indicator score (0-100).

Implements ``composite_technical_score`` from the frozen contract
(:mod:`stockhot.technical_analyzer.contract`).  Imports all indicator
functions from :mod:`stockhot.technical_analyzer.indicators`.

Weights (hardcoded — source: A-share technical analysis convention):
    MA arrangement      30%
    RSI                 15%
    MACD                20%
    KDJ                 15%
    Bollinger position  10%
    Volume-price        10%
    ────────────────────────
    Total              100%
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from stockhot.technical_analyzer.indicators import (
    bollinger,
    kdj,
    ma,
    macd,
    rsi,
    volume_price_analysis,
)

_WEIGHTS = {
    "ma_arrangement": 0.30,
    "rsi": 0.15,
    "macd": 0.20,
    "kdj": 0.15,
    "bollinger": 0.10,
    "volume_price": 0.10,
}

_STRONG_THRESHOLD = 65.0
_WEAK_THRESHOLD = 35.0


def _is_nan(val: Any) -> bool:
    if val is None:
        return True
    try:
        return math.isnan(float(val))
    except (TypeError, ValueError):
        return True


def composite_technical_score(df: pd.DataFrame) -> dict:
    signals: list[dict] = []

    ma5 = ma(df, 5)
    ma10 = ma(df, 10)
    ma20 = ma(df, 20)

    ma5_v = ma5.iloc[-1] if len(ma5) > 0 else float("nan")
    ma10_v = ma10.iloc[-1] if len(ma10) > 0 else float("nan")
    ma20_v = ma20.iloc[-1] if len(ma20) > 0 else float("nan")

    if not _is_nan(ma5_v) and not _is_nan(ma10_v) and not _is_nan(ma20_v):
        bullish = ma5_v > ma10_v > ma20_v
        bearish = ma5_v < ma10_v < ma20_v
        hit = bool(bullish)
        contribution = 1.0 if bullish else (0.0 if bearish else 0.5)
    else:
        hit = False
        contribution = 0.5
    signals.append({"name": "ma_arrangement", "weight": _WEIGHTS["ma_arrangement"], "hit": hit})
    ma_score = contribution * 100.0

    rsi_series = rsi(df)
    rsi_v = rsi_series.iloc[-1] if len(rsi_series) > 0 else float("nan")
    if not _is_nan(rsi_v):
        hit = bool(rsi_v > 50.0)
        contribution = float(rsi_v) / 100.0
    else:
        hit = False
        contribution = 0.5
    signals.append({"name": "rsi", "weight": _WEIGHTS["rsi"], "hit": hit})
    rsi_score = contribution * 100.0

    macd_df = macd(df)
    if len(macd_df) >= 2:
        hist_latest = macd_df["macd_hist"].iloc[-1]
        hist_prev = macd_df["macd_hist"].iloc[-2]
        if not _is_nan(hist_latest) and not _is_nan(hist_prev):
            hit = bool(hist_latest > 0)
            contribution = 1.0 if hist_latest > 0 else (0.0 if hist_latest < 0 else 0.5)
        else:
            hit = False
            contribution = 0.5
    else:
        hit = False
        contribution = 0.5
    signals.append({"name": "macd", "weight": _WEIGHTS["macd"], "hit": hit})
    macd_score = contribution * 100.0

    kdj_df = kdj(df)
    if len(kdj_df) > 0:
        k_v = kdj_df["k"].iloc[-1]
        d_v = kdj_df["d"].iloc[-1]
        if not _is_nan(k_v) and not _is_nan(d_v):
            hit = bool(k_v > d_v)
            contribution = max(0.0, min(1.0, float(k_v) / 100.0))
        else:
            hit = False
            contribution = 0.5
    else:
        hit = False
        contribution = 0.5
    signals.append({"name": "kdj", "weight": _WEIGHTS["kdj"], "hit": hit})
    kdj_score = contribution * 100.0

    boll_df = bollinger(df)
    close_v = df["close"].iloc[-1] if len(df) > 0 else float("nan")
    if len(boll_df) > 0:
        upper = boll_df["boll_upper"].iloc[-1]
        lower = boll_df["boll_lower"].iloc[-1]
        if not _is_nan(upper) and not _is_nan(lower) and not _is_nan(close_v):
            band_width = upper - lower
            if band_width > 0:
                position = (float(close_v) - float(lower)) / band_width
                hit = bool(position < 0.3)
                contribution = max(0.0, min(1.0, 1.0 - position))
            else:
                hit = False
                contribution = 0.5
        else:
            hit = False
            contribution = 0.5
    else:
        hit = False
        contribution = 0.5
    signals.append({"name": "bollinger", "weight": _WEIGHTS["bollinger"], "hit": hit})
    boll_score = contribution * 100.0

    vp = volume_price_analysis(df)
    vp_trend = vp.get("volume_trend", "flat")
    vp_divergence = vp.get("price_volume_divergence", False)
    if vp_trend == "increasing" and not vp_divergence:
        hit = True
        contribution = 1.0
    elif vp_trend == "decreasing" and not vp_divergence:
        hit = False
        contribution = 0.0
    elif vp_divergence:
        hit = False
        contribution = 0.3
    else:
        hit = False
        contribution = 0.5
    signals.append({"name": "volume_price", "weight": _WEIGHTS["volume_price"], "hit": hit})
    vp_score = contribution * 100.0

    total_score = (
        ma_score * _WEIGHTS["ma_arrangement"]
        + rsi_score * _WEIGHTS["rsi"]
        + macd_score * _WEIGHTS["macd"]
        + kdj_score * _WEIGHTS["kdj"]
        + boll_score * _WEIGHTS["bollinger"]
        + vp_score * _WEIGHTS["volume_price"]
    )

    total_score = max(0.0, min(100.0, total_score))

    if total_score > _STRONG_THRESHOLD:
        state = "强势"
    elif total_score < _WEAK_THRESHOLD:
        state = "弱势"
    else:
        state = "震荡"

    return {
        "state": state,
        "score": round(total_score, 2),
        "signals": signals,
    }
