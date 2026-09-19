from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from davis_webui.backend.persistence import (
    deserialize_result,
    serialize_result,
    serialize_prosperity_result,
)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskInfo:
    task_id: str
    status: TaskStatus
    progress: float
    message: str
    result: Any = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    top_n: int = 0
    dry_run: bool = False


_DATA_DIR = Path(__file__).parent.parent / "data" / "tasks"
_MAX_HISTORY = 50


class TaskManager:
    def __init__(self) -> None:
        self.tasks: dict[str, TaskInfo] = {}
        self._lock = threading.Lock()

    def start_screening(self, top_n: int = 30, dry_run: bool = False) -> str:
        with self._lock:
            for info in self.tasks.values():
                if info.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
                    raise RuntimeError("A screening task is already running")

            task_id = uuid.uuid4().hex[:12]
            self.tasks[task_id] = TaskInfo(
                task_id=task_id,
                status=TaskStatus.PENDING,
                progress=0.0,
                message="Queued for screening",
                top_n=top_n,
                dry_run=dry_run,
            )

        stop_event = threading.Event()

        thread = threading.Thread(
            target=self._run_pipeline,
            args=(task_id, top_n, dry_run, stop_event),
            daemon=True,
        )
        thread.start()

        hb_thread = threading.Thread(
            target=self._heartbeat,
            args=(task_id, stop_event),
            daemon=True,
        )
        hb_thread.start()

        return task_id

    def start_prosperity_sector(self, top_n_per_industry: int = 10) -> str:
        with self._lock:
            for info in self.tasks.values():
                if info.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
                    raise RuntimeError("A screening task is already running")

            task_id = uuid.uuid4().hex[:12]
            self.tasks[task_id] = TaskInfo(
                task_id=task_id,
                status=TaskStatus.PENDING,
                progress=0.0,
                message="Queued for prosperity sector analysis",
                top_n=top_n_per_industry,
            )

        stop_event = threading.Event()

        thread = threading.Thread(
            target=self._run_prosperity_pipeline,
            args=(task_id, top_n_per_industry, stop_event),
            daemon=True,
        )
        thread.start()

        hb_thread = threading.Thread(
            target=self._heartbeat,
            args=(task_id, stop_event),
            daemon=True,
        )
        hb_thread.start()

        return task_id

    def _run_pipeline(
        self,
        task_id: str,
        top_n: int,
        dry_run: bool,
        stop_event: threading.Event,
    ) -> None:
        with self._lock:
            info = self.tasks.get(task_id)
            if info is None:
                return
            info.status = TaskStatus.RUNNING
            info.message = "Screening pipeline running"

        try:
            from davis_analyzer.pipeline import run_screening_pipeline

            result = run_screening_pipeline(dry_run=dry_run, top_n=top_n)

            stop_event.set()

            with self._lock:
                info = self.tasks.get(task_id)
                if info is not None:
                    info.status = TaskStatus.COMPLETED
                    info.progress = 100.0
                    info.message = "Screening completed"
                    info.result = result

            self._save_task(task_id)

        except Exception as exc:
            stop_event.set()

            with self._lock:
                info = self.tasks.get(task_id)
                if info is not None:
                    info.status = TaskStatus.FAILED
                    info.message = "Screening failed"
                    info.error = str(exc)

    def _run_prosperity_pipeline(
        self,
        task_id: str,
        top_n_per_industry: int,
        stop_event: threading.Event,
    ) -> None:
        with self._lock:
            info = self.tasks.get(task_id)
            if info is None:
                return
            info.status = TaskStatus.RUNNING
            info.message = "Prosperity sector pipeline running"

        try:
            from davis_analyzer.sector_pipeline import run_prosperity_sector_pipeline

            result = run_prosperity_sector_pipeline(top_n_per_industry=top_n_per_industry)

            stop_event.set()

            with self._lock:
                info = self.tasks.get(task_id)
                if info is not None:
                    info.status = TaskStatus.COMPLETED
                    info.progress = 100.0
                    info.message = "Prosperity sector completed"
                    info.result = result

            self._save_task(task_id)

        except Exception as exc:
            stop_event.set()

            with self._lock:
                info = self.tasks.get(task_id)
                if info is not None:
                    info.status = TaskStatus.FAILED
                    info.message = "Prosperity sector failed"
                    info.error = str(exc)

    def _heartbeat(self, task_id: str, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            threading.Event().wait(timeout=3.0)
            if stop_event.is_set():
                break

            with self._lock:
                info = self.tasks.get(task_id)
                if info is None or info.status != TaskStatus.RUNNING:
                    break
                if info.progress < 90.0:
                    info.progress = min(90.0, info.progress + 2.0)

    def get_task(self, task_id: str) -> TaskInfo | None:
        with self._lock:
            return self.tasks.get(task_id)

    def get_task_result(self, task_id: str) -> Any:
        with self._lock:
            info = self.tasks.get(task_id)
            if info is not None and info.status == TaskStatus.COMPLETED:
                return info.result
            return None

    def _save_task(self, task_id: str) -> None:
        with self._lock:
            info = self.tasks.get(task_id)
            if info is None or info.result is None:
                return
            if hasattr(info.result, "industry_scores"):
                data = serialize_prosperity_result(task_id, info, info.result)
            else:
                data = serialize_result(task_id, info, info.result)

        _DATA_DIR.mkdir(parents=True, exist_ok=True)

        task_file = _DATA_DIR / f"{task_id}.json"
        tmp_file = _DATA_DIR / f"{task_id}.json.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.rename(tmp_file, task_file)

        self._update_index(task_id, info)

    def _update_index(self, task_id: str, info: TaskInfo) -> None:
        index_file = _DATA_DIR / "_index.json"
        entries: list[dict] = []

        if index_file.exists():
            try:
                entries = json.loads(index_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                entries = []

        entries = [e for e in entries if e.get("task_id") != task_id]

        total_count = 0
        if info.result is not None:
            if hasattr(info.result, "industry_scores"):
                total_count = len(info.result.industry_scores)
            elif hasattr(info.result, "scores"):
                total_count = len(info.result.scores)
            else:
                total_count = 0

        entries.append(
            {
                "task_id": task_id,
                "created_at": info.created_at,
                "top_n": info.top_n,
                "total_count": total_count,
            }
        )

        entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)

        if len(entries) > _MAX_HISTORY:
            for old_entry in entries[_MAX_HISTORY:]:
                old_file = _DATA_DIR / f"{old_entry['task_id']}.json"
                old_file.unlink(missing_ok=True)
            entries = entries[:_MAX_HISTORY]

        tmp_index = _DATA_DIR / "_index.json.tmp"
        with open(tmp_index, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False)
        os.rename(tmp_index, index_file)

    def load_task_from_disk(self, task_id: str) -> bool:
        task_file = _DATA_DIR / f"{task_id}.json"
        if not task_file.exists():
            return False

        try:
            data = json.loads(task_file.read_text(encoding="utf-8"))
            result = deserialize_result(data)
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            return False

        with self._lock:
            self.tasks[task_id] = TaskInfo(
                task_id=task_id,
                status=TaskStatus.COMPLETED,
                progress=100.0,
                message="Screening completed",
                result=result,
                created_at=data.get("created_at", datetime.now().isoformat()),
                top_n=data.get("top_n", 0),
                dry_run=data.get("dry_run", False),
            )

        return True

    def list_history(self) -> list[dict]:
        index_file = _DATA_DIR / "_index.json"
        if not index_file.exists():
            return []

        try:
            entries = json.loads(index_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

        entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        return [
            {
                "task_id": e.get("task_id", ""),
                "created_at": e.get("created_at", ""),
                "top_n": e.get("top_n", 0),
                "total_count": e.get("total_count", 0),
            }
            for e in entries
        ]

    def remove_stock(self, task_id: str, ts_code: str) -> bool:
        with self._lock:
            info = self.tasks.get(task_id)
            if info is None or info.result is None:
                return False
            result = info.result
            result.scores = [s for s in result.scores if s.ts_code != ts_code]
            result.stock_infos.pop(ts_code, None)
            result.valuation_data.pop(ts_code, None)
            result.prosperity_scores.pop(ts_code, None)
            result.distress_signals.pop(ts_code, None)
            result.financial_data.pop(ts_code, None)
            result.trend_scores.pop(ts_code, None)
            info.message = f"Removed {ts_code}"

        self._save_task(task_id)
        return True

    def delete_task(self, task_id: str) -> bool:
        task_file = _DATA_DIR / f"{task_id}.json"
        deleted = task_file.unlink(missing_ok=True)

        index_file = _DATA_DIR / "_index.json"
        if index_file.exists():
            try:
                entries = json.loads(index_file.read_text(encoding="utf-8"))
                new_entries = [e for e in entries if e.get("task_id") != task_id]
                if len(new_entries) != len(entries):
                    tmp_index = _DATA_DIR / "_index.json.tmp"
                    with open(tmp_index, "w", encoding="utf-8") as f:
                        json.dump(new_entries, f, ensure_ascii=False)
                    os.rename(tmp_index, index_file)
            except (json.JSONDecodeError, OSError):
                pass

        with self._lock:
            self.tasks.pop(task_id, None)

        return deleted or True


task_manager = TaskManager()
