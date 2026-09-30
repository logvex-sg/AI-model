"""Append-only operation log.

Every action the agent takes is recorded as one JSON object per line so the
audit trail can be replayed, grepped, or shipped to a SIEM without parsing
free-form text. Secrets are redacted before anything is written.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .secrets import redact


class OperationLog:
    """Thread-safe JSONL audit log."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _write(self, record: Dict[str, Any]) -> Dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, default=str, sort_keys=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return record

    def record(
        self,
        agent: str,
        operation: str,
        *,
        target: str = "",
        command: str = "",
        result: str = "",
        exit_code: Optional[int] = None,
        status: str = "ok",
        cwd: str = "",
        duration_ms: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Append one operation record and return it."""
        entry: Dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "epoch": time.time(),
            "agent": agent,
            "operation": operation,
            "target": target,
            "command": redact(command),
            "cwd": cwd,
            "result": redact(result),
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "status": status,
        }
        if extra:
            for key, value in extra.items():
                entry.setdefault(key, redact(str(value)))
        return self._write(entry)

    def read(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return parsed records, most recent last."""
        if not self.path.exists():
            return []
        records: List[Dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if limit is not None and limit >= 0:
            return records[-limit:]
        return records

    def tail(self, limit: int = 20) -> Iterator[Dict[str, Any]]:
        yield from self.read(limit=limit)
