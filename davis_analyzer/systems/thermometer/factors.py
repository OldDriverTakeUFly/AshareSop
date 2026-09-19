"""五族因子(纯函数,v2 中期窗口):水平+斜率正交化,截面 z 合成族分.

panel 契约: 长表按 (index_code, trade_date) 排序,列
index_code/trade_date/close/amount/vol/pct_change/main_net_pct/limit_ratio。
窗口单一真相源 constants.THERMOMETER_WINDOWS(2026-09-13 v2 中期重构)。
"""

from __future__ import annotations

import pandas as pd

from davis_analyzer.core.constants import (
    THERMOMETER_FAMILY_INNER_WEIGHTS,
    THERMOMETER_PRICE_VOLUME_DECAY,
    THERMOMETER_WINDOWS,
)

_Z_CLIP = 3.0


def add_factor_columns(panel: pd.DataFrame) -> pd.DataFrame:
    """按 index_code 分组滚动计算 10 个子指标 + pv_decay(不改入参).

    v2 中期口径:momentum (60,120) / flow (20,60) / volume (20,120) /
    trend (60,120) / limit (20,60);量价交互方向判据 = ret60。
    """
    df = panel.copy()
    g = df.groupby("index_code", sort=False)

    m_fast, m_slow = THERMOMETER_WINDOWS["momentum"]
    ret_f = g["close"].transform(lambda s: s / s.shift(m_fast) - 1)
    ret_s = g["close"].transform(lambda s: s / s.shift(m_slow) - 1)
    ret1 = g["close"].transform(lambda s: s / s.shift(1) - 1)
    df["mom_level"] = 0.5 * ret_f + 0.5 * ret_s
    df["mom_slope"] = ret_f - ret_s / (m_slow / m_fast)

    f_fast, f_slow = THERMOMETER_WINDOWS["flow"]
    df["flow_level"] = g["main_net_pct"].transform(lambda s: s.rolling(f_slow).sum())
    df["flow_slope"] = (
        g["main_net_pct"].transform(lambda s: s.rolling(f_fast).mean())
        - g["main_net_pct"].transform(lambda s: s.rolling(f_slow).mean())
    )

    v_fast, v_slow = THERMOMETER_WINDOWS["volume"]
    ma_slow_amt = g["amount"].transform(lambda s: s.rolling(v_slow).mean())
    ma_fast_amt = g["amount"].transform(lambda s: s.rolling(v_fast).mean())
    df["vol_level"] = df["amount"] / ma_slow_amt - 1
    df["vol_slope"] = ma_fast_amt / ma_slow_amt - 1

    t_fast, t_slow = THERMOMETER_WINDOWS["trend"]
    ma_f = g["close"].transform(lambda s: s.rolling(t_fast).mean())
    ma_s = g["close"].transform(lambda s: s.rolling(t_slow).mean())
    hh_s = g["close"].transform(lambda s: s.rolling(t_slow).max())
    align = ((df["close"] > ma_f).astype(float) + (df["close"] > ma_s).astype(float)
             + (ma_f > ma_s).astype(float)) / 3.0
    df["trend_level"] = 0.5 * align + 0.5 * (df["close"] / hh_s)
    up1 = (ret1 > 0).astype(float)
    df["trend_slope"] = up1.groupby(df["index_code"]).transform(
        lambda s: s.rolling(t_fast).mean())

    l_fast, l_slow = THERMOMETER_WINDOWS["limit"]
    df["limit_level"] = g["limit_ratio"].transform(lambda s: s.rolling(l_slow).mean())
    df["limit_slope"] = (
        g["limit_ratio"].transform(lambda s: s.rolling(l_fast).mean())
        - g["limit_ratio"].transform(lambda s: s.rolling(l_slow).mean())
    )

    # 量价交互(v2):价格方向 = 中期(ret60)方向;放量 = vol_level>0
    decay = THERMOMETER_PRICE_VOLUME_DECAY
    same_dir = ret_f > 0
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
    # 量价交互:量能族分数 × 价格方向衰减
    df["vol_score"] = df["vol_score"] * df["pv_decay"]
    return df
