"""Orchestrator — wires the four agents into one runtime.

The orchestrator owns the shared handles (config, killswitch, log, state) and
drives the leader → worker → verify loop. It is what the CLI, the API, and the
desktop console all talk to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .agents import BuilderAgent, ExecutorAgent, LeaderAgent, PentesterAgent
from .agents.base import Agent, AgentResult
from .config import Config
from .diagnostics import DiagnosticsReport, run_diagnostics
from .errors import AegisHaltedError
from .filesystem import FileSystem
from .killswitch import Killswitch
from .logging import OperationLog
from .recovery import RecoveryEngine
from .shell import Shell
from .state import StateStore, Task, TaskStatus


@dataclass
class Runtime:
    """The assembled agent runtime."""

    config: Config
    killswitch: Killswitch
    log: OperationLog
    state: StateStore
    shell: Shell
    fs: FileSystem
    agents: Dict[str, Agent] = field(default_factory=dict)
    recovery: Optional[RecoveryEngine] = None

    @classmethod
    def build(cls, config: Config, *, dry_run: bool = False) -> "Runtime":
        config.ensure_home()
        killswitch = Killswitch(config)
        log = OperationLog(config.log_path)
        state = StateStore(config)
        shell = Shell(config, killswitch, log, agent="executor", dry_run=dry_run)
        fs = FileSystem(config, killswitch, log, agent="builder")
        recovery = RecoveryEngine(
            killswitch,
            log,
            max_attempts=config.max_retries,
            backoff_s=config.retry_backoff_s,
            agent="leader",
        )

        agents: Dict[str, Agent] = {
            "leader": LeaderAgent(config, killswitch, log=log),
            "builder": BuilderAgent(config, killswitch, log=log),
            "pentester": PentesterAgent(config, killswitch, log=log),
            "executor": ExecutorAgent(config, killswitch, log=log),
        }
        return cls(
            config=config,
            killswitch=killswitch,
            log=log,
            state=state,
            shell=shell,
            fs=fs,
            agents=agents,
            recovery=recovery,
        )

    # -- convenience accessors ----------------------------------------------
    @property
    def leader(self) -> LeaderAgent:
        return self.agents["leader"]  # type: ignore[return-value]

    @property
    def builder(self) -> BuilderAgent:
        return self.agents["builder"]  # type: ignore[return-value]

    @property
    def pentester(self) -> PentesterAgent:
        return self.agents["pentester"]  # type: ignore[return-value]

    @property
    def executor(self) -> ExecutorAgent:
        return self.agents["executor"]  # type: ignore[return-value]

    def agent_status(self) -> List[Dict[str, Any]]:
        return [a.status() for a in self.agents.values()]

    def introspect(self) -> Dict[str, Any]:
        """Team-level self-model: who we are and what we can actually do."""
        return {
            "team": [a.introspect().to_dict() for a in self.agents.values()],
            "agents": sorted(self.agents),
            "killswitch_engaged": self.killswitch.is_engaged(),
            "killswitch_reason": self.killswitch.reason(),
            "model_provider": self.config.model_provider,
            "llm_configured": self.config.model_provider not in {"", "none"},
            "dry_run": self.shell.dry_run,
        }

    def diagnostics(self) -> DiagnosticsReport:
        """Run the startup health assessment."""
        return run_diagnostics(self.config)

    # -- observability -------------------------------------------------------
    def monitor(self) -> Dict[str, Any]:
        """Per-agent live state for the monitor and the desktop agent bar."""
        return {
            "agents": [
                {
                    "name": a.name,
                    "role": a.role,
                    **a.runtime.to_dict(),
                }
                for a in self.agents.values()
            ],
            "queue": [t.id for t in self.state.queue()],
            "queue_length": len(self.state.queue()),
            "interrupted": [t.id for t in self.state.interrupted()],
            "killswitch": self.killswitch.is_engaged(),
        }

    def messages(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Structured inter-agent messages emitted this session."""
        out: List[Dict[str, Any]] = []
        for agent in self.agents.values():
            out.extend(m.to_dict() for m in agent.outbox)
        return out[-limit:]

    # -- workflows -----------------------------------------------------------
    def plan(self, objective: str, scope: str = "") -> Dict[str, Any]:
        """Create a task and decompose it, without executing anything."""
        task = self.state.create(objective, scope)
        result = self.leader.decompose(task)
        self.state.save()
        return {"task_id": task.id, "plan": result.to_dict()}

    def run_task(self, objective: str, scope: str = "") -> Dict[str, Any]:
        """Plan and execute an objective end-to-end.

        Only subtasks the deterministic core can actually carry out are
        executed. A subtask that needs an LLM or an explicit command is marked
        BLOCKED with a reason rather than being reported as done. The run stops
        cleanly if the killswitch engages, marking the task INTERRUPTED.
        """
        task = self.state.create(objective, scope)
        return self._execute_task(task, scope)

    def resume_task(self, task_id: str) -> Dict[str, Any]:
        """Re-run an INTERRUPTED task from its existing plan."""
        task = self.state.get(task_id)
        for sub in task.subtasks:
            if sub.status in {"RUNNING", "BLOCKED"}:
                sub.status = TaskStatus.PENDING.value
                sub.result = ""
        self.state.save()
        return self._execute_task(task, task.scope)

    def _execute_task(self, task: Task, scope: str) -> Dict[str, Any]:
        self.state.update_status(task.id, TaskStatus.RUNNING)
        plan = self.leader.decompose(task) if not task.subtasks else self._existing_plan(task)

        results: List[AgentResult] = []
        blocked: List[str] = []
        interrupted = False
        try:
            for sub in list(task.subtasks):
                self.killswitch.guard("task step")
                agent = self.agents.get(sub.agent)
                if agent is None:
                    continue
                sub.status = TaskStatus.RUNNING.value
                outcome = self._dispatch(agent, sub, scope)
                results.append(outcome)
                if outcome.ok:
                    sub_status = "COMPLETE"
                else:
                    sub_status = "BLOCKED"
                    blocked.append(outcome.summary)
                self.state.complete_subtask(task.id, sub.id, outcome.summary, status=sub_status)
        except AegisHaltedError:
            interrupted = True
            self.state.update_status(task.id, TaskStatus.INTERRUPTED)

        if interrupted:
            report = self._report(
                task, plan, results, TaskStatus.INTERRUPTED,
                "killswitch engaged", blocked,
            )
        elif blocked:
            report = self._report(
                task, plan, results, TaskStatus.BLOCKED,
                f"{len(blocked)} subtask(s) could not be executed by the deterministic core",
                blocked,
            )
        else:
            verification = self.leader.verify(task, results)
            final = TaskStatus.COMPLETE if verification.ok else TaskStatus.FAILED
            self.state.update_status(task.id, final)
            report = self._report(task, plan, results, final, verification.summary, blocked)

        self.state.set_report(task.id, report)
        return report

    def _existing_plan(self, task: Task) -> AgentResult:
        return AgentResult(
            agent="leader",
            action="plan",
            ok=True,
            summary=f"resumed existing plan of {len(task.subtasks)} subtasks",
            data={
                "steps": [
                    {"description": s.description, "agent": s.agent, "risk": s.risk}
                    for s in task.subtasks
                ]
            },
        )

    def _dispatch(self, agent: Agent, sub, scope: str) -> AgentResult:
        """Route a subtask to a concrete, executable action.

        Returns a failed result with an explanatory summary when the step has
        no deterministic implementation — the caller turns that into BLOCKED.
        """
        if isinstance(agent, PentesterAgent) and scope:
            target = scope.split()[0]
            return agent.recon(target)
        return AgentResult(
            agent=agent.name,
            action="dispatch",
            ok=False,
            summary=(
                f"no deterministic action for '{sub.description}'; "
                f"provide an explicit command or configure an LLM"
            ),
            errors=[f"unimplemented deterministic step: {sub.description}"],
        )

    def _report(
        self,
        task: Task,
        plan: AgentResult,
        results: List[AgentResult],
        status: TaskStatus,
        verification: str,
        blocked: List[str],
    ) -> Dict[str, Any]:
        commands = [
            r.data["command"]
            for r in results
            if isinstance(r.data, dict) and r.data.get("command")
        ]
        files = [
            r.data.get("path")
            for r in results
            if isinstance(r.data, dict) and r.data.get("path")
        ]
        return {
            "OBJECTIVE": task.objective,
            "SCOPE": task.scope or "(unspecified)",
            "PLAN": plan.data.get("steps", []),
            "ACTIVE AGENTS": sorted({r.agent for r in results}),
            "ACTIONS": [r.action for r in results],
            "RESULTS": [r.summary for r in results],
            "ERRORS": [e for r in results for e in r.errors],
            "VERIFICATION": verification,
            "FILES CHANGED": [f for f in files if f],
            "COMMANDS EXECUTED": commands,
            "NEXT ACTION": self._next_action(status, blocked),
            "STATUS": status.value,
            "task_id": task.id,
        }

    @staticmethod
    def _next_action(status: TaskStatus, blocked: List[str]) -> str:
        if status is TaskStatus.COMPLETE:
            return "none"
        if status is TaskStatus.INTERRUPTED:
            return "release the killswitch and resume the task"
        if status is TaskStatus.BLOCKED:
            return "supply explicit commands or configure an LLM for the blocked steps"
        return "review failures"
