"""Tests for async DB read adapter using a temp SQLite database."""

import json
import sqlite3

import pytest
import pytest_asyncio

from stockhot.api.db import (
    get_analysis_result,
    get_available_dates,
    get_daily_data,
    get_latest_date,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS daily_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    data_type TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(trade_date, data_type)
);
CREATE TABLE IF NOT EXISTS analysis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    analysis_type TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(trade_date, analysis_type)
);
"""


@pytest_asyncio.fixture
async def tmp_db(tmp_path):
    """Create a temp DB with schema and sample data, return its path string."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)

    conn.execute(
        "INSERT INTO daily_data (trade_date, data_type, data_json) VALUES (?, ?, ?)",
        ("2026-05-14", "limit_up_pool", json.dumps([{"code": "000001", "name": "平安银行"}])),
    )
    conn.execute(
        "INSERT INTO daily_data (trade_date, data_type, data_json) VALUES (?, ?, ?)",
        ("2026-05-14", "dragon_tiger_detail", json.dumps([{"code": "000002", "name": "万科A"}])),
    )
    conn.execute(
        "INSERT INTO daily_data (trade_date, data_type, data_json) VALUES (?, ?, ?)",
        ("2026-05-13", "limit_up_pool", json.dumps([])),
    )
    conn.execute(
        "INSERT INTO analysis_results (trade_date, analysis_type, result_json) VALUES (?, ?, ?)",
        ("2026-05-14", "limit_up_analysis", json.dumps({"summary": "涨停 1 只"})),
    )
    conn.commit()
    conn.close()
    return str(db_path)


@pytest.mark.asyncio
async def test_get_daily_data_found(tmp_db):
    result = await get_daily_data("2026-05-14", db_path=tmp_db)
    assert result["date"] == "2026-05-14"
    assert len(result["limit_up_pool"]) == 1
    assert result["limit_up_pool"][0]["code"] == "000001"
    assert len(result["dragon_tiger_detail"]) == 1


@pytest.mark.asyncio
async def test_get_daily_data_missing_date(tmp_db):
    result = await get_daily_data("2020-01-01", db_path=tmp_db)
    assert result == {"date": "2020-01-01"}


@pytest.mark.asyncio
async def test_get_analysis_result_found(tmp_db):
    result = await get_analysis_result("2026-05-14", "limit_up_analysis", db_path=tmp_db)
    assert result is not None
    assert result["summary"] == "涨停 1 只"


@pytest.mark.asyncio
async def test_get_analysis_result_missing(tmp_db):
    result = await get_analysis_result("2026-05-14", "nonexistent", db_path=tmp_db)
    assert result is None


@pytest.mark.asyncio
async def test_get_analysis_result_wrong_date(tmp_db):
    result = await get_analysis_result("2020-01-01", "limit_up_analysis", db_path=tmp_db)
    assert result is None


@pytest.mark.asyncio
async def test_get_available_dates(tmp_db):
    dates = await get_available_dates(db_path=tmp_db)
    assert dates == ["2026-05-14", "2026-05-13"]


@pytest.mark.asyncio
async def test_get_available_dates_empty_db(tmp_path):
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    conn.close()
    dates = await get_available_dates(db_path=str(db_path))
    assert dates == []


@pytest.mark.asyncio
async def test_get_latest_date(tmp_db):
    latest = await get_latest_date(db_path=tmp_db)
    assert latest == "2026-05-14"


@pytest.mark.asyncio
async def test_get_latest_date_empty_db(tmp_path):
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    conn.close()
    latest = await get_latest_date(db_path=str(db_path))
    assert latest is None
