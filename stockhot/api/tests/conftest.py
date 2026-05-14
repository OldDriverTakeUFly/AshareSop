import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from stockhot.api.auth import verify_credentials
from stockhot.api.main import app
from stockhot.storage.database import init_database


@pytest.fixture(autouse=True)
def _bypass_auth():
    app.dependency_overrides[verify_credentials] = lambda: True
    yield
    app.dependency_overrides.pop(verify_credentials, None)


@pytest.fixture()
def test_db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    with patch("stockhot.storage.database.DB_PATH", db_path), patch(
        "stockhot.core.config.DB_PATH", db_path
    ):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.close()
        init_database()
        yield db_path


@pytest.fixture()
def client():
    return TestClient(app)
