from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
