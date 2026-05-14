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

MARKET_FLOW = [
    {"date": "2026-05-14", "main_net": 50.5, "main_pct": 0.012, "huge_net": 30.0,
     "large_net": 20.5, "medium_net": -15.0, "small_net": -35.5}
]
SECTOR_FLOW = [
    {"name": "半导体", "change_pct": 2.5, "main_net": 10.0, "main_pct": 0.05,
     "huge_net": 5.0, "large_net": 5.0, "medium_net": -2.0, "small_net": -8.0},
    {"name": "银行", "change_pct": 1.0, "main_net": 20.0, "main_pct": 0.03,
     "huge_net": 15.0, "large_net": 5.0, "medium_net": -3.0, "small_net": -5.0},
]
TREND = {
    "trend": {
        "direction": "持续流入", "momentum": "加速",
        "large_vs_retail_divergence": True, "lookback_rows": 5, "avg_main_net": 12.34,
    },
    "summary": "主力资金持续流入",
}


@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO daily_data (trade_date, data_type, data_json) VALUES (?, ?, ?)",
        ("2026-05-14", "fund_flow_market", json.dumps(MARKET_FLOW)),
    )
    conn.execute(
        "INSERT INTO daily_data (trade_date, data_type, data_json) VALUES (?, ?, ?)",
        ("2026-05-14", "fund_flow_sector", json.dumps(SECTOR_FLOW)),
    )
    conn.execute(
        "INSERT INTO analysis_results (trade_date, analysis_type, result_json) VALUES (?, ?, ?)",
        ("2026-05-14", "fund_flow_trend", json.dumps(TREND)),
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


class TestFundFlowAnalysis:
    def test_with_data(self, client, seeded_db):
        resp = client.get("/api/fund-flow/2026-05-14", auth=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["date"] == "2026-05-14"
        assert body["status"] == "ok"
        assert len(body["market_flow"]) == 1
        assert body["market_flow"][0]["main_net"] == 50.5
        assert len(body["sector_flow"]) == 2
        assert body["trend"]["direction"] == "持续流入"
        assert body["trend"]["avg_main_net"] == 12.34

    def test_no_data(self, client, empty_db):
        resp = client.get("/api/fund-flow/2026-05-14", auth=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["date"] == "2026-05-14"
        assert body["status"] == "no_data"
        assert body["market_flow"] == []
        assert body["sector_flow"] == []
        assert body["summary"] == ""


class TestFundFlowMarket:
    def test_market_endpoint(self, client, seeded_db):
        resp = client.get("/api/fund-flow/2026-05-14/market", auth=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["date"] == "2026-05-14"
        assert len(body["data"]) == 1
        assert body["data"][0]["main_net"] == 50.5

    def test_market_empty(self, client, empty_db):
        resp = client.get("/api/fund-flow/2026-05-14/market", auth=AUTH)
        assert resp.status_code == 200
        assert resp.json()["data"] == []


class TestFundFlowSectors:
    def test_sectors_sorted_by_main_net_desc(self, client, seeded_db):
        resp = client.get("/api/fund-flow/2026-05-14/sectors", auth=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["date"] == "2026-05-14"
        assert len(body["data"]) == 2
        assert body["data"][0]["name"] == "银行"
        assert body["data"][0]["main_net"] == 20.0
        assert body["data"][1]["name"] == "半导体"
        assert body["data"][1]["main_net"] == 10.0

    def test_sectors_empty(self, client, empty_db):
        resp = client.get("/api/fund-flow/2026-05-14/sectors", auth=AUTH)
        assert resp.status_code == 200
        assert resp.json()["data"] == []
