"""Typed error hierarchy for KALI-OPS.

Every failure the agent can surface maps to one of these so the CLI can exit
with a meaningful code and the API can render a structured error.
"""

from __future__ import annotations


class KaliOpsError(Exception):
    """Base class for all KALI-OPS errors."""

    exit_code = 1


class ConfigError(KaliOpsError):
    """Configuration could not be loaded or validated."""

    exit_code = 2


class KillswitchActive(KaliOpsError):
    """An operation was refused because the killswitch is engaged."""

    exit_code = 3


class ScopeViolation(KaliOpsError):
    """A security action targeted something outside the authorized scope."""

    exit_code = 4


class RiskDenied(KaliOpsError):
    """An operation was denied because of its risk classification."""

    exit_code = 5


class CommandError(KaliOpsError):
    """A shell command failed."""

    exit_code = 6

    def __init__(self, message: str, *, returncode: int = 1, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class NotFoundError(KaliOpsError):
    """A referenced object (task, repo, agent) does not exist."""

    exit_code = 7
