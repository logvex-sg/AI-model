"""Secret detection and redaction.

The agent routinely prints command output and file contents. Before any of
that reaches a log, a report, or the API response it passes through
:func:`redact`, which replaces recognised credential shapes with
``[REDACTED]``.

This is defence in depth, not a guarantee: the operating principle is to
never *intentionally* print a secret, and to redact the obvious ones by
default.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Pattern, Tuple

REDACTED = "[REDACTED]"

# Ordered so that more specific patterns win before generic ones.
_PATTERNS: List[Tuple[str, Pattern[str]]] = [
    # Private key blocks (PEM).
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    # JSON Web Tokens.
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    # GitHub tokens (classic and fine-grained).
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    # AWS access key IDs.
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    # Slack tokens.
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    # Bearer tokens in headers.
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{12,}")),
    # key=value / key: value credential assignments.
    (
        "assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd|"
            r"access[_-]?key|client[_-]?secret|private[_-]?key)\b"
            r"\s*([:=])\s*[\"']?([^\s\"',;]{6,})[\"']?"
        ),
    ),
]


def _replace_assignment(match: "re.Match[str]") -> str:
    key, sep = match.group(1), match.group(2)
    return f"{key}{sep} {REDACTED}"


def redact(text: object) -> str:
    """Return *text* with recognised secrets replaced by ``[REDACTED]``."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    for name, pattern in _PATTERNS:
        if name == "assignment":
            text = pattern.sub(_replace_assignment, text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


def find_secrets(text: str) -> List[str]:
    """Return the names of secret patterns present in *text* (for reporting)."""
    found: List[str] = []
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            found.append(name)
    return found


def contains_secret(text: str) -> bool:
    return bool(find_secrets(text))


def scrub_lines(lines: Iterable[str]) -> List[str]:
    """Redact an iterable of lines (e.g. a file read line by line)."""
    return [redact(line) for line in lines]
