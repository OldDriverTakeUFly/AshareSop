"""做T入场的因果特征（09:40 决策时点前全部可得，杜绝前视）.

gap_pct      当日开盘较昨收跳空（bar0 open 已验证==日线 open）
trend_up     昨收 > 昨日 MA20（中期趋势过滤）
ret5/ret20   前 5/20 日收益（shift 一日）
vol_ratio1   首 5 分钟 bar 量 / 前 20 日首bar量中位（开盘承接量能）
norm_gap     跳空深度 / 前 20 日平均振幅（跨股标准化）
atr20/atr5   前 20/5 日平均振幅（shift 一日）——日内振幅的可预测代理
prev_amp     前一交易日振幅（(high-low)/pre_close，shift 一日）
mkt_ret0940  宇宙内个股 09:40 bar 开盘相对昨收的截面中位——自建大盘温度计
             （baostock 无指数分钟线，2026-08-19 实测；指数日线粒度不够）
"""

from __future__ import annotations

import pandas as pd


def build_features(
    minute_df: pd.DataFrame,
    daily_df: pd.DataFrame,
) -> pd.DataFrame:
    """返回以 (ts_code, trade_date) 为键的特征表（普通列）."""
    m = minute_df.sort_values(["ts_code", "trade_date", "trade_time"])
    grouped = m.groupby(["ts_code", "trade_date"], sort=True)
    first = grouped.head(1)[["ts_code", "trade_date", "open", "volume"]].rename(
        columns={"open": "day_open", "volume": "vol1"}
    )
    second = (
        grouped.head(2).groupby(["ts_code", "trade_date"], sort=True).tail(1)
        [["ts_code", "trade_date", "open"]].rename(columns={"open": "open_0940"})
    )

    # ── 个股日级趋势特征（全部 shift 一日：昨日及以前的信息） ──
    d = daily_df.sort_values(["ts_code", "trade_date"]).copy()
    g = d.groupby("ts_code", sort=False)
    d["ma20"] = g["close"].transform(lambda s: s.rolling(20).mean())
    d["trend_up"] = g.apply(
        lambda x: (x["close"] > x["ma20"]).shift(1)
    ).reset_index(level=0, drop=True)
    d["ret5"] = g.apply(
        lambda x: (x["close"] / x["close"].shift(5) - 1).shift(1)
    ).reset_index(level=0, drop=True)
    d["ret20"] = g.apply(
        lambda x: (x["close"] / x["close"].shift(20) - 1).shift(1)
    ).reset_index(level=0, drop=True)
    amp = (d["high"] - d["low"]) / d["pre_close"]
    d["atr20"] = amp.groupby(d["ts_code"]).transform(
        lambda s: s.rolling(20).mean().shift(1)
    )
    d["atr5"] = amp.groupby(d["ts_code"]).transform(
        lambda s: s.rolling(5).mean().shift(1)
    )
    d["prev_amp"] = amp.groupby(d["ts_code"]).shift(1)

    feat = first.merge(
        d[["ts_code", "trade_date", "pre_close", "trend_up", "ret5", "ret20",
           "atr20", "atr5", "prev_amp"]],
        on=["ts_code", "trade_date"], how="left",
    )
    feat["gap_pct"] = feat["day_open"] / feat["pre_close"] - 1.0
    feat["norm_gap"] = feat["gap_pct"] / feat["atr20"]

    # ── 首 bar 量比（前 20 日首bar量的滚动中位，shift 一日） ──
    v = first.sort_values(["ts_code", "trade_date"]).copy()
    v["vol1_med20"] = v.groupby("ts_code")["vol1"].transform(
        lambda s: s.rolling(20, min_periods=5).median().shift(1)
    )
    feat = feat.merge(
        v[["ts_code", "trade_date", "vol1_med20"]], on=["ts_code", "trade_date"]
    )
    feat["vol_ratio1"] = feat["vol1"] / feat["vol1_med20"]

    # ── 大盘 09:40 温度计：宇宙个股 09:40 开盘相对昨收的截面中位 ──
    mk = second.merge(
        daily_df[["ts_code", "trade_date", "pre_close"]],
        on=["ts_code", "trade_date"],
    )
    mk["r0940"] = mk["open_0940"] / mk["pre_close"] - 1.0
    mkt = (
        mk.groupby("trade_date")["r0940"].median()
        .rename("mkt_ret0940").reset_index()
    )
    feat = feat.merge(mkt, on="trade_date", how="left")

    keep = ["ts_code", "trade_date", "gap_pct", "norm_gap", "trend_up",
            "ret5", "ret20", "atr20", "atr5", "prev_amp",
            "vol_ratio1", "mkt_ret0940"]
    return feat[keep]
