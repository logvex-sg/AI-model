"""Append-only JSONL audit log with secret redaction."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REDACTED = "[REDACTED]"

_SECRET_KEY = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|credential|authorization|private[_-]?key)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


def redact(value: Any) -> Any:
    """Recursively redact values that look like credentials."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if _SECRET_KEY.search(str(key)) else redact(val))
            for key, val in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub(REDACTED, value)
    return value


@dataclass(frozen=True)
class AuditEvent:
    task_id: str
    mode: str
    tool: str
    permission: str
    arguments: dict[str, Any]
    status: str
    result: Any = None
    confirmation: str | None = None
    timestamp: float | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp if self.timestamp is not None else time.time(),
            "task_id": self.task_id,
            "mode": self.mode,
            "tool": self.tool,
            "permission": self.permission,
            "arguments": redact(self.arguments),
            "status": self.status,
            "result": redact(_truncate(self.result)),
            "confirmation": self.confirmation,
        }


def _truncate(value: Any, limit: int = 2000) -> Any:
    text = value if isinstance(value, str) else value
    if isinstance(text, str) and len(text) > limit:
        return text[:limit] + f"... [{len(text) - limit} chars truncated]"
    return value


class AuditLog:
    """Thread-safe JSONL audit log. The log is never truncated by JARVIS."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: AuditEvent) -> dict[str, Any]:
        record = event.to_record()
        line = json.dumps(record, default=str)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return record

    def tail(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as handle:
            lines = handle.readlines()[-limit:]
        records = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records
