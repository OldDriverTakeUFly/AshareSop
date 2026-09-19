"""大盘五维温度:指数趋势/宽度/量能/资金/涨停情绪,扩展窗口分位锚定."""

from __future__ import annotations

import json
import sqlite3
import time

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.core.constants import THERMOMETER_MARKET_DIM_WEIGHTS
from davis_analyzer.limitup import db as limitup_db
from davis_analyzer.thermometer.moneyflow_agg import market_flow_from_sectors

_MIN_HISTORY = 250  # 约一年交易日,不足不分位
_LABELS = [(85.0, "过热"), (65.0, "偏热"), (35.0, "温和"), (15.0, "低温"), (-1.0, "冰点")]
_DIMS = ["trend_dim", "width_dim", "volume_dim", "flow_dim", "sentiment_dim"]


def _expanding_pct(s: pd.Series) -> pd.Series:
    """当日值在截至当日全部历史中的分位(0-1);前 _MIN_HISTORY-1 日为 NaN."""
    vals = s.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    for i in range(_MIN_HISTORY - 1, len(vals)):
        if np.isnan(vals[i]):
            continue
        hist = vals[: i + 1]
        hist = hist[~np.isnan(hist)]
        if len(hist) == 0:
            continue
        out[i] = float((hist <= vals[i]).sum() / len(hist))
    return pd.Series(out, index=s.index)


def _trend_axis(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """沪深300+创业板:MA 排列与距 250 日高点合成,两指数均值 → (trade_date, trend_raw)."""
    frames = []
    for code in ("000300.SH", "399006.SZ"):
        df = pd.read_sql_query(
            "SELECT trade_date, close FROM index_daily WHERE ts_code=? "
            "AND trade_date>=? AND trade_date<=? ORDER BY trade_date",
            conn, params=(code, start, end))
        if df.empty:
            continue
        ma20 = df["close"].rolling(20).mean()
        ma60 = df["close"].rolling(60).mean()
        ma250 = df["close"].rolling(250).mean()
        hh250 = df["close"].rolling(250).max()
        align = ((df["close"] > ma20).astype(float) + (df["close"] > ma60).astype(float)
                 + (ma20 > ma250).astype(float)) / 3.0
        frames.append((df["trade_date"], 0.5 * align + 0.5 * df["close"] / hh250))
    if not frames:
        return pd.DataFrame(columns=["trade_date", "trend_raw"])
    out = pd.DataFrame({"trade_date": frames[0][0], "trend_raw": frames[0][1]})
    for td, raw in frames[1:]:
        out = out.merge(pd.DataFrame({"trade_date": td, "trend_raw_b": raw}),
                        on="trade_date", how="outer")
        out["trend_raw"] = out[["trend_raw", "trend_raw_b"]].mean(axis=1)
        out = out.drop(columns=["trend_raw_b"])
    return out


def _width_volume_axis(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """宽度(涨跌家数比+20日新高占比)与量能(全市场成交额)——SQL 仿 sentiment._breadth_axes."""
    breadth = pd.read_sql_query(
        "SELECT trade_date, "
        "SUM(CASE WHEN close > pre_close THEN 1.0 ELSE 0 END) / COUNT(*) AS up_ratio, "
        "SUM(amount) AS volume_raw "
        "FROM daily_price WHERE trade_date >= ? AND trade_date <= ? "
        "GROUP BY trade_date ORDER BY trade_date",
        conn, params=(start, end))
    nh = pd.read_sql_query(
        "SELECT trade_date, AVG(CASE WHEN close >= hh20 THEN 1.0 ELSE 0 END) "
        "AS new_high_ratio FROM ("
        "  SELECT trade_date, close, MAX(close) OVER "
        "  (PARTITION BY ts_code ORDER BY trade_date ROWS 19 PRECEDING) AS hh20 "
        "  FROM daily_price WHERE trade_date >= ? AND trade_date <= ?) "
        "GROUP BY trade_date ORDER BY trade_date",
        conn, params=(start, end))
    df = breadth.merge(nh, on="trade_date", how="left")
    df["width_raw"] = 0.5 * df["up_ratio"] + 0.5 * df["new_high_ratio"].fillna(0.0)
    return df[["trade_date", "width_raw", "volume_raw"]]


def _sentiment_axis(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """涨停数/最高连板/炸板率合成(炸板率反向)——涨停情绪维度."""
    d0, d1 = limitup_db.to_dash_date(start), limitup_db.to_dash_date(end)
    zt = pd.read_sql_query(
        "SELECT trade_date, COUNT(*) AS zt_n FROM limit_pool WHERE pool_kind='limit_up' "
        "AND trade_date>=? AND trade_date<=? GROUP BY trade_date",
        conn, params=(d0, d1))
    brk = pd.read_sql_query(
        "SELECT trade_date, COUNT(*) AS brk_n FROM limit_pool WHERE pool_kind='broken' "
        "AND trade_date>=? AND trade_date<=? GROUP BY trade_date",
        conn, params=(d0, d1))
    hi = pd.read_sql_query(
        "SELECT trade_date, MAX(consecutive_boards) AS hi_board FROM limit_pool "
        "WHERE pool_kind='limit_up' AND trade_date>=? AND trade_date<=? GROUP BY trade_date",
        conn, params=(d0, d1))
    df = zt.merge(hi, on="trade_date", how="outer").merge(brk, on="trade_date", how="outer")
    if df.empty:
        return pd.DataFrame(columns=["trade_date", "sentiment_raw"])
    df["zt_n"] = df["zt_n"].fillna(0)
    df["brk_n"] = df["brk_n"].fillna(0)
    df["broken_rate"] = df["brk_n"] / (df["zt_n"] + df["brk_n"])
    df["sentiment_raw"] = (0.4 * df["zt_n"].clip(upper=150) / 150
                           + 0.3 * df["hi_board"].fillna(0).clip(upper=12) / 12
                           + 0.3 * (1 - df["broken_rate"].fillna(0.5)))
    return df[["trade_date", "sentiment_raw"]]


def compute_market_history(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """五维 raw → 扩展窗口分位 → 加权温度 → 落库 thermometer_market(覆写幂等)."""
    trend = _trend_axis(conn, start, end)
    wv = _width_volume_axis(conn, start, end)
    sent = _sentiment_axis(conn, start, end)
    # 历史口径:L1 板块聚合求和(mkt_cap 已沉淀,不受 daily_basic 30 天滚动清理影响)
    flow = market_flow_from_sectors(conn, start, end)
    flow["flow_raw"] = flow["main_net_sum"] / flow["circ_mv_sum"].replace(0, np.nan)
    flow["flow_raw"] = flow["flow_raw"].rolling(5).mean()

    for df in (wv, sent):
        df["trade_date"] = df["trade_date"].str.replace("-", "", regex=False)

    cal = limitup_db.trading_dates(conn, start, end)
    base = pd.DataFrame({"trade_date": pd.Series(cal, dtype="object")})
    df = (base.merge(trend, on="trade_date", how="left")
          .merge(wv, on="trade_date", how="left")
          .merge(flow[["trade_date", "flow_raw"]], on="trade_date", how="left")
          .merge(sent, on="trade_date", how="left"))

    for raw, dim in (("trend_raw", "trend_dim"), ("width_raw", "width_dim"),
                     ("volume_raw", "volume_dim"), ("flow_raw", "flow_dim"),
                     ("sentiment_raw", "sentiment_dim")):
        df[dim] = _expanding_pct(df[raw])
    w = THERMOMETER_MARKET_DIM_WEIGHTS
    df["temperature"] = (w["trend"] * df["trend_dim"] + w["width"] * df["width_dim"]
                         + w["volume"] * df["volume_dim"] + w["flow"] * df["flow_dim"]
                         + w["sentiment"] * df["sentiment_dim"]) * 100

    def _label(t: float) -> str:
        for th, name in _LABELS:
            if t >= th:
                return name
        return _LABELS[-1][1]

    df["regime_label"] = df["temperature"].map(lambda t: _label(t) if pd.notna(t) else None)

    now = time.time()
    for r in df.itertuples():
        temp = r.temperature
        label = _label(temp) if pd.notna(temp) else None
        detail = None
        if pd.notna(temp):
            vals = {d: getattr(r, d) for d in _DIMS}
            v = [x for x in vals.values() if pd.notna(x)]
            if len(v) == len(_DIMS) and max(v) - min(v) > 0.5:
                hot = [d for d, x in vals.items() if x >= 0.7]
                cold = [d for d, x in vals.items() if x <= 0.3]
                detail = json.dumps({"divergence": {"hot": hot, "cold": cold}},
                                    ensure_ascii=False)
        conn.execute(
            "INSERT OR REPLACE INTO thermometer_market "
            "(trade_date,trend_dim,width_dim,volume_dim,flow_dim,sentiment_dim,"
            "temperature,regime_label,detail,fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r.trade_date, r.trend_dim, r.width_dim, r.volume_dim, r.flow_dim,
             r.sentiment_dim, temp, label, detail, now))
    conn.commit()
    logger.info("thermometer_market: {} 日 [{},{}] 有温度 {} 日",
                len(df), start, end, int(df["temperature"].notna().sum()))
    return df
