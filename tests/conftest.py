from __future__ import annotations

import pytest

from jarvis.audit import AuditLog
from jarvis.executor import Executor
from jarvis.killswitch import KillSwitch
from jarvis.memory import MemoryStore
from jarvis.monitor import BehaviorMonitor
from jarvis.permissions import Mode, PermissionLevel, PolicyEngine
from jarvis.tools import build_registry
from jarvis.tools.base import Argument, Tool, ToolRegistry


@pytest.fixture()
def state_dir(tmp_path):
    return tmp_path


@pytest.fixture()
def store(tmp_path):
    memory_store = MemoryStore(tmp_path / "memory.db")
    yield memory_store
    memory_store.close()


@pytest.fixture()
def registry(store) -> ToolRegistry:
    return build_registry(store)


@pytest.fixture()
def killswitch(tmp_path) -> KillSwitch:
    return KillSwitch(tmp_path / "EMERGENCY_STOP")


@pytest.fixture()
def audit(tmp_path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


@pytest.fixture()
def executor_factory(registry, audit, killswitch):
    created: list[Executor] = []

    def factory(mode: Mode = Mode.NORMAL, confirm=None, monitor=None) -> Executor:
        executor = Executor(
            registry=registry,
            policy=PolicyEngine(mode),
            audit=audit,
            killswitch=killswitch,
            monitor=monitor or BehaviorMonitor(),
            confirm=confirm,
        )
        created.append(executor)
        return executor

    yield factory
    for executor in created:
        executor.shutdown()


@pytest.fixture()
def echo_tool(registry) -> Tool:
    return registry.register(
        Tool(
            name="test.echo",
            description="Echo a value back.",
            category="TEST",
            permission=PermissionLevel.SAFE,
            handler=lambda value: {"value": value},
            arguments=(Argument("value", str, "Value to echo."),),
        )
    )
