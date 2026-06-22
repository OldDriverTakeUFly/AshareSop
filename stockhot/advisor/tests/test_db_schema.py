"""TDD tests for invest_watchlist + advisor_runs DB schema."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from stockhot.invest_sop.utils.db_helpers import _ALLOWED_TABLES
from stockhot.storage import database as db_module


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Point DB_PATH to a temp file and initialize the schema."""
    temp_path = tmp_path / "test_advisor.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_path)
    db_module.init_database()
    yield temp_path


def _get_table_names(db_path: Path) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [r[0] for r in cursor.fetchall()]
    finally:
        conn.close()


def _get_index_names(db_path: Path) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
        )
        return [r[0] for r in cursor.fetchall()]
    finally:
        conn.close()


class TestInvestWatchlistSchema:
    def test_table_exists(self, temp_db):
        tables = _get_table_names(temp_db)
        assert "invest_watchlist" in tables

    def test_unique_code_constraint(self, temp_db):
        conn = sqlite3.connect(str(temp_db))
        try:
            conn.execute(
                "INSERT INTO invest_watchlist (code, added_date) VALUES (?, ?)",
                ("000001", "2025-01-01"),
            )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO invest_watchlist (code, added_date) VALUES (?, ?)",
                    ("000001", "2025-01-02"),
                )
        finally:
            conn.close()

    def test_status_index_exists(self, temp_db):
        indexes = _get_index_names(temp_db)
        assert "idx_watchlist_status" in indexes


class TestAdvisorRunsSchema:
    def test_table_exists(self, temp_db):
        tables = _get_table_names(temp_db)
        assert "advisor_runs" in tables

    def test_unique_composite_constraint(self, temp_db):
        conn = sqlite3.connect(str(temp_db))
        try:
            conn.execute(
                "INSERT INTO advisor_runs "
                "(trade_date, stock_code, recommendation_type, action, confidence) "
                "VALUES (?, ?, ?, ?, ?)",
                ("2025-01-01", "000001", "buy", "strong_buy", "high"),
            )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO advisor_runs "
                    "(trade_date, stock_code, recommendation_type, action, confidence) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("2025-01-01", "000001", "buy", "hold", "medium"),
                )
        finally:
            conn.close()

    def test_different_recommendation_type_allowed(self, temp_db):
        """Same date+code but different recommendation_type should succeed."""
        conn = sqlite3.connect(str(temp_db))
        try:
            conn.execute(
                "INSERT INTO advisor_runs "
                "(trade_date, stock_code, recommendation_type, action, confidence) "
                "VALUES (?, ?, ?, ?, ?)",
                ("2025-01-01", "000001", "buy", "strong_buy", "high"),
            )
            conn.execute(
                "INSERT INTO advisor_runs "
                "(trade_date, stock_code, recommendation_type, action, confidence) "
                "VALUES (?, ?, ?, ?, ?)",
                ("2025-01-01", "000001", "sell", "hold", "medium"),
            )
            cursor = conn.execute("SELECT COUNT(*) FROM advisor_runs")
            assert cursor.fetchone()[0] == 2
        finally:
            conn.close()

    def test_date_index_exists(self, temp_db):
        indexes = _get_index_names(temp_db)
        assert "idx_advisor_runs_date" in indexes


class TestAllowedTables:
    def test_invest_watchlist_in_allowlist(self):
        assert "invest_watchlist" in _ALLOWED_TABLES

    def test_advisor_runs_in_allowlist(self):
        assert "advisor_runs" in _ALLOWED_TABLES
