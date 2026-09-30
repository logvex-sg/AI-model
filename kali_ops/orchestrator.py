"""Orchestrator — wires the four agents into one runtime.

The orchestrator owns the shared handles (config, killswitch, log, state) and
drives the leader → worker → verify loop. It is what the CLI, the API, and the
desktop app all talk to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .agents import BuilderAgent, ExecutorAgent, LeaderAgent, PentesterAgent
from .agents.base import Agent, AgentResult
from .config import Config
from .errors import KillswitchActive
from .filesystem import FileSystem
from .killswitch import Killswitch
from .logging import OperationLog
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

    @classmethod
    def build(cls, config: Config, *, dry_run: bool = False) -> "Runtime":
        config.ensure_home()
        killswitch = Killswitch(config)
        log = OperationLog(config.log_path)
        state = StateStore(config)
        shell = Shell(config, killswitch, log, agent="executor", dry_run=dry_run)
        fs = FileSystem(config, killswitch, log, agent="builder")

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
        self.state.update_status(task.id, TaskStatus.RUNNING)
        plan = self.leader.decompose(task)

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
        except KillswitchActive:
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
        return {
            "OBJECTIVE": task.objective,
            "SCOPE": task.scope or "(unspecified)",
            "PLAN": plan.data.get("steps", []),
            "ACTIVE AGENTS": sorted({r.agent for r in results}),
            "ACTIONS": [r.action for r in results],
            "RESULTS": [r.summary for r in results],
            "ERRORS": [e for r in results for e in r.errors],
            "VERIFICATION": verification,
            "FILES CHANGED": [],
            "COMMANDS EXECUTED": [],
            "NEXT ACTION": self._next_action(status, blocked),
            "STATUS": status.value,
            "task_id": task.id,
        }

    @staticmethod
    def _next_action(status: TaskStatus, blocked: List[str]) -> str:
        if status is TaskStatus.COMPLETE:
            return "none"
        if status is TaskStatus.INTERRUPTED:
            return "release the killswitch and re-run"
        if status is TaskStatus.BLOCKED:
            return "supply explicit commands or configure an LLM for the blocked steps"
        return "review failures"
