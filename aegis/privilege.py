"""Privilege handling — detection and explicit, auditable elevation.

Two separate questions, deliberately kept apart:

* **Am I root?** A fact about this process (``os.geteuid() == 0``).
* **May I elevate?** A policy decision. ``allow_root`` must be on, and the
  operator must have opted in, before a command is ever prefixed with ``sudo``.

Elevation is applied per command, never process-wide. We never call
``os.setuid`` and we never re-exec the whole agent as root: the blast radius of
a mistake stays confined to the commands that actually needed elevation, and
every elevated command is visible in the audit trail with ``elevated: true``.

The default is fail-closed. If a command needs root and elevation is not
available, it is refused with a reason rather than silently run unprivileged
and failing in a confusing way.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple


class Privilege(str, Enum):
    """What privilege a command will actually run with."""

    USER = "user"          # ordinary unprivileged execution
    ROOT = "root"          # this process is already uid 0
    ELEVATED = "elevated"  # prefixed with sudo for this command


def is_root() -> bool:
    """True when this process is running as uid 0."""
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def current_user() -> str:
    """Best-effort current username."""
    for getter in ("getlogin", "getpass"):
        try:
            if getter == "getlogin":
                name = os.getlogin()
            else:  # pragma: no cover - fallback path
                import getpass

                name = getpass.getuser()
        except Exception:
            continue
        if name:
            return name
    return os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"


def sudo_path() -> Optional[str]:
    """Absolute path to sudo, or None when sudo is not installed."""
    return shutil.which("sudo")


def sudo_nopasswd() -> bool:
    """True when ``sudo -n true`` succeeds — i.e. no password is required.

    This is what makes non-interactive elevation (the REST API, automation)
    possible. Without it, sudo would block on a password prompt we cannot
    answer.
    """
    if not sudo_path():
        return False
    try:
        proc = subprocess.run(
            ["sudo", "-n", "true"],
            capture_output=True,
            timeout=5,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@dataclass(frozen=True)
class PrivilegeReport:
    """A snapshot of the machine's privilege situation, for doctor/status."""

    is_root: bool
    user: str
    uid: int
    sudo_installed: bool
    sudo_passwordless: bool
    allow_root: bool

    @property
    def can_elevate(self) -> bool:
        """Whether a command can actually be raised to root right now."""
        return self.is_root or (self.sudo_installed and self.sudo_passwordless)

    @property
    def capability(self) -> str:
        """Human-readable summary for the diagnostics table."""
        if self.is_root:
            return "running as root"
        if not self.sudo_installed:
            return "no sudo installed"
        if self.sudo_passwordless:
            return "sudo available (passwordless)"
        return "sudo installed (password required)"

    def to_dict(self) -> dict:
        return {
            "is_root": self.is_root,
            "user": self.user,
            "uid": self.uid,
            "sudo_installed": self.sudo_installed,
            "sudo_passwordless": self.sudo_passwordless,
            "allow_root": self.allow_root,
            "can_elevate": self.can_elevate,
            "capability": self.capability,
        }


class PrivilegeManager:
    """Decides, per command, whether and how to elevate."""

    def __init__(
        self,
        *,
        allow_root: bool = False,
        interactive: bool = False,
    ) -> None:
        #: Policy switch. Without it, nothing is ever elevated.
        self.allow_root = allow_root
        #: When True, a password prompt is acceptable (the CLI). When False
        #: (API, automation, GUI worker) only passwordless sudo is usable.
        self.interactive = interactive

    # -- reporting -----------------------------------------------------------
    def report(self) -> PrivilegeReport:
        geteuid = getattr(os, "geteuid", None)
        return PrivilegeReport(
            is_root=is_root(),
            user=current_user(),
            uid=geteuid() if geteuid else -1,
            sudo_installed=sudo_path() is not None,
            sudo_passwordless=sudo_nopasswd() if not is_root() else False,
            allow_root=self.allow_root,
        )

    # -- decisions -----------------------------------------------------------
    def elevate(self, command: str, *, requires_root: bool = False) -> Tuple[str, Privilege]:
        """Return ``(command_to_run, privilege)`` for *command*.

        * An unprivileged command is returned untouched.
        * A command that needs root is prefixed with ``sudo`` when policy
          allows and elevation is possible; otherwise a :class:`PermissionError`
          explains exactly what is missing.
        """
        stripped = command.lstrip()
        already_sudo = stripped.startswith(("sudo ", "sudo\t", "doas "))

        if is_root():
            return command, Privilege.ROOT

        # A command that already carries its own sudo needs no help from us,
        # and double-prefixing would be wrong.
        if already_sudo:
            if not self.allow_root:
                raise PermissionError(
                    "command uses sudo but allow_root is disabled; "
                    "enable it with --root or KALI_AEGIS_ALLOW_ROOT=1"
                )
            return command, Privilege.ELEVATED

        if not requires_root:
            return command, Privilege.USER

        if not self.allow_root:
            raise PermissionError(
                f"command requires root but allow_root is disabled. "
                f"Enable it with --root or KALI_AEGIS_ALLOW_ROOT=1: {command!r}"
            )
        if not sudo_path():
            raise PermissionError(
                f"command requires root but sudo is not installed: {command!r}"
            )
        if not self.interactive and not sudo_nopasswd():
            raise PermissionError(
                "command requires root but passwordless sudo is unavailable. "
                "Configure a NOPASSWD sudoers entry, or run the CLI "
                "interactively where a password prompt is allowed."
            )

        # `-n` keeps non-interactive paths from blocking on a prompt; an
        # interactive CLI can answer one.
        prefix = "sudo " if self.interactive else "sudo -n "
        return prefix + command, Privilege.ELEVATED

    def describe(self) -> List[str]:
        """Short lines used by ``aegis status`` and the console."""
        rep = self.report()
        lines = [f"privilege  : {rep.capability}"]
        lines.append(f"allow_root : {'enabled' if rep.allow_root else 'disabled'}")
        if rep.allow_root and not rep.can_elevate:
            lines.append("warning    : allow_root is on but elevation is unavailable")
        return lines
