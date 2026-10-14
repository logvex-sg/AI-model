"""Failure recovery engine.

Implements the recovery loop from operating-model section 17:

    capture -> classify -> diagnose -> strategise -> apply -> retry -> verify
    -> record

The loop is deliberately bounded and refuses to repeat a *changed nothing*
attempt. If a retry is about to run an identical operation with no new
information and no delay, that is not recovery — it is a spin. The engine stops
instead and reports ``RecoveryExhausted``.

The killswitch is honoured between attempts so a stop signal ends the loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .errors import ErrorClass, RecoveryExhausted, classify_returncode
from .killswitch import Killswitch
from .logging import OperationLog


@dataclass
class Attempt:
    """One recorded attempt in a recovery loop."""

    number: int
    ok: bool
    summary: str
    error_class: str
    strategy: str
    delay_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attempt": self.number,
            "ok": self.ok,
            "summary": self.summary,
            "error_class": self.error_class,
            "strategy": self.strategy,
            "delay_s": round(self.delay_s, 3),
        }


@dataclass
class RecoveryReport:
    """Outcome of a recovery loop, suitable for the audit log."""

    operation: str
    recovered: bool
    attempts: List[Attempt] = field(default_factory=list)
    final_error: str = ""

    @property
    def count(self) -> int:
        return len(self.attempts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation,
            "recovered": self.recovered,
            "attempts": [a.to_dict() for a in self.attempts],
            "final_error": self.final_error,
        }


#: How to respond to each failure class.
#: ``retry``     - run the same operation again, possibly after a delay
#: ``replan``    - a prerequisite is missing; the caller must change the plan
#: ``abort``     - no automatic recovery is safe
_STRATEGIES: Dict[ErrorClass, str] = {
    ErrorClass.TRANSIENT: "retry",
    ErrorClass.ENVIRONMENT: "replan",
    ErrorClass.PERMISSION: "abort",
    ErrorClass.INPUT: "abort",
    ErrorClass.LOGIC: "abort",
    ErrorClass.UNKNOWN: "replan",
}


class RecoveryEngine:
    """Bounded retry loop with backoff and an anti-spin guard."""

    def __init__(
        self,
        killswitch: Killswitch,
        log: Optional[OperationLog] = None,
        *,
        max_attempts: int = 1,
        backoff_s: float = 1.0,
        agent: str = "leader",
    ) -> None:
        self.killswitch = killswitch
        self.log = log
        self.max_attempts = max(1, max_attempts)
        self.backoff_s = max(0.0, backoff_s)
        self.agent = agent

    def strategy_for(self, error_class: ErrorClass) -> str:
        return _STRATEGIES.get(error_class, "abort")

    def classify_exception(self, exc: BaseException) -> ErrorClass:
        """Best-effort classification of a raised exception."""
        cls = getattr(exc, "error_class", None)
        if isinstance(cls, ErrorClass):
            return cls
        returncode = getattr(exc, "returncode", None)
        if isinstance(returncode, int) and returncode != 0:
            return classify_returncode(returncode)
        return ErrorClass.UNKNOWN

    def run(
        self,
        operation: str,
        fn: Callable[[], Tuple[bool, str]],
        *,
        max_attempts: Optional[int] = None,
    ) -> RecoveryReport:
        """Drive *fn* until it succeeds, is non-retryable, or the budget ends.

        ``fn`` returns ``(ok, summary)`` and may raise; exceptions are classified
        rather than swallowed.
        """
        budget = max_attempts if max_attempts is not None else self.max_attempts
        budget = max(1, budget)
        report = RecoveryReport(operation=operation, recovered=False)
        #: Guards against burning the budget on an unchanging operation.
        seen_signatures: set = set()

        for attempt_no in range(1, budget + 1):
            self.killswitch.guard("recovery attempt")
            delay = 0.0
            raised: Optional[BaseException] = None
            try:
                ok, summary = fn()
                error_class = ErrorClass.UNKNOWN if ok else ErrorClass.LOGIC
            except Exception as exc:  # noqa: BLE001 - classified, then reported
                raised = exc
                ok, summary, error_class = False, f"{type(exc).__name__}: {exc}", self.classify_exception(exc)

            strategy = self.strategy_for(error_class)
            signature = (summary, strategy)

            if ok:
                report.attempts.append(
                    Attempt(attempt_no, True, summary, error_class.value, "none")
                )
                report.recovered = attempt_no > 1
                self._record(report)
                return report

            # "abort" means no automatic recovery is safe: surface the original
            # exception so its type and exit code survive.
            if strategy == "abort" and raised is not None:
                report.attempts.append(
                    Attempt(attempt_no, False, summary, error_class.value, strategy)
                )
                report.final_error = summary
                self._record(report)
                raise raised

            # Re-running an operation that failed identically, with nothing
            # changed, is a spin rather than a recovery. Stop instead.
            if signature in seen_signatures:
                report.final_error = (
                    f"{summary} (identical failure repeated with nothing changed; "
                    "stopping rather than spinning)"
                )
                report.attempts.append(
                    Attempt(attempt_no, False, summary, error_class.value, "spin-guard")
                )
                break

            seen_signatures.add(signature)
            report.attempts.append(
                Attempt(attempt_no, False, summary, error_class.value, strategy, delay)
            )
            report.final_error = summary

            if strategy != "retry":
                break

            if attempt_no < budget:
                delay = self.backoff_s * (2 ** (attempt_no - 1))
                report.attempts[-1].delay_s = delay
                # Sleep in short slices so a killswitch engages promptly.
                deadline = time.time() + delay
                while time.time() < deadline:
                    self.killswitch.guard("recovery backoff")
                    time.sleep(min(0.2, max(0.0, deadline - time.time())))

        self._record(report)
        last = report.attempts[-1] if report.attempts else None
        exhausted = (
            last is not None
            and not last.ok
            and last.strategy not in {"abort", "replan", "spin-guard"}
        )
        if exhausted:
            raise RecoveryExhausted(
                f"{operation} failed after {report.count} attempt(s): {report.final_error}"
            )
        return report

    def _record(self, report: RecoveryReport) -> None:
        if self.log is None:
            return
        self.log.record(
            self.agent,
            "recovery",
            target=report.operation,
            status="recovered" if report.recovered else "unrecovered",
            result=report.final_error or "ok",
            extra={"recovery": report.to_dict()},
        )
