import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from stockhot.api.main import app
from stockhot.storage.database import init_database


@pytest.fixture()
def client_with_db(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("stockhot.storage.database.DB_PATH", db_path), patch(
        "stockhot.core.config.DB_PATH", db_path
    ), patch("stockhot.api.config.settings.DB_PATH", str(db_path)):
        init_database()
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO daily_data (trade_date, data_type, data_json) "
            "VALUES (?, ?, ?)",
            ("2026-05-13", "limit_up_pool", json.dumps([{"code": "000001"}])),
        )
        conn.execute(
            "INSERT INTO daily_data (trade_date, data_type, data_json) "
            "VALUES (?, ?, ?)",
            ("2026-05-12", "dragon_tiger_detail", json.dumps([])),
        )
        conn.commit()
        conn.close()
        yield TestClient(app), db_path


def test_health_check_returns_ok(client_with_db):
    client, db_path = client_with_db
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "db_path" in data
    assert "latest_dates" in data
    assert data["latest_dates"]["limit_up_pool"] == "2026-05-13"
    assert data["latest_dates"]["dragon_tiger_detail"] == "2026-05-12"


def test_dates_returns_list(client_with_db):
    client, db_path = client_with_db
    resp = client.get("/api/dates")
    assert resp.status_code == 200
    data = resp.json()
    assert "dates" in data
    assert "2026-05-13" in data["dates"]
    assert "2026-05-12" in data["dates"]
