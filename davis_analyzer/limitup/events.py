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


def _ex_div_mask(prices: pd.DataFrame) -> pd.Series:
    """除权日旗标：adj_factor 相对前一交易日（同股票）变化（按 ts_code 分组）."""
    g = prices.groupby("ts_code", sort=False)
    prev_adj = g["adj_factor"].shift(1)
    return (prev_adj.notna() & (prices["adj_factor"] != prev_adj)).fillna(False)


def _drop_ex_dividend(prices: pd.DataFrame) -> pd.DataFrame:
    mask = _ex_div_mask(prices)
    if mask.any():
        logger.info("剔除除权日事件 {} 条", int(mask.sum()))
    return prices[~mask].copy()


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
    prices_full = prices.copy()  # 未剔除除权的完整帧：收益标签日历对齐用
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
    lp = attach_return_labels(lp, prices_full)
    lp = attach_volume_features(lp, prices)
    lp = attach_lhb_features(lp, conn, start, end)
    lp = attach_news_proxies(lp, conn, start, end)

    logger.info("build_events: {} 条事件 [{} → {}]", len(lp), start, end)
    return lp.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)


def _shift_day(ymd: str, days: int) -> str:
    dt = pd.to_datetime(ymd, format="%Y%m%d") + pd.Timedelta(days=days)
    return dt.strftime("%Y%m%d")


# ── forward return labels ──

# attach_return_labels 产出的 7 个标签列（跨除权窗口须整体置 NaN）
RETURN_LABEL_COLS = ["ret_open_1", "ret_close_1", "ret_high_1", "ret_low_1",
                     "ret_3d", "ret_5d", "promoted"]


def _any_ex_div_next5(s: pd.Series) -> pd.Series:
    """T+1..T+5（同股票后续 5 个价格行）中是否存在除权日."""
    m = pd.Series(False, index=s.index)
    for k in range(1, 6):
        m = m | s.shift(-k, fill_value=False)
    return m


def attach_return_labels(
    events: pd.DataFrame, prices_full: pd.DataFrame
) -> pd.DataFrame:
    """Attach T+1/T+3/T+5 returns and promotion flag (T+1 closes limit-up).

    shift 必须在**未剔除除权日**的完整价格帧上做（保证 T+n 标签取到真实
    T+n 行，不因删行错位取到 T+n+1）；对「标签窗口（T+1..T+5）内含除权日
    或事件自身为除权日」的事件，7 个标签列置 NaN（宁缺毋错——跨除权收益
    与 unadjusted 涨停价不可比）。除权日事件的剔除由上游 inner merge 完成。
    """
    p = prices_full.sort_values(["ts_code", "trade_date"]).copy()
    g = p.groupby("ts_code", sort=False)
    p["t1_open"] = g["open"].shift(-1)
    p["t1_high"] = g["high"].shift(-1)
    p["t1_low"] = g["low"].shift(-1)
    p["t1_close"] = g["close"].shift(-1)
    p["t1_pre_close"] = g["pre_close"].shift(-1)
    p["t3_close"] = g["close"].shift(-3)
    p["t5_close"] = g["close"].shift(-5)
    p["is_ex_div"] = _ex_div_mask(p)
    p["label_window_ex_div"] = p.groupby("ts_code", sort=False)[
        "is_ex_div"
    ].transform(_any_ex_div_next5)
    label_cols = ["t1_open", "t1_high", "t1_low", "t1_close", "t1_pre_close",
                  "t3_close", "t5_close"]
    ev = events.merge(
        p[["ts_code", "trade_date", *label_cols, "is_ex_div", "label_window_ex_div"]],
        on=["ts_code", "trade_date"], how="left",
    )
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
    bad = ev["label_window_ex_div"].fillna(False) | ev["is_ex_div"].fillna(False)
    for c in RETURN_LABEL_COLS:
        ev[c] = ev[c].where(~bad)
    return ev.drop(columns=[*label_cols, "is_ex_div", "label_window_ex_div"])


# ── volume features ──

def attach_volume_features(
    events: pd.DataFrame, prices: pd.DataFrame
) -> pd.DataFrame:
    """Attach 量比（当日量/前 20 日均量，不含当日）与近 5 日温和放量天数."""
    p = prices.sort_values(["ts_code", "trade_date"]).copy()
    g = p.groupby("ts_code", sort=False)
    p["vol_ma20_prev"] = g["vol"].transform(lambda s: s.rolling(20).mean().shift(1))
    p["vol_ratio_20"] = p["vol"] / p["vol_ma20_prev"]
    p["_mild"] = ((p["vol"] > p["vol_ma20_prev"] * 1.2)
                  & (p["vol"] < p["vol_ma20_prev"] * 2.5)).astype(float)
    p["mild_vol_days_5"] = p.groupby("ts_code")["_mild"].transform(
        lambda s: s.rolling(5).sum().shift(1)
    )
    return events.merge(
        p[["ts_code", "trade_date", "vol_ratio_20", "mild_vol_days_5"]],
        on=["ts_code", "trade_date"], how="left",
    )


# ── dragon-tiger join ──

def attach_lhb_features(
    events: pd.DataFrame, conn: sqlite3.Connection, start: str, end: str
) -> pd.DataFrame:
    """Join 龙虎榜 (top_list) aggregates onto events by (ts_code, trade_date)."""
    lhb = db.read_top_list(conn, start, end)
    if lhb.empty:
        events["on_lhb"] = False
        for col in ("lhb_net_amount", "lhb_net_rate", "lhb_amount_rate"):
            events[col] = np.nan
        events["lhb_reason"] = ""
        return events
    lhb = lhb.rename(columns={
        "net_amount": "lhb_net_amount", "net_rate": "lhb_net_rate",
        "amount_rate": "lhb_amount_rate", "reason": "lhb_reason",
    })
    lhb["on_lhb"] = True
    ev = events.merge(
        lhb[["ts_code", "trade_date", "on_lhb", "lhb_net_amount",
             "lhb_net_rate", "lhb_amount_rate", "lhb_reason"]],
        on=["ts_code", "trade_date"], how="left",
    )
    ev["on_lhb"] = ev["on_lhb"].fillna(False)
    return ev


# ── news proxies: sector linkage + negative corp events ──

def attach_news_proxies(
    events: pd.DataFrame, conn: sqlite3.Connection, start: str, end: str
) -> pd.DataFrame:
    """Attach 板块联动（同日同板块涨停家数/占比）与 30 日内利空事件（解禁/减持）代理."""
    ev = events.copy()
    day_count = ev.groupby("trade_date")["ts_code"].transform("size")
    ev["sector_linkage"] = ev.groupby(["trade_date", "sector"])["ts_code"].transform("size")
    ev["sector_share"] = ev["sector_linkage"] / day_count

    codes = sorted(ev["ts_code"].unique())
    ev_start = _shift_day(db.normalize_date(start), -45)
    ce = db.read_corp_events(conn, codes, ev_start, end)
    neg = ce[
        (ce["event_type"] == "share_float")
        | ((ce["event_type"] == "holder_trade") & (ce["direction"] == "减持"))
    ]
    neg_map: dict[str, list[str]] = {}
    for _, r in neg.iterrows():
        neg_map.setdefault(r["ts_code"], []).append(r["ann_date"])
    ev["negative_event_30d"] = ev.apply(
        lambda r: _has_neg_within(neg_map.get(r["ts_code"], []), r["trade_date"]), axis=1
    )
    return ev


def _has_neg_within(ann_dates: list[str], trade_date: str, days: int = 30) -> bool:
    """True if any announcement date falls within [trade_date - days, trade_date]."""
    if not ann_dates:
        return False
    t = pd.to_datetime(trade_date, format="%Y%m%d")
    return any(0 <= (t - pd.to_datetime(a, format="%Y%m%d")).days <= days
               for a in ann_dates)
