"""Emergency stop, implemented outside the model's decision loop.

The killswitch is a file on disk plus an in-process event. Any JARVIS process
(GUI, CLI, scheduler) observes the same file, so ``jarvis emergency-stop`` from
a second terminal halts a running agent. Only an explicit ``resume`` clears it.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class EmergencyStop(RuntimeError):
    """Raised when execution is attempted while the killswitch is engaged."""


class KillSwitch:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._event = threading.Event()
        if self.path.exists():
            self._event.set()

    @property
    def engaged(self) -> bool:
        engaged = self.path.exists()
        if engaged:
            self._event.set()
        else:
            self._event.clear()
        return engaged

    @property
    def event(self) -> threading.Event:
        """Event set while the killswitch is engaged (for cooperative waits)."""
        _ = self.engaged  # refresh from disk
        return self._event

    def engage(self, reason: str = "manual emergency stop") -> None:
        payload = {"reason": reason, "timestamp": time.time()}
        self.path.write_text(json.dumps(payload))
        self._event.set()

    def release(self) -> None:
        if self.path.exists():
            self.path.unlink()
        self._event.clear()

    def reason(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text()).get("reason")
        except (json.JSONDecodeError, OSError):
            return "unknown"

    def check(self) -> None:
        if self.engaged:
            raise EmergencyStop(f"emergency stop engaged: {self.reason()}")
