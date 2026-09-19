"""量价形态: C1/C2/C3 副本筛选 + 16 形态标签库(纯计算, spec §5.11/§5.12)."""

from __future__ import annotations

import math

import pandas as pd

from davis_analyzer.core.constants import PATTERN_PARAMS as PP

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


# ── 5.12 16 形态标签库(都标不互斥) ──

def detect_tags(
    px: pd.DataFrame, cyq: pd.Series | None,
    resistance_dist: float, position: dict,
) -> list[str]:
    tags: list[str] = []
    close = float(px["close"].iloc[-1])
    open_ = float(px["open"].iloc[-1])
    vma = _vma(px, int(PP["vma_period"]))
    vma_today = float(vma.iloc[-1]) if pd.notna(vma.iloc[-1]) else _NAN
    ratio = float(px["vol"].iloc[-1]) / vma_today if vma_today == vma_today and vma_today > 0 else _NAN
    pos250 = position.get("pos_250d", _NAN)

    # 位置组
    if pos250 == pos250 and ratio == ratio:
        if pos250 < PP["bottom_pos_max"] and ratio >= 2.0:
            tags.append("底部放量")
        upper_shadow = float(px["high"].iloc[-1]) - max(open_, close)
        body = abs(close - open_)
        crowded = bool(cyq is not None and pd.notna(cyq.get("winner_rate"))
                       and cyq["winner_rate"] >= PP["winner_crowd"])
        if (pos250 > PP["top_pos_min"] and ratio >= 2.0
                and (upper_shadow >= body * 0.5 or crowded)):
            tags.append("高位分歧")
    if len(px) >= 120:
        h250 = float(px["high"].tail(250).max())
        if h250 and close >= h250 * 0.995:
            tags.append("创新高")
        if pos250 == pos250 and pos250 < 0.15:
            t20 = px.tail(20)
            drop = 1 - float(t20["low"].min()) / float(t20["high"].max())
            if drop >= 0.25:
                tags.append("超跌反弹")

    # 突破组
    if len(px) > int(PP["plateau_days"]):
        plateau_high = float(px.iloc[-1 - int(PP["plateau_days"]): -1]["high"].max())
        if close > plateau_high:
            tags.append("平台突破")
    if len(px) > int(PP["box_days"]):
        box = px.iloc[-1 - int(PP["box_days"]): -1]
        if box["low"].min() > 0:
            box_range = float(box["high"].max()) / float(box["low"].min()) - 1
            if box_range <= PP["box_max_range"] and close > float(box["high"].max()):
                tags.append("箱体突破")
    if len(px) > 121:
        prior_high = float(px["high"].iloc[-121: -20].max())
        if close > prior_high:
            tags.append("前高突破")
    if len(px) >= 2 and float(px["low"].iloc[-1]) > float(px["high"].iloc[-2]):
        tags.append("跳空缺口")

    # 量能组
    if ratio == ratio and ratio >= PP["huge_vol_ratio"]:
        tags.append("天量")
    if len(px) >= 61:
        ma20v = float(px["vol"].tail(20).mean())
        pre20v = float(px["vol"].iloc[-40: -20].mean())
        if (close / float(px["close"].iloc[-21]) - 1 > 0
                and float(px["close"].tail(20).max()) >= float(px["close"].tail(60).max())
                and ma20v < pre20v):
            tags.append("量价背离")
        ma5v = float(px["vol"].tail(5).mean())
        pre5v = float(px["vol"].iloc[-10: -5].mean())
        if (vma_today == vma_today and vma_today > 0
                and 1.2 <= ma5v / vma_today <= 2.0 and ma5v > pre5v):
            tags.append("温和放量")

    # 筹码组
    if cyq is not None and pd.notna(cyq.get("cost_5pct")) and pd.notna(cyq.get("cost_95pct")):
        dense = float(cyq["cost_95pct"]) / float(cyq["cost_5pct"]) - 1
        if len(px) >= 120:
            lo250 = float(px["low"].tail(250).min())
            hi250 = float(px["high"].tail(250).max())
            wa_pos = ((float(cyq["weight_avg"]) - lo250) / (hi250 - lo250)
                      if hi250 > lo250 and pd.notna(cyq.get("weight_avg")) else _NAN)
            if dense <= PP["chip_dense_range"] and wa_pos == wa_pos and wa_pos < 0.40:
                tags.append("筹码低位密集")
        if pd.notna(cyq.get("winner_rate")) and cyq["winner_rate"] >= PP["winner_crowd"]:
            tags.append("获利盘拥挤")
    if resistance_dist is not None and resistance_dist == resistance_dist \
            and resistance_dist < PP["near_resist"]:
        tags.append("上方套牢近")

    # 趋势组(MA250 不满窗时取可得均值,tail().mean() 自然降级)
    if len(px) >= 120:
        mas = {n: float(px["close"].tail(n).mean()) for n in (20, 60, 120, 250)}
        if mas[20] > mas[60] > mas[120] > mas[250] and close > mas[20]:
            tags.append("均线多头")
    if len(px) >= 2:
        prev = px.iloc[-2]
        prev_yin = prev["close"] < prev["open"]
        today_yang = close > open_
        today_body = abs(close - open_)
        prev_body = abs(float(prev["close"]) - float(prev["open"]))
        if (prev_yin and today_yang and today_body >= prev_body
                and open_ <= float(prev["close"])):
            tags.append("大阳反包")
    return tags
