"""recap 台账:market_data.db 自管表 recap_episodes(模式 B,limitup 先例)。"""
from __future__ import annotations

import json
import sqlite3
import time

RECAP_STATUSES: tuple[str, ...] = (
    "selected", "scripted", "sheeted", "recorded", "packed", "composed", "published",
)


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS recap_episodes ("
        "trade_date TEXT PRIMARY KEY, "
        "status TEXT NOT NULL, "
        "candidates_json TEXT, "
        "episode_json TEXT, "
        "facts_json TEXT, "
        "pushed_at TEXT, "
        "packed_at TEXT, "
        "created_at REAL, "
        "updated_at REAL)"
    )
    conn.commit()


def save_episode(conn: sqlite3.Connection, meta: dict) -> None:
    row = conn.execute("SELECT created_at FROM recap_episodes WHERE trade_date=?",
                       (meta["trade_date"],)).fetchone()
    now = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO recap_episodes "
        "(trade_date, status, candidates_json, episode_json, facts_json, "
        " pushed_at, packed_at, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (meta["trade_date"], meta["status"],
         meta.get("candidates_json"), meta.get("episode_json"), meta.get("facts_json"),
         meta.get("pushed_at"), meta.get("packed_at"),
         row[0] if row else now, now),
    )
    conn.commit()


def update_status(conn: sqlite3.Connection, trade_date: str, status: str) -> None:
    if status not in RECAP_STATUSES:
        raise ValueError(f"未知状态 {status!r},合法: {RECAP_STATUSES}")
    conn.execute("UPDATE recap_episodes SET status=?, updated_at=? WHERE trade_date=?",
                 (status, time.time(), trade_date))
    conn.commit()


def get_episode(conn: sqlite3.Connection, trade_date: str) -> dict | None:
    row = conn.execute(
        "SELECT trade_date, status, candidates_json, episode_json, facts_json, "
        "pushed_at, packed_at FROM recap_episodes WHERE trade_date=?", (trade_date,)
    ).fetchone()
    if not row:
        return None
    return {"trade_date": row[0], "status": row[1],
            "candidates": json.loads(row[2]) if row[2] else None,
            "episode": json.loads(row[3]) if row[3] else None,
            "facts": json.loads(row[4]) if row[4] else None,
            "pushed_at": row[5], "packed_at": row[6]}
