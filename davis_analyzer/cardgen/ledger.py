# davis_analyzer/cardgen/ledger.py
"""content_cards.db 台账:cards / revisions / validate_log 三表。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from davis_analyzer.cardgen.types import Failure

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "storage" / "database" / "content_cards.db"
STATUSES = ("drafting", "validated", "rendered", "queued")


def connect(db: Path | None = None) -> sqlite3.Connection:
    path = db or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS cards(
        topic TEXT PRIMARY KEY, spec_path TEXT NOT NULL, current_version INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'drafting' CHECK(status IN ('drafting','validated','rendered','queued')),
        as_of TEXT, expires_at TEXT, updated_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS revisions(
        topic TEXT NOT NULL, version INTEGER NOT NULL, reason TEXT NOT NULL,
        facts_digest TEXT, spec_digest TEXT, ts TEXT NOT NULL,
        PRIMARY KEY(topic, version))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS validate_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, version INTEGER NOT NULL,
        passed INTEGER NOT NULL, failures_json TEXT, ts TEXT NOT NULL)""")
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def register_card(conn: sqlite3.Connection, topic: str, spec_path: str) -> int:
    row = get_card(conn, topic)
    if row:
        return int(row["current_version"])
    conn.execute("INSERT INTO cards(topic, spec_path, updated_at) VALUES(?,?,?)",
                 (topic, spec_path, _now()))
    conn.commit()
    return 1


def get_card(conn: sqlite3.Connection, topic: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM cards WHERE topic=?", (topic,)).fetchone()


def record_render(conn: sqlite3.Connection, topic: str, version: int, reason: str,
                  facts_digest: str, spec_digest: str) -> None:
    conn.execute("INSERT OR REPLACE INTO revisions(topic, version, reason, facts_digest, spec_digest, ts) "
                 "VALUES(?,?,?,?,?,?)", (topic, version, reason, facts_digest, spec_digest, _now()))
    set_status(conn, topic, "rendered")


def bump_version(conn: sqlite3.Connection, topic: str, reason: str,
                 facts_digest: str, spec_digest: str) -> int:
    if not reason.strip():
        raise ValueError("bump_version 需要 reason(修订原因)")
    row = get_card(conn, topic)
    if row is None:
        raise ValueError(f"未登记的工程: {topic}")
    new_v = int(row["current_version"]) + 1
    conn.execute("UPDATE cards SET current_version=?, status='drafting', updated_at=? WHERE topic=?",
                 (new_v, _now(), topic))
    conn.execute("INSERT OR REPLACE INTO revisions(topic, version, reason, facts_digest, spec_digest, ts) "
                 "VALUES(?,?,?,?,?,?)", (topic, new_v, reason, facts_digest, spec_digest, _now()))
    conn.commit()
    return new_v


def set_status(conn: sqlite3.Connection, topic: str, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"非法状态: {status}")
    conn.execute("UPDATE cards SET status=?, updated_at=? WHERE topic=?", (status, _now(), topic))
    conn.commit()


def log_validate(conn: sqlite3.Connection, topic: str, version: int,
                 passed: bool, failures: list[Failure]) -> None:
    payload = json.dumps([f.__dict__ for f in failures], ensure_ascii=False)
    conn.execute("INSERT INTO validate_log(topic, version, passed, failures_json, ts) VALUES(?,?,?,?,?)",
                 (topic, version, int(passed), payload, _now()))
    conn.commit()


def status_rows(conn: sqlite3.Connection, topic: str | None = None) -> list[sqlite3.Row]:
    if topic:
        return conn.execute("SELECT * FROM cards WHERE topic=? ORDER BY updated_at DESC", (topic,)).fetchall()
    return conn.execute("SELECT * FROM cards ORDER BY updated_at DESC").fetchall()
