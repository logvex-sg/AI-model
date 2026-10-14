"""Centralized tool executor.

Every tool call in JARVIS goes through :class:`Executor`. It is the single
place where the killswitch, the behaviour monitor, the policy engine, argument
validation, timeouts and auditing are applied. Nothing else may call a tool
handler directly.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any

from jarvis.audit import AuditEvent, AuditLog
from jarvis.killswitch import EmergencyStop, KillSwitch
from jarvis.monitor import BehaviorMonitor
from jarvis.permissions import Decision, PolicyEngine
from jarvis.tools.base import ToolError, ToolRegistry

ConfirmCallback = Callable[[str, dict[str, Any], str], bool]


@dataclass
class ToolResult:
    tool: str
    status: str  # ok | rejected | error | needs_confirmation | stopped
    result: Any = None
    error: str | None = None
    duration: float = 0.0
    task_id: str = ""
    reason: str | None = None
    audit_record: dict[str, Any] | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class Executor:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyEngine,
        audit: AuditLog,
        killswitch: KillSwitch,
        monitor: BehaviorMonitor | None = None,
        confirm: ConfirmCallback | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.audit = audit
        self.killswitch = killswitch
        self.monitor = monitor or BehaviorMonitor()
        self.confirm = confirm
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="jarvis-tool")

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def call(
        self, name: str, arguments: dict[str, Any] | None = None, *, task_id: str | None = None
    ) -> ToolResult:
        arguments = dict(arguments or {})
        task_id = task_id or uuid.uuid4().hex[:12]
        mode = self.policy.mode.value

        resolved: dict[str, Any] = {"permission": "unknown"}

        def finish(status: str, confirmation: str | None = None, **kwargs: Any) -> ToolResult:
            record = self.audit.write(
                AuditEvent(
                    task_id=task_id,
                    mode=mode,
                    tool=name,
                    permission=resolved["permission"],
                    arguments=arguments,
                    status=status,
                    result=kwargs.get("result") or kwargs.get("error") or kwargs.get("reason"),
                    confirmation=confirmation,
                )
            )
            return ToolResult(
                tool=name, status=status, task_id=task_id, audit_record=record, **kwargs
            )

        if self.killswitch.engaged:
            self.monitor.record_stopped()
            return finish("stopped", reason=f"emergency stop engaged: {self.killswitch.reason()}")

        if self.monitor.suspended:
            return finish("rejected", reason=f"autonomy suspended: {self.monitor.suspend_reason}")

        try:
            tool = self.registry.get(name)
        except ToolError as exc:
            self.monitor.record_failure(name)
            return finish("error", error=str(exc))

        resolved["permission"] = tool.permission.value
        decision = self.policy.evaluate(tool.permission)
        if decision.decision is Decision.REJECT:
            self.monitor.record_permission_denied(name)
            return finish("rejected", reason=decision.reason)

        confirmation = None
        if decision.decision is Decision.CONFIRM:
            if self.confirm is None:
                return finish("needs_confirmation", reason=decision.reason)
            if not self.confirm(name, arguments, decision.reason):
                self.monitor.record_permission_denied(name)
                return finish(
                    "rejected", reason="user declined confirmation", confirmation="denied"
                )
            confirmation = "granted"

        try:
            validated = tool.validate(arguments)
        except ToolError as exc:
            self.monitor.record_failure(name)
            return finish("error", error=str(exc), confirmation=confirmation)

        started = time.monotonic()
        future = self._pool.submit(tool.handler, **validated)
        try:
            result = future.result(timeout=tool.timeout)
        except FutureTimeout:
            future.cancel()
            self.monitor.record_failure(name)
            return finish(
                "error",
                error=f"tool '{name}' timed out after {tool.timeout}s",
                duration=time.monotonic() - started,
                confirmation=confirmation,
            )
        except EmergencyStop as exc:
            return finish("stopped", reason=str(exc), duration=time.monotonic() - started)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user and audited
            self.monitor.record_failure(name)
            return finish(
                "error",
                error=f"{type(exc).__name__}: {exc}",
                duration=time.monotonic() - started,
                confirmation=confirmation,
            )

        self.monitor.record_success(name)
        return finish(
            "ok",
            result=result,
            duration=time.monotonic() - started,
            confirmation=confirmation,
        )
