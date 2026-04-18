"""Database module for StockHot-CN."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from stockhot.core.config import DB_PATH, DATA_RETENTION_DAYS
from stockhot.core.exceptions import DatabaseError


def get_connection() -> sqlite3.Connection:
    """Get database connection."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_database() -> None:
    """Initialize database schema."""
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS daily_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                data_type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trade_date, data_type)
            );

            CREATE INDEX IF NOT EXISTS idx_daily_data_date ON daily_data(trade_date);

            CREATE TABLE IF NOT EXISTS analysis_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                analysis_type TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trade_date, analysis_type)
            );

            CREATE INDEX IF NOT EXISTS idx_analysis_date ON analysis_results(trade_date);

            CREATE TABLE IF NOT EXISTS generated_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                image_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_images_date ON generated_images(trade_date);

            CREATE TABLE IF NOT EXISTS publish_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                platform TEXT NOT NULL,
                status TEXT NOT NULL,
                response_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_publish_date ON publish_records(trade_date);
        """)
        conn.commit()
    finally:
        conn.close()


def save_daily_data(data: dict[str, Any]) -> None:
    """Save daily market data."""
    import json

    conn = get_connection()
    try:
        for key, value in data.items():
            if key == "date":
                continue
            conn.execute(
                "INSERT OR REPLACE INTO daily_data (trade_date, data_type, data_json) VALUES (?, ?, ?)",
                (data["date"], key, json.dumps(value, ensure_ascii=False)),
            )
        conn.commit()
    except Exception as e:
        raise DatabaseError(f"保存每日数据失败: {e}")
    finally:
        conn.close()


def get_daily_data(date: str) -> dict[str, Any]:
    """Get daily market data by date."""
    import json

    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT data_type, data_json FROM daily_data WHERE trade_date = ?",
            (date,),
        )
        result = {"date": date}
        for row in cursor:
            result[row["data_type"]] = json.loads(row["data_json"])
        return result
    finally:
        conn.close()


def cleanup_old_data(days: int = DATA_RETENTION_DAYS) -> int:
    """Clean up data older than specified days."""
    conn = get_connection()
    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        cursor = conn.execute("DELETE FROM daily_data WHERE trade_date < ?", (cutoff,))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def save_analysis_result(date: str, analysis_type: str, result: dict) -> None:
    """Save AI analysis result."""
    import json

    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO analysis_results (trade_date, analysis_type, result_json) VALUES (?, ?, ?)",
            (date, analysis_type, json.dumps(result, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def get_analysis_result(date: str, analysis_type: str) -> dict | None:
    """Get AI analysis result."""
    import json

    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT result_json FROM analysis_results WHERE trade_date = ? AND analysis_type = ?",
            (date, analysis_type),
        )
        row = cursor.fetchone()
        return json.loads(row["result_json"]) if row else None
    finally:
        conn.close()


def save_image_path(date: str, image_type: str, file_path: str) -> None:
    """Save generated image path."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO generated_images (trade_date, image_type, file_path) VALUES (?, ?, ?)",
            (date, image_type, file_path),
        )
        conn.commit()
    finally:
        conn.close()


def get_images_by_date(date: str) -> list[dict]:
    """Get all images for a specific date."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT image_type, file_path FROM generated_images WHERE trade_date = ?",
            (date,),
        )
        return [dict(row) for row in cursor]
    finally:
        conn.close()