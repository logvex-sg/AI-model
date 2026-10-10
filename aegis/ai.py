"""The reasoning loop — turning an objective into actions and answers.

This is the part that makes the platform an *agent* rather than a task table.
It runs a bounded tool-calling loop against :class:`~aegis.llm.LLMClient`:

    objective -> model picks a tool -> we execute it under the normal policy
              -> result goes back to the model -> repeat until it answers

Two invariants hold throughout:

* **Nothing bypasses the core.** Every tool action runs through ``Shell``,
  ``FileSystem``, the risk classifier, the killswitch, and the audit log, with
  the same guarantees as a CLI command. The model decides *what*; the platform
  decides *whether*.
* **Honesty over fluency.** A denied or failed action is fed back to the model
  as a failed result, labelled as such. The loop can report that it could not
  do something; it cannot invent that it did.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .errors import AegisHaltedError, RiskDenied
from .llm import LLMClient, LLMError
from .risk import classify

#: Sent to the model as the system prompt. Kept explicit about the boundary
#: between choosing an action and being permitted to run it.
SYSTEM_PROMPT = """\
You are KALI-AEGIS, an autonomous security-engineering assistant on a Linux host.

You have tools. Use them to actually accomplish the operator's objective rather
than describing how it could be done. Work in small steps: run one action, read
its output, then decide the next.

Rules:
- Prefer real execution over explanation. If the operator asked for something on
  this machine, do it.
- Call `run_command` for shell work, `read_file`/`write_file` for files,
  `list_dir` to inspect directories, and `finish` when the objective is done.
- Every command is classified by risk and may be refused. If a tool returns an
  error or a refusal, do not pretend it succeeded. Say what failed and why, then
  try a different approach or report the blocker.
- Destructive operations (deleting data, formatting disks, changing auth) will
  be refused unless the operator has authorized them. Do not try to evade that.
- Never fabricate command output. Only report what a tool actually returned.
- Be concise. When you finish, summarise what you did and what you found.
"""


@dataclass
class ToolCall:
    """One tool invocation the model asked for."""

    name: str
    arguments: Dict[str, Any]
    id: str = ""


@dataclass
class Step:
    """A recorded step in the reasoning loop, for the UI and the audit trail."""

    kind: str                 # "tool" | "answer" | "error"
    tool: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    output: str = ""
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "tool": self.tool,
            "arguments": self.arguments,
            "ok": self.ok,
            "output": self.output,
            "detail": self.detail,
        }


@dataclass
class AgentRun:
    """The outcome of one objective: the answer plus how it was reached."""

    objective: str
    answer: str = ""
    steps: List[Step] = field(default_factory=list)
    ok: bool = True
    error: str = ""
    iterations: int = 0
    tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "objective": self.objective,
            "answer": self.answer,
            "ok": self.ok,
            "error": self.error,
            "iterations": self.iterations,
            "tokens": self.tokens,
            "steps": [s.to_dict() for s in self.steps],
        }


#: Tool schema advertised to the model, in OpenAI function-calling form.
TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command on this machine and return its stdout, "
                "stderr, and exit code. The command is risk-classified and "
                "audited; dangerous ones may be refused."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to run."},
                    "cwd": {"type": "string", "description": "Working directory (optional)."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file from this machine.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text to a file, creating parent directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List the entries of a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "recursive": {"type": "boolean"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Finish the objective and give the operator your final answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string", "description": "Your final answer or summary."}
                },
                "required": ["answer"],
            },
        },
    },
]


class AIAgent:
    """Runs the tool-calling loop for one runtime."""

    def __init__(
        self,
        *,
        client: LLMClient,
        execute_command: Callable[..., Any],
        read_file: Callable[..., str],
        write_file: Callable[..., Any],
        list_dir: Callable[..., List[str]],
        max_iterations: int = 12,
    ) -> None:
        self.client = client
        self.execute_command = execute_command
        self.read_file = read_file
        self.write_file = write_file
        self.list_dir = list_dir
        self.max_iterations = max_iterations

    @property
    def available(self) -> bool:
        return self.client.available

    # -- the loop ------------------------------------------------------------
    def run(self, objective: str, *, scope: str = "") -> AgentRun:
        """Drive the objective to completion or a bounded stop."""
        run = AgentRun(objective=objective)
        if not self.available:
            run.ok = False
            run.error = "no LLM configured"
            return run

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._brief(objective, scope)},
        ]

        for _ in range(self.max_iterations):
            run.iterations += 1
            try:
                response = self.client.complete(messages, tools=TOOL_SPECS)
            except LLMError as exc:
                run.ok = False
                run.error = str(exc)
                run.steps.append(Step(kind="error", detail=str(exc), ok=False))
                return run

            run.tokens += response.total_tokens
            call = self._extract_call(response.raw)

            if call is None:
                # The model answered in prose without calling a tool.
                run.answer = response.text
                run.steps.append(Step(kind="answer", output=response.text))
                return run

            messages.append(self._assistant_message(response.raw))
            step, tool_message = self._dispatch(call)
            run.steps.append(step)
            messages.append(tool_message)

            if call.name == "finish":
                run.answer = str(call.arguments.get("answer", "")).strip()
                return run

        run.ok = False
        run.error = f"stopped after {self.max_iterations} iterations without finishing"
        return run

    # -- helpers -------------------------------------------------------------
    def _brief(self, objective: str, scope: str) -> str:
        lines = [f"Objective: {objective}"]
        if scope:
            lines.append(f"Authorized scope for security actions: {scope}")
        lines.append(
            "Use your tools to accomplish this. Finish with the `finish` tool."
        )
        return "\n".join(lines)

    @staticmethod
    def _extract_call(raw: Dict[str, Any]) -> Optional[ToolCall]:
        choices = raw.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        calls = message.get("tool_calls") or []
        if not calls:
            return None
        first = calls[0]
        function = first.get("function") or {}
        args = function.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_raw": args}
        return ToolCall(
            name=function.get("name", ""),
            arguments=args or {},
            id=first.get("id", ""),
        )

    @staticmethod
    def _assistant_message(raw: Dict[str, Any]) -> Dict[str, Any]:
        choices = raw.get("choices") or [{}]
        message = choices[0].get("message") or {}
        return {
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls") or [],
        }

    def _dispatch(self, call: ToolCall):
        """Run one tool call, returning a :class:`Step` and the tool message."""
        if call.name == "finish":
            answer = str(call.arguments.get("answer", ""))
            step = Step(kind="tool", tool="finish", arguments=call.arguments,
                        output=answer)
            return step, self._tool_message(call, {"status": "finished"})

        try:
            if call.name == "run_command":
                step = self._do_command(call)
            elif call.name == "read_file":
                step = self._do_read(call)
            elif call.name == "write_file":
                step = self._do_write(call)
            elif call.name == "list_dir":
                step = self._do_list(call)
            else:
                step = Step(kind="tool", tool=call.name, ok=False,
                            detail=f"unknown tool: {call.name}")
        except AegisHaltedError as exc:
            step = Step(kind="tool", tool=call.name, ok=False,
                        detail=f"halted: {exc}")
        except RiskDenied as exc:
            step = Step(kind="tool", tool=call.name, ok=False,
                        detail=f"refused by risk policy: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            step = Step(kind="tool", tool=call.name, ok=False,
                        detail=f"tool error: {exc}")

        payload = {
            "status": "ok" if step.ok else "error",
            "output": step.output[:4000],
            "detail": step.detail,
        }
        return step, self._tool_message(call, payload)

    def _do_command(self, call: ToolCall) -> Step:
        command = str(call.arguments.get("command", "")).strip()
        if not command:
            return Step(kind="tool", tool="run_command", ok=False,
                        detail="no command provided")
        assessment = classify(command)
        result = self.execute_command(command, cwd=call.arguments.get("cwd"))
        data = self._as_dict(result)
        executed = data.get("executed") or command
        elevated = bool(data.get("elevated"))
        detail = f"exit {data.get('returncode')} ({assessment.risk.name} risk)"
        if elevated:
            detail += ", elevated"
        return Step(
            kind="tool",
            tool="run_command",
            arguments={"command": command, "executed": executed, "elevated": elevated},
            ok=bool(data.get("ok")),
            output=(data.get("stdout") or "") + (data.get("stderr") or ""),
            detail=detail,
        )

    @staticmethod
    def _as_dict(result: Any) -> Dict[str, Any]:
        """Normalise whatever the executor returns into a plain dict."""
        if hasattr(result, "to_dict"):
            return result.to_dict()
        if hasattr(result, "data"):
            return dict(result.data)
        return dict(result)

    def _do_read(self, call: ToolCall) -> Step:
        path = str(call.arguments.get("path", ""))
        content = self.read_file(path)
        return Step(kind="tool", tool="read_file", arguments={"path": path},
                    output=content[:8000])

    def _do_write(self, call: ToolCall) -> Step:
        path = str(call.arguments.get("path", ""))
        content = str(call.arguments.get("content", ""))
        result = self.write_file(path, content)
        return Step(kind="tool", tool="write_file", arguments={"path": path},
                    output=f"wrote {len(content)} bytes to {result}")

    def _do_list(self, call: ToolCall) -> Step:
        path = str(call.arguments.get("path", "."))
        recursive = bool(call.arguments.get("recursive", False))
        entries = self.list_dir(path, recursive=recursive)
        return Step(kind="tool", tool="list_dir", arguments={"path": path},
                    output="\n".join(entries[:500]))

    @staticmethod
    def _tool_message(call: ToolCall, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": call.id,
            "name": call.name,
            "content": json.dumps(payload),
        }
