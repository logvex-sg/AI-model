"""Persistent task and project state.

State is a single JSON document under ``$KALI_AEGIS_HOME/state.json``. It holds
tasks, their subtask breakdown, assigned agent, and status. Writes are atomic
(write-temp-then-rename) so an interrupted run cannot corrupt the file.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List

from .config import Config
from .errors import NotFoundError


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"


@dataclass
class SubTask:
    """One unit of work assigned to an agent."""

    id: str
    description: str
    agent: str
    status: str = TaskStatus.PENDING.value
    result: str = ""
    risk: str = "LOW"

    @staticmethod
    def new(description: str, agent: str, *, risk: str = "LOW") -> "SubTask":
        return SubTask(id=uuid.uuid4().hex[:8], description=description, agent=agent, risk=risk)


@dataclass
class Task:
    """A top-level objective plus its decomposition."""

    id: str
    objective: str
    scope: str = ""
    status: str = TaskStatus.PENDING.value
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    subtasks: List[SubTask] = field(default_factory=list)
    report: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def new(objective: str, scope: str = "") -> "Task":
        return Task(id=uuid.uuid4().hex[:8], objective=objective, scope=scope)

    def touch(self) -> None:
        self.updated_at = time.time()


@dataclass
class Project:
    """A workspace the platform has scaffolded or been pointed at."""

    id: str
    path: str
    kind: str = "unknown"
    created_at: float = field(default_factory=time.time)
    last_build: str = ""
    last_test: str = ""

    @staticmethod
    def new(path: str, kind: str = "unknown") -> "Project":
        return Project(id=uuid.uuid4().hex[:8], path=path, kind=kind)


class StateStore:
    """Loads, mutates, and persists task and project state."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.path = config.state_path
        self._tasks: Dict[str, Task] = {}
        self._projects: Dict[str, Project] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for raw in data.get("tasks", []):
            raw = dict(raw)
            subtasks = [SubTask(**s) for s in raw.pop("subtasks", [])]
            task = Task(**raw, subtasks=subtasks)
            self._tasks[task.id] = task
        for raw in data.get("projects", []):
            project = Project(**raw)
            self._projects[project.id] = project

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "tasks": [asdict(t) for t in self._tasks.values()],
            "projects": [asdict(p) for p in self._projects.values()],
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- task lifecycle ------------------------------------------------------
    def create(self, objective: str, scope: str = "") -> Task:
        task = Task.new(objective, scope)
        self._tasks[task.id] = task
        self.save()
        return task

    def get(self, task_id: str) -> Task:
        if task_id not in self._tasks:
            raise NotFoundError(f"no such task: {task_id}")
        return self._tasks[task_id]

    def all(self) -> List[Task]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    def by_status(self, status: TaskStatus) -> List[Task]:
        return [t for t in self.all() if t.status == status.value]

    def queue(self) -> List[Task]:
        """Tasks not yet finished, oldest first."""
        pending = {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}
        return sorted(
            (t for t in self._tasks.values() if t.status in pending),
            key=lambda t: t.created_at,
        )

    def failed(self) -> List[Task]:
        return self.by_status(TaskStatus.FAILED)

    def interrupted(self) -> List[Task]:
        """Tasks a killswitch or crash left unfinished, safe to resume."""
        return self.by_status(TaskStatus.INTERRUPTED)

    def update_status(self, task_id: str, status: TaskStatus) -> Task:
        task = self.get(task_id)
        task.status = status.value
        task.touch()
        self.save()
        return task

    def add_subtask(self, task_id: str, subtask: SubTask) -> SubTask:
        task = self.get(task_id)
        task.subtasks.append(subtask)
        task.touch()
        self.save()
        return subtask

    def complete_subtask(self, task_id: str, subtask_id: str, result: str, status: str = "COMPLETE") -> SubTask:
        task = self.get(task_id)
        for sub in task.subtasks:
            if sub.id == subtask_id:
                sub.result = result
                sub.status = status
                task.touch()
                self.save()
                return sub
        raise NotFoundError(f"no such subtask: {subtask_id}")

    def set_report(self, task_id: str, report: Dict[str, Any]) -> Task:
        task = self.get(task_id)
        task.report = report
        task.touch()
        self.save()
        return task

    # -- projects ------------------------------------------------------------
    def add_project(self, path: str, kind: str = "unknown") -> Project:
        existing = next((p for p in self._projects.values() if p.path == path), None)
        if existing is not None:
            existing.kind = kind
            self.save()
            return existing
        project = Project.new(path, kind)
        self._projects[project.id] = project
        self.save()
        return project

    def get_project(self, project_id: str) -> Project:
        if project_id not in self._projects:
            raise NotFoundError(f"no such project: {project_id}")
        return self._projects[project_id]

    def projects(self) -> List[Project]:
        return sorted(self._projects.values(), key=lambda p: p.created_at, reverse=True)

    def record_build(self, path: str, summary: str) -> None:
        project = self.add_project(path)
        project.last_build = summary
        self.save()

    def record_test(self, path: str, summary: str) -> None:
        project = self.add_project(path)
        project.last_test = summary
        self.save()
