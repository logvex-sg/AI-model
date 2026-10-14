"""Agent loop: model -> tool call -> executor -> observation -> model.

The model may only act by emitting a JSON tool-call object. Everything it emits
is treated as untrusted input: names are looked up in the registry, arguments
are schema-validated and the policy engine decides whether the call runs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from jarvis.executor import Executor, ToolResult
from jarvis.memory import MemoryStore
from jarvis.permissions import MODE_POLICIES, Mode
from jarvis.providers.base import Message, Provider, ProviderError
from jarvis.tasks import Task, TaskManager, TaskStatus


def _json_objects(text: str) -> list[str]:
    """Yield the balanced ``{...}`` spans in ``text``, ignoring braces in strings."""
    spans: list[str] = []
    depth = 0
    start = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0:
                spans.append(text[start : index + 1])
    return spans


SYSTEM_PROMPT = """You are JARVIS, a Linux desktop assistant.

You can inspect and operate the machine only by calling tools. To call a tool,
reply with a single JSON object and nothing else:

{"tool": "<tool name>", "arguments": {...}}

Rules:
- Use exactly one tool call per reply.
- Never claim you performed an action you did not perform through a tool.
- Never invent tool output; wait for the observation.
- When you have enough information, reply with a normal answer and no JSON.

Available tools:
{tools}
"""


@dataclass
class Turn:
    """One model turn plus any tool call it produced."""

    content: str
    tool_call: dict[str, Any] | None = None
    tool_result: ToolResult | None = None


@dataclass
class AgentReply:
    text: str
    turns: list[Turn] = field(default_factory=list)
    task: Task | None = None

    @property
    def tool_results(self) -> list[ToolResult]:
        return [turn.tool_result for turn in self.turns if turn.tool_result is not None]


def parse_tool_call(text: str) -> dict[str, Any] | None:
    """Extract a tool call from model output, or None for a plain answer."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\n?|```$", "", candidate).strip()
    for blob in _json_objects(candidate):
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("tool"), str):
            arguments = parsed.get("arguments") or parsed.get("args") or {}
            if not isinstance(arguments, dict):
                continue
            return {"tool": parsed["tool"], "arguments": arguments}
    return None


class Agent:
    def __init__(
        self,
        provider: Provider,
        executor: Executor,
        store: MemoryStore | None = None,
        tasks: TaskManager | None = None,
        history_limit: int = 12,
    ) -> None:
        self.provider = provider
        self.executor = executor
        self.store = store
        self.tasks = tasks or TaskManager()
        self.history_limit = history_limit
        self.history: list[Message] = []

    @property
    def mode(self) -> Mode:
        return self.executor.policy.mode

    def system_message(self) -> Message:
        lines = [
            f"- {schema['name']} ({schema['permission']}): {schema['description']} "
            f"args={list(schema['parameters']['properties'])}"
            for schema in self.executor.registry.schemas()
        ]
        prompt = SYSTEM_PROMPT.replace("{tools}", "\n".join(lines))
        prompt += f"\nActive mode: {self.mode.indicator}."
        return Message("system", prompt)

    def build_context(self, user_input: str) -> list[Message]:
        """Recent history + relevant memories, not the entire conversation."""
        messages = [self.system_message()]
        if self.store is not None:
            relevant = self.store.relevant(user_input, limit=5)
            if relevant:
                facts = "\n".join(f"- {memory.key}: {memory.value}" for memory in relevant)
                messages.append(Message("system", f"Relevant remembered facts:\n{facts}"))
        messages.extend(self.history[-self.history_limit :])
        messages.append(Message("user", user_input))
        return messages

    def chat(self, user_input: str) -> AgentReply:
        task = self.tasks.create(description=user_input, mode=self.mode.value)
        task.status = TaskStatus.RUNNING
        messages = self.build_context(user_input)
        self.history.append(Message("user", user_input))
        if self.store is not None:
            self.store.append_message("user", user_input)

        budget = MODE_POLICIES[self.mode].max_tool_calls
        turns: list[Turn] = []

        for _ in range(budget):
            if task.cancelled:
                task.finish(TaskStatus.CANCELLED)
                return AgentReply("Task cancelled.", turns, task)
            if self.executor.killswitch.engaged:
                task.finish(TaskStatus.CANCELLED, error="emergency stop engaged")
                return AgentReply(
                    f"Emergency stop engaged: {self.executor.killswitch.reason()}", turns, task
                )
            try:
                completion = self.provider.complete(messages)
            except ProviderError as exc:
                task.finish(TaskStatus.FAILED, error=str(exc))
                return AgentReply(f"Model unavailable: {exc}", turns, task)

            call = parse_tool_call(completion.content)
            if call is None:
                turns.append(Turn(content=completion.content))
                self.history.append(Message("assistant", completion.content))
                if self.store is not None:
                    self.store.append_message("assistant", completion.content)
                task.finish(TaskStatus.COMPLETED, result=completion.content)
                return AgentReply(completion.content, turns, task)

            step = task.add_step(f"call {call['tool']}", status="running")
            result = self.executor.call(call["tool"], call["arguments"], task_id=task.id)
            step.status = result.status
            step.detail = result.error or result.reason
            turns.append(Turn(completion.content, call, result))

            if result.status == "stopped":
                task.finish(TaskStatus.CANCELLED, error=result.reason)
                return AgentReply(f"Execution stopped: {result.reason}", turns, task)
            if result.status == "needs_confirmation":
                task.status = TaskStatus.WAITING_FOR_CONFIRMATION
                return AgentReply(
                    f"'{call['tool']}' requires confirmation: {result.reason}", turns, task
                )

            observation = json.dumps(
                {
                    "tool": result.tool,
                    "status": result.status,
                    "result": result.result,
                    "error": result.error or result.reason,
                },
                default=str,
            )[:4000]
            messages.append(Message("assistant", completion.content))
            messages.append(Message("system", f"Tool observation: {observation}"))

        task.finish(TaskStatus.FAILED, error="tool call budget exhausted")
        return AgentReply(
            f"Stopped after {budget} tool calls without a final answer.", turns, task
        )
