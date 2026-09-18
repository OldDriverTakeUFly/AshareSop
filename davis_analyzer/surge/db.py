"""Surge 子系统 SQLite 表管理与读取（表挂 market_data.db，日期 YYYYMMDD）.

七张自管表（spec §4）：cyq_perf_cache / surge_snapshot / major_events /
cninfo_announcement / cninfo_org_map / surge_pattern_hits / surge_tags。
不写任何既有表；巨潮事件独立落 major_events，与 corp_event 不混写。
"""

from __future__ import annotations

import sqlite3

import pandas as pd

# ── DDL（幂等）──

_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS cyq_perf_cache (
      ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
      his_low REAL, his_high REAL,
      cost_5pct REAL, cost_15pct REAL, cost_50pct REAL,
      cost_85pct REAL, cost_95pct REAL,
      weight_avg REAL, winner_rate REAL,
      fetched_at REAL,
      PRIMARY KEY (ts_code, trade_date))
    """,
    """
    CREATE TABLE IF NOT EXISTS surge_snapshot (
      trade_date TEXT NOT NULL, ts_code TEXT NOT NULL,
      name TEXT, industry TEXT, pct_chg REAL, amount_k REAL,
      pos_250d REAL, dist_ma20 REAL, dist_ma60 REAL,
      dist_ma120 REAL, dist_ma250 REAL, dd_high_250 REAL,
      ladder_label TEXT, is_st INTEGER, is_new INTEGER,
      elg_net_d0 REAL, lg_net_5d REAL, net_ratio_d0 REAL,
      consec_net_days INTEGER,
      cost_5pct REAL, cost_50pct REAL, cost_95pct REAL, weight_avg REAL,
      winner_rate REAL, winner_delta_5d REAL,
      resistance_price REAL, resistance_dist REAL,
      support_price REAL, support_dist REAL,
      hype_tags TEXT, risk_flags TEXT,
      hype_count INTEGER, risk_flag_count INTEGER,
      composite REAL, rank INTEGER,
      fetched_at REAL,
      PRIMARY KEY (trade_date, ts_code))
    """,
    """
    CREATE TABLE IF NOT EXISTS major_events (
      ts_code TEXT NOT NULL, ann_date TEXT NOT NULL,
      event_type TEXT NOT NULL,
      title TEXT,
      direction TEXT,
      source TEXT,
      fetched_at REAL,
      PRIMARY KEY (ts_code, ann_date, event_type, title))
    """,
    """
    CREATE TABLE IF NOT EXISTS cninfo_announcement (
      ts_code TEXT NOT NULL, ann_date TEXT NOT NULL,
      title TEXT NOT NULL,
      fetched_at REAL,
      PRIMARY KEY (ts_code, ann_date, title))
    """,
    """
    CREATE TABLE IF NOT EXISTS cninfo_org_map (
      ts_code TEXT PRIMARY KEY, org_id TEXT, updated_at REAL)
    """,
    """
    CREATE TABLE IF NOT EXISTS surge_pattern_hits (
      trade_date TEXT NOT NULL, ts_code TEXT NOT NULL,
      boom_date TEXT, boom_pct REAL, boom_vol_ratio REAL,
      pullback_start TEXT, pullback_end TEXT,
      pullback_depth REAL, vol_decay REAL,
      plateau_high REAL, plateau_days INTEGER,
      breakout_pct REAL,
      fetched_at REAL,
      PRIMARY KEY (trade_date, ts_code))
    """,
    """
    CREATE TABLE IF NOT EXISTS surge_tags (
      trade_date TEXT NOT NULL, ts_code TEXT NOT NULL,
      tag TEXT NOT NULL,
      fetched_at REAL,
      PRIMARY KEY (trade_date, ts_code, tag))
    """,
]


def connect() -> sqlite3.Connection:
    from stockhot.data_layer.market_db import get_connection

    return get_connection()


def ensure_tables(conn: sqlite3.Connection) -> None:
    for ddl in _TABLES:
        conn.execute(ddl)
    conn.commit()


# ── code / date normalization ──

_RULES_2 = {"60": ".SH", "68": ".SH", "00": ".SZ", "30": ".SZ", "92": ".BJ"}
_RULES_1 = {"8": ".BJ", "4": ".BJ"}


def to_suffixed_code(code: str) -> str:
    if not code or "." in code:
        return code
    suffix = _RULES_2.get(code[:2]) or _RULES_1.get(code[:1])
    return code + suffix if suffix else code


def strip_code_suffix(code: str) -> str:
    return code.split(".")[0] if "." in code else code


def normalize_date(d: str) -> str:
    return d.replace("-", "")


def to_dash_date(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if "-" not in d else d


# ── readers ──

def latest_trade_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
    return row[0] if row and row[0] else None


def trading_dates(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM daily_price "
        "WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
        (normalize_date(start), normalize_date(end)),
    ).fetchall()
    return [r[0] for r in rows]


def read_pool(conn: sqlite3.Connection, day: str) -> pd.DataFrame:
    """当日 pct_chg>7 命中池（join stock_basic 取名称；行业由 screen 层补 sw_member）."""
    day = normalize_date(day)
    df = pd.read_sql_query(
        "SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, "
        "d.pre_close, d.pct_chg, d.vol, d.amount, d.adj_factor, "
        "s.name AS name, s.list_date "
        "FROM daily_price d LEFT JOIN stock_basic s ON d.ts_code=s.ts_code "
        "WHERE d.trade_date=? AND d.pct_chg > 7.0",
        conn, params=(day,),
    )
    if df.empty:
        return df
    df["is_st"] = df["name"].fillna("").str.contains("ST").astype(int)
    return df.sort_values("pct_chg", ascending=False).reset_index(drop=True)


def read_sw_industry(conn: sqlite3.Connection, ts_codes: list[str]) -> pd.DataFrame:
    """con_code→行业(优先L2,无则L1;取最新快照且 out_date 为空)."""
    out_cols = ["ts_code", "index_code", "level", "name"]
    snap = conn.execute(
        "SELECT MAX(snapshot_date) FROM sw_member").fetchone()[0]
    if not snap or not ts_codes:
        return pd.DataFrame(columns=out_cols)
    # 库内 con_code 带后缀(000019.SZ);同查带/不带两种形式兜底
    codes = list({to_suffixed_code(c) for c in ts_codes} | set(ts_codes))
    ph = ",".join("?" * len(codes))
    df = pd.read_sql_query(
        f"SELECT m.con_code AS ts_code, m.index_code, i.level, i.name "
        f"FROM sw_member m JOIN sw_index i ON m.index_code=i.index_code "
        f"WHERE m.snapshot_date=? AND m.con_code IN ({ph}) "
        f"AND (m.out_date IS NULL OR m.out_date='')",
        conn, params=(snap, *codes))
    if df.empty:
        return pd.DataFrame(columns=out_cols)
    df["ts_code"] = df["ts_code"].map(to_suffixed_code)
    # L2 优先('L2'>'L1' 字典序,降序取首行)
    df = df.sort_values(["ts_code", "level"], ascending=[True, False])
    return df.drop_duplicates("ts_code", keep="first").reset_index(drop=True)


def read_sw_daily_all(conn: sqlite3.Connection, end_day: str,
                      lookback: int = 260) -> pd.DataFrame:
    """全行业收盘序列(供截面动量;日期格式以库内样例为准,归一为 YYYYMMDD)."""
    sample = conn.execute(
        "SELECT trade_date FROM sw_daily LIMIT 1").fetchone()
    if not sample:
        return pd.DataFrame()
    norm = sample[0].replace("-", "")  # 兼容 YYYY-MM-DD 落库格式
    end = normalize_date(end_day)
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM sw_daily WHERE replace(trade_date,'-','')<=? "
        "ORDER BY trade_date DESC LIMIT ?", (end, lookback))]
    if not dates:
        return pd.DataFrame()
    ph = ",".join("?" * len(dates))
    df = pd.read_sql_query(
        f"SELECT ts_code AS index_code, replace(trade_date,'-','') AS trade_date, "
        f"close FROM sw_daily WHERE trade_date IN ({ph})", conn, params=dates)
    return df
