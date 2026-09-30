"""Shell execution engine.

Runs approved commands, captures the full execution metadata the operating
model requires (command, cwd, requesting agent, stdout, stderr, exit code,
duration, resulting state), and redacts secrets from everything it returns.

The killswitch is consulted before every execution so a stop signal halts new
work immediately.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .config import Config
from .errors import CommandError, ErrorClass, classify_returncode
from .killswitch import Killswitch
from .logging import OperationLog
from .risk import Risk, RiskAssessment, classify
from .secrets import redact

#: Sentinel the operator can use in place of the risk argument.
CONFIRM_HIGH = "confirm-high-risk"


@dataclass
class CommandResult:
    """Full execution metadata for one command.

    Mirrors the fields section 04 of the operating model requires the executor
    to capture. Nothing here is inferred after the fact: every value is read
    from the process that actually ran.
    """

    command: str
    returncode: int
    stdout: str
    stderr: str
    risk: Risk
    cwd: str = ""
    agent: str = "executor"
    duration_ms: int = 0
    started_at: float = 0.0
    timed_out: bool = False
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.dry_run

    @property
    def error_class(self) -> ErrorClass:
        return classify_returncode(self.returncode)

    def to_dict(self) -> Dict[str, object]:
        state = "dry-run" if self.dry_run else ("ok" if self.ok else "failed")
        return {
            "command": self.command,
            "cwd": self.cwd,
            "agent": self.agent,
            "returncode": self.returncode,
            "exit_code": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "risk": str(self.risk),
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "dry_run": self.dry_run,
            "error_class": self.error_class.value,
            "state": state,
            "ok": self.ok,
        }


@dataclass
class BatchResult:
    """Aggregate outcome of a sequence of commands."""

    results: List[CommandResult] = field(default_factory=list)
    halted: bool = False

    @property
    def ok(self) -> bool:
        return not self.halted and all(r.ok for r in self.results)

    @property
    def failed_index(self) -> Optional[int]:
        for i, r in enumerate(self.results):
            if not r.ok:
                return i
        return None

    def to_dict(self) -> Dict[str, object]:
        return {
            "count": len(self.results),
            "ok": self.ok,
            "halted": self.halted,
            "failed_index": self.failed_index,
            "results": [r.to_dict() for r in self.results],
        }


class Shell:
    """Executes shell commands under the KALI-AEGIS safety policy."""

    def __init__(
        self,
        config: Config,
        killswitch: Killswitch,
        log: Optional[OperationLog] = None,
        *,
        agent: str = "executor",
        dry_run: bool = False,
    ) -> None:
        self.config = config
        self.killswitch = killswitch
        self.log = log
        self.agent = agent
        self.dry_run = dry_run

    def assess(self, command: str) -> RiskAssessment:
        return classify(command)

    def run(
        self,
        command: str,
        *,
        confirmed: bool = False,
        timeout: Optional[int] = None,
        cwd: Optional[str] = None,
        check: bool = False,
        agent: Optional[str] = None,
    ) -> CommandResult:
        """Execute *command* subject to the killswitch and risk policy.

        Raises:
            AegisHaltedError: the switch is engaged.
            RiskDenied: the command is HIGH risk and not confirmed.
            CommandError: ``check=True`` and the command failed.
        """
        self.killswitch.guard("shell command")
        assessment = self.assess(command)
        requesting_agent = agent or self.agent
        working_dir = cwd or os.getcwd()

        if (
            assessment.risk is Risk.HIGH
            and not confirmed
            and not self.config.auto_approve_high_risk
        ):
            self._record(
                command,
                status="denied",
                result=assessment.reason,
                cwd=working_dir,
                agent=requesting_agent,
            )
            from .errors import RiskDenied

            raise RiskDenied(
                f"HIGH risk command denied without confirmation: {command!r} "
                f"({assessment.reason}). Re-run with confirmation or set "
                f"KALI_AEGIS_AUTO_APPROVE_HIGH_RISK=1 in a disposable environment."
            )

        if self.dry_run:
            self._record(
                command,
                status="dry-run",
                result="not executed",
                cwd=working_dir,
                agent=requesting_agent,
            )
            return CommandResult(
                command=command,
                returncode=0,
                stdout="",
                stderr="",
                risk=assessment.risk,
                cwd=working_dir,
                agent=requesting_agent,
                dry_run=True,
                started_at=time.time(),
            )

        effective_timeout = timeout or self.config.command_timeout
        started = time.time()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                cwd=cwd,
            )
            result = CommandResult(
                command=command,
                returncode=proc.returncode,
                stdout=redact(proc.stdout),
                stderr=redact(proc.stderr),
                risk=assessment.risk,
                cwd=working_dir,
                agent=requesting_agent,
                duration_ms=int((time.time() - started) * 1000),
                started_at=started,
            )
        except subprocess.TimeoutExpired:
            result = CommandResult(
                command=command,
                returncode=124,
                stdout="",
                stderr=f"command timed out after {effective_timeout}s",
                risk=assessment.risk,
                cwd=working_dir,
                agent=requesting_agent,
                duration_ms=int((time.time() - started) * 1000),
                started_at=started,
                timed_out=True,
            )

        self._record(
            command,
            status="ok" if result.ok else "failed",
            result=result.stdout or result.stderr,
            exit_code=result.returncode,
            cwd=working_dir,
            agent=requesting_agent,
            extra={"duration_ms": result.duration_ms, "error_class": result.error_class.value},
        )

        if check and not result.ok:
            raise CommandError(
                f"command failed ({result.returncode}): {command}",
                returncode=result.returncode,
                stderr=result.stderr,
            )
        return result

    def run_many(
        self,
        commands: Sequence[str],
        *,
        stop_on_error: bool = False,
        **kwargs,
    ) -> BatchResult:
        """Run several commands, optionally halting at the first failure."""
        batch = BatchResult()
        for command in commands:
            self.killswitch.guard("shell batch")
            batch.results.append(self.run(command, **kwargs))
            if stop_on_error and not batch.results[-1].ok:
                break
        return batch

    def _record(
        self,
        command: str,
        *,
        status: str,
        result: str = "",
        exit_code: Optional[int] = None,
        cwd: str = "",
        agent: str = "",
        extra: Optional[Dict[str, object]] = None,
    ) -> None:
        if self.log is not None:
            self.log.record(
                agent or self.agent,
                "shell",
                command=command,
                result=result,
                exit_code=exit_code,
                status=status,
                cwd=cwd,
                extra=extra,
            )
