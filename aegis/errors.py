"""Typed error hierarchy for KALI-AEGIS.

Every failure the agent can surface maps to one of these so the CLI can exit
with a meaningful code, the API can render a structured error, and the recovery
engine can pick a strategy from the error's :class:`ErrorClass`.
"""

from __future__ import annotations

import enum


class ErrorClass(str, enum.Enum):
    """Coarse failure category, used to choose a recovery strategy."""

    TRANSIENT = "transient"      # retrying may succeed unchanged
    ENVIRONMENT = "environment"  # a prerequisite is missing
    INPUT = "input"              # the request itself is malformed
    PERMISSION = "permission"    # denied by policy or the OS
    LOGIC = "logic"              # the operation itself is wrong
    UNKNOWN = "unknown"


class AegisError(Exception):
    """Base class for all KALI-AEGIS errors."""

    exit_code = 1
    error_class = ErrorClass.UNKNOWN
    #: Whether blindly re-running the same operation could plausibly work.
    retryable = False


class ConfigError(AegisError):
    """Configuration could not be loaded or validated."""

    exit_code = 2
    error_class = ErrorClass.INPUT


class AegisHaltedError(AegisError):
    """An operation was refused because the killswitch is engaged."""

    exit_code = 3
    error_class = ErrorClass.PERMISSION


class ScopeViolation(AegisError):
    """A security action targeted something outside the authorized scope."""

    exit_code = 4
    error_class = ErrorClass.PERMISSION


class RiskDenied(AegisError):
    """An operation was denied because of its risk classification."""

    exit_code = 5
    error_class = ErrorClass.PERMISSION


class CommandError(AegisError):
    """A shell command failed."""

    exit_code = 6

    def __init__(self, message: str, *, returncode: int = 1, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr

    @property
    def error_class(self) -> ErrorClass:  # type: ignore[override]
        return classify_returncode(self.returncode)


class NotFoundError(AegisError):
    """A referenced object (task, repo, agent) does not exist."""

    exit_code = 7
    error_class = ErrorClass.INPUT


class RecoveryExhausted(AegisError):
    """A recoverable operation failed after exhausting its retry budget."""

    exit_code = 8
    error_class = ErrorClass.TRANSIENT


#: Exit codes that conventionally mean "try again" rather than "fix the code".
_TRANSIENT_CODES = {124, 137, 143}   # timeout, SIGKILL, SIGTERM
_ENVIRONMENT_CODES = {2, 126, 127}   # misuse, not executable, not found
_PERMISSION_CODES = {1, 13}          # generic deny, EACCES


def classify_returncode(returncode: int) -> ErrorClass:
    """Map a process exit code onto an :class:`ErrorClass`."""
    if returncode in _TRANSIENT_CODES:
        return ErrorClass.TRANSIENT
    if returncode in _ENVIRONMENT_CODES:
        return ErrorClass.ENVIRONMENT
    if returncode in _PERMISSION_CODES:
        return ErrorClass.PERMISSION
    if returncode == 0:
        return ErrorClass.UNKNOWN
    return ErrorClass.LOGIC
