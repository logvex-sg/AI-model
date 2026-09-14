"""Tool registry construction."""

from __future__ import annotations

from jarvis.memory import MemoryStore
from jarvis.tools import files, memory_tools, network, processes, system
from jarvis.tools.base import Argument, Tool, ToolError, ToolRegistry

__all__ = ["Argument", "Tool", "ToolError", "ToolRegistry", "build_registry"]


def build_registry(store: MemoryStore | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    system.register(registry)
    files.register(registry)
    processes.register(registry)
    network.register(registry)
    if store is not None:
        memory_tools.register(registry, store)
    return registry
