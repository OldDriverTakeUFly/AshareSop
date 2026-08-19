"""日内研究沙盒的 SQLite 数据层（独立库，与生产缓存隔离）.

minute_bar     —— 分钟 K 线（PK: ts_code+freq+trade_date+trade_time）
backfill_chunk —— 回补进度台账（PK: ts_code+freq+month），仅当整月写入完成后
                  记账，断点续跑按此跳过，避免在途半月在重启后被误判已完成。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pandas as pd

# ── schema ──

_SCHEMA_MINUTE_BAR = """
CREATE TABLE IF NOT EXISTS minute_bar (
    ts_code TEXT NOT NULL,
    freq TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    trade_time TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL,
    source TEXT NOT NULL DEFAULT 'baostock',
    fetched_at TEXT,
    PRIMARY KEY (ts_code, freq, trade_date, trade_time)
)
"""

_SCHEMA_BACKFILL_CHUNK = """
CREATE TABLE IF NOT EXISTS backfill_chunk (
    ts_code TEXT NOT NULL,
    freq TEXT NOT NULL,
    month TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    rows INTEGER,
    finished_at TEXT,
    PRIMARY KEY (ts_code, freq, month)
)
"""


def research_db_path() -> Path:
    from stockhot.core.config import STORAGE_DIR

    return STORAGE_DIR / "database" / "intraday_research.db"


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else research_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA_MINUTE_BAR)
    conn.execute(_SCHEMA_BACKFILL_CHUNK)
    conn.commit()


# ── writers ──

_BAR_COLUMNS = (
    "ts_code, freq, trade_date, trade_time, "
    "open, high, low, close, volume, amount, source, fetched_at"
)


def upsert_bars(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """幂等写入分钟 K 线（同 PK 覆盖），返回写入行数。"""
    if df.empty:
        return 0
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    payload = df.assign(fetched_at=now)
    conn.executemany(
        f"INSERT OR REPLACE INTO minute_bar ({_BAR_COLUMNS}) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                r.ts_code, r.freq, r.trade_date, r.trade_time,
                r.open, r.high, r.low, r.close, r.volume, r.amount,
                r.source, r.fetched_at,
            )
            for r in payload.itertuples(index=False)
        ],
    )
    conn.commit()
    return len(payload)


def mark_chunk_done(
    conn: sqlite3.Connection,
    ts_code: str, freq: str, month: str,
    start_date: str, end_date: str, rows: int,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO backfill_chunk "
        "(ts_code, freq, month, start_date, end_date, rows, finished_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (ts_code, freq, month, start_date, end_date, rows,
         time.strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()


# ── readers ──

def finished_chunks(conn: sqlite3.Connection, freq: str) -> set[tuple[str, str]]:
    """已完成的 (ts_code, month) 集合（断点续跑依据）。"""
    rows = conn.execute(
        "SELECT ts_code, month FROM backfill_chunk WHERE freq=?", (freq,)
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def coverage_summary(conn: sqlite3.Connection) -> pd.DataFrame:
    """每只票的覆盖月数与分钟行数（status 子命令输出）。"""
    return pd.read_sql_query(
        "SELECT c.ts_code, c.freq, COUNT(*) AS months_done, "
        "       COALESCE(b.rows_total, 0) AS minute_rows, "
        "       MIN(c.start_date) AS since, MAX(c.end_date) AS until "
        "FROM backfill_chunk c "
        "LEFT JOIN (SELECT ts_code, freq, SUM(n) AS rows_total FROM "
        "           (SELECT ts_code, freq, COUNT(*) AS n FROM minute_bar "
        "            GROUP BY ts_code, freq) GROUP BY ts_code, freq) b "
        "  ON b.ts_code=c.ts_code AND b.freq=c.freq "
        "GROUP BY c.ts_code, c.freq ORDER BY c.ts_code",
        conn,
    )


def read_bars(
    conn: sqlite3.Connection,
    ts_codes: list[str],
    start: str, end: str,
    freq: str = "5min",
) -> pd.DataFrame:
    """读取分钟 K 线（供后续日内回测引擎调用；trade_date 归一为 YYYYMMDD）。"""
    if not ts_codes:
        return pd.DataFrame()
    frames = []
    for i in range(0, len(ts_codes), 900):
        chunk = ts_codes[i : i + 900]
        ph = ",".join("?" * len(chunk))
        frames.append(pd.read_sql_query(
            f"SELECT ts_code, trade_date, trade_time, open, high, low, close, "
            f"volume, amount FROM minute_bar WHERE freq='{freq}' "
            f"AND ts_code IN ({ph}) AND trade_date>=? AND trade_date<=? "
            "ORDER BY ts_code, trade_date, trade_time",
            conn,
            params=(*chunk, start.replace("-", ""), end.replace("-", "")),
        ))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
