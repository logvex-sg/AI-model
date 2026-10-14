"""Tests for the recovery engine, diagnostics, agent runtime state, and messaging."""

from __future__ import annotations

import os
import unittest

from aegis.agents.base import AgentState
from aegis.diagnostics import MISSING, OK, WARN, run_diagnostics
from aegis.errors import CommandError, ErrorClass, RecoveryExhausted, RiskDenied
from aegis.recovery import RecoveryEngine

from . import RuntimeTestCase


class RecoveryTests(RuntimeTestCase):
    """The recovery loop must retry, stop, and classify correctly."""

    def _engine(self, **kwargs) -> RecoveryEngine:
        return RecoveryEngine(self.killswitch, self.log, **kwargs)

    def test_success_on_first_attempt(self) -> None:
        engine = self._engine(max_attempts=3)
        report = engine.run("op", lambda: (True, "done"))
        self.assertTrue(report.attempts[-1].ok)
        self.assertFalse(report.recovered)  # succeeded first try, not a recovery
        self.assertEqual(report.count, 1)

    def test_retries_then_recovers(self) -> None:
        calls = {"n": 0}

        def flaky() -> tuple:
            calls["n"] += 1
            if calls["n"] < 3:
                # Make each failure distinct so the spin-guard does not trip.
                raise CommandError(f"transient failure {calls['n']}", returncode=124)
            return True, "ok"

        engine = self._engine(max_attempts=5, backoff_s=0.0)
        report = engine.run("flaky", flaky)
        self.assertTrue(report.attempts[-1].ok)
        self.assertTrue(report.recovered)
        self.assertEqual(calls["n"], 3)

    def test_spin_guard_stops_identical_failures(self) -> None:
        # A retryable failure whose message never changes must not burn the
        # whole budget: the engine stops rather than spinning.
        def same() -> tuple:
            raise CommandError("same transient error", returncode=124)

        engine = self._engine(max_attempts=10, backoff_s=0.0)
        report = engine.run("spin", same)
        self.assertLess(report.count, 10)
        self.assertEqual(report.attempts[-1].strategy, "spin-guard")

    def test_abort_re_raises_original_exception(self) -> None:
        # A permission error is not retryable; the typed exception must survive
        # so its exit code is preserved.
        engine = self._engine(max_attempts=3)
        with self.assertRaises(RiskDenied) as ctx:
            engine.run("denied", lambda: (_ for _ in ()).throw(RiskDenied("nope")))
        self.assertEqual(ctx.exception.exit_code, 5)

    def test_exhausted_raises_recovery_exhausted(self) -> None:
        # Distinct transient messages force genuine retries until the budget ends.
        counter = {"n": 0}

        def distinct() -> tuple:
            counter["n"] += 1
            raise CommandError(f"timeout variant {counter['n']}", returncode=124)

        engine = self._engine(max_attempts=2, backoff_s=0.0)
        with self.assertRaises(RecoveryExhausted):
            engine.run("distinct", distinct)

    def test_classify_returncode(self) -> None:
        engine = self._engine()
        self.assertEqual(engine.classify_exception(CommandError("t", returncode=124)), ErrorClass.TRANSIENT)
        self.assertEqual(engine.classify_exception(CommandError("e", returncode=127)), ErrorClass.ENVIRONMENT)
        self.assertEqual(engine.classify_exception(CommandError("p", returncode=13)), ErrorClass.PERMISSION)

    def test_strategy_map(self) -> None:
        engine = self._engine()
        self.assertEqual(engine.strategy_for(ErrorClass.TRANSIENT), "retry")
        self.assertEqual(engine.strategy_for(ErrorClass.PERMISSION), "abort")
        self.assertEqual(engine.strategy_for(ErrorClass.ENVIRONMENT), "replan")


class DiagnosticsTests(RuntimeTestCase):
    """The health assessment must report real, structured results."""

    def test_report_structure(self) -> None:
        report = run_diagnostics(self.config)
        names = {c.component for c in report.checks}
        for expected in {"OS", "PYTHON", "GUI", "GIT", "NETWORK", "KILLSWITCH", "MODEL"}:
            self.assertIn(expected, names)

    def test_gui_check_reports_a_concrete_status(self) -> None:
        """The console toolkit must be diagnosed, not discovered on failure.

        A missing tkinter used to be invisible until `aegis gui` died. The
        check has to say which of the three states the host is in: no tkinter,
        tkinter but headless, or a working display.
        """
        report = run_diagnostics(self.config)
        gui = next(c for c in report.checks if c.component == "GUI")
        self.assertIn(gui.status, {OK, WARN, MISSING})
        self.assertTrue(gui.detail)
        headless = not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        if headless and gui.status != MISSING:
            self.assertIn("DISPLAY", gui.detail)

    def test_every_check_has_a_status(self) -> None:
        report = run_diagnostics(self.config)
        for check in report.checks:
            self.assertIn(check.status, {OK, WARN, MISSING})

    def test_renders_a_table(self) -> None:
        rendered = run_diagnostics(self.config).render()
        self.assertIn("COMPONENT", rendered)
        self.assertIn("STATUS", rendered)

    def test_required_failure_marks_not_ok(self) -> None:
        # A missing required component must fail the report.
        report = run_diagnostics(self.config)
        report.checks.append(
            type(report.checks[0])(component="FAKE", status=MISSING, detail="x", required=True)
        )
        self.assertFalse(report.ok)


class AgentStateTests(RuntimeTestCase):
    """Agents must report the state they are actually in."""

    def test_agents_start_idle(self) -> None:
        rt = self.build_runtime()
        for agent in rt.agents.values():
            self.assertIs(agent.runtime.state, AgentState.IDLE)

    def test_plan_transitions_and_returns_to_idle(self) -> None:
        rt = self.build_runtime()
        rt.leader.plan("create a repository")
        self.assertIs(rt.leader.runtime.state, AgentState.IDLE)
        self.assertGreaterEqual(rt.leader.runtime.completed, 1)

    def test_monitor_reports_all_agents(self) -> None:
        rt = self.build_runtime()
        monitor = rt.monitor()
        self.assertEqual(len(monitor["agents"]), 4)
        self.assertIn("queue_length", monitor)

    def test_killswitch_forces_stopped_state(self) -> None:
        rt = self.build_runtime()
        rt.killswitch.engage("test")
        for agent in rt.agents.values():
            self.assertEqual(agent.introspect().status, "STOPPED")

    def test_error_is_recorded_with_class(self) -> None:
        rt = self.build_runtime()
        result = rt.executor.execute("rm -rf /")
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 5)
        self.assertEqual(result.error_class, "permission")


class MessagingTests(RuntimeTestCase):
    """Inter-agent messages must be structured and observable."""

    def test_leader_emits_assignments(self) -> None:
        rt = self.build_runtime()
        rt.plan("create a repository")
        messages = rt.messages()
        self.assertTrue(messages)
        assignment = [m for m in messages if m["type"] == "assignment"]
        self.assertTrue(assignment)
        for msg in assignment:
            for field in ("from", "to", "type", "status"):
                self.assertIn(field, msg)

    def test_task_report_includes_commands_and_files(self) -> None:
        rt = self.build_runtime()
        report = rt.run_task("create a repository")
        for key in (
            "OBJECTIVE", "SCOPE", "PLAN", "ACTIVE AGENTS", "ACTIONS", "RESULTS",
            "ERRORS", "VERIFICATION", "FILES CHANGED", "COMMANDS EXECUTED",
            "NEXT ACTION", "STATUS",
        ):
            self.assertIn(key, report)


class RiskMetadataTests(RuntimeTestCase):
    """Risk assessments carry reversibility and privilege facts."""

    def test_irreversible_command_flagged(self) -> None:
        from aegis.risk import classify

        self.assertFalse(classify("rm -rf /tmp/x").reversible)
        self.assertTrue(classify("ls -la").reversible)

    def test_root_detected_in_sudo(self) -> None:
        from aegis.risk import classify

        self.assertTrue(classify("sudo apt-get update").requires_root)

    def test_offensive_tool_is_high_and_categorised(self) -> None:
        from aegis.risk import classify

        verdict = classify("nmap 10.0.0.5")
        self.assertEqual(verdict.category, "offensive")
        self.assertTrue(verdict.needs_confirmation)


if __name__ == "__main__":
    unittest.main()
