"""Shared SQLite data access for the limitup module.

All readers normalize trade_date to YYYYMMDD and ts_code to the
suffixed form (603311 -> 603311.SH) so downstream frames join cleanly.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
from loguru import logger

# ── code / date normalization ──

_SUFFIX_RULES_2 = {"60": ".SH", "68": ".SH", "00": ".SZ", "30": ".SZ", "92": ".BJ"}
_SUFFIX_RULES_1 = {"8": ".BJ", "4": ".BJ"}


def to_suffixed_code(code: str) -> str:
    if not code or "." in code:
        return code
    suffix = _SUFFIX_RULES_2.get(code[:2]) or _SUFFIX_RULES_1.get(code[:1])
    if suffix is None:
        logger.warning("unknown code prefix: {}", code)
        return code
    return code + suffix


def strip_code_suffix(code: str) -> str:
    return code.split(".")[0] if "." in code else code


def normalize_date(d: str) -> str:
    return d.replace("-", "")


def to_dash_date(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if "-" not in d else d


def connect() -> sqlite3.Connection:
    from stockhot.data_layer.market_db import get_connection

    return get_connection()


# ── readers ──

def trading_dates(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM daily_price "
        "WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
        (normalize_date(start), normalize_date(end)),
    ).fetchall()
    return [r[0] for r in rows]


def normalize_seal_time(t: object) -> object:
    """Zero-pad seal-time strings to HHMMSS (Tushare stores '92500' for 09:25:00)."""
    if t is None or (isinstance(t, float) and pd.isna(t)):
        return t
    s = str(t).strip()
    return s.zfill(6) if s.isdigit() else s


def read_limit_pool(
    conn: sqlite3.Connection, start: str, end: str, pool_kind: str = "limit_up"
) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT trade_date, ts_code, name, sector, change_pct, seal_amount, "
        "consecutive_boards, broken_count, first_seal_time, last_seal_time, "
        "turnover_rate FROM limit_pool "
        "WHERE pool_kind=? AND trade_date>=? AND trade_date<=?",
        conn,
        params=(pool_kind, to_dash_date(start), to_dash_date(end)),
    )
    if df.empty:
        return df
    df["trade_date"] = df["trade_date"].map(normalize_date)
    df["ts_code"] = df["ts_code"].map(to_suffixed_code)
    df["first_seal_time"] = df["first_seal_time"].map(normalize_seal_time)
    df["last_seal_time"] = df["last_seal_time"].map(normalize_seal_time)
    return df.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)


def read_limit_pool_ext(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT trade_date, ts_code, float_mv FROM limit_pool_ext "
        "WHERE trade_date>=? AND trade_date<=?",
        conn,
        params=(to_dash_date(start), to_dash_date(end)),
    )
    if df.empty:
        return df
    df["trade_date"] = df["trade_date"].map(normalize_date)
    df["ts_code"] = df["ts_code"].map(to_suffixed_code)
    return df


def _chunked(codes: list[str], size: int = 900) -> list[list[str]]:
    return [codes[i : i + size] for i in range(0, len(codes), size)]


def _read_in_codes(
    conn: sqlite3.Connection, table: str, columns: str, codes: list[str],
    start: str, end: str,
) -> pd.DataFrame:
    frames = []
    for chunk in _chunked(list(codes)):
        ph = ",".join("?" * len(chunk))
        frames.append(
            pd.read_sql_query(
                f"SELECT {columns} FROM {table} WHERE ts_code IN ({ph}) "
                "AND trade_date>=? AND trade_date<=? ORDER BY ts_code, trade_date",
                conn,
                params=(*chunk, normalize_date(start), normalize_date(end)),
            )
        )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def read_daily_prices(
    conn: sqlite3.Connection, ts_codes: list[str], start: str, end: str
) -> pd.DataFrame:
    return _read_in_codes(
        conn, "daily_price",
        "ts_code, trade_date, open, high, low, close, pre_close, vol, amount, adj_factor",
        ts_codes, start, end,
    )


def read_intraday_features(
    conn: sqlite3.Connection, ts_codes: list[str], start: str, end: str
) -> pd.DataFrame:
    return _read_in_codes(
        conn, "intraday_feature",
        "ts_code, trade_date, gap, amplitude, close_position, "
        "upper_shadow, lower_shadow, body_ratio",
        ts_codes, start, end,
    )


def read_top_list(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT trade_date, ts_code, l_buy, l_sell, net_amount, net_rate, "
        "amount_rate, reason FROM top_list WHERE trade_date>=? AND trade_date<=?",
        conn,
        params=(normalize_date(start), normalize_date(end)),
    )


def read_index_daily(
    conn: sqlite3.Connection, ts_code: str, start: str, end: str
) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT ts_code, trade_date, open, high, low, close, vol, amount, pct_chg "
        "FROM index_daily WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
        "ORDER BY trade_date",
        conn,
        params=(ts_code, normalize_date(start), normalize_date(end)),
    )


def read_stock_basic(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT ts_code, name, list_date, list_status FROM stock_basic", conn
    )


def read_corp_events(
    conn: sqlite3.Connection, ts_codes: list[str], start: str, end: str
) -> pd.DataFrame:
    if not ts_codes:
        return pd.DataFrame(
            columns=["ts_code", "ann_date", "event_type", "direction"]
        )
    frames = []
    for chunk in _chunked(list(ts_codes)):
        ph = ",".join("?" * len(chunk))
        frames.append(
            pd.read_sql_query(
                "SELECT ts_code, ann_date, event_type, direction FROM corp_event "
                f"WHERE ts_code IN ({ph}) AND ann_date>=? AND ann_date<=?",
                conn,
                params=(*chunk, normalize_date(start), normalize_date(end)),
            )
        )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
