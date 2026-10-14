from __future__ import annotations

import json
import time

from jarvis.monitor import BehaviorMonitor, Thresholds
from jarvis.permissions import Mode, PermissionLevel
from jarvis.tools.base import Argument, Tool


def test_safe_tool_runs_and_is_audited(executor_factory, echo_tool, audit):
    executor = executor_factory()
    result = executor.call("test.echo", {"value": "hi"})
    assert result.ok
    assert result.result == {"value": "hi"}
    record = json.loads(audit.path.read_text().splitlines()[-1])
    assert record["tool"] == "test.echo"
    assert record["status"] == "ok"
    assert record["permission"] == "safe"


def test_unknown_tool_is_an_error(executor_factory):
    result = executor_factory().call("does.not.exist")
    assert result.status == "error"
    assert "unknown tool" in result.error


def test_invalid_arguments_are_rejected_before_execution(executor_factory, echo_tool):
    result = executor_factory().call("test.echo", {"wrong": 1})
    assert result.status == "error"
    assert "unknown argument" in result.error


def test_destructive_tool_rejected_in_normal_mode(executor_factory, tmp_path):
    target = tmp_path / "victim.txt"
    target.write_text("x")
    result = executor_factory().call(
        "files.delete", {"path": str(target), "confine_to_home": False}
    )
    assert result.status == "rejected"
    assert target.exists()


def test_confirmation_required_and_honoured(executor_factory, registry, tmp_path):
    calls = []

    def deny(name, arguments, reason):
        calls.append(name)
        return False

    executor = executor_factory(Mode.NORMAL, confirm=deny)
    target = tmp_path / "new.txt"
    result = executor.call(
        "files.write",
        {"path": str(target), "content": "data", "confine_to_home": False},
    )
    assert result.status == "rejected"
    assert calls == ["files.write"]
    assert not target.exists()

    executor = executor_factory(Mode.NORMAL, confirm=lambda *_: True)
    result = executor.call(
        "files.write",
        {"path": str(target), "content": "data", "confine_to_home": False},
    )
    assert result.ok
    assert target.read_text() == "data"


def test_missing_confirm_callback_reports_needs_confirmation(executor_factory, tmp_path):
    result = executor_factory().call(
        "files.write", {"path": str(tmp_path / "a"), "content": "b", "confine_to_home": False}
    )
    assert result.status == "needs_confirmation"


def test_killswitch_blocks_execution(executor_factory, echo_tool, killswitch):
    executor = executor_factory()
    killswitch.engage("test stop")
    result = executor.call("test.echo", {"value": "hi"})
    assert result.status == "stopped"
    killswitch.release()
    executor.monitor.resume()
    assert executor.call("test.echo", {"value": "hi"}).ok


def test_tool_timeout_is_enforced(executor_factory, registry):
    registry.register(
        Tool(
            name="test.slow",
            description="Sleep forever.",
            category="TEST",
            permission=PermissionLevel.SAFE,
            handler=lambda: time.sleep(5),
            timeout=0.2,
        )
    )
    result = executor_factory().call("test.slow")
    assert result.status == "error"
    assert "timed out" in result.error


def test_handler_exception_is_captured(executor_factory, registry):
    def explode():
        raise ValueError("boom")

    registry.register(
        Tool("test.boom", "Fail.", "TEST", PermissionLevel.SAFE, explode)
    )
    result = executor_factory().call("test.boom")
    assert result.status == "error"
    assert "ValueError: boom" in result.error


def test_monitor_suspends_after_repeated_failures(executor_factory, registry):
    monitor = BehaviorMonitor(Thresholds(max_consecutive_failures=3))
    executor = executor_factory(monitor=monitor)
    for _ in range(3):
        executor.call("nope")
    assert monitor.suspended
    result = executor.call("nope")
    assert result.status == "rejected"
    assert "autonomy suspended" in result.reason


def test_secrets_are_redacted_in_audit(executor_factory, registry, audit):
    registry.register(
        Tool(
            "test.login",
            "Pretend login.",
            "TEST",
            PermissionLevel.SAFE,
            lambda password: {"ok": True},
            (Argument("password", str, "Secret."),),
        )
    )
    executor_factory().call("test.login", {"password": "hunter2"})
    line = audit.path.read_text().splitlines()[-1]
    assert "hunter2" not in line
    assert "[REDACTED]" in line
