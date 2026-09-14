"""Task engine: bounded, observable, cancellable units of work."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ACTIVE_STATUSES = frozenset(
    {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_FOR_CONFIRMATION}
)


@dataclass
class TaskStep:
    description: str
    status: str = "pending"
    detail: Any = None


@dataclass
class Task:
    description: str
    mode: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: TaskStatus = TaskStatus.QUEUED
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    steps: list[TaskStep] = field(default_factory=list)
    result: Any = None
    error: str | None = None
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def cancel(self) -> None:
        self._cancel.set()
        if self.status in _ACTIVE_STATUSES:
            self.status = TaskStatus.CANCELLED
            self.finished_at = time.time()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def add_step(self, description: str, status: str = "pending", detail: Any = None) -> TaskStep:
        step = TaskStep(description=description, status=status, detail=detail)
        self.steps.append(step)
        return step

    def finish(self, status: TaskStatus, result: Any = None, error: str | None = None) -> None:
        self.status = status
        self.result = result
        self.error = error
        self.finished_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "mode": self.mode,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "steps": [
                {"description": step.description, "status": step.status, "detail": step.detail}
                for step in self.steps
            ],
            "error": self.error,
        }


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def create(self, description: str, mode: str) -> Task:
        task = Task(description=description, mode=mode)
        with self._lock:
            self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def all(self) -> list[Task]:
        return sorted(self._tasks.values(), key=lambda task: task.started_at, reverse=True)

    def active(self) -> list[Task]:
        return [
            task
            for task in self._tasks.values()
            if task.status in _ACTIVE_STATUSES
        ]

    def cancel_all(self) -> int:
        tasks = self.active()
        for task in tasks:
            task.cancel()
        return len(tasks)
