"""End-to-end integration tests for the full FastAPI application.

Tests the app with all routers registered, auth middleware applied,
and a real SQLite database — verifying that the pieces work together.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from stockhot.api.auth import verify_credentials
from stockhot.api.main import app
from stockhot.storage.database import init_database

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DATE = "2026-05-13"


@pytest.fixture()
def _real_auth():
    """Remove the autouse auth bypass so real HTTP Basic Auth is enforced."""
    app.dependency_overrides.pop(verify_credentials, None)
    yield
    # Restore the bypass so other test files are unaffected
    app.dependency_overrides[verify_credentials] = lambda: True


@pytest.fixture()
def seeded_db(tmp_path):
    """Create a temp DB with init_database and seed rows for every data type."""
    db_path = tmp_path / "integration.db"
    with (
        patch("stockhot.storage.database.DB_PATH", db_path),
        patch("stockhot.core.config.DB_PATH", db_path),
    ):
        init_database()
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO daily_data (trade_date, data_type, data_json) " "VALUES (?, ?, ?)",
            (
                DATE,
                "limit_up_pool",
                json.dumps(
                    [
                        {
                            "code": "000001",
                            "name": "TestStock",
                            "change_pct": 10.0,
                            "seal_amount": 5e8,
                            "max_board": 10.0,
                            "consecutive_boards": 1,
                            "sector": "科技",
                            "broken_count": 0,
                            "first_seal_time": "09:30:00",
                            "last_seal_time": "15:00:00",
                            "turnover_rate": 5.2,
                        }
                    ]
                ),
            ),
        )
        conn.execute(
            "INSERT INTO daily_data (trade_date, data_type, data_json) " "VALUES (?, ?, ?)",
            (DATE, "broken_pool", json.dumps([])),
        )
        conn.execute(
            "INSERT INTO daily_data (trade_date, data_type, data_json) " "VALUES (?, ?, ?)",
            (DATE, "limit_down_pool", json.dumps([])),
        )
        conn.execute(
            "INSERT INTO daily_data (trade_date, data_type, data_json) " "VALUES (?, ?, ?)",
            (
                DATE,
                "dragon_tiger_detail",
                json.dumps(
                    [
                        {
                            "code": "000002",
                            "name": "DgStock",
                            "reason": "涨幅偏离",
                            "close_price": 15.0,
                            "change_pct": 9.5,
                            "net_buy_amount": 1e7,
                            "buy_amount": 2e7,
                            "sell_amount": 1e7,
                            "list_date": DATE,
                        }
                    ]
                ),
            ),
        )
        conn.execute(
            "INSERT INTO daily_data (trade_date, data_type, data_json) " "VALUES (?, ?, ?)",
            (
                DATE,
                "fund_flow_market",
                json.dumps(
                    [
                        {
                            "date": DATE,
                            "main_net": -1e9,
                            "main_pct": -0.5,
                            "huge_net": -5e8,
                            "large_net": -3e8,
                            "medium_net": 2e8,
                            "small_net": 6e8,
                        }
                    ]
                ),
            ),
        )
        conn.execute(
            "INSERT INTO daily_data (trade_date, data_type, data_json) " "VALUES (?, ?, ?)",
            (
                DATE,
                "fund_flow_sector",
                json.dumps(
                    [
                        {
                            "name": "电子",
                            "change_pct": 1.2,
                            "main_net": 3e8,
                            "main_pct": 0.8,
                            "huge_net": 1e8,
                            "large_net": 1e8,
                            "medium_net": 5e7,
                            "small_net": 5e7,
                        }
                    ]
                ),
            ),
        )
        conn.execute(
            "INSERT INTO daily_data (trade_date, data_type, data_json) " "VALUES (?, ?, ?)",
            (
                DATE,
                "risk_alert_raw",
                json.dumps(
                    {
                        "st_stocks": [
                            {"代码": "600001", "名称": "ST测试", "最新价": 3.5, "涨跌幅": -2.0}
                        ],
                        "suspended_stocks": [],
                        "abnormal_volatility": [],
                        "capital_flight": [],
                        "high_position_risks": [],
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO analysis_results (trade_date, analysis_type, result_json) "
            "VALUES (?, ?, ?)",
            (
                DATE,
                "limit_up_analysis",
                json.dumps(
                    {
                        "consecutive_boards": [],
                        "sector_correlation": [],
                        "seal_strength_ranking": [],
                        "summary": "涨停分析摘要",
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO analysis_results (trade_date, analysis_type, result_json) "
            "VALUES (?, ?, ?)",
            (
                DATE,
                "dragon_tiger",
                json.dumps(
                    {
                        "institutional": [],
                        "brokers": [],
                        "hot_money": [],
                        "summary": "龙虎榜摘要",
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO analysis_results (trade_date, analysis_type, result_json) "
            "VALUES (?, ?, ?)",
            (
                DATE,
                "fund_flow_trend",
                json.dumps(
                    {
                        "trend": {
                            "direction": "净流出",
                            "momentum": "减速",
                            "large_vs_retail_divergence": False,
                            "lookback_rows": 5,
                            "avg_main_net": -8e8,
                        },
                        "summary": "资金流向摘要",
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO analysis_results (trade_date, analysis_type, result_json) "
            "VALUES (?, ?, ?)",
            (
                DATE,
                "risk_alert",
                json.dumps({"summary": "风险提示摘要"}),
            ),
        )
        conn.commit()
        conn.close()
    return db_path


@pytest.fixture()
def integration_client(seeded_db):
    """TestClient wired to the seeded temp DB (auth bypassed)."""
    with (
        patch("stockhot.api.config.settings.DB_PATH", str(seeded_db)),
        patch("stockhot.api.db.DB_PATH", str(seeded_db)),
    ):
        yield TestClient(app)


@pytest.fixture()
def auth_client(seeded_db, _real_auth):
    """TestClient with real auth enforcement and seeded DB."""
    with (
        patch("stockhot.api.config.settings.DB_PATH", str(seeded_db)),
        patch("stockhot.api.db.DB_PATH", str(seeded_db)),
    ):
        yield TestClient(app)


# ===================================================================
# 1. Public endpoint tests
# ===================================================================


class TestPublicEndpoints:
    """Health and dates are public — no auth needed."""

    def test_health_returns_200_with_ok_status(self, integration_client):
        resp = integration_client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "db_path" in body

    def test_health_includes_latest_dates(self, integration_client):
        resp = integration_client.get("/api/health")
        body = resp.json()
        assert body["latest_dates"].get("limit_up_pool") == DATE

    def test_dates_returns_200_with_list(self, integration_client):
        resp = integration_client.get("/api/dates")
        assert resp.status_code == 200
        body = resp.json()
        assert "dates" in body
        assert DATE in body["dates"]


# ===================================================================
# 2. Auth protection tests — no / wrong credentials → 401
# ===================================================================


class TestAuthProtection:
    """Every protected route must reject unauthenticated requests."""

    PROTECTED_GET = [
        "/api/limit-up/2026-05-13",
        "/api/dragon-tiger/2026-05-13",
        "/api/fund-flow/2026-05-13",
        "/api/risk-alert/2026-05-13",
    ]
    PROTECTED_POST = ["/api/trigger/2026-05-13"]
    PROTECTED_STATUS = "/api/trigger/status"

    # --- no credentials at all ---

    @pytest.mark.parametrize("url", PROTECTED_GET)
    def test_get_no_auth_returns_401(self, auth_client, url):
        assert auth_client.get(url).status_code == 401

    @pytest.mark.parametrize("url", PROTECTED_POST)
    def test_post_no_auth_returns_401(self, auth_client, url):
        assert auth_client.post(url).status_code == 401

    def test_trigger_status_no_auth_returns_401(self, auth_client):
        assert auth_client.get(self.PROTECTED_STATUS).status_code == 401

    # --- wrong credentials ---

    @pytest.mark.parametrize("url", PROTECTED_GET)
    def test_get_wrong_auth_returns_401(self, auth_client, url):
        resp = auth_client.get(url, auth=("bad", "bad"))
        assert resp.status_code == 401

    @pytest.mark.parametrize("url", PROTECTED_POST)
    def test_post_wrong_auth_returns_401(self, auth_client, url):
        resp = auth_client.post(url, auth=("bad", "bad"))
        assert resp.status_code == 401

    def test_trigger_status_wrong_auth_returns_401(self, auth_client):
        resp = auth_client.get(self.PROTECTED_STATUS, auth=("bad", "bad"))
        assert resp.status_code == 401

    # --- correct credentials → NOT 401 ---

    @pytest.mark.parametrize("url", PROTECTED_GET)
    def test_get_correct_auth_returns_non401(self, auth_client, url):
        resp = auth_client.get(url, auth=("stockhot", "stockhot"))
        assert resp.status_code != 401

    @pytest.mark.parametrize("url", PROTECTED_POST)
    def test_post_correct_auth_returns_non401(self, auth_client, url):
        mock_proc = AsyncMock()
        mock_proc.pid = 99
        with patch(
            "stockhot.api.routers.trigger.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            resp = auth_client.post(url, auth=("stockhot", "stockhot"))
        assert resp.status_code != 401

    def test_trigger_status_correct_auth_returns_200(self, auth_client):
        resp = auth_client.get(self.PROTECTED_STATUS, auth=("stockhot", "stockhot"))
        assert resp.status_code == 200


# ===================================================================
# 3. Data endpoint tests with auth + seeded data
# ===================================================================


class TestLimitUpWithData:
    def test_returns_ok_with_data(self, auth_client):
        resp = auth_client.get(f"/api/limit-up/{DATE}", auth=("stockhot", "stockhot"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["date"] == DATE
        assert len(body["limit_up_pool"]) == 1
        assert body["limit_up_pool"][0]["code"] == "000001"
        assert body["analysis"]["summary"] == "涨停分析摘要"

    def test_summary_returns_200(self, auth_client):
        resp = auth_client.get(f"/api/limit-up/{DATE}/summary", auth=("stockhot", "stockhot"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["date"] == DATE
        assert body["summary"] == "涨停分析摘要"


class TestDragonTigerWithData:
    def test_returns_ok_with_data(self, auth_client):
        resp = auth_client.get(f"/api/dragon-tiger/{DATE}", auth=("stockhot", "stockhot"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["date"] == DATE
        assert len(body["detail"]) == 1
        assert body["detail"][0]["code"] == "000002"
        assert body["summary"] == "龙虎榜摘要"

    def test_summary_returns_200(self, auth_client):
        resp = auth_client.get(f"/api/dragon-tiger/{DATE}/summary", auth=("stockhot", "stockhot"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["date"] == DATE
        assert body["summary"] == "龙虎榜摘要"


class TestFundFlowWithData:
    def test_returns_ok_with_data(self, auth_client):
        resp = auth_client.get(f"/api/fund-flow/{DATE}", auth=("stockhot", "stockhot"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["date"] == DATE
        assert len(body["market_flow"]) == 1
        assert len(body["sector_flow"]) == 1
        assert body["summary"] == "资金流向摘要"


class TestRiskAlertWithData:
    def test_returns_ok_with_data(self, auth_client):
        resp = auth_client.get(f"/api/risk-alert/{DATE}", auth=("stockhot", "stockhot"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["date"] == DATE
        assert len(body["data"]["st_stocks"]) == 1
        assert body["data"]["summary"] == "风险提示摘要"


class TestNoDataDate:
    """Requesting a date with no data should return status no_data."""

    def test_limit_up_no_data(self, auth_client):
        resp = auth_client.get("/api/limit-up/2099-01-01", auth=("stockhot", "stockhot"))
        assert resp.status_code == 200
        assert resp.json()["status"] == "no_data"

    def test_dragon_tiger_no_data(self, auth_client):
        resp = auth_client.get("/api/dragon-tiger/2099-01-01", auth=("stockhot", "stockhot"))
        assert resp.status_code == 200
        assert resp.json()["status"] == "no_data"

    def test_fund_flow_no_data(self, auth_client):
        resp = auth_client.get("/api/fund-flow/2099-01-01", auth=("stockhot", "stockhot"))
        assert resp.status_code == 200
        assert resp.json()["status"] == "no_data"

    def test_risk_alert_no_data(self, auth_client):
        resp = auth_client.get("/api/risk-alert/2099-01-01", auth=("stockhot", "stockhot"))
        assert resp.status_code == 200
        assert resp.json()["status"] == "no_data"


# ===================================================================
# 4. Trigger endpoint tests (with subprocess mock)
# ===================================================================


class TestTriggerEndpoint:
    def test_trigger_collection_with_auth(self, auth_client):
        mock_proc = AsyncMock()
        mock_proc.pid = 54321
        with patch(
            "stockhot.api.routers.trigger.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            resp = auth_client.post(f"/api/trigger/{DATE}", auth=("stockhot", "stockhot"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "triggered"
        assert body["date"] == DATE
        assert body["pid"] == 54321

    def test_trigger_status_with_auth(self, auth_client):
        resp = auth_client.get("/api/trigger/status", auth=("stockhot", "stockhot"))
        assert resp.status_code == 200
        assert resp.json() == {"status": "available"}
