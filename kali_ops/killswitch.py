"""Global killswitch.

The killswitch is the operator's hard stop. It engages when **either**:

* the environment variable ``KALI_OPS_KILLSWITCH`` is truthy, or
* a sentinel file exists (default ``$KALI_OPS_HOME/KILLSWITCH``).

Engaging it refuses new work, and callers are expected to abandon queued and
running work. Disengaging is always explicit — nothing auto-restarts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .config import Config
from .errors import KillswitchActive

ENV_VAR = "KALI_OPS_KILLSWITCH"
_TRUTHY = {"1", "true", "yes", "on", "enabled"}


class Killswitch:
    """Read/engage/release the operator killswitch."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._path = config.killswitch_path

    @property
    def path(self) -> Path:
        return self._path

    def env_engaged(self) -> bool:
        return os.environ.get(ENV_VAR, "").strip().lower() in _TRUTHY

    def file_engaged(self) -> bool:
        return self._path.exists()

    def is_engaged(self) -> bool:
        return self.env_engaged() or self.file_engaged()

    def reason(self) -> Optional[str]:
        """Human-readable explanation of why the switch is engaged."""
        if self.env_engaged() and self.file_engaged():
            return f"{ENV_VAR} is set and sentinel file {self._path} exists"
        if self.env_engaged():
            return f"{ENV_VAR} is set"
        if self.file_engaged():
            return f"sentinel file {self._path} exists"
        return None

    def engage(self, reason: str = "operator engaged killswitch") -> Path:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(reason + "\n", encoding="utf-8")
        return self._path

    def release(self) -> bool:
        """Remove the sentinel file. Returns True if a file was removed.

        Note this cannot clear the environment variable; the operator must
        unset that themselves (the CLI reports it if it is still set).
        """
        if self._path.exists():
            self._path.unlink()
            return True
        return False

    def guard(self, action: str = "operation") -> None:
        """Raise :class:`KillswitchActive` if the switch is engaged."""
        if self.is_engaged():
            raise KillswitchActive(
                f"killswitch engaged ({self.reason()}); refusing {action}"
            )
