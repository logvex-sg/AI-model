"""Behaviour monitor: watches JARVIS itself and suspends autonomy.

The monitor lives outside the model loop. The model has no tool that can reset
or disable it; only the user can, via ``jarvis resume`` or the GUI.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class Thresholds:
    max_consecutive_failures: int = 5
    max_calls_per_window: int = 40
    window_seconds: float = 60.0
    max_permission_denials: int = 5
    max_identical_calls: int = 8


@dataclass
class BehaviorMonitor:
    thresholds: Thresholds = field(default_factory=Thresholds)
    suspended: bool = False
    suspend_reason: str | None = None
    consecutive_failures: int = 0
    permission_denials: int = 0
    total_calls: int = 0
    _calls: deque = field(default_factory=deque, repr=False)
    _recent_names: deque = field(default_factory=lambda: deque(maxlen=16), repr=False)

    def _tick(self, name: str) -> None:
        now = time.monotonic()
        self.total_calls += 1
        self._calls.append(now)
        self._recent_names.append(name)
        while self._calls and now - self._calls[0] > self.thresholds.window_seconds:
            self._calls.popleft()
        if len(self._calls) > self.thresholds.max_calls_per_window:
            self.suspend(
                f"excessive tool calls: {len(self._calls)} in "
                f"{self.thresholds.window_seconds:.0f}s"
            )
        identical = list(self._recent_names)[-self.thresholds.max_identical_calls :]
        if (
            len(identical) == self.thresholds.max_identical_calls
            and len(set(identical)) == 1
        ):
            self.suspend(f"possible loop: '{name}' called {len(identical)} times in a row")

    def record_success(self, name: str) -> None:
        self._tick(name)
        self.consecutive_failures = 0

    def record_failure(self, name: str) -> None:
        self._tick(name)
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.thresholds.max_consecutive_failures:
            self.suspend(f"{self.consecutive_failures} consecutive tool failures")

    def record_permission_denied(self, name: str) -> None:
        self._tick(name)
        self.permission_denials += 1
        if self.permission_denials >= self.thresholds.max_permission_denials:
            self.suspend(f"{self.permission_denials} permission denials")

    def record_stopped(self) -> None:
        self.suspend("emergency stop engaged")

    def suspend(self, reason: str) -> None:
        self.suspended = True
        self.suspend_reason = reason

    def resume(self) -> None:
        """Manual resume. Only the user performs this."""
        self.suspended = False
        self.suspend_reason = None
        self.consecutive_failures = 0
        self.permission_denials = 0
        self._calls.clear()
        self._recent_names.clear()

    def snapshot(self) -> dict[str, object]:
        return {
            "suspended": self.suspended,
            "suspend_reason": self.suspend_reason,
            "total_calls": self.total_calls,
            "consecutive_failures": self.consecutive_failures,
            "permission_denials": self.permission_denials,
            "calls_in_window": len(self._calls),
        }
