"""LEADER — project coordinator, planner, and verifier.

The leader turns a free-text objective into an ordered set of subtasks, each
assigned to the agent best suited to it, then verifies the assembled result.

Planning is deterministic by default: keyword and intent detection map an
objective onto a workflow template. If an LLM is configured the leader can
delegate planning to it, but the built-in planner keeps the system fully
functional offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..state import SubTask, Task, TaskStatus
from .base import Agent, AgentResult, AgentState


@dataclass
class PlanStep:
    description: str
    agent: str
    risk: str = "LOW"


# Workflow templates keyed by intent. Each entry is (keywords, steps).
_TEMPLATES: List[tuple] = [
    (
        ("repo", "repository", "project", "scaffold", "create a", "build a"),
        [
            PlanStep("Inspect the existing repository structure", "leader"),
            PlanStep("Create the project layout and files", "builder"),
            PlanStep("Build and compile the project", "builder", "MEDIUM"),
            PlanStep("Run the test suite", "builder"),
            PlanStep("Review changes and produce a summary report", "leader"),
        ],
    ),
    (
        ("pentest", "penetration", "vulnerab", "scan", "recon", "security assessment"),
        [
            PlanStep("Confirm the authorized scope for testing", "pentester"),
            PlanStep("Enumerate services on the authorized target", "pentester", "HIGH"),
            PlanStep("Identify technologies and attack surface", "pentester", "HIGH"),
            PlanStep("Validate findings safely and record evidence", "pentester", "HIGH"),
            PlanStep("Generate remediation guidance", "pentester"),
        ],
    ),
    (
        ("monitor", "network", "observ"),
        [
            PlanStep("Define the monitoring objective and metrics", "leader"),
            PlanStep("Implement the monitoring tool", "builder"),
            PlanStep("Test the tool against a local target", "builder"),
            PlanStep("Document usage and deployment", "builder"),
        ],
    ),
    (
        ("container", "docker", "containerize"),
        [
            PlanStep("Author the container definition", "builder"),
            PlanStep("Build the container image", "builder", "MEDIUM"),
            PlanStep("Smoke-test the container", "builder", "MEDIUM"),
        ],
    ),
    (
        ("ci", "pipeline", "workflow", "github action"),
        [
            PlanStep("Design the CI pipeline stages", "builder"),
            PlanStep("Write the pipeline configuration", "builder"),
            PlanStep("Validate the configuration syntax", "builder"),
        ],
    ),
]

_DEFAULT_STEPS = [
    PlanStep("Clarify the objective and constraints", "leader"),
    PlanStep("Implement the requested change", "builder"),
    PlanStep("Verify the result", "leader"),
]


class LeaderAgent(Agent):
    """Coordinates the team: plan, delegate, verify."""

    name = "leader"
    role = "project coordinator, task decomposition, verification"
    capabilities = ("plan", "decompose", "verify")
    limitations = (
        "cannot implement code itself; delegates to the builder",
        "cannot execute shell commands; delegates to the executor",
        "planning is template-based, not open-ended reasoning",
    )
    requires_llm = False

    def plan(self, objective: str, scope: str = "") -> List[PlanStep]:
        """Decompose an objective into ordered :class:`PlanStep`s."""
        self.enter(AgentState.THINKING, task=objective, tool="plan")
        try:
            lowered = objective.lower()
            steps: List[PlanStep] = []
            for keywords, template in _TEMPLATES:
                if any(k in lowered for k in keywords):
                    steps.extend(template)
            if not steps:
                steps = list(_DEFAULT_STEPS)
            return steps
        finally:
            self.leave()

    def decompose(self, task: Task) -> AgentResult:
        """Attach a plan (as subtasks) to *task* and return the plan."""

        def _run() -> tuple:
            self.enter(AgentState.PLANNING, task=task.objective, tool="decompose")
            steps = self.plan(task.objective, task.scope)
            for step in steps:
                task.subtasks.append(
                    SubTask.new(step.description, step.agent, risk=step.risk)
                )
            self._record("plan", task, f"{len(steps)} subtasks")
            # Tell each agent what it has been assigned, in the structured
            # message form the operating model specifies.
            for step in steps:
                if step.agent != self.name:
                    self.send(
                        step.agent,
                        "assignment",
                        payload={"description": step.description, "risk": step.risk, "task": task.id},
                    )
            data = {
                "steps": [
                    {"description": s.description, "agent": s.agent, "risk": s.risk}
                    for s in steps
                ]
            }
            return f"decomposed into {len(steps)} subtasks", data

        return self._timed("plan", _run)

    def verify(self, task: Task, results: List[AgentResult]) -> AgentResult:
        """Check that every subtask produced a successful result."""

        def _run() -> tuple:
            self.enter(AgentState.THINKING, task=task.objective, tool="verify")
            failed = [r for r in results if not r.ok]
            errors = [e for r in failed for e in r.errors]
            ok = not failed
            summary = (
                "all subtasks succeeded"
                if ok
                else f"{len(failed)} of {len(results)} subtasks failed"
            )
            self._record("verify", task, summary)
            if failed:
                # The leader does not silently accept a bad result: it reports
                # the failure and a recommended action back up the chain.
                self.send(
                    "operator",
                    "verification",
                    status="failed",
                    error=summary,
                    recommended_action="review failed subtasks before proceeding",
                    payload={"failed": [r.action for r in failed]},
                )
            return summary, {"failed": [r.action for r in failed], "errors": errors}

        return self._timed("verify", _run)

    def _record(self, operation: str, task: Task, result: str) -> None:
        if self.log is not None:
            self.log.record(self.name, operation, target=task.id, result=result, status="ok")
