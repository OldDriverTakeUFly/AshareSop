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


def refresh_recent(conn: sqlite3.Connection, gw: TushareGateway,
                    dates: list[str] | None = None, lookback_days: int = 12) -> dict:
    """盘后自举:补最近缺失交易日的 daily_price/moneyflow/circ_mv/limit_pool/sw_daily.

    直连 Tushare,不依赖 stockhot 采集链先行——无人值守 cron 的数据自给自足
    (2026-09-17 事故沉淀:机器停数日后 moneyflow/daily_price 陈旧,增量链空转)。
    dates 显式传入(测试)或缺省取最近 lookback_days 自然日,非交易日自然空返。
    """
    from datetime import datetime, timedelta

    if dates is None:
        today = datetime.now()
        dates = [(today - timedelta(days=i)).strftime("%Y%m%d")
                 for i in range(lookback_days - 1, -1, -1)]
    out = {"daily_days": 0, "moneyflow_days": 0, "limit_days": 0}
    for d in dates:
        have = conn.execute(
            "SELECT COUNT(*) FROM daily_price WHERE trade_date=?", (d,)).fetchone()[0]
        if have <= 1000:
            df = gw.call("daily", trade_date=d, fields=(
                "ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount"),
                paginate=True)
            if df is not None and not df.empty:
                conn.executemany(
                    "INSERT OR REPLACE INTO daily_price (ts_code,trade_date,open,high,"
                    "low,close,pre_close,pct_chg,vol,amount,fetched_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(r["ts_code"], str(r["trade_date"]), r.get("open"), r.get("high"),
                      r.get("low"), r["close"], r.get("pre_close"), r.get("pct_chg"),
                      r.get("vol"), r.get("amount"), time.time()) for _, r in df.iterrows()])
                conn.commit()
                out["daily_days"] += 1
        have = conn.execute(
            "SELECT COUNT(*) FROM moneyflow WHERE trade_date=?", (d,)).fetchone()[0]
        if have <= 1000:
            df = gw.call("moneyflow", trade_date=d, fields=(
                "trade_date,ts_code,buy_sm_amount,sell_sm_amount,buy_md_amount,"
                "sell_md_amount,buy_lg_amount,sell_lg_amount,buy_elg_amount,"
                "sell_elg_amount,net_mf_amount"), paginate=True)
            if df is not None and not df.empty:
                conn.executemany(
                    "INSERT OR REPLACE INTO moneyflow (trade_date,ts_code,buy_sm_amount,"
                    "sell_sm_amount,buy_md_amount,sell_md_amount,buy_lg_amount,"
                    "sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount,"
                    "fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(str(r["trade_date"]), r["ts_code"], r.get("buy_sm_amount"),
                      r.get("sell_sm_amount"), r.get("buy_md_amount"),
                      r.get("sell_md_amount"), r.get("buy_lg_amount"),
                      r.get("sell_lg_amount"), r.get("buy_elg_amount"),
                      r.get("sell_elg_amount"), r.get("net_mf_amount"), time.time())
                     for _, r in df.iterrows()])
                conn.commit()
                out["moneyflow_days"] += 1
    if dates:
        backfill_daily_basic_circ_mv(conn, gw, dates[0], dates[-1])
        from davis_analyzer.limitup import backfill as lu_bf, db as lu_db
        lu_bf.ensure_ext_table(conn)  # limit_pool_ext 为 limitup 模块自管表,可能不存在
        for d in dates:
            dash = lu_db.to_dash_date(d)
            if not lu_bf.day_has_ext(conn, dash):
                got = False
                for lt, pk in lu_bf.POOL_KIND_BY_TYPE.items():
                    df = gw.call("limit_list_d", trade_date=d, limit_type=lt)
                    if df is not None and not df.empty:
                        got = True
                        lu_bf.write_pool_day(conn, d, df, lt, pk)
                if got:
                    out["limit_days"] += 1
        backfill_sw_daily(conn, gw, dates[0], dates[-1])
    logger.info("refresh_recent: {}", out)
    return out


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
