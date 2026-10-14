"""Persistent SQLite memory with secret rejection."""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"\b(password|passwd|api[_-]?key|secret|token|credential)\b\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
]

STOPWORDS = {
    "what",
    "where",
    "when",
    "which",
    "that",
    "this",
    "with",
    "your",
    "does",
    "about",
    "there",
    "have",
    "from",
    "tell",
    "show",
    "please",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    value TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_key ON memories(key);
"""


class SecretRejected(ValueError):
    """Raised when content that looks like a credential is stored."""


@dataclass(frozen=True)
class Memory:
    id: int
    key: str
    value: str
    tags: str
    created_at: float
    updated_at: float


def looks_like_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def remember(self, key: str, value: str, tags: str = "") -> Memory:
        if looks_like_secret(value) or looks_like_secret(key):
            raise SecretRejected("refusing to store content that looks like a credential")
        now = time.time()
        self._connection.execute(
            """
            INSERT INTO memories (key, value, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, tags=excluded.tags,
                                           updated_at=excluded.updated_at
            """,
            (key, value, tags, now, now),
        )
        self._connection.commit()
        stored = self.get(key)
        assert stored is not None
        return stored

    def get(self, key: str) -> Memory | None:
        row = self._connection.execute(
            "SELECT * FROM memories WHERE key = ?", (key,)
        ).fetchone()
        return _row_to_memory(row) if row else None

    def search(self, query: str, limit: int = 10) -> list[Memory]:
        rows = self._connection.execute(
            """
            SELECT * FROM memories
            WHERE key LIKE ? OR value LIKE ? OR tags LIKE ?
            ORDER BY updated_at DESC LIMIT ?
            """,
            (f"%{query}%", f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        return [_row_to_memory(row) for row in rows]

    def relevant(self, text: str, limit: int = 5) -> list[Memory]:
        """Keyword retrieval for context building.

        A whole user sentence rarely appears verbatim in a memory, so the text
        is tokenised and memories are ranked by how many tokens they match.
        """
        tokens = {
            token
            for token in re.findall(r"[A-Za-z0-9_.@-]{4,}", text.lower())
            if token not in STOPWORDS
        }
        if not tokens:
            return []
        scored: list[tuple[int, Memory]] = []
        for memory in self._all():
            haystack = f"{memory.key} {memory.value} {memory.tags}".lower()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, memory))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return [memory for _score, memory in scored[:limit]]

    def _all(self) -> list[Memory]:
        rows = self._connection.execute("SELECT * FROM memories").fetchall()
        return [_row_to_memory(row) for row in rows]

    def recent(self, limit: int = 10) -> list[Memory]:
        rows = self._connection.execute(
            "SELECT * FROM memories ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_memory(row) for row in rows]

    def forget(self, key: str) -> bool:
        cursor = self._connection.execute("DELETE FROM memories WHERE key = ?", (key,))
        self._connection.commit()
        return cursor.rowcount > 0

    def clear(self) -> int:
        cursor = self._connection.execute("DELETE FROM memories")
        self._connection.commit()
        return cursor.rowcount

    def append_message(self, role: str, content: str) -> None:
        self._connection.execute(
            "INSERT INTO conversation (role, content, created_at) VALUES (?, ?, ?)",
            (role, content, time.time()),
        )
        self._connection.commit()

    def history(self, limit: int = 20) -> list[dict[str, str]]:
        rows = self._connection.execute(
            "SELECT role, content FROM conversation ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    def clear_history(self) -> int:
        cursor = self._connection.execute("DELETE FROM conversation")
        self._connection.commit()
        return cursor.rowcount


def _row_to_memory(row: sqlite3.Row) -> Memory:
    return Memory(
        id=row["id"],
        key=row["key"],
        value=row["value"],
        tags=row["tags"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
