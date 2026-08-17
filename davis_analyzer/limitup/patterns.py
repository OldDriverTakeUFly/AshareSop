"""形态识别：K 线形态（intraday_feature）+ 位置形态（daily_price）→ 形态标签.

档位为研究前固定的先验（规格 §7.1），禁止连续寻优。
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from davis_analyzer.limitup import db

# ── seal quality ──

def seal_band(first_seal_time: str) -> str:
    if not isinstance(first_seal_time, str) or first_seal_time in ("", "000000"):
        return "未知"
    if first_seal_time < "090000":
        return "未知"
    if first_seal_time < "100000":
        return "早盘"
    if first_seal_time < "140000":
        return "午盘"
    return "尾盘"


def attach_kline_features(
    events: pd.DataFrame, conn: sqlite3.Connection, start: str, end: str
) -> pd.DataFrame:
    """Join intraday K线特征（gap/振幅/收盘位置/上下影线/实体占比）+ 封板时段特征."""
    codes = sorted(events["ts_code"].unique())
    kl = db.read_intraday_features(conn, codes, start, end)
    kl = kl.rename(columns={
        "gap": "k_gap", "amplitude": "k_amplitude",
        "close_position": "k_close_position", "upper_shadow": "k_upper_shadow",
        "lower_shadow": "k_lower_shadow", "body_ratio": "k_body_ratio",
    })
    if kl.empty:
        kl = pd.DataFrame(columns=[
            "ts_code", "trade_date", "k_gap", "k_amplitude", "k_close_position",
            "k_upper_shadow", "k_lower_shadow", "k_body_ratio",
        ])
    ev = events.merge(kl, on=["ts_code", "trade_date"], how="left")
    ev["first_seal_band"] = ev["first_seal_time"].map(seal_band)
    ev["late_reseal"] = ev["last_seal_time"].map(
        lambda t: isinstance(t, str) and t >= "143000"
    )
    return ev


# ── positional patterns (computed on prices up to T-1) ──

def classify_from_prices(events: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """四类位置形态（突破/趋势加速/超跌反转/横盘）→ 互斥形态标签."""
    if prices.empty:
        out = events.copy()
        out["prior_high60"] = np.nan
        for col in ("is_breakout", "is_trend_accel", "is_oversold",
                    "is_consolidation"):
            out[col] = False
        out["pattern_label"] = np.nan
        return out
    p = prices.sort_values(["ts_code", "trade_date"]).copy()
    g = p.groupby("ts_code", sort=False)
    p["prior_high60"] = g["high"].transform(lambda s: s.rolling(60).max().shift(1))
    p["box40"] = (
        g["high"].transform(lambda s: s.rolling(40).max().shift(1))
        / g["low"].transform(lambda s: s.rolling(40).min().shift(1)) - 1
    )
    p["ma20"] = g["close"].transform(lambda s: s.rolling(20).mean())
    p["ma20_rising"] = p.groupby("ts_code")["ma20"].transform(
        lambda s: s > s.shift(5))
    p["ret20p"] = g["close"].transform(
        lambda s: s.shift(1) / s.shift(21) - 1)
    ma60 = g["close"].transform(lambda s: s.rolling(60).mean())
    p["ret60p"] = g["close"].transform(
        lambda s: s.shift(1) / s.shift(61) - 1)
    p["range120p"] = (
        g["close"].transform(lambda s: s.rolling(120).max().shift(1))
        / g["close"].transform(lambda s: s.rolling(120).min().shift(1)) - 1
    )
    p["is_breakout"] = (p["close"] >= p["prior_high60"] * 0.98) & (p["box40"] < 0.25)
    p["is_trend_accel"] = (
        (p["close"] > p["ma20"]) & p["ma20_rising"] & p["ret20p"].between(0.15, 0.40)
    )
    p["is_oversold"] = (p["ret60p"] < -0.30) & (p["close"] < ma60 * 0.90)
    p["is_consolidation"] = p["range120p"] < 0.20
    p["pattern_label"] = np.select(
        [p["is_breakout"], p["is_trend_accel"], p["is_consolidation"], p["is_oversold"]],
        ["突破型", "趋势加速型", "横盘首板型", "超跌反转型"],
        default="其他",
    )
    cols = ["prior_high60", "is_breakout", "is_trend_accel", "is_oversold",
            "is_consolidation", "pattern_label"]
    out = events.merge(p[["ts_code", "trade_date", *cols]],
                       on=["ts_code", "trade_date"], how="left")
    for col in ("is_breakout", "is_trend_accel", "is_oversold", "is_consolidation"):
        out[col] = out[col].fillna(False).astype(bool)
    return out


def attach_pattern_features(
    events: pd.DataFrame, conn: sqlite3.Connection, start: str, end: str
) -> pd.DataFrame:
    ev = attach_kline_features(events, conn, start, end)
    buffer_start = _shift(db.normalize_date(start), -30)
    buffer_end = _shift(db.normalize_date(end), 15)
    prices = db.read_daily_prices(
        conn, sorted(ev["ts_code"].unique()), buffer_start, buffer_end
    )
    return classify_from_prices(ev, prices)


def _shift(ymd: str, days: int) -> str:
    dt = pd.to_datetime(ymd, format="%Y%m%d") + pd.Timedelta(days=days)
    return dt.strftime("%Y%m%d")
