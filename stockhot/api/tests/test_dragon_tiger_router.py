import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from stockhot.api.auth import verify_credentials
from stockhot.api.main import app
from stockhot.storage.database import init_database

SAMPLE_DRAGON_TIGER_DETAIL = [
    {
        "code": "000001",
        "name": "平安银行",
        "reason": "涨幅偏离值达7%",
        "close_price": 15.5,
        "change_pct": 10.0,
        "net_buy_amount": 1.2e8,
        "buy_amount": 3.0e8,
        "sell_amount": 1.8e8,
        "list_date": "20260514",
    }
]
SAMPLE_INSTITUTIONAL = [
    {
        "inst_code": "机构1",
        "inst_name": "机构专用",
        "buy_amount": 5e8,
        "sell_amount": 2e8,
        "net_amount": 3e8,
    }
]
SAMPLE_BROKERS = [
    {
        "broker_name": "东方财富证券拉萨团结路",
        "buy_amount": 1e8,
        "sell_amount": 0.5e8,
        "net_amount": 0.5e8,
    }
]
SAMPLE_HOT_MONEY = [
    {
        "broker": "某某营业部",
        "buy_targets": ["000001"],
        "sell_targets": ["000002"],
        "net_direction": "net_buy",
    }
]
SAMPLE_ANALYSIS = {
    "institutional": SAMPLE_INSTITUTIONAL,
    "brokers": SAMPLE_BROKERS,
    "hot_money": SAMPLE_HOT_MONEY,
    "summary": "龙虎榜上榜股票数: 1",
}


@pytest.fixture()
def client_with_data(tmp_path):
    db_path = tmp_path / "test.db"
    app.dependency_overrides[verify_credentials] = lambda: True
    with patch("stockhot.storage.database.DB_PATH", db_path), patch(
        "stockhot.core.config.DB_PATH", db_path
    ), patch("stockhot.api.config.settings.DB_PATH", str(db_path)):
        init_database()
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO daily_data (trade_date, data_type, data_json) VALUES (?, ?, ?)",
            ("2026-05-14", "dragon_tiger_detail", json.dumps(SAMPLE_DRAGON_TIGER_DETAIL)),
        )
        conn.execute(
            "INSERT INTO analysis_results (trade_date, analysis_type, result_json) VALUES (?, ?, ?)",
            ("2026-05-14", "dragon_tiger", json.dumps(SAMPLE_ANALYSIS)),
        )
        conn.commit()
        conn.close()
        yield TestClient(app), db_path
    app.dependency_overrides.pop(verify_credentials, None)


@pytest.fixture()
def client_empty(tmp_path):
    db_path = tmp_path / "test.db"
    app.dependency_overrides[verify_credentials] = lambda: True
    with patch("stockhot.storage.database.DB_PATH", db_path), patch(
        "stockhot.core.config.DB_PATH", db_path
    ), patch("stockhot.api.config.settings.DB_PATH", str(db_path)):
        init_database()
        yield TestClient(app), db_path
    app.dependency_overrides.pop(verify_credentials, None)


def test_get_dragon_tiger_with_data(client_with_data):
    client, _ = client_with_data
    resp = client.get("/api/dragon-tiger/2026-05-14")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["date"] == "2026-05-14"
    assert len(data["detail"]) == 1
    assert data["detail"][0]["code"] == "000001"
    assert data["detail"][0]["reason"] == "涨幅偏离值达7%"
    assert len(data["institutional"]) == 1
    assert data["institutional"][0]["inst_name"] == "机构专用"
    assert len(data["brokers"]) == 1
    assert data["brokers"][0]["broker_name"] == "东方财富证券拉萨团结路"
    assert len(data["hot_money"]) == 1
    assert data["hot_money"][0]["net_direction"] == "net_buy"
    assert data["summary"] == "龙虎榜上榜股票数: 1"


def test_get_dragon_tiger_no_data(client_empty):
    client, _ = client_empty
    resp = client.get("/api/dragon-tiger/2026-05-14")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "no_data"
    assert data["date"] == "2026-05-14"
    assert data["detail"] == []
    assert data["institutional"] == []
    assert data["brokers"] == []
    assert data["hot_money"] == []
    assert data["summary"] == ""


def test_get_dragon_tiger_summary_with_data(client_with_data):
    client, _ = client_with_data
    resp = client.get("/api/dragon-tiger/2026-05-14/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["date"] == "2026-05-14"
    assert data["summary"] == "龙虎榜上榜股票数: 1"


def test_get_dragon_tiger_summary_no_data(client_empty):
    client, _ = client_empty
    resp = client.get("/api/dragon-tiger/2026-05-14/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["date"] == "2026-05-14"
    assert data["summary"] == "暂无数据"
