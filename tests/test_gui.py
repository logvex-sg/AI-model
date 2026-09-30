"""Desktop console (Tkinter) tests.

These drive the real widget tree, not mocks. They need a display; when Tk or a
DISPLAY is unavailable the whole module is skipped so the suite still runs on a
headless build machine without Tk installed.
"""

from __future__ import annotations

import time
import unittest

from tests import RuntimeTestCase

try:  # pragma: no cover - depends on host libraries
    import tkinter  # noqa: F401

    _HAS_TK = True
except ImportError:  # pragma: no cover
    _HAS_TK = False


@unittest.skipUnless(_HAS_TK, "Tkinter is not installed")
class GlassGuiTest(RuntimeTestCase):
    def setUp(self) -> None:
        super().setUp()
        from aegis.gui import GlassApp

        self.runtime = self.build_runtime()
        try:
            self.app = GlassApp(self.runtime)
        except Exception as exc:  # pragma: no cover - no display
            self.skipTest(f"no usable display: {exc}")
        self.app.root.update()

    def tearDown(self) -> None:
        try:
            self.app.root.destroy()
        except Exception:  # pragma: no cover - already gone
            pass
        super().tearDown()

    def _pump(self, predicate, timeout: float = 6.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.root.update()
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    def test_every_view_renders(self) -> None:
        for view in ("thread", "team", "tasks", "security", "logs", "settings"):
            self.app._show(view)
            self.app.root.update()
            self.assertEqual(self.app._view, view)

    def test_agent_rail_lists_all_four_agents(self) -> None:
        self.app._refresh_agent_rail()
        self.app.root.update()
        self.assertEqual(
            sorted(self.app._agent_rows),
            ["builder", "executor", "leader", "pentester"],
        )

    def test_submitting_an_objective_produces_a_report_turn(self) -> None:
        self.app._show("thread")
        self.app.root.update()
        self.app._entry.insert("1.0", "create a python tool")
        self.app._submit()
        self.assertTrue(
            self._pump(lambda: not self.app._busy),
            "objective did not finish in time",
        )
        self.assertEqual(len(self.app._turns), 2)
        self.assertEqual(self.app._turns[0]["kind"], "user")
        assistant = self.app._turns[1]
        self.assertEqual(assistant["kind"], "assistant")
        self.assertIn("report", assistant)
        self.assertIn(
            assistant["status"], {"COMPLETE", "BLOCKED", "FAILED", "INTERRUPTED"}
        )

    def test_killswitch_toggle_from_the_console(self) -> None:
        self.app._engage_kill()
        self.app.root.update()
        self.assertTrue(self.runtime.killswitch.is_engaged())
        self.app._release_kill()
        self.app.root.update()
        self.assertFalse(self.runtime.killswitch.is_engaged())

    def test_submit_is_a_noop_while_busy(self) -> None:
        self.app._busy = True
        before = len(self.app._turns)
        self.app._submit()
        self.assertEqual(len(self.app._turns), before)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
