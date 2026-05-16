"""Shared test fixtures for invest_sop tests.

Provides a temporary database fixture that patches DB_PATH so tests
never touch the production stockhot.db.
"""

import pytest


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Create a temporary SQLite database with the full schema initialised.

    Patches ``stockhot.storage.database.DB_PATH`` so every call to
    ``get_connection()`` uses the temp file instead of the production DB.
    """
    db_path = tmp_path / "test_stockhot.db"
    monkeypatch.setattr("stockhot.storage.database.DB_PATH", db_path)

    from stockhot.storage.database import init_database

    init_database()
    return db_path
