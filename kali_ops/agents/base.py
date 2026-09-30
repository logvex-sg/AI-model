"""Agent base class, self-model, and shared result type.

Every agent carries an explicit **self-model**: the capabilities it actually
implements, the limitations it knows about, and whether it needs an LLM to
function. The self-model is introspectable at runtime (``kali-ops agents``)
and is used to decide honestly whether a subtask can be executed or must be
reported as BLOCKED.

The design rule behind the self-model is simple: an agent must never claim a
capability it does not implement. If a step needs reasoning the deterministic
core cannot supply, the agent says so instead of pretending.
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..filesystem import FileSystem
from ..killswitch import Killswitch
from ..logging import OperationLog
from ..shell import Shell


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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "action": self.action,
            "ok": self.ok,
            "summary": self.summary,
            "data": self.data,
            "errors": self.errors,
            "duration_s": round(self.duration_s, 4),
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
        }


class Agent:
    """Common context and self-model shared by every specialised agent."""

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

    # -- self-model ----------------------------------------------------------
    def implemented_actions(self) -> Tuple[str, ...]:
        """Public methods that constitute this agent's real API."""
        reserved = {"status", "introspect", "implemented_actions", "can_handle"}
        found = []
        for attr in dir(self):
            if attr.startswith("_") or attr in reserved:
                continue
            member = getattr(self, attr)
            if inspect.ismethod(member) and not inspect.isbuiltin(member):
                found.append(attr)
        return tuple(sorted(found))

    def introspect(self) -> SelfModel:
        """Return this agent's self-model."""
        return SelfModel(
            name=self.name,
            role=self.role,
            capabilities=self.capabilities,
            limitations=self.limitations,
            requires_llm=self.requires_llm,
            implemented_actions=self.implemented_actions(),
            status="halted" if self.killswitch.is_engaged() else "ready",
            killswitch=self.killswitch.is_engaged(),
        )

    def can_handle(self, action: str) -> bool:
        """True only if *action* is in this agent's declared capabilities."""
        return action in self.capabilities

    def status(self) -> Dict[str, Any]:
        return self.introspect().to_dict()

    # -- execution helper ----------------------------------------------------
    def _timed(self, action: str, fn, *args, **kwargs) -> AgentResult:
        """Run *fn* and wrap the outcome in an :class:`AgentResult`."""
        start = time.time()
        try:
            summary, data = fn(*args, **kwargs)
            return AgentResult(
                agent=self.name,
                action=action,
                ok=True,
                summary=summary,
                data=data or {},
                duration_s=time.time() - start,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
            if self.log is not None:
                self.log.record(self.name, action, status="error", result=str(exc))
            return AgentResult(
                agent=self.name,
                action=action,
                ok=False,
                summary=f"{type(exc).__name__}: {exc}",
                errors=[str(exc)],
                duration_s=time.time() - start,
            )
