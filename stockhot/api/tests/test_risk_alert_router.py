import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from stockhot.api.main import app

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

RISK_RAW = {
    "st_stocks": [{"代码": "000001", "名称": "ST某某", "最新价": 5.5, "涨跌幅": -5.0}],
    "suspended_stocks": [{"代码": "000002", "名称": "停牌股"}],
    "abnormal_volatility": [{"code": "000003", "note": "振幅异常"}],
    "capital_flight": [],
    "high_position_risks": [],
}
RISK_ANALYSIS = {"summary": "风险提示: 共检出 1 项风险信号。"}


@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO daily_data (trade_date, data_type, data_json) VALUES (?, ?, ?)",
        ("2026-05-14", "risk_alert_raw", json.dumps(RISK_RAW)),
    )
    conn.execute(
        "INSERT INTO analysis_results (trade_date, analysis_type, result_json) VALUES (?, ?, ?)",
        ("2026-05-14", "risk_alert", json.dumps(RISK_ANALYSIS)),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr("stockhot.api.db.DB_PATH", db_path)
    yield db_path


@pytest.fixture()
def empty_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    monkeypatch.setattr("stockhot.api.db.DB_PATH", db_path)
    yield db_path


@pytest.fixture()
def client():
    return TestClient(app)


AUTH = ("stockhot", "stockhot")


class TestRiskAlert:
    def test_with_data(self, client, seeded_db):
        resp = client.get("/api/risk-alert/2026-05-14", auth=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["date"] == "2026-05-14"
        assert body["status"] == "ok"
        assert len(body["data"]["st_stocks"]) == 1
        assert body["data"]["st_stocks"][0]["代码"] == "000001"
        assert len(body["data"]["suspended_stocks"]) == 1
        assert len(body["data"]["abnormal_volatility"]) == 1
        assert body["data"]["capital_flight"] == []
        assert body["data"]["high_position_risks"] == []
        assert body["data"]["summary"] == "风险提示: 共检出 1 项风险信号。"

    def test_no_data(self, client, empty_db):
        resp = client.get("/api/risk-alert/2026-05-14", auth=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["date"] == "2026-05-14"
        assert body["status"] == "no_data"
        assert body["data"]["st_stocks"] == []
        assert body["data"]["suspended_stocks"] == []
        assert body["data"]["abnormal_volatility"] == []
        assert body["data"]["capital_flight"] == []
        assert body["data"]["high_position_risks"] == []
        assert body["data"]["summary"] == ""
