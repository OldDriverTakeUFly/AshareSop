"""量价形态: C1/C2/C3 副本筛选 + 16 形态标签库(纯计算, spec §5.11/§5.12)."""

from __future__ import annotations

import math

import pandas as pd

from davis_analyzer.constants import PATTERN_PARAMS as PP

_NAN = float("nan")


def _vma(px: pd.DataFrame, n: int) -> pd.Series:
    return px["vol"].rolling(n).mean()


def detect_pattern(px: pd.DataFrame) -> dict | None:
    """C1 量能纪律 ∧ C2 回调结构 ∧ C3 平台突破; 未命中→None.

    价格未复权口径(与现价一致);量用原始 vol。
    """
    if px is None or len(px) < int(PP["vma_period"]) + 5:
        return None
    close = float(px["close"].iloc[-1])
    vma = _vma(px, int(PP["vma_period"]))
    vma_today = vma.iloc[-1]
    if pd.isna(vma_today) or float(px["vol"].iloc[-1]) < vma_today:
        return None  # 突破日必须站上均量

    # C1: 近 vol_window 日(不含今日) vol<VMA 最长连续段 ≤2
    win = px.tail(int(PP["vol_window"]) + 1).iloc[:-1]
    below = (win["vol"].values
             < vma.loc[win.index].values).astype(int)
    streak = mx = 0
    for b in below:
        streak = streak + 1 if b else 0
        mx = max(mx, streak)
    if mx > int(PP["vol_max_below_streak"]):
        return None

    # C2: 放量阳锚(回看 boom_lookback_min~max 日前,取最近)
    boom_idx: int | None = None
    lo = len(px) - 1 - int(PP["boom_lookback_max"])
    hi = len(px) - 1 - int(PP["boom_lookback_min"])
    for i in range(hi, max(lo, 0) - 1, -1):
        row = px.iloc[i]
        if not (row["close"] > row["open"]):
            continue
        pct = (float(row["close"]) / float(px["close"].iloc[i - 1]) - 1) * 100
        if (pct >= PP["boom_pct_min"]
                and float(row["vol"]) >= PP["boom_vol_ratio"] * float(vma.iloc[i])):
            boom_idx = i
            break
    if boom_idx is None:
        return None
    boom = px.iloc[boom_idx]

    seg = px.iloc[boom_idx + 1: -1]  # 回调段(不含今日)
    if len(seg) < 4:
        return None
    seg_high = max(float(seg["high"].max()), float(boom["high"]))
    seg_low = float(seg["low"].min())
    depth = 1 - seg_low / seg_high
    if depth > PP["pullback_depth_max"] or seg_low < float(boom["low"]):
        return None
    half = len(seg) // 2
    v_front = float(seg["vol"].iloc[:half].mean())
    v_back = float(seg["vol"].iloc[half:].mean())
    if v_front <= 0 or v_back / v_front > PP["vol_decay_ratio"]:
        return None  # 量未萎缩

    def _avg_yin_body(rows: pd.DataFrame) -> float:
        bodies = (rows["close"] - rows["open"])
        yin = bodies[bodies < 0]
        return float(-yin.mean()) if len(yin) else 0.0  # 无阴线=更小(跌不动)

    if _avg_yin_body(seg.iloc[half:]) > _avg_yin_body(seg.iloc[:half]):
        return None  # 阴线未越来越小

    # C3: 平台突破
    plateau_high = float(px.iloc[-1 - int(PP["plateau_days"]): -1]["high"].max())
    if close <= plateau_high:
        return None

    boom_pct = (float(boom["close"]) / float(px["close"].iloc[boom_idx - 1]) - 1) * 100
    return {
        "boom_date": str(boom["trade_date"]),
        "boom_pct": boom_pct,
        "boom_vol_ratio": float(boom["vol"]) / float(vma.iloc[boom_idx]),
        "pullback_start": str(seg["trade_date"].iloc[0]),
        "pullback_end": str(seg["trade_date"].iloc[-1]),
        "pullback_depth": depth,
        "vol_decay": v_back / v_front,
        "plateau_high": plateau_high,
        "plateau_days": int(PP["plateau_days"]),
        "breakout_pct": close / plateau_high - 1,
    }
