"""Desktop console (Tkinter) tests.

These drive the real widget tree, not mocks. They need a display; when Tk or a
DISPLAY is unavailable the whole module is skipped so the suite still runs on a
headless build machine without Tk installed.
"""

from __future__ import annotations

import importlib.util
import time
import unittest

from tests import RuntimeTestCase

#: Tkinter is an optional dependency; probe for it instead of importing, so the
#: module has no unused import and still skips cleanly on a headless build box.
_HAS_TK = importlib.util.find_spec("tkinter") is not None


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
        self.app.close()
        super().tearDown()

    def _pump(self, predicate, timeout: float = 8.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.root.update()
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    def test_every_view_renders(self) -> None:
        for view in ("thread", "team", "tasks", "security", "audit", "settings"):
            self.app._show(view)
            self.app.root.update()
            self.assertEqual(self.app._view, view)

    def test_agent_rail_lists_all_four_agents(self) -> None:
        self.app._refresh_agents()
        self.app.root.update()
        self.assertEqual(
            sorted(self.app._agent_rows),
            ["builder", "executor", "leader", "pentester"],
        )

    def test_submitting_an_objective_produces_a_report_turn(self) -> None:
        self.app._show("thread")
        self.app.root.update()
        self.app.entry.insert("1.0", "create a python tool")
        self.app._submit()
        self.assertTrue(
            self._pump(lambda: not self.app._busy),
            "objective did not finish in time",
        )
        self.assertEqual(len(self.app._turns), 2)
        self.assertEqual(self.app._turns[0]["kind"], "user")
        assistant = self.app._turns[1]
        self.assertEqual(assistant["kind"], "assistant")
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

    def test_objective_while_halted_is_refused_before_dispatch(self) -> None:
        self.app._show("thread")
        self.runtime.killswitch.engage("test")
        try:
            self.app.entry.insert("1.0", "do something")
            self.app._submit()
            self.app.root.update()
        finally:
            self.runtime.killswitch.release()
        # _submit refuses outright when the switch is engaged, so no turn is
        # appended and nothing is dispatched to a worker.
        self.assertEqual(self.app._turns, [])
        self.assertFalse(self.app._busy)

    def test_close_cancels_the_tick_loop(self) -> None:
        self.app.close()
        self.assertIsNone(self.app._after_id)
        # Rebuild so tearDown has a valid window to close.
        from aegis.gui import GlassApp

        self.app = GlassApp(self.runtime)
        self.app.root.update()

    def test_panels_are_visibly_lighter_than_the_backdrop(self) -> None:
        """The glass has to actually read as glass.

        An earlier revision blended the panel fill almost exactly onto the
        background (a luminance delta under 2), so the layout looked like one
        flat field. This pins a real separation so a future palette tweak that
        flattens it again fails here instead of shipping.
        """
        from aegis import liquid as L

        behind = L.background_at(self.app.HEIGHT * 0.45, self.app.HEIGHT)
        for name, tint, alpha in (
            ("left", L.SURFACE_HIGH, 0.72),
            ("center", L.SURFACE_HIGH, 0.62),
            ("right", L.SURFACE_HIGH, 0.68),
        ):
            panel = L.blend(behind, tint, alpha)

            def lum(rgb):
                return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]

            delta = lum(panel) - lum(behind)
            self.assertGreater(
                delta, 8.0,
                f"{name} panel is only {delta:.1f} luminance above the background",
            )

    def test_nav_highlights_the_active_view(self) -> None:
        from aegis import liquid as L

        self.app._show("audit")
        self.app.root.update()
        self.assertEqual(self.app._view, "audit")
        # Exactly the active row is painted in the accent colour; the rest stay
        # dim. This is the only visual cue for which view is on screen.
        accent = L.to_hex(L.ACCENT)
        hot = [k for k, b in self.app._nav_buttons.items() if b.cget("fg") == accent]
        self.assertEqual(hot, ["audit"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
