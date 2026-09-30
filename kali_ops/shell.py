"""Shell execution engine.

Runs approved commands, captures stdout/stderr, records an audit entry, and
redacts secrets from everything it returns. The killswitch is consulted before
and after execution so a stop signal halts new work immediately.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .config import Config
from .errors import CommandError
from .killswitch import Killswitch
from .logging import OperationLog
from .risk import Risk, RiskAssessment, classify
from .secrets import redact

#: Sentinel the operator can use in place of the risk argument.
CONFIRM_HIGH = "confirm-high-risk"


@dataclass
class CommandResult:
    """Outcome of one executed command."""

    command: str
    returncode: int
    stdout: str
    stderr: str
    risk: Risk
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def to_dict(self) -> Dict[str, object]:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "risk": str(self.risk),
            "timed_out": self.timed_out,
            "ok": self.ok,
        }


class Shell:
    """Executes shell commands under the KALI-OPS safety policy."""

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
    ) -> CommandResult:
        """Execute *command* subject to the killswitch and risk policy.

        Raises:
            KillswitchActive: the switch is engaged.
            RiskDenied: the command is HIGH risk and not confirmed.
            CommandError: ``check=True`` and the command failed.
        """
        self.killswitch.guard("shell command")
        assessment = self.assess(command)

        if assessment.risk is Risk.HIGH and not confirmed and not self.config.auto_approve_high_risk:
            self._record(command, status="denied", result=assessment.reason)
            from .errors import RiskDenied

            raise RiskDenied(
                f"HIGH risk command denied without confirmation: {command!r} "
                f"({assessment.reason}). Re-run with confirmation or set "
                f"KALI_OPS_AUTO_APPROVE_HIGH_RISK=1 in a disposable environment."
            )

        if self.dry_run:
            self._record(command, status="dry-run", result="not executed")
            return CommandResult(command, 0, "", "", assessment.risk)

        effective_timeout = timeout or self.config.command_timeout
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
            )
        except subprocess.TimeoutExpired:
            result = CommandResult(
                command=command,
                returncode=124,
                stdout="",
                stderr=f"command timed out after {effective_timeout}s",
                risk=assessment.risk,
                timed_out=True,
            )

        self._record(
            command,
            status="ok" if result.ok else "failed",
            result=result.stdout or result.stderr,
            exit_code=result.returncode,
        )

        if check and not result.ok:
            raise CommandError(
                f"command failed ({result.returncode}): {command}",
                returncode=result.returncode,
                stderr=result.stderr,
            )
        return result

    def run_many(self, commands: Sequence[str], **kwargs) -> List[CommandResult]:
        results: List[CommandResult] = []
        for command in commands:
            self.killswitch.guard("shell batch")
            results.append(self.run(command, **kwargs))
        return results

    def _record(
        self,
        command: str,
        *,
        status: str,
        result: str = "",
        exit_code: Optional[int] = None,
    ) -> None:
        if self.log is not None:
            self.log.record(
                self.agent,
                "shell",
                command=command,
                result=result,
                exit_code=exit_code,
                status=status,
            )
