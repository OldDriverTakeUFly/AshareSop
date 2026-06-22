import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from stockhot.api.auth import verify_credentials
from stockhot.api.main import app
from stockhot.storage.database import init_database

SAMPLE_LIMIT_UP_POOL = [
    {
        "code": "000001",
        "name": "平安银行",
        "change_pct": 10.0,
        "seal_amount": 5e7,
        "max_board": 3.0,
        "consecutive_boards": 2.0,
        "sector": "银行",
        "broken_count": 0.0,
        "first_seal_time": "09:30:00",
        "last_seal_time": "14:55:00",
        "turnover_rate": 8.5,
    }
]
SAMPLE_BROKEN_POOL = [
    {
        "code": "000002",
        "name": "万科A",
        "change_pct": 5.2,
        "broken_count": 3.0,
        "sector": "房地产",
    }
]
SAMPLE_LIMIT_DOWN_POOL = [{"code": "000003", "name": "测试", "change_pct": -10.0, "sector": "其他"}]
SAMPLE_ANALYSIS = {
    "consecutive_boards": [],
    "sector_correlation": [],
    "seal_strength_ranking": [],
    "summary": "涨停 1 只股票",
}


@pytest.fixture()
def client_with_data(tmp_path):
    db_path = tmp_path / "test.db"
    app.dependency_overrides[verify_credentials] = lambda: True
    with (
        patch("stockhot.storage.database.DB_PATH", db_path),
        patch("stockhot.core.config.DB_PATH", db_path),
        patch("stockhot.api.config.settings.DB_PATH", str(db_path)),
    ):
        init_database()
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO daily_data (trade_date, data_type, data_json) VALUES (?, ?, ?)",
            ("2026-05-14", "limit_up_pool", json.dumps(SAMPLE_LIMIT_UP_POOL)),
        )
        conn.execute(
            "INSERT INTO daily_data (trade_date, data_type, data_json) VALUES (?, ?, ?)",
            ("2026-05-14", "broken_pool", json.dumps(SAMPLE_BROKEN_POOL)),
        )
        conn.execute(
            "INSERT INTO daily_data (trade_date, data_type, data_json) VALUES (?, ?, ?)",
            ("2026-05-14", "limit_down_pool", json.dumps(SAMPLE_LIMIT_DOWN_POOL)),
        )
        conn.execute(
            "INSERT INTO analysis_results (trade_date, analysis_type, result_json) VALUES (?, ?, ?)",
            ("2026-05-14", "limit_up_analysis", json.dumps(SAMPLE_ANALYSIS)),
        )
        conn.commit()
        conn.close()
        yield TestClient(app), db_path
    app.dependency_overrides.pop(verify_credentials, None)


@pytest.fixture()
def client_empty(tmp_path):
    db_path = tmp_path / "test.db"
    app.dependency_overrides[verify_credentials] = lambda: True
    with (
        patch("stockhot.storage.database.DB_PATH", db_path),
        patch("stockhot.core.config.DB_PATH", db_path),
        patch("stockhot.api.config.settings.DB_PATH", str(db_path)),
    ):
        init_database()
        yield TestClient(app), db_path
    app.dependency_overrides.pop(verify_credentials, None)


def test_get_limit_up_with_data(client_with_data):
    client, _ = client_with_data
    resp = client.get("/api/limit-up/2026-05-14")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["date"] == "2026-05-14"
    assert len(data["limit_up_pool"]) == 1
    assert data["limit_up_pool"][0]["code"] == "000001"
    assert len(data["broken_pool"]) == 1
    assert data["broken_pool"][0]["code"] == "000002"
    assert len(data["limit_down_pool"]) == 1
    assert data["limit_down_pool"][0]["code"] == "000003"
    assert data["analysis"]["summary"] == "涨停 1 只股票"


def test_get_limit_up_no_data(client_empty):
    client, _ = client_empty
    resp = client.get("/api/limit-up/2026-05-14")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "no_data"
    assert data["date"] == "2026-05-14"
    assert data["limit_up_pool"] == []
    assert data["broken_pool"] == []
    assert data["limit_down_pool"] == []


def test_get_limit_up_summary_with_data(client_with_data):
    client, _ = client_with_data
    resp = client.get("/api/limit-up/2026-05-14/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["date"] == "2026-05-14"
    assert data["summary"] == "涨停 1 只股票"


def test_get_limit_up_summary_no_data(client_empty):
    client, _ = client_empty
    resp = client.get("/api/limit-up/2026-05-14/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["date"] == "2026-05-14"
    assert data["summary"] == "暂无数据"
