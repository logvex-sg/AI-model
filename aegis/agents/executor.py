"""EXECUTOR — execution and automation worker.

Runs approved shell commands, recovers from transient failures through the
shared :class:`~aegis.recovery.RecoveryEngine`, and stops the moment the
killswitch engages. It performs no interpretation of its own: the risk policy
and scope checks live in the shell and pentester layers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ..errors import AegisHaltedError, CommandError, RecoveryExhausted, RiskDenied
from ..recovery import RecoveryEngine
from .base import Agent, AgentResult


class ExecutorAgent(Agent):
    """Executes commands and scripts on behalf of the team."""

    name = "executor"
    role = "execution and automation worker"
    capabilities = ("execute", "execute_many", "run_script")
    limitations = (
        "does not decide what to run; it runs what it is given",
        "HIGH risk commands are refused unless confirmed",
    )
    requires_llm = False

    def execute(
        self,
        command: str,
        *,
        confirmed: bool = False,
        retries: Optional[int] = None,
        timeout: Optional[int] = None,
        cwd: Optional[str] = None,
    ) -> AgentResult:
        """Run one command, recovering from transient failures.

        Retries are driven by the recovery engine, which classifies the failure
        before deciding whether another attempt is even justified.
        """

        def _run() -> tuple:
            budget = self.config.max_retries if retries is None else retries
            engine = RecoveryEngine(
                self.killswitch,
                self.log,
                max_attempts=budget,
                backoff_s=self.config.retry_backoff_s,
                agent=self.name,
            )
            captured: Dict[str, Any] = {}

            def attempt() -> tuple:
                self.killswitch.guard("execute")
                result = self.shell.run(
                    command, confirmed=confirmed, timeout=timeout, cwd=cwd
                )
                captured["last"] = result.to_dict()
                if result.ok:
                    return True, f"executed: {command}"
                return False, f"command failed ({result.returncode}): {result.stderr or result.stdout}"

            try:
                report = engine.run(f"execute:{command}", attempt, max_attempts=budget)
            except RecoveryExhausted as exc:
                raise RuntimeError(str(exc)) from exc
            except (RiskDenied, AegisHaltedError, CommandError):
                # Policy and halt decisions are not retryable and must keep
                # their typed exit codes rather than becoming a generic failure.
                raise

            if not report.recovered and report.attempts and not report.attempts[-1].ok:
                raise RuntimeError(report.final_error)

            data = dict(captured.get("last", {}))
            data["recovery"] = report.to_dict()
            return f"executed: {command}", data

        return self._timed("execute", _run)

    def execute_many(self, commands: Sequence[str], **kwargs) -> List[AgentResult]:
        """Run several commands in order, stopping early on killswitch."""
        results: List[AgentResult] = []
        self.runtime.queue_length = len(commands)
        for command in commands:
            try:
                self.killswitch.guard("execute batch")
            except AegisHaltedError:
                break
            results.append(self.execute(command, **kwargs))
            self.runtime.queue_length = max(0, self.runtime.queue_length - 1)
        return results

    def run_script(self, path: str, *, interpreter: str = "bash") -> AgentResult:
        """Execute a script file with the given interpreter."""
        return self.execute(f"{interpreter} {path}")
