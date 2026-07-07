"""Tests for persistence layer: serialize/deserialize, NaN sanitization, disk I/O."""

from __future__ import annotations

import json

import davis_webui.backend.tasks as tasks_module
from davis_analyzer.types import PipelineResult
from davis_webui.backend.persistence import deserialize_result, serialize_result
from davis_webui.backend.tasks import TaskInfo, TaskStatus, task_manager


def _make_task_info(
    task_id: str,
    result: PipelineResult,
    created_at: str = "2024-06-01T12:00:00",
) -> TaskInfo:
    return TaskInfo(
        task_id=task_id,
        status=TaskStatus.COMPLETED,
        progress=100.0,
        message="Done",
        result=result,
        created_at=created_at,
        top_n=3,
        dry_run=False,
    )


def test_serialize_deserialize_roundtrip(mock_pipeline_result):
    task_info = _make_task_info("test-rt", mock_pipeline_result)
    data = serialize_result("test-rt", task_info, mock_pipeline_result)
    json_str = json.dumps(data, allow_nan=False)

    deserialized = deserialize_result(json.loads(json_str))
    assert deserialized.scores[0].ts_code == mock_pipeline_result.scores[0].ts_code
    assert isinstance(deserialized.valuation_data["000001.SZ"], tuple)
    assert isinstance(deserialized.distress_signals["000001.SZ"].signals_detail, dict)


def test_factor_signals_roundtrip(mock_pipeline_result):
    """Supplementary factor dicts survive serialize → JSON → deserialize."""
    task_info = _make_task_info("test-factors", mock_pipeline_result)
    data = serialize_result("test-factors", task_info, mock_pipeline_result)
    json_str = json.dumps(data, allow_nan=False)
    deserialized = deserialize_result(json.loads(json_str))

    code = mock_pipeline_result.scores[0].ts_code
    assert code in deserialized.momentum_signals
    assert code in deserialized.dividend_signals
    assert code in deserialized.forecast_signals
    # Values preserved through the round-trip.
    assert deserialized.momentum_signals[code].momentum_score == 67.5
    assert deserialized.dividend_signals[code].latest_yield_pct == 4.2
    assert deserialized.forecast_signals[code].leading_score == 85.0
    # window_returns dict (non-trivial value) survives.
    assert deserialized.momentum_signals[code].window_returns == {60: 12.0}


def test_old_task_file_without_factor_keys_deserializes(mock_pipeline_result):
    """A persisted task file from before the factor fields existed (no
    momentum_signals/dividend_signals/forecast_signals keys) must deserialize
    cleanly into empty dicts — backward compatibility."""
    task_info = _make_task_info("test-old", mock_pipeline_result)
    data = serialize_result("test-old", task_info, mock_pipeline_result)
    # Simulate an old file by stripping the factor keys.
    for key in ("momentum_signals", "dividend_signals", "forecast_signals"):
        data["result"].pop(key, None)

    deserialized = deserialize_result(data)
    assert deserialized.momentum_signals == {}
    assert deserialized.dividend_signals == {}
    assert deserialized.forecast_signals == {}
    # Core data still intact.
    assert len(deserialized.scores) == len(mock_pipeline_result.scores)


def test_nan_sanitized(mock_pipeline_result):
    mock_pipeline_result.scores[0].valuation_score = float("nan")
    task_info = _make_task_info("test-nan", mock_pipeline_result)
    data = serialize_result("test-nan", task_info, mock_pipeline_result)
    json_str = json.dumps(data, allow_nan=False)
    assert "NaN" not in json_str


def test_tuple_preserved(mock_pipeline_result):
    task_info = _make_task_info("test-tuple", mock_pipeline_result)
    data = serialize_result("test-tuple", task_info, mock_pipeline_result)
    deserialized = deserialize_result(data)
    for key, val in deserialized.valuation_data.items():
        assert isinstance(val, tuple), f"{key} is {type(val)}, expected tuple"


def test_save_and_load(mock_pipeline_result, clean_task_manager, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_module, "_DATA_DIR", tmp_path)
    task_id = "save-load-001"
    task_manager.tasks[task_id] = _make_task_info(
        task_id, mock_pipeline_result
    )
    task_manager._save_task(task_id)
    assert (tmp_path / f"{task_id}.json").exists()

    task_manager.tasks.clear()
    loaded = task_manager.load_task_from_disk(task_id)
    assert loaded is True
    assert task_id in task_manager.tasks
    info = task_manager.tasks[task_id]
    assert info.status == TaskStatus.COMPLETED
    assert info.progress == 100.0
    assert info.result is not None
    assert len(info.result.scores) > 0


def test_list_history_sorted(mock_pipeline_result, clean_task_manager, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_module, "_DATA_DIR", tmp_path)
    timestamps = [
        "2024-01-01T00:00:00",
        "2024-02-01T00:00:00",
        "2024-03-01T00:00:00",
    ]
    for i, ts in enumerate(timestamps):
        tid = f"hist-{i:03d}"
        task_manager.tasks[tid] = _make_task_info(tid, mock_pipeline_result, created_at=ts)
        task_manager._save_task(tid)

    history = task_manager.list_history()
    assert len(history) == 3
    assert history[0]["created_at"] == "2024-03-01T00:00:00"
    assert history[-1]["created_at"] == "2024-01-01T00:00:00"
    for entry in history:
        assert "task_id" in entry
        assert "created_at" in entry
        assert "top_n" in entry
        assert "total_count" in entry


def test_corrupt_file_skipped(clean_task_manager, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_module, "_DATA_DIR", tmp_path)
    (tmp_path / "corrupt.json").write_text("{ this is not valid json", encoding="utf-8")

    history = task_manager.list_history()
    assert all(e["task_id"] != "corrupt" for e in history)

    result = task_manager.load_task_from_disk("corrupt")
    assert result is False


def test_retention_cap(mock_pipeline_result, clean_task_manager, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_module, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(tasks_module, "_MAX_HISTORY", 3)

    timestamps = [
        "2024-01-01T00:00:00",
        "2024-02-01T00:00:00",
        "2024-03-01T00:00:00",
        "2024-04-01T00:00:00",
    ]
    for i, ts in enumerate(timestamps):
        tid = f"ret-{i:03d}"
        task_manager.tasks[tid] = _make_task_info(tid, mock_pipeline_result, created_at=ts)
        task_manager._save_task(tid)

    history = task_manager.list_history()
    assert len(history) == 3
    oldest_file = tmp_path / "ret-000.json"
    assert not oldest_file.exists()
