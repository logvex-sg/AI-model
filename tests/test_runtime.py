"""Tests for configuration, killswitch, state, and the orchestrator."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from aegis.config import Config, load_config
from aegis.errors import ConfigError, AegisHaltedError
from aegis.killswitch import ENV_VAR, Killswitch
from aegis.state import StateStore, TaskStatus

from . import RuntimeTestCase


class ConfigTests(unittest.TestCase):
    def test_defaults_are_valid(self) -> None:
        cfg = Config(home=Path("/tmp/kaliops-cfg"))
        self.assertEqual(cfg.api_host, "127.0.0.1")
        self.assertFalse(cfg.auto_approve_high_risk)

    def test_invalid_log_level_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            Config(home=Path("/tmp/x"), log_level="LOUD")

    def test_invalid_port_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            Config(home=Path("/tmp/x"), api_port=99999)

    def test_env_override_applies(self) -> None:
        os.environ["KALI_AEGIS_API_PORT"] = "9999"
        try:
            cfg = load_config()
            self.assertEqual(cfg.api_port, 9999)
        finally:
            del os.environ["KALI_AEGIS_API_PORT"]

    def test_unknown_override_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            load_config(overrides={"nonsense": 1})


class KillswitchTests(RuntimeTestCase):
    def test_disengaged_by_default(self) -> None:
        self.assertFalse(self.killswitch.is_engaged())
        self.assertIsNone(self.killswitch.reason())

    def test_engage_creates_sentinel(self) -> None:
        path = self.killswitch.engage()
        self.assertTrue(path.exists())
        self.assertTrue(self.killswitch.is_engaged())

    def test_release_removes_sentinel(self) -> None:
        self.killswitch.engage()
        self.assertTrue(self.killswitch.release())
        self.assertFalse(self.killswitch.is_engaged())

    def test_env_var_engages(self) -> None:
        os.environ[ENV_VAR] = "1"
        try:
            self.assertTrue(Killswitch(self.config).is_engaged())
        finally:
            del os.environ[ENV_VAR]

    def test_guard_raises_when_engaged(self) -> None:
        self.killswitch.engage()
        with self.assertRaises(AegisHaltedError):
            self.killswitch.guard("test")


class StateTests(RuntimeTestCase):
    def test_create_and_reload(self) -> None:
        store = StateStore(self.config)
        task = store.create("objective", "scope")
        reloaded = StateStore(self.config).get(task.id)
        self.assertEqual(reloaded.objective, "objective")

    def test_update_status(self) -> None:
        store = StateStore(self.config)
        task = store.create("o")
        store.update_status(task.id, TaskStatus.RUNNING)
        self.assertEqual(store.get(task.id).status, "RUNNING")


class OrchestratorTests(RuntimeTestCase):
    def test_agents_expose_self_models(self) -> None:
        rt = self.build_runtime()
        model = rt.introspect()
        names = {a["name"] for a in model["team"]}
        self.assertEqual(names, {"leader", "builder", "pentester", "executor"})
        for agent in model["team"]:
            self.assertIn("capabilities", agent)
            self.assertIn("limitations", agent)

    def test_declared_capabilities_match_implemented_actions(self) -> None:
        rt = self.build_runtime()
        for agent in rt.agents.values():
            model = agent.introspect()
            for cap in model.capabilities:
                self.assertIn(
                    cap,
                    model.implemented_actions,
                    f"{agent.name} claims {cap} but does not implement it",
                )

    def test_plan_produces_subtasks(self) -> None:
        rt = self.build_runtime()
        result = rt.plan("create a python monitoring tool")
        self.assertGreater(len(result["plan"]["data"]["steps"]), 0)

    def test_run_task_reports_blocked_for_unimplemented_steps(self) -> None:
        rt = self.build_runtime()
        report = rt.run_task("create a python project")
        self.assertIn(report["STATUS"], {"BLOCKED", "COMPLETE", "FAILED"})
        self.assertIn("NEXT ACTION", report)

    def test_run_task_interrupted_by_killswitch(self) -> None:
        rt = self.build_runtime()
        rt.killswitch.engage()
        report = rt.run_task("do something")
        self.assertEqual(report["STATUS"], "INTERRUPTED")

    def test_builder_scaffold_creates_files(self) -> None:
        rt = self.build_runtime()
        dest = self.home / "proj"
        result = rt.builder.scaffold_project(str(dest), "python")
        self.assertTrue(result.ok)
        self.assertTrue((dest / "src" / "main.py").exists())


if __name__ == "__main__":
    unittest.main()
