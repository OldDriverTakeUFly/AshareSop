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
    if first_seal_time.isdigit():
        first_seal_time = first_seal_time.zfill(6)  # 无前导零时间归一（'92500'→'092500'）
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

# 冻结先验阈值（规格 §7.1）：默认行为与参数化前的字面量完全一致；
# thresholds 传参仅供 ±20% 扰动检验复用同一分类器，先验本身不因此改变
PATTERN_THRESHOLDS: dict[str, float] = {
    "breakout_close": 0.98,   # close ≥ prior_high60 × 0.98 → 突破
    "breakout_box": 0.25,     # 40 日箱体振幅上限（突破须箱体紧凑）
    "accel_lo": 0.15,         # 20 日涨幅下限（趋势加速）
    "accel_hi": 0.40,         # 20 日涨幅上限（趋势加速）
    "oversold": -0.30,        # 60 日跌幅阈值（超跌反转）
    "consolidation": 0.20,    # 120 日区间振幅上限（横盘）
}

# classify_from_prices 附加的形态列（重分类前需从事件表剥离，避免 merge 后缀冲突）
PATTERN_FEATURE_COLS = ["prior_high60", "is_breakout", "is_trend_accel",
                        "is_oversold", "is_consolidation", "pattern_label"]


def classify_from_prices(
    events: pd.DataFrame, prices: pd.DataFrame,
    *, thresholds: dict[str, float] | None = None,
) -> pd.DataFrame:
    """四类位置形态（突破/趋势加速/超跌反转/横盘）→ 互斥形态标签.

    thresholds=None 用冻结先验 PATTERN_THRESHOLDS（与历史默认行为完全
    一致）；部分传参仅覆盖给定键，其余键回落先验。
    """
    t = {**PATTERN_THRESHOLDS, **(thresholds or {})}
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
    p["is_breakout"] = (
        (p["close"] >= p["prior_high60"] * t["breakout_close"])
        & (p["box40"] < t["breakout_box"])
    )
    p["is_trend_accel"] = (
        (p["close"] > p["ma20"]) & p["ma20_rising"]
        & p["ret20p"].between(t["accel_lo"], t["accel_hi"])
    )
    p["is_oversold"] = (p["ret60p"] < t["oversold"]) & (p["close"] < ma60 * 0.90)
    p["is_consolidation"] = p["range120p"] < t["consolidation"]
    p["pattern_label"] = np.select(
        [p["is_breakout"], p["is_trend_accel"], p["is_consolidation"], p["is_oversold"]],
        ["突破型", "趋势加速型", "横盘首板型", "超跌反转型"],
        default="其他",
    )
    out = events.merge(p[["ts_code", "trade_date", *PATTERN_FEATURE_COLS]],
                       on=["ts_code", "trade_date"], how="left")
    for col in ("is_breakout", "is_trend_accel", "is_oversold", "is_consolidation"):
        out[col] = out[col].fillna(False).astype(bool)
    return out


def read_buffered_prices(
    events: pd.DataFrame, conn: sqlite3.Connection, start: str, end: str
) -> pd.DataFrame:
    """按位置形态口径拉取日线（start-200 自然日缓冲 → end+15）.

    与 attach_pattern_features 同一缓冲窗口：位置形态最长窗口为 120 交易日
    （range120p）+ rolling 计算行，缓冲不足会使研究区间头部事件的横盘/突破
    特征因窗口不足静默退化（数据充分性，非调参）。扰动检验复用同一口径。
    """
    buffer_start = _shift(db.normalize_date(start), -200)
    buffer_end = _shift(db.normalize_date(end), 15)
    return db.read_daily_prices(
        conn, sorted(events["ts_code"].unique()), buffer_start, buffer_end
    )


def attach_pattern_features(
    events: pd.DataFrame, conn: sqlite3.Connection, start: str, end: str
) -> pd.DataFrame:
    """Attach K线 + 位置形态特征（组合入口）."""
    ev = attach_kline_features(events, conn, start, end)
    return classify_from_prices(ev, read_buffered_prices(ev, conn, start, end))


def _shift(ymd: str, days: int) -> str:
    dt = pd.to_datetime(ymd, format="%Y%m%d") + pd.Timedelta(days=days)
    return dt.strftime("%Y%m%d")
