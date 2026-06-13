"""Tests for history API endpoints: list, load, delete, results-after-load."""

from __future__ import annotations

from fastapi.testclient import TestClient

import davis_webui.backend.tasks as tasks_module
from davis_webui.backend.tasks import TaskInfo, TaskStatus, task_manager


def _save_task_to_disk(
    task_id: str,
    mock_pipeline_result,
    created_at: str = "2024-06-01T12:00:00",
):
    task_manager.tasks[task_id] = TaskInfo(
        task_id=task_id,
        status=TaskStatus.COMPLETED,
        progress=100.0,
        message="Done",
        result=mock_pipeline_result,
        created_at=created_at,
        top_n=3,
        dry_run=False,
    )
    task_manager._save_task(task_id)


def test_history_empty(
    client: TestClient,
    clean_task_manager,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(tasks_module, "_DATA_DIR", tmp_path)
    response = client.get("/api/history/")
    assert response.status_code == 200
    assert response.json() == {"history": []}


def test_history_list(
    client: TestClient,
    clean_task_manager,
    mock_pipeline_result,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(tasks_module, "_DATA_DIR", tmp_path)
    _save_task_to_disk("hist-api-001", mock_pipeline_result)

    response = client.get("/api/history/")
    assert response.status_code == 200
    body = response.json()
    assert len(body["history"]) >= 1
    entry = body["history"][0]
    assert "task_id" in entry
    assert "created_at" in entry
    assert "top_n" in entry
    assert "total_count" in entry


def test_history_load(
    client: TestClient,
    clean_task_manager,
    mock_pipeline_result,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(tasks_module, "_DATA_DIR", tmp_path)
    task_id = "hist-load-001"
    _save_task_to_disk(task_id, mock_pipeline_result)
    task_manager.tasks.clear()

    response = client.get(f"/api/history/{task_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == task_id
    assert body["loaded"] is True


def test_history_load_not_found(
    client: TestClient,
    clean_task_manager,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(tasks_module, "_DATA_DIR", tmp_path)
    response = client.get("/api/history/nonexistent-id")
    assert response.status_code == 404


def test_history_delete(
    client: TestClient,
    clean_task_manager,
    mock_pipeline_result,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(tasks_module, "_DATA_DIR", tmp_path)
    task_id = "hist-del-001"
    _save_task_to_disk(task_id, mock_pipeline_result)

    response = client.delete(f"/api/history/{task_id}")
    assert response.status_code == 200
    assert response.json()["deleted"] is True

    list_resp = client.get("/api/history/")
    assert list_resp.json() == {"history": []}


def test_loaded_task_results_accessible(
    client: TestClient,
    clean_task_manager,
    mock_pipeline_result,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(tasks_module, "_DATA_DIR", tmp_path)
    task_id = "hist-results-001"
    _save_task_to_disk(task_id, mock_pipeline_result)
    task_manager.tasks.clear()

    load_resp = client.get(f"/api/history/{task_id}")
    assert load_resp.status_code == 200
    assert load_resp.json()["loaded"] is True

    results_resp = client.get(f"/api/screening/{task_id}/results")
    assert results_resp.status_code == 200
