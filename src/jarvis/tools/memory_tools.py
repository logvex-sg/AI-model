"""Tools exposing the persistent memory store to the agent."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from jarvis.memory import MemoryStore
from jarvis.permissions import PermissionLevel
from jarvis.tools.base import Argument, Tool, ToolRegistry


def register(registry: ToolRegistry, store: MemoryStore) -> None:
    def remember(key: str, value: str, tags: str = "") -> dict[str, Any]:
        return asdict(store.remember(key, value, tags))

    def search(query: str, limit: int = 10) -> dict[str, Any]:
        return {"results": [asdict(memory) for memory in store.search(query, limit)]}

    def forget(key: str) -> dict[str, Any]:
        return {"key": key, "deleted": store.forget(key)}

    def clear() -> dict[str, Any]:
        return {"deleted": store.clear()}

    registry.register(
        Tool(
            "memory.remember",
            "Store a durable fact. Content that looks like a credential is rejected.",
            "MEMORY",
            PermissionLevel.SAFE,
            remember,
            (
                Argument("key", str, "Short unique key."),
                Argument("value", str, "Fact to remember."),
                Argument("tags", str, "Comma separated tags.", False, ""),
            ),
        )
    )
    registry.register(
        Tool(
            "memory.search",
            "Search stored memories by substring.",
            "MEMORY",
            PermissionLevel.SAFE,
            search,
            (
                Argument("query", str, "Search string."),
                Argument("limit", int, "Maximum results.", False, 10),
            ),
        )
    )
    registry.register(
        Tool(
            "memory.forget",
            "Delete a single memory by key.",
            "MEMORY",
            PermissionLevel.MODERATE,
            forget,
            (Argument("key", str, "Memory key."),),
        )
    )
    registry.register(
        Tool(
            "memory.clear",
            "Delete all stored memories.",
            "MEMORY",
            PermissionLevel.DESTRUCTIVE,
            clear,
        )
    )
