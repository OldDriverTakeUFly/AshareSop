"""Tests for HTTP Basic Auth middleware."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stockhot.api.auth import verify_credentials
from stockhot.api.main import app


@pytest.fixture()
def raw_client():
    app.dependency_overrides.pop(verify_credentials, None)
    return TestClient(app)


class TestPublicRoutes:
    def test_health_no_auth_required(self, raw_client):
        resp = raw_client.get("/api/health")
        assert resp.status_code == 200

    def test_dates_no_auth_required(self, raw_client):
        resp = raw_client.get("/api/dates")
        assert resp.status_code == 200


class TestProtectedRoutes:
    def test_limit_up_returns_401_without_credentials(self, raw_client):
        resp = raw_client.get("/api/limit-up/2026-05-13")
        assert resp.status_code == 401

    def test_limit_up_returns_401_with_wrong_credentials(self, raw_client):
        resp = raw_client.get("/api/limit-up/2026-05-13", auth=("wrong", "wrong"))
        assert resp.status_code == 401

    def test_limit_up_returns_401_with_wrong_password(self, raw_client):
        resp = raw_client.get("/api/limit-up/2026-05-13", auth=("stockhot", "wrong"))
        assert resp.status_code == 401

    def test_limit_up_returns_non401_with_correct_credentials(self, raw_client):
        resp = raw_client.get(
            "/api/limit-up/2026-05-13", auth=("stockhot", "stockhot")
        )
        assert resp.status_code != 401

    def test_fund_flow_returns_401_without_credentials(self, raw_client):
        resp = raw_client.get("/api/fund-flow/2026-05-13")
        assert resp.status_code == 401

    def test_risk_alert_returns_401_without_credentials(self, raw_client):
        resp = raw_client.get("/api/risk-alert/2026-05-13")
        assert resp.status_code == 401

    def test_trigger_returns_401_without_credentials(self, raw_client):
        resp = raw_client.get("/api/trigger/status")
        assert resp.status_code == 401

    def test_trigger_returns_non401_with_correct_credentials(self, raw_client):
        resp = raw_client.get(
            "/api/trigger/status", auth=("stockhot", "stockhot")
        )
        assert resp.status_code != 401
