from fastapi.testclient import TestClient

from davis_webui.backend.routers import checklists


def test_generate_checklists(client: TestClient, mock_task):
    response = client.post(
        "/api/checklists/generate",
        json={"task_id": mock_task, "top_n": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["checklists"]) == 2
    assert body["checklists"][0]["ts_code"] == "000001.SZ"
    assert body["checklists"][0]["rank"] == 1
    assert len(body["checklists"][0]["sections"]) == 5


def test_generate_checklists_task_not_found(
    client: TestClient, clean_task_manager
):
    response = client.post(
        "/api/checklists/generate",
        json={"task_id": "nonexistent", "top_n": 5},
    )
    assert response.status_code == 404


def test_fill_checklist(client: TestClient):
    checklists._filled_checklists.clear()
    response = client.post(
        "/api/checklists/000001.SZ/fill",
        json={
            "prosperity_adjustment": 5.0,
            "distress_adjustment": -3.0,
        },
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    checklists._filled_checklists.clear()


def test_fill_clamps_values(client: TestClient):
    checklists._filled_checklists.clear()
    response = client.post(
        "/api/checklists/000001.SZ/fill",
        json={
            "prosperity_adjustment": 25.0,
            "distress_adjustment": -30.0,
        },
    )
    assert response.status_code == 200
    stored = checklists._filled_checklists["000001.SZ"]
    assert stored.prosperity_adjustment == 20.0
    assert stored.distress_adjustment == -20.0
    checklists._filled_checklists.clear()


def test_rescore(client: TestClient, mock_task):
    checklists._filled_checklists.clear()
    client.post(
        "/api/checklists/000001.SZ/fill",
        json={
            "prosperity_adjustment": 5.0,
            "distress_adjustment": 10.0,
        },
    )
    response = client.post(
        "/api/checklists/rescore",
        json={"task_id": mock_task},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert result["ts_code"] == "000001.SZ"
    assert result["adjusted_prosperity"] == 77.0
    assert result["adjusted_distress"] == 68.0
    checklists._filled_checklists.clear()


def test_rescore_no_filled_checklists(client: TestClient, mock_task):
    checklists._filled_checklists.clear()
    response = client.post(
        "/api/checklists/rescore",
        json={"task_id": mock_task},
    )
    assert response.status_code == 200
    assert len(response.json()["results"]) == 0
