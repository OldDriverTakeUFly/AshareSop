# davis_analyzer/metrics/db.py
"""xhs_metrics 台账:多账号 schema 与写入(快照式,幂等)。"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger

DB_PATH = Path(__file__).resolve().parents[2] / "storage" / "database" / "xhs_metrics.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
    account_id TEXT PRIMARY KEY, platform TEXT NOT NULL DEFAULT 'xhs',
    name TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notes(
    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    topic TEXT, grp TEXT, published_at TEXT, title TEXT NOT NULL, url TEXT,
    UNIQUE(account_id, title));
CREATE TABLE IF NOT EXISTS note_metrics(
    note_id INTEGER NOT NULL REFERENCES notes(note_id),
    captured_at TEXT NOT NULL, views INTEGER, likes INTEGER, collects INTEGER,
    comments INTEGER, shares INTEGER, source TEXT NOT NULL,
    PRIMARY KEY(note_id, captured_at));
CREATE TABLE IF NOT EXISTS account_metrics(
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    captured_at TEXT NOT NULL, followers INTEGER, following INTEGER,
    total_likes INTEGER, source TEXT NOT NULL,
    PRIMARY KEY(account_id, captured_at));
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def init_db(account_id: str, name: str = "", platform: str = "xhs") -> None:
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO accounts(account_id,platform,name,created_at) VALUES(?,?,?,?)",
                  (account_id, platform, name, datetime.now().isoformat(timespec="seconds")))
        print(f"账号就绪: {account_id} [{name or platform}]")


def upsert_note(conn: sqlite3.Connection, account_id: str, title: str, *,
                topic: str = "", grp: str = "", published_at: str = "", url: str = "") -> int:
    """按 (account_id, title) 幂等登记笔记,返回 note_id。"""
    cur = conn.execute(
        "INSERT INTO notes(account_id,topic,grp,published_at,title,url) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(account_id,title) DO UPDATE SET "
        "topic=CASE WHEN excluded.topic!='' THEN excluded.topic ELSE notes.topic END, "
        "grp=CASE WHEN excluded.grp!='' THEN excluded.grp ELSE notes.grp END, "
        "published_at=CASE WHEN excluded.published_at!='' THEN excluded.published_at ELSE notes.published_at END, "
        "url=CASE WHEN excluded.url!='' THEN excluded.url ELSE notes.url END",
        (account_id, topic, grp, published_at, title, url))
    if cur.lastrowid:  # INSERT 路径
        return int(cur.lastrowid)
    row = conn.execute("SELECT note_id FROM notes WHERE account_id=? AND title=?",
                       (account_id, title)).fetchone()
    return int(row["note_id"])


def record_note_metrics(conn: sqlite3.Connection, note_id: int, captured_at: str, *,
                        views: int | None = None, likes: int | None = None,
                        collects: int | None = None, comments: int | None = None,
                        shares: int | None = None, source: str = "manual") -> None:
    """写入一条快照(manual 覆盖同时刻 vision;数值缺省 NULL 表示未采集)。"""
    conn.execute(
        "INSERT INTO note_metrics(note_id,captured_at,views,likes,collects,comments,shares,source) "
        "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(note_id,captured_at) DO UPDATE SET "
        "views=excluded.views, likes=excluded.likes, collects=excluded.collects, "
        "comments=excluded.comments, shares=excluded.shares, source=excluded.source",
        (note_id, captured_at, views, likes, collects, comments, shares, source))
    logger.debug(f"note_metrics {note_id}@{captured_at} src={source}")


def record_account_metrics(conn: sqlite3.Connection, account_id: str, captured_at: str, *,
                           followers: int | None = None, following: int | None = None,
                           total_likes: int | None = None, source: str = "manual") -> None:
    conn.execute(
        "INSERT INTO account_metrics(account_id,captured_at,followers,following,total_likes,source) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(account_id,captured_at) DO UPDATE SET "
        "followers=excluded.followers, following=excluded.following, "
        "total_likes=excluded.total_likes, source=excluded.source",
        (account_id, captured_at, followers, following, total_likes, source))
