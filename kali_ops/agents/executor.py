"""EXECUTOR — execution and automation worker.

Runs approved shell commands, retries transient failures, and stops the moment
the killswitch engages. It performs no interpretation of its own: the risk
policy and scope checks live in the shell and pentester layers.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

from ..errors import CommandError, KillswitchActive
from .base import Agent, AgentResult

#: Substrings that indicate a retryable, transient failure.
_TRANSIENT = (
    "temporarily unavailable",
    "connection reset",
    "connection refused",
    "timed out",
    "timeout",
    "try again",
    "resource busy",
    "could not resolve host",
)


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
        retries: int = 0,
        timeout: Optional[int] = None,
        cwd: Optional[str] = None,
    ) -> AgentResult:
        """Run one command, retrying transient failures up to *retries* times."""

        def _run() -> tuple:
            attempt = 0
            last: Optional[Dict[str, Any]] = None
            while True:
                self.killswitch.guard("execute")
                result = self.shell.run(command, confirmed=confirmed, timeout=timeout, cwd=cwd)
                last = result.to_dict()
                if result.ok:
                    return f"executed: {command}", last
                if attempt >= retries or not self._is_transient(result.stderr):
                    break
                attempt += 1
                time.sleep(min(2 ** attempt, 8))
            raise RuntimeError(
                f"command failed ({last['returncode']}): {last['stderr'] or last['stdout']}"
            )

        return self._timed("execute", _run)

    def execute_many(self, commands: Sequence[str], **kwargs) -> List[AgentResult]:
        """Run several commands in order, stopping early on killswitch."""
        results: List[AgentResult] = []
        for command in commands:
            try:
                self.killswitch.guard("execute batch")
            except KillswitchActive:
                break
            results.append(self.execute(command, **kwargs))
        return results

    def run_script(self, path: str, *, interpreter: str = "bash") -> AgentResult:
        """Execute a script file with the given interpreter."""
        return self.execute(f"{interpreter} {path}")

    @staticmethod
    def _is_transient(stderr: str) -> bool:
        lowered = (stderr or "").lower()
        return any(marker in lowered for marker in _TRANSIENT)
