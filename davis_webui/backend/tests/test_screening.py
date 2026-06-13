from fastapi.testclient import TestClient

from davis_webui.backend.tasks import task_manager


def test_start_screening_returns_task_id(
    client: TestClient,
    clean_task_manager,
    monkeypatch,
    mock_pipeline_result,
):
    import davis_analyzer.pipeline

    monkeypatch.setattr(
        davis_analyzer.pipeline,
        "run_screening_pipeline",
        lambda **kw: mock_pipeline_result,
    )

    response = client.post(
        "/api/screening/start",
        json={"top_n": 3, "dry_run": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert "task_id" in body
    assert len(body["task_id"]) > 0


def test_get_status(
    client: TestClient,
    mock_task,
):
    response = client.get(f"/api/screening/{mock_task}/status")
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == mock_task
    assert body["status"] == "completed"
    assert body["progress"] == 100.0


def test_get_status_not_found(client: TestClient, clean_task_manager):
    response = client.get("/api/screening/nonexistent/status")
    assert response.status_code == 404


def test_get_results_completed(client: TestClient, mock_task):
    response = client.get(f"/api/screening/{mock_task}/results")
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 3
    assert len(body["scores"]) == 3
    assert body["scores"][0]["ts_code"] == "000001.SZ"


def test_get_results_not_found(client: TestClient, clean_task_manager):
    response = client.get("/api/screening/nonexistent/results")
    assert response.status_code == 404


def test_get_results_not_completed(client: TestClient, mock_running_task):
    response = client.get(f"/api/screening/{mock_running_task}/results")
    assert response.status_code == 400
