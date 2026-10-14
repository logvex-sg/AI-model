"""Agent base class, self-model, runtime state, and inter-agent messaging.

Every agent carries an explicit **self-model**: the capabilities it actually
implements, the limitations it knows about, and whether it needs an LLM to
function. The self-model is introspectable at runtime (``aegis agents``) and is
used to decide honestly whether a subtask can be executed or must be reported
as BLOCKED.

It also carries a **runtime state** (IDLE / THINKING / EXECUTING / ...) with
live counters, so the monitor and desktop console can report what each agent is
actually doing rather than guessing.

The design rule behind both is simple: an agent must never claim a capability
it does not implement, and never report a state it is not actually in.
"""

from __future__ import annotations

import enum
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..filesystem import FileSystem
from ..killswitch import Killswitch
from ..logging import OperationLog
from ..shell import Shell


class AgentState(str, enum.Enum):
    """Runtime states an agent can be in (operating model section 15)."""

    IDLE = "IDLE"
    THINKING = "THINKING"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    TESTING = "TESTING"
    WAITING = "WAITING"
    ERROR = "ERROR"
    STOPPED = "STOPPED"


@dataclass
class AgentResult:
    """What an agent returns from a unit of work."""

    agent: str
    action: str
    ok: bool
    summary: str
    data: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    duration_s: float = 0.0
    #: Process exit code the CLI should return. Mirrors the AegisError taxonomy
    #: so a policy denial is distinguishable from a genuine command failure.
    exit_code: int = 0
    error_class: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "action": self.action,
            "ok": self.ok,
            "summary": self.summary,
            "data": self.data,
            "errors": self.errors,
            "duration_s": round(self.duration_s, 4),
            "exit_code": self.exit_code,
            "error_class": self.error_class,
        }


@dataclass
class AgentMessage:
    """A structured message between agents (operating model section 19)."""

    sender: str
    recipient: str
    type: str
    status: str
    error: str = ""
    recommended_action: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "from": self.sender,
            "to": self.recipient,
            "type": self.type,
            "status": self.status,
        }
        if self.error:
            out["error"] = self.error
        if self.recommended_action:
            out["recommended_action"] = self.recommended_action
        if self.payload:
            out["payload"] = self.payload
        return out


@dataclass
class RuntimeStats:
    """Live counters shown by the monitor and the desktop agent bar."""

    state: AgentState = AgentState.IDLE
    current_task: str = ""
    current_tool: str = ""
    last_action: str = ""
    started_at: float = 0.0
    error_count: int = 0
    queue_length: int = 0
    completed: int = 0

    def elapsed_s(self) -> float:
        if self.state is AgentState.IDLE or not self.started_at:
            return 0.0
        return time.time() - self.started_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "current_task": self.current_task,
            "current_tool": self.current_tool,
            "last_action": self.last_action,
            "elapsed_s": round(self.elapsed_s(), 3),
            "error_count": self.error_count,
            "queue_length": self.queue_length,
            "completed": self.completed,
        }


@dataclass
class SelfModel:
    """An agent's honest description of itself."""

    name: str
    role: str
    capabilities: Tuple[str, ...]
    limitations: Tuple[str, ...]
    requires_llm: bool
    implemented_actions: Tuple[str, ...]
    status: str
    killswitch: bool
    runtime: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "capabilities": list(self.capabilities),
            "limitations": list(self.limitations),
            "requires_llm": self.requires_llm,
            "implemented_actions": list(self.implemented_actions),
            "status": self.status,
            "killswitch": self.killswitch,
            "runtime": self.runtime,
        }


class Agent:
    """Common context, self-model, and runtime state for every agent."""

    name = "agent"
    role = "generic agent"
    #: Actions this agent can actually carry out deterministically.
    capabilities: Tuple[str, ...] = ()
    #: Things this agent knowingly cannot do.
    limitations: Tuple[str, ...] = ()
    #: True if the agent needs an LLM for its primary actions.
    requires_llm: bool = False

    def __init__(
        self,
        config: Config,
        killswitch: Killswitch,
        *,
        log: Optional[OperationLog] = None,
        shell: Optional[Shell] = None,
        fs: Optional[FileSystem] = None,
        dry_run: bool = False,
    ) -> None:
        self.config = config
        self.killswitch = killswitch
        self.log = log
        self.shell = shell or Shell(config, killswitch, log, agent=self.name, dry_run=dry_run)
        self.fs = fs or FileSystem(config, killswitch, log, agent=self.name)
        self.dry_run = dry_run
        self.runtime = RuntimeStats()
        #: Messages this agent has emitted this session.
        self.outbox: List[AgentMessage] = []

    # -- self-model ----------------------------------------------------------
    def implemented_actions(self) -> Tuple[str, ...]:
        """Public methods that constitute this agent's real API."""
        reserved = {
            "status", "introspect", "implemented_actions", "can_handle",
            "send", "emit", "enter", "leave", "record_error",
        }
        found = []
        for attr in dir(self):
            if attr.startswith("_") or attr in reserved:
                continue
            member = getattr(self, attr)
            if inspect.ismethod(member) and not inspect.isbuiltin(member):
                found.append(attr)
        return tuple(sorted(found))

    def introspect(self) -> SelfModel:
        """Return this agent's self-model, including live runtime state."""
        halted = self.killswitch.is_engaged()
        state = AgentState.STOPPED if halted else self.runtime.state
        return SelfModel(
            name=self.name,
            role=self.role,
            capabilities=self.capabilities,
            limitations=self.limitations,
            requires_llm=self.requires_llm,
            implemented_actions=self.implemented_actions(),
            status=state.value,
            killswitch=halted,
            runtime=self.runtime.to_dict() if not halted else {**self.runtime.to_dict(), "state": state.value},
        )

    def can_handle(self, action: str) -> bool:
        """True only if *action* is in this agent's declared capabilities."""
        return action in self.capabilities

    def status(self) -> Dict[str, Any]:
        return self.introspect().to_dict()

    # -- runtime state -------------------------------------------------------
    def enter(self, state: AgentState, *, task: str = "", tool: str = "") -> None:
        """Transition into *state*, starting the elapsed clock if idle."""
        if self.runtime.state is AgentState.IDLE or not self.runtime.started_at:
            self.runtime.started_at = time.time()
        self.runtime.state = state
        if task:
            self.runtime.current_task = task
        if tool:
            self.runtime.current_tool = tool

    def leave(self, *, ok: bool = True) -> None:
        """Return to IDLE, tallying completion or error."""
        self.runtime.state = AgentState.IDLE
        self.runtime.current_tool = ""
        self.runtime.started_at = 0.0
        if ok:
            self.runtime.completed += 1
        else:
            self.runtime.error_count += 1

    def record_error(self, message: str) -> None:
        self.runtime.error_count += 1
        self.runtime.state = AgentState.ERROR
        self.runtime.last_action = message
        if self.log is not None:
            self.log.record(self.name, "error", status="error", result=message)

    # -- messaging -----------------------------------------------------------
    def send(
        self,
        recipient: str,
        type: str,
        *,
        status: str = "ok",
        error: str = "",
        recommended_action: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> AgentMessage:
        """Emit a structured message to another agent."""
        message = AgentMessage(
            sender=self.name,
            recipient=recipient,
            type=type,
            status=status,
            error=error,
            recommended_action=recommended_action,
            payload=payload or {},
        )
        self.outbox.append(message)
        if self.log is not None:
            self.log.record(
                self.name,
                "message",
                target=recipient,
                status=status,
                result=error or type,
                extra={"message": message.to_dict()},
            )
        return message

    # -- execution helper ----------------------------------------------------
    def _timed(self, action: str, fn, *args, **kwargs) -> AgentResult:
        """Run *fn* and wrap the outcome in an :class:`AgentResult`."""
        start = time.time()
        self.enter(AgentState.EXECUTING, task=action, tool=action)
        self.runtime.last_action = action
        try:
            summary, data = fn(*args, **kwargs)
            self.leave(ok=True)
            return AgentResult(
                agent=self.name,
                action=action,
                ok=True,
                summary=summary,
                data=data or {},
                duration_s=time.time() - start,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
            self.record_error(f"{type(exc).__name__}: {exc}")
            if self.log is not None:
                self.log.record(self.name, action, status="error", result=str(exc))
            error_class = getattr(exc, "error_class", None)
            return AgentResult(
                agent=self.name,
                action=action,
                ok=False,
                summary=f"{type(exc).__name__}: {exc}",
                errors=[str(exc)],
                duration_s=time.time() - start,
                exit_code=getattr(exc, "exit_code", 1),
                error_class=error_class.value if error_class is not None else "unknown",
            )
