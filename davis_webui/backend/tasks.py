from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


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

        except Exception as exc:
            stop_event.set()

            with self._lock:
                info = self.tasks.get(task_id)
                if info is not None:
                    info.status = TaskStatus.FAILED
                    info.message = "Screening failed"
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


task_manager = TaskManager()
