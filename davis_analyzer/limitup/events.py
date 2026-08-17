"""涨停事件表构建：基础字段、股票池过滤、收益标签、量价与龙虎榜特征."""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.limitup import db

# ── helpers ──

def limit_ratio_for(ts_code: str) -> float:
    """涨停幅度：创业板/科创板 20cm，主板 10cm（ST/北交所已在股票池剔除）."""
    return 0.20 if ts_code.startswith(("30", "68")) else 0.10


def is_limit_up_close(close: float, pre_close: float, ratio: float) -> bool:
    if not (close > 0 and pre_close > 0):
        return False
    limit_px = round(pre_close * (1 + ratio) + 1e-9, 2)
    return abs(close - limit_px) <= 0.005


def prev_window_count(ranks: np.ndarray, window: int = 60) -> np.ndarray:
    """For sorted ranks, count of prior elements within [r-window, r)."""
    left = np.searchsorted(ranks, ranks - window, side="left")
    return np.arange(len(ranks)) - left


def _drop_ex_dividend(prices: pd.DataFrame) -> pd.DataFrame:
    g = prices.sort_values(["ts_code", "trade_date"]).groupby("ts_code", sort=False)
    prev_adj = g["adj_factor"].shift(1)
    drop_mask = prev_adj.notna() & (prices["adj_factor"] != prev_adj)
    if drop_mask.any():
        logger.info("剔除除权日事件 {} 条", int(drop_mask.sum()))
    return prices[~drop_mask].copy()


# ── main builder ──

def build_events(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    lp = db.read_limit_pool(conn, start, end, pool_kind="limit_up")
    if lp.empty:
        return pd.DataFrame()
    ext = db.read_limit_pool_ext(conn, start, end)
    if not ext.empty:
        lp = lp.merge(ext, on=["ts_code", "trade_date"], how="left")
    else:
        lp["float_mv"] = np.nan

    # 股票池过滤：ST / 北交所 / 上市<60 自然日
    lp = lp[~lp["name"].str.contains("ST", na=False)]
    lp = lp[~lp["ts_code"].str.endswith(".BJ")]
    basic = db.read_stock_basic(conn)[["ts_code", "list_date"]]
    lp = lp.merge(basic, on="ts_code", how="left")
    list_dt = pd.to_datetime(lp["list_date"], format="%Y%m%d", errors="coerce")
    trade_dt = pd.to_datetime(lp["trade_date"], format="%Y%m%d")
    lp = lp[(trade_dt - list_dt).dt.days >= 60]
    lp = lp.drop(columns=["list_date"])
    if lp.empty:
        return pd.DataFrame()

    # 价格数据（含窗口前后缓冲，供标签/形态用）
    buffer_start = _shift_day(db.normalize_date(start), -30)
    buffer_end = _shift_day(db.normalize_date(end), 15)
    prices = db.read_daily_prices(
        conn, sorted(lp["ts_code"].unique()), buffer_start, buffer_end
    )
    prices = _drop_ex_dividend(prices)
    price_cols = ["open", "high", "low", "close", "pre_close", "vol", "amount",
                  "adj_factor"]
    lp = lp.merge(prices[["ts_code", "trade_date", *price_cols]],
                  on=["ts_code", "trade_date"], how="inner")

    # 涨停价真实性校验（数据噪声防线）
    ok = lp.apply(
        lambda r: is_limit_up_close(r["close"], r["pre_close"], limit_ratio_for(r["ts_code"])),
        axis=1,
    )
    lp = lp[ok]
    lp["limit_price"] = lp["close"]
    lp["seal_ratio"] = np.where(
        lp["float_mv"].fillna(0) > 0, lp["seal_amount"] / lp["float_mv"], np.nan
    )

    # 前 60 交易日涨停次数
    rank_map = {d: i for i, d in enumerate(
        db.trading_dates(conn, buffer_start, buffer_end))}
    lp["rank"] = lp["trade_date"].map(rank_map)
    parts = []
    for _, g in lp.sort_values(["ts_code", "rank"]).groupby("ts_code", sort=False):
        ranks = g["rank"].to_numpy()
        g = g.copy()
        g["prev_limit_count_60"] = prev_window_count(ranks, 60)
        parts.append(g)
    lp = pd.concat(parts, ignore_index=True) if parts else lp

    # 前瞻收益标签（T+1 开盘/收盘/冲高/回撤 + 3日/5日 + 晋级）
    lp = attach_return_labels(lp, prices)

    logger.info("build_events: {} 条事件 [{} → {}]", len(lp), start, end)
    return lp.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)


def _shift_day(ymd: str, days: int) -> str:
    dt = pd.to_datetime(ymd, format="%Y%m%d") + pd.Timedelta(days=days)
    return dt.strftime("%Y%m%d")


# ── forward return labels ──

def attach_return_labels(
    events: pd.DataFrame, prices: pd.DataFrame
) -> pd.DataFrame:
    """Attach T+1/T+3/T+5 returns and promotion flag (T+1 closes limit-up)."""
    p = prices.sort_values(["ts_code", "trade_date"]).copy()
    g = p.groupby("ts_code", sort=False)
    p["t1_open"] = g["open"].shift(-1)
    p["t1_high"] = g["high"].shift(-1)
    p["t1_low"] = g["low"].shift(-1)
    p["t1_close"] = g["close"].shift(-1)
    p["t1_pre_close"] = g["pre_close"].shift(-1)
    p["t3_close"] = g["close"].shift(-3)
    p["t5_close"] = g["close"].shift(-5)
    label_cols = ["t1_open", "t1_high", "t1_low", "t1_close", "t1_pre_close",
                  "t3_close", "t5_close"]
    ev = events.merge(p[["ts_code", "trade_date", *label_cols]],
                      on=["ts_code", "trade_date"], how="left")
    ev["ret_open_1"] = ev["t1_open"] / ev["limit_price"] - 1
    ev["ret_close_1"] = ev["t1_close"] / ev["limit_price"] - 1
    ev["ret_high_1"] = ev["t1_high"] / ev["limit_price"] - 1
    ev["ret_low_1"] = ev["t1_low"] / ev["limit_price"] - 1
    ev["ret_3d"] = ev["t3_close"] / ev["limit_price"] - 1
    ev["ret_5d"] = ev["t5_close"] / ev["limit_price"] - 1
    ev["promoted"] = ev.apply(
        lambda r: bool(
            pd.notna(r["t1_close"]) and pd.notna(r["t1_pre_close"])
            and is_limit_up_close(r["t1_close"], r["t1_pre_close"],
                                  limit_ratio_for(r["ts_code"]))
        ),
        axis=1,
    )
    return ev.drop(columns=label_cols)
