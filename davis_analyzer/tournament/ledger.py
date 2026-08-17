"""OOS ledger — the single enforcement point of version discipline (§5.5)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from loguru import logger

# ──────────────────────────── schema ────────────────────────────

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS tournament_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    op_type TEXT NOT NULL,
    run_date TEXT NOT NULL,
    participants TEXT NOT NULL,
    params_version TEXT NOT NULL,
    oos_windows_used INTEGER NOT NULL,
    detail TEXT
);
"""


@dataclass
class LedgerRecord:
    op_type: str  # "run" | "replay" | "evolve" | "promote" | "deploy"
    run_date: date
    participants: list[tuple[str, str]]  # (name, version)
    params_version: str
    oos_windows_used: int
    detail: dict = field(default_factory=dict)


# ──────────────────────────── connection & DDL ────────────────────────────


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(LEDGER_DDL)
    conn.commit()


def open_db() -> sqlite3.Connection:
    from stockhot.data_layer.market_db import get_connection
    conn = get_connection()
    ensure_tables(conn)
    return conn


# ──────────────────────────── record I/O & discipline checks ────────────────────────────


def append_record(conn: sqlite3.Connection, rec: LedgerRecord) -> int:
    cur = conn.execute(
        "INSERT INTO tournament_ledger (op_type, run_date, participants, "
        "params_version, oos_windows_used, detail) VALUES (?,?,?,?,?,?)",
        (rec.op_type, rec.run_date.isoformat(),
         json.dumps(rec.participants), rec.params_version,
         rec.oos_windows_used, json.dumps(rec.detail, ensure_ascii=False)),
    )
    conn.commit()
    return int(cur.lastrowid)


def count_campaigns(conn: sqlite3.Connection, year: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM tournament_ledger WHERE op_type='evolve' "
        "AND substr(run_date,1,4)=?", (str(year),)
    ).fetchone()
    return int(row[0])


def detect_continual_tweaking(
    conn: sqlite3.Connection, days: int = 30, max_runs: int = 2
) -> bool:
    """Same params_version evolved too often inside a rolling window."""
    rows = conn.execute(
        "SELECT run_date, params_version FROM tournament_ledger "
        "WHERE op_type='evolve' ORDER BY run_date"
    ).fetchall()
    by_version: dict[str, list[date]] = {}
    for run_date_str, version in rows:
        by_version.setdefault(version, []).append(date.fromisoformat(run_date_str))
    for version, dates in by_version.items():
        for i, d in enumerate(dates):
            window = [x for x in dates[i:] if x <= d + timedelta(days=days)]
            if len(window) > max_runs:
                logger.warning("continual tweaking suspected: {} ×{} within {}d", version, len(window), days)
                return True
    return False
