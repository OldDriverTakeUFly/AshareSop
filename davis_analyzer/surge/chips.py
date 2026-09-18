"""cyq_perf 官方筹码数据拉取与缓存（每日按 trade_date 一次拉全市场）."""

from __future__ import annotations

import sqlite3
import time

import pandas as pd
from loguru import logger

from davis_analyzer.surge import db

_COLUMNS = ["ts_code", "trade_date", "his_low", "his_high", "cost_5pct",
            "cost_15pct", "cost_50pct", "cost_85pct", "cost_95pct",
            "weight_avg", "winner_rate"]

# 全市场约 5500 行;阈值防半截数据被当作完整日期
_FULL_DAY_MIN_ROWS = 1000


def fetch_cyq_by_date(pro, day: str) -> pd.DataFrame:
    day = db.normalize_date(day)
    df = pro.cyq_perf(trade_date=day)
    if df is None or df.empty:
        return pd.DataFrame(columns=_COLUMNS)
    missing = [c for c in _COLUMNS if c not in df.columns]
    if missing:
        raise RuntimeError(f"cyq_perf 响应缺列 {missing}(接口变更风险,单点断言)")
    return df[_COLUMNS]


def _insert(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    df = df.copy()
    df["trade_date"] = df["trade_date"].map(db.normalize_date)
    df["fetched_at"] = time.time()
    conn.executemany(
        "INSERT OR REPLACE INTO cyq_perf_cache VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        df[_COLUMNS + ["fetched_at"]].itertuples(index=False, name=None),
    )
    conn.commit()
    return len(df)


def _has_date(conn: sqlite3.Connection, day: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM cyq_perf_cache WHERE trade_date=?", (day,)).fetchone()
    return bool(row and row[0] >= _FULL_DAY_MIN_ROWS)


def _recent_full_day(conn: sqlite3.Connection, day: str) -> str | None:
    """day 之前(含回看≤10个缓存日)最近一个完整缓存日."""
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM cyq_perf_cache WHERE trade_date<? "
        "ORDER BY trade_date DESC LIMIT 10", (day,))]
    for d in dates:
        if _has_date(conn, d):
            return d
    return None


def ensure_cyq(conn: sqlite3.Connection, pro, day: str) -> str:
    """确保 day 筹码可用;缺当日回退最近完整缓存日;全无→空串(调用方标缺失)."""
    day = db.normalize_date(day)
    if _has_date(conn, day):
        return day
    try:
        n = _insert(conn, fetch_cyq_by_date(pro, day))
    except Exception as e:  # 健壮性审查C1:网络异常≠崩溃,按"当日未出"走回退
        logger.warning("cyq_perf {} 拉取异常(走回退): {}", day, e)
        n = 0
    if n >= _FULL_DAY_MIN_ROWS:
        logger.info("cyq_perf {} 拉取 {} 行", day, n)
        return day
    if n:
        logger.warning("cyq_perf {} 仅 {} 行(<{}),视为不完整", day, n, _FULL_DAY_MIN_ROWS)
    prev = _recent_full_day(conn, day)
    if prev:
        logger.warning("cyq_perf {} 未出/不完整,回退 {}", day, prev)
        return prev
    logger.warning("cyq_perf {} 无可用数据", day)
    return ""


def backfill_cyq(conn: sqlite3.Connection, pro, dates: list[str]) -> list[str]:
    done: list[str] = []
    for d in dates:
        d = db.normalize_date(d)
        if _has_date(conn, d):
            continue
        if _insert(conn, fetch_cyq_by_date(pro, d)) >= _FULL_DAY_MIN_ROWS:
            done.append(d)
            time.sleep(0.2)
    return done


def read_cyq(
    conn: sqlite3.Connection, ts_codes: list[str], end_day: str, lookback: int
) -> pd.DataFrame:
    """读取筹码缓存(含 end_day 往前 lookback 个缓存日,供 winner_delta_5d)."""
    if not ts_codes:
        return pd.DataFrame(columns=_COLUMNS)
    end_day = db.normalize_date(end_day)
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM cyq_perf_cache WHERE trade_date<=? "
        "ORDER BY trade_date DESC LIMIT ?", (end_day, lookback))]
    if not dates:
        return pd.DataFrame(columns=_COLUMNS)
    ph = ",".join("?" * len(dates))
    return pd.read_sql_query(
        f"SELECT {','.join(_COLUMNS)} FROM cyq_perf_cache WHERE trade_date IN ({ph})",
        conn, params=dates)
