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
from .ai import AIAgent
from .config import Config
from .diagnostics import DiagnosticsReport, run_diagnostics
from .errors import AegisHaltedError
from .filesystem import FileSystem
from .killswitch import Killswitch
from .llm import LLMClient, resolve_base_url
from .logging import OperationLog
from .privilege import PrivilegeManager
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
    privileges: Optional[PrivilegeManager] = None
    ai: Optional[AIAgent] = None

    @classmethod
    def build(
        cls,
        config: Config,
        *,
        dry_run: bool = False,
        interactive: bool = False,
    ) -> "Runtime":
        config.ensure_home()
        killswitch = Killswitch(config)
        log = OperationLog(config.log_path)
        state = StateStore(config)
        privileges = PrivilegeManager(
            allow_root=config.allow_root, interactive=interactive
        )
        shell = Shell(
            config,
            killswitch,
            log,
            agent="executor",
            dry_run=dry_run,
            privileges=privileges,
        )
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

        client = LLMClient(
            base_url=resolve_base_url(config.model_provider, config.model_base_url),
            model=config.model_name if config.model_name != "none" else "",
            api_key=config.model_api_key,
            timeout=config.model_timeout,
            temperature=config.model_temperature,
            max_tokens=config.model_max_tokens,
        )
        ai = AIAgent(
            client=client,
            execute_command=shell.run,
            read_file=lambda p: fs.read(p),
            write_file=fs.write,
            list_dir=fs.list,
            max_iterations=config.max_agent_iterations,
        )

        return cls(
            config=config,
            killswitch=killswitch,
            log=log,
            state=state,
            shell=shell,
            fs=fs,
            agents=agents,
            recovery=recovery,
            privileges=privileges,
            ai=ai,
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
            "llm_configured": self.llm_available,
            "privilege": self.privileges.report().to_dict() if self.privileges else {},
            "dry_run": self.shell.dry_run,
        }

    @property
    def llm_available(self) -> bool:
        """Whether the reasoning loop can actually reach a model."""
        return bool(self.ai and self.ai.available and self.config.llm_ready)

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
    def _topic_plan(self, objective: str, scope: str) -> Dict[str, Any]:
        """Build a plan skeleton, preferring the model when one is available.

        Kept for callers that want the plan without running anything.
        """
        task = self.state.create(objective, scope)
        result = self.leader.decompose(task)
        self.state.save()
        return {"task_id": task.id, "plan": result.to_dict()}

    def plan(self, objective: str, scope: str = "") -> Dict[str, Any]:
        """Create a task and decompose it, without executing anything."""
        return self._topic_plan(objective, scope)

    def act(self, objective: str, scope: str = "") -> Dict[str, Any]:
        """Pursue an objective with the reasoning loop, executing real actions.

        This is the AI path. The model chooses actions; every one runs through
        the same policy, privilege, killswitch, and audit layers a CLI command
        would. When no model is configured the deterministic task runner is used
        instead, so the method always returns a truthful report of what happened.
        """
        if self.killswitch.is_engaged():
            return {
                "OBJECTIVE": objective,
                "SCOPE": scope or "(unspecified)",
                "MODE": "halted",
                "PLAN": [], "ACTIVE AGENTS": [], "ACTIONS": [], "RESULTS": [],
                "ERRORS": [self.killswitch.reason() or "killswitch engaged"],
                "VERIFICATION": "nothing ran", "FILES CHANGED": [],
                "COMMANDS EXECUTED": [], "ELEVATED": [],
                "NEXT ACTION": "release the killswitch and retry",
                "STATUS": "INTERRUPTED",
                "STEPS": [],
            }
        if not self.llm_available:
            report = self._deterministic(objective, scope)
            report["MODE"] = "deterministic (no LLM configured)"
            return report

        run = self.ai.run(objective, scope=scope)
        return self._ai_report(objective, scope, run)

    def _ai_report(self, objective: str, scope: str, run) -> Dict[str, Any]:
        commands = [
            s.arguments.get("command", "")
            for s in run.steps
            if s.tool == "run_command" and s.arguments.get("command")
        ]
        files = [
            s.arguments.get("path", "")
            for s in run.steps
            if s.tool in {"write_file", "read_file"} and s.arguments.get("path")
        ]
        results = [
            f"{s.tool}: {'ok' if s.ok else 'FAILED'}" + (f" — {s.detail}" if s.detail else "")
            for s in run.steps
        ]
        elevated = [
            s.arguments.get("executed") or s.arguments.get("command", "")
            for s in run.steps
            if s.tool == "run_command" and s.arguments.get("elevated")
        ]
        errors = [s.detail for s in run.steps if not s.ok and s.detail]
        if run.error:
            errors.append(run.error)

        if run.ok:
            status = "COMPLETE"
        elif errors:
            status = "BLOCKED"
        else:
            status = "FAILED"

        return {
            "OBJECTIVE": objective,
            "SCOPE": scope or "(unspecified)",
            "MODE": f"AI ({self.config.model_name} via {self.config.model_provider})",
            "PLAN": [s.tool for s in run.steps if s.kind == "tool"],
            "ACTIVE AGENTS": ["leader", "executor"],
            "ACTIONS": results,
            "RESULTS": [run.answer] if run.answer else [],
            "ERRORS": errors,
            "VERIFICATION": (
                f"{run.iterations} model turns, {run.tokens} tokens, "
                f"{len(run.steps)} actions"
            ),
            "FILES CHANGED": sorted({f for f in files if f}),
            "COMMANDS EXECUTED": commands,
            "ELEVATED": elevated,
            "NEXT ACTION": "none" if run.ok else "review the errors above and retry",
            "STATUS": status,
            "STEPS": [s.to_dict() for s in run.steps],
        }

    def _deterministic(self, objective: str, scope: str) -> Dict[str, Any]:
        task = self.state.create(objective, scope)
        return self._execute_task(task, scope)

    def run_task(self, objective: str, scope: str = "") -> Dict[str, Any]:
        """Pursue an objective using whichever engine is available.

        Delegates to the reasoning loop when a model is configured and to the
        deterministic runner otherwise.
        """
        return self.act(objective, scope)

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
