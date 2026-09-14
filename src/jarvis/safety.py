"""Path confinement and fixed-argument subprocess execution."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class SafetyError(RuntimeError):
    """Raised when an operation violates a safety boundary."""


def resolve_path(raw: str, *, confine_to_home: bool = True, must_exist: bool = False) -> Path:
    """Resolve ``raw`` to an absolute path, optionally confined to $HOME.

    Symlinks are resolved before the confinement check, so a symlink inside
    $HOME pointing outside it is rejected.
    """
    path = Path(raw).expanduser()
    resolved = path.resolve()
    if confine_to_home:
        home = Path.home().resolve()
        if resolved != home and home not in resolved.parents:
            raise SafetyError(f"path '{resolved}' is outside the home directory")
    if must_exist and not resolved.exists():
        raise SafetyError(f"path '{resolved}' does not exist")
    return resolved


def run_command(argv: list[str], *, timeout: float = 15.0) -> dict:
    """Run a fixed argument vector without a shell.

    Never accepts a command string: callers build the vector, so user or model
    text can never be interpreted as shell syntax.
    """
    if not argv:
        raise SafetyError("empty command")
    executable = shutil.which(argv[0])
    if executable is None:
        raise SafetyError(f"command '{argv[0]}' is not installed")
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            [executable, *argv[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SafetyError(f"command '{argv[0]}' timed out after {timeout}s") from exc
    return {
        "command": argv,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def command_available(name: str) -> bool:
    return shutil.which(name) is not None
