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
    conn.execute("PRAGMA journal_mode=WAL")
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

        # invest_sop tables
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS invest_overseas_market (
                date TEXT PRIMARY KEY,
                sp500_pct REAL,
                nasdaq_pct REAL,
                dow_pct REAL,
                us_10y REAL,
                us_10y_change_bp REAL,
                vix REAL,
                a50_pct REAL,
                usd_cny REAL,
                us_vix REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_invest_overseas_date ON invest_overseas_market(date);

            CREATE TABLE IF NOT EXISTS invest_domestic_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                event_name TEXT NOT NULL,
                affected_sector TEXT,
                impact_direction TEXT,
                severity TEXT,
                source TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, event_name)
            );

            CREATE TABLE IF NOT EXISTS invest_supply_chain (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                sector TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                value REAL,
                unit TEXT,
                source TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, sector, metric_name)
            );

            CREATE INDEX IF NOT EXISTS idx_invest_supply_sector_metric
                ON invest_supply_chain(sector, metric_name);

            CREATE TABLE IF NOT EXISTS invest_futures_sentiment (
                date TEXT PRIMARY KEY,
                if_pct REAL,
                ic_pct REAL,
                im_pct REAL,
                if_basis REAL,
                ic_basis REAL,
                northbound_net REAL,
                margin_balance REAL,
                put_call_ratio REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_invest_futures_date ON invest_futures_sentiment(date);

            CREATE TABLE IF NOT EXISTS invest_morning_data (
                date TEXT PRIMARY KEY,
                a50_morning_pct REAL,
                nikkei_pct REAL,
                kospi_pct REAL,
                usd_cny_morning REAL,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_invest_morning_date ON invest_morning_data(date);

            CREATE TABLE IF NOT EXISTS invest_cycle_assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sector TEXT UNIQUE NOT NULL,
                cycle_position TEXT,
                crowding_score INTEGER,
                assessment_date TEXT,
                notes TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS invest_holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                name TEXT,
                sector TEXT,
                entry_price REAL,
                current_price REAL,
                stop_loss_logic REAL,
                stop_loss_technical REAL,
                stop_loss_hard REAL,
                target_price REAL,
                position_pct REAL,
                quantity INTEGER DEFAULT 0,
                avg_cost REAL,
                entry_date TEXT,
                status TEXT DEFAULT 'active',
                notes TEXT,
                davis_score_at_buy REAL,
                thesis_snapshot_json TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_invest_holdings_code ON invest_holdings(code);

            CREATE TABLE IF NOT EXISTS invest_holdings_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                holding_id INTEGER NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('buy', 'sell')),
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                date TEXT NOT NULL,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (holding_id) REFERENCES invest_holdings(id)
            );

            CREATE INDEX IF NOT EXISTS idx_invest_transactions_holding
                ON invest_holdings_transactions(holding_id);

            CREATE TABLE IF NOT EXISTS invest_sector_rules (
                sector TEXT PRIMARY KEY,
                stop_loss_pct REAL NOT NULL DEFAULT -0.12,
                target_pct REAL NOT NULL DEFAULT 0.20,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS invest_watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                name TEXT,
                sector TEXT,
                added_date TEXT NOT NULL,
                trigger_reason TEXT,
                target_entry_low REAL,
                target_entry_high REAL,
                stop_loss_pct REAL,
                priority INTEGER DEFAULT 1,
                status TEXT DEFAULT 'watching',
                source TEXT DEFAULT 'manual',
                notes TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_watchlist_status ON invest_watchlist(status);

            CREATE TABLE IF NOT EXISTS advisor_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                recommendation_type TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence TEXT NOT NULL,
                reasoning_json TEXT,
                prompt_version TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                model_name TEXT,
                data_age_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trade_date, stock_code, recommendation_type)
            );

            CREATE INDEX IF NOT EXISTS idx_advisor_runs_date ON advisor_runs(trade_date);
        """)

        # Migrate: add quantity and avg_cost columns if missing
        holdings_cols = [r[1] for r in conn.execute("PRAGMA table_info(invest_holdings)").fetchall()]
        if "quantity" not in holdings_cols:
            conn.execute("ALTER TABLE invest_holdings ADD COLUMN quantity INTEGER DEFAULT 0")
        if "avg_cost" not in holdings_cols:
            conn.execute("ALTER TABLE invest_holdings ADD COLUMN avg_cost REAL")

        if "davis_score_at_buy" not in holdings_cols:
            conn.execute("ALTER TABLE invest_holdings ADD COLUMN davis_score_at_buy REAL")
        if "thesis_snapshot_json" not in holdings_cols:
            conn.execute("ALTER TABLE invest_holdings ADD COLUMN thesis_snapshot_json TEXT")

        # Insert default sector rules
        default_rules = [
            ('AI', -0.15, 0.25),
            ('半导体', -0.15, 0.25),
            ('软件', -0.12, 0.20),
            ('锂电', -0.15, 0.25),
            ('光伏', -0.15, 0.25),
            ('新能源车', -0.15, 0.25),
            ('有色', -0.12, 0.20),
            ('化工', -0.12, 0.20),
            ('煤炭', -0.10, 0.15),
            ('消费', -0.10, 0.15),
            ('金融', -0.08, 0.12),
            ('医药', -0.12, 0.20),
            ('default', -0.12, 0.20),
        ]
        for sector, sl, tp in default_rules:
            conn.execute(
                "INSERT OR IGNORE INTO invest_sector_rules (sector, stop_loss_pct, target_pct) VALUES (?, ?, ?)",
                (sector, sl, tp),
            )

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


def get_preferred_analysis_result(date: str, analysis_types: tuple[str, ...]) -> dict | None:
    """Return the first available analysis result from a list of preferred types.

    Iterates through *analysis_types* in order and returns the first non-None
    result found for the given *date*.  Returns ``None`` when none of the
    types have a stored result.
    """
    for analysis_type in analysis_types:
        result = get_analysis_result(date, analysis_type)
        if result is not None:
            return result
    return None