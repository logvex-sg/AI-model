from __future__ import annotations

import pytest

from jarvis.killswitch import EmergencyStop, KillSwitch
from jarvis.tasks import TaskManager, TaskStatus


def test_killswitch_is_visible_to_another_process_object(tmp_path):
    path = tmp_path / "EMERGENCY_STOP"
    first = KillSwitch(path)
    second = KillSwitch(path)
    first.engage("cli emergency-stop")
    assert second.engaged is True
    assert second.reason() == "cli emergency-stop"
    with pytest.raises(EmergencyStop):
        second.check()
    second.release()
    assert first.engaged is False


def test_killswitch_survives_restart(tmp_path):
    path = tmp_path / "EMERGENCY_STOP"
    KillSwitch(path).engage("stay stopped")
    assert KillSwitch(path).engaged is True


def test_task_lifecycle_and_cancellation():
    manager = TaskManager()
    task = manager.create("inspect system", "normal")
    assert task.status is TaskStatus.QUEUED
    task.status = TaskStatus.RUNNING
    step = task.add_step("call system.metrics")
    step.status = "ok"
    assert manager.active() == [task]
    task.finish(TaskStatus.COMPLETED, result="done")
    assert manager.active() == []
    assert task.snapshot()["steps"][0]["status"] == "ok"


def test_cancel_all_marks_active_tasks_cancelled():
    manager = TaskManager()
    first = manager.create("a", "normal")
    second = manager.create("b", "beast")
    second.finish(TaskStatus.COMPLETED)
    assert manager.cancel_all() == 1
    assert first.status is TaskStatus.CANCELLED
    assert first.cancelled is True
    assert second.status is TaskStatus.COMPLETED
