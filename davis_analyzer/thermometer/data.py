"""sw_daily(按 trade_date)与 ths_daily(按 ts_code)的回补与增量,断点续跑."""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

from davis_analyzer.limitup import db as limitup_db

if TYPE_CHECKING:
    from stockhot.data_layer.tushare_gateway import TushareGateway

_SW_FIELDS = "ts_code,trade_date,open,high,low,close,vol,amount,pct_change"
_COMMIT_EVERY = 60  # 交易日


def _write_sw_day(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """写一日 sw_daily 全量(INSERT OR REPLACE,幂等)."""
    conn.executemany(
        "INSERT OR REPLACE INTO sw_daily "
        "(ts_code,trade_date,open,high,low,close,vol,amount,pct_change,fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(r["ts_code"], str(r["trade_date"]), r.get("open"), r.get("high"),
          r.get("low"), r["close"], r.get("vol"), r.get("amount"),
          r.get("pct_change"), time.time()) for _, r in df.iterrows()],
    )
    return len(df)


def _day_covered(conn: sqlite3.Connection, d: str, min_rows: int = 31) -> bool:
    """该交易日已有全量(L1 数 31 为下限兜底,防半截日)."""
    return conn.execute(
        "SELECT COUNT(*) FROM sw_daily WHERE trade_date=?", (d,)).fetchone()[0] >= min_rows


def backfill_sw_daily(
    conn: sqlite3.Connection, gw: TushareGateway, start: str, end: str,
    min_rows: int = 31,
) -> dict:
    """按交易日历逐日拉 sw_daily 全量(一日 439 指数),幂等跳过已覆盖日."""
    days_done = rows = skipped = 0
    pending = 0
    for d in limitup_db.trading_dates(conn, start, end):
        if _day_covered(conn, d, min_rows):
            skipped += 1
            continue
        df = gw.call("sw_daily", trade_date=d, fields=_SW_FIELDS)
        if df is None or df.empty:
            logger.warning("sw_daily 无数据 {}", d)
            continue
        df["trade_date"] = df["trade_date"].astype(str)
        rows += _write_sw_day(conn, df)
        days_done += 1
        pending += 1
        if pending % _COMMIT_EVERY == 0:
            conn.commit()
            logger.info("sw_daily 进度: {} 天完成", days_done)
    conn.commit()
    logger.info("sw_daily 回补: done={} rows={} skipped={}", days_done, rows, skipped)
    return {"days_done": days_done, "rows_written": rows, "days_skipped": skipped}


def update_sw_daily_incremental(conn: sqlite3.Connection, gw: TushareGateway) -> dict:
    """补最新交易日(daily_price 日历的 MAX),已有则跳过."""
    latest = limitup_db.latest_trade_date(conn)
    if latest is None or _day_covered(conn, latest):
        return {"days_done": 0, "rows_written": 0, "days_skipped": 1}
    return backfill_sw_daily(conn, gw, latest, latest)


def backfill_daily_basic_circ_mv(
    conn: sqlite3.Connection, gw: TushareGateway, start: str, end: str,
) -> dict:
    """全市场 circ_mv 回补(daily_basic 表 UPSERT,只写 circ_mv 不动其他列).

    共享表 daily_basic 既有稀疏行(davis 自选股缓存)——INSERT OR REPLACE 会
    把 pe/pb 等列置空,必须用 ON CONFLICT DO UPDATE 局部更新。
    """
    days = rows = 0
    pending = 0
    for d in limitup_db.trading_dates(conn, start, end):
        df = gw.call("daily_basic", trade_date=d,
                     fields="ts_code,trade_date,circ_mv", paginate=True)
        if df is None or df.empty or "circ_mv" not in df.columns:
            logger.warning("daily_basic circ_mv 无数据 {}", d)
            continue
        have = [(r["ts_code"], str(r["trade_date"]), None if pd.isna(r["circ_mv"]) else float(r["circ_mv"]))
                for _, r in df.iterrows() if not pd.isna(r["circ_mv"])]
        conn.executemany(
            "INSERT INTO daily_basic (ts_code, trade_date, circ_mv, fetched_at) "
            "VALUES (?,?,?,?) ON CONFLICT(ts_code, trade_date) DO UPDATE SET "
            "circ_mv=excluded.circ_mv, fetched_at=excluded.fetched_at",
            [(c, t, v, time.time()) for c, t, v in have],
        )
        rows += len(have)
        days += 1
        pending += 1
        if pending % _COMMIT_EVERY == 0:
            conn.commit()
            logger.info("daily_basic circ_mv 进度: {} 天", days)
    conn.commit()
    logger.info("daily_basic circ_mv 回补: {} 日 {} 行", days, rows)
    return {"days": days, "rows": rows}


def backfill_ths_daily(conn: sqlite3.Connection, gw: TushareGateway) -> dict:
    """按概念 ts_code 全历史回补 ths_daily;已有任何行的码跳过(增量由 run 单点补)."""
    codes = [r[0] for r in conn.execute("SELECT ts_code FROM ths_index").fetchall()]
    done = rows = 0
    end = limitup_db.latest_trade_date(conn) or "20991231"
    for i, code in enumerate(codes, 1):
        have = conn.execute(
            "SELECT COUNT(*) FROM ths_daily WHERE ts_code=?", (code,)).fetchone()[0]
        if have > 0:
            continue
        df = gw.call("ths_daily", ts_code=code, start_date="20150101", end_date=end)
        if df is not None and not df.empty:
            conn.executemany(
                "INSERT OR REPLACE INTO ths_daily "
                "(ts_code,trade_date,close,pct_change,vol,turnover_rate,fetched_at) "
                "VALUES (?,?,?,?,?,?,?)",
                [(code, str(r["trade_date"]), r.get("close"), r.get("pct_change"),
                  r.get("vol"), r.get("turnover_rate"), time.time())
                 for _, r in df.iterrows()],
            )
            rows += len(df)
            done += 1
        if i % 50 == 0:
            conn.commit()
            logger.info("ths_daily 进度: {}/{} 概念", i, len(codes))
    conn.commit()
    logger.info("ths_daily 回补: codes={} rows={}", done, rows)
    return {"codes_done": done, "rows_written": rows}
