import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from stockhot.api.main import app


@pytest.fixture()
def client():
    return TestClient(app)


def test_trigger_collection_returns_triggered(client):
    mock_proc = AsyncMock()
    mock_proc.pid = 12345

    with patch(
        "stockhot.api.routers.trigger.asyncio.create_subprocess_exec",
        return_value=mock_proc,
    ):
        resp = client.post("/api/trigger/2026-05-13")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "triggered"
    assert data["date"] == "2026-05-13"
    assert data["pid"] == 12345


def test_trigger_status_returns_available(client):
    resp = client.get("/api/trigger/status")
    assert resp.status_code == 200
    assert resp.json() == {"status": "available"}
