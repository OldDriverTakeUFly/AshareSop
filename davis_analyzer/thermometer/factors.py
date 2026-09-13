"""五族因子(纯函数):水平+斜率正交化,截面 z 合成族分.

panel 契约: 长表按 (index_code, trade_date) 排序,列
index_code/trade_date/close/amount/vol/pct_change/main_net_pct/limit_ratio。
"""

from __future__ import annotations

import pandas as pd

from davis_analyzer.constants import (
    THERMOMETER_FAMILY_INNER_WEIGHTS,
    THERMOMETER_PRICE_VOLUME_DECAY,
)

_Z_CLIP = 3.0


def add_factor_columns(panel: pd.DataFrame) -> pd.DataFrame:
    """按 index_code 分组滚动计算 10 个子指标 + pv_decay(不改入参)."""
    df = panel.copy()
    g = df.groupby("index_code", sort=False)

    ret1 = g["close"].transform(lambda s: s / s.shift(1) - 1)
    ret3 = g["close"].transform(lambda s: s / s.shift(3) - 1)
    ret20 = g["close"].transform(lambda s: s / s.shift(20) - 1)
    df["mom_level"] = ret20
    df["mom_slope"] = ret3 - ret20 / 20

    df["flow_level"] = g["main_net_pct"].transform(lambda s: s.rolling(20).sum())
    df["flow_slope"] = (
        g["main_net_pct"].transform(lambda s: s.rolling(3).mean())
        - g["main_net_pct"].transform(lambda s: s.rolling(20).mean())
    )

    ma20_amt = g["amount"].transform(lambda s: s.rolling(20).mean())
    ma5_amt = g["amount"].transform(lambda s: s.rolling(5).mean())
    df["vol_level"] = df["amount"] / ma20_amt - 1
    df["vol_slope"] = ma5_amt / ma20_amt - 1

    ma20 = g["close"].transform(lambda s: s.rolling(20).mean())
    ma60 = g["close"].transform(lambda s: s.rolling(60).mean())
    hh60 = g["close"].transform(lambda s: s.rolling(60).max())
    align3 = ((df["close"] > ma20).astype(float) + (df["close"] > ma60).astype(float)
              + (ma20 > ma60).astype(float)) / 3.0
    df["trend_level"] = 0.5 * align3 + 0.5 * (df["close"] / hh60)
    # 上行天数占比:1 日收益方向 → bool → 按 index_code 滚动均值
    up1 = (ret1 > 0).astype(float)
    df["trend_slope"] = up1.groupby(df["index_code"]).transform(
        lambda s: s.rolling(20).mean())

    df["limit_level"] = g["limit_ratio"].transform(lambda s: s.rolling(20).mean())
    df["limit_slope"] = (
        g["limit_ratio"].transform(lambda s: s.rolling(5).mean())
        - g["limit_ratio"].transform(lambda s: s.rolling(20).mean())
    )

    # 量价交互(spec §5 规则2):价格方向 × 放量与否决定量能族衰减
    decay = THERMOMETER_PRICE_VOLUME_DECAY
    same_dir = ret1 > 0
    amplified = df["vol_level"] > 0
    df["pv_decay"] = [
        (decay["opposite"] if not up
         else decay["amplified_same"] if amp else decay["shrinking_same"])
        for up, amp in zip(same_dir, amplified)
    ]
    return df


def cross_section_z(panel: pd.DataFrame, col: str) -> pd.Series:
    """当日截面 z-score(winsorize clip ±3),index 与 panel 对齐;std=0 → 全 0."""
    z = panel.groupby("trade_date")[col].transform(
        lambda s: (s - s.mean()) / s.std(ddof=0) if s.std(ddof=0) > 0 else s * 0.0)
    return z.clip(-_Z_CLIP, _Z_CLIP)


def family_scores(panel: pd.DataFrame) -> pd.DataFrame:
    """各族 水平z×w_level + 斜率z×w_slope → 族分;量能族乘 pv_decay."""
    df = panel.copy()
    wl_m, ws_m = THERMOMETER_FAMILY_INNER_WEIGHTS["momentum"]
    df["mom_score"] = (wl_m * cross_section_z(df, "mom_level")
                       + ws_m * cross_section_z(df, "mom_slope"))
    wl_f, ws_f = THERMOMETER_FAMILY_INNER_WEIGHTS["flow"]
    df["flow_score"] = (wl_f * cross_section_z(df, "flow_level")
                        + ws_f * cross_section_z(df, "flow_slope"))
    wl_v, ws_v = THERMOMETER_FAMILY_INNER_WEIGHTS["volume"]
    df["vol_score"] = (wl_v * cross_section_z(df, "vol_level")
                       + ws_v * cross_section_z(df, "vol_slope"))
    wl_t, ws_t = THERMOMETER_FAMILY_INNER_WEIGHTS["trend"]
    df["trend_score"] = (wl_t * cross_section_z(df, "trend_level")
                         + ws_t * cross_section_z(df, "trend_slope"))
    wl_l, ws_l = THERMOMETER_FAMILY_INNER_WEIGHTS["limit"]
    df["limit_score"] = (wl_l * cross_section_z(df, "limit_level")
                         + ws_l * cross_section_z(df, "limit_slope"))
    # 量价交互:量能族分数 × 价格方向衰减(spec §5 规则2)
    df["vol_score"] = df["vol_score"] * df["pv_decay"]
    return df
