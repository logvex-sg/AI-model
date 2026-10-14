"""Tool definition and argument-schema validation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis.permissions import PermissionLevel


class ToolError(RuntimeError):
    """Raised for invalid arguments or a failed tool execution."""


@dataclass(frozen=True)
class Argument:
    name: str
    type: type
    description: str
    required: bool = True
    default: Any = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    category: str
    permission: PermissionLevel
    handler: Callable[..., Any]
    arguments: tuple[Argument, ...] = ()
    timeout: float = 30.0
    audit: bool = True

    def schema(self) -> dict[str, Any]:
        """JSON-schema-ish description used for prompts and for validation."""
        properties = {
            argument.name: {
                "type": _json_type(argument.type),
                "description": argument.description,
            }
            for argument in self.arguments
        }
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "permission": self.permission.value,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": [a.name for a in self.arguments if a.required],
            },
        }

    def validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        known = {argument.name: argument for argument in self.arguments}
        unknown = set(arguments) - set(known)
        if unknown:
            raise ToolError(f"unknown argument(s) for '{self.name}': {sorted(unknown)}")

        validated: dict[str, Any] = {}
        for name, argument in known.items():
            if name not in arguments:
                if argument.required:
                    raise ToolError(f"missing required argument '{name}' for '{self.name}'")
                validated[name] = argument.default
                continue
            validated[name] = _coerce(self.name, argument, arguments[name])
        return validated


def _coerce(tool_name: str, argument: Argument, value: Any) -> Any:
    if argument.type is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise ToolError(f"argument '{argument.name}' of '{tool_name}' must be a boolean")
    if argument.type in (int, float) and isinstance(value, bool):
        raise ToolError(
            f"argument '{argument.name}' of '{tool_name}' must be {argument.type.__name__}"
        )
    try:
        return argument.type(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(
            f"argument '{argument.name}' of '{tool_name}' must be {argument.type.__name__}"
        ) from exc


def _json_type(python_type: type) -> str:
    return {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }.get(python_type, "string")


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> Tool:
        if tool.name in self.tools:
            raise ValueError(f"tool '{tool.name}' is already registered")
        self.tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool:
        try:
            return self.tools[name]
        except KeyError as exc:
            raise ToolError(f"unknown tool '{name}'") from exc

    def names(self) -> list[str]:
        return sorted(self.tools)

    def by_category(self) -> dict[str, list[Tool]]:
        grouped: dict[str, list[Tool]] = {}
        for tool in self.tools.values():
            grouped.setdefault(tool.category, []).append(tool)
        for tools in grouped.values():
            tools.sort(key=lambda t: t.name)
        return dict(sorted(grouped.items()))

    def schemas(self) -> list[dict[str, Any]]:
        return [self.tools[name].schema() for name in self.names()]
