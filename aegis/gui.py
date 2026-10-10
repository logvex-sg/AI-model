"""Desktop console — a liquid-glass AI assistant surface (Tkinter).

Conversation-first, Codex-style: you describe an objective, the reasoning loop
works on it with real tools, and the thread streams what is happening. Raw
commands and file output stay inside their own monospace cards so the prose
stays readable.

The glass is composited by :mod:`aegis.liquid` — an animated liquid background,
frosted panels with lit top bevels, and a specular sweep across the hero.

Threading note: the objective runs on a worker thread, which must never touch a
widget. It hands its result to the main loop through a queue that ``_tick()``
drains. Calling ``after()`` from the worker raises
``main thread is not in main loop``.
"""

from __future__ import annotations

import queue
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple

from . import liquid as L
from .config import Config
from .orchestrator import Runtime
from .security.scope import local_addresses

_TK_HELP = (
    "Tkinter is unavailable. Install the Tk libraries for your platform, e.g.\n"
    "  Debian/Ubuntu/Kali:  sudo apt-get install -y python3-tk\n"
    "  Fedora:              sudo dnf install -y python3-tkinter\n"
    "  macOS (Homebrew):    brew install python-tk\n"
    "Then re-run `aegis gui`."
)

_STATE_COLOR: Dict[str, L.RGB] = {
    "IDLE": L.TEXT_FAINT,
    "THINKING": L.ACCENT,
    "PLANNING": L.ACCENT_WARM,
    "EXECUTING": L.ACCENT_GREEN,
    "TESTING": L.ACCENT_AMBER,
    "WAITING": L.ACCENT,
    "ERROR": L.ACCENT_RED,
    "STOPPED": L.ACCENT_RED,
}

_VIEWS = [
    ("thread", "Thread", "your conversation with the team"),
    ("team", "Team", "each agent's self-model"),
    ("tasks", "Tasks", "objectives and their state"),
    ("security", "Security", "authorized scope and posture"),
    ("audit", "Audit", "append-only operation log"),
    ("settings", "Settings", "effective configuration"),
]


class GlassApp:
    """The console window."""

    WIDTH = 1440
    HEIGHT = 900
    RAIL_W = 208
    SIDE_W = 244

    def __init__(self, runtime: Runtime, config: Optional[Config] = None) -> None:
        import tkinter as tk

        self.tk = tk
        self.runtime = runtime
        self.config = config or runtime.config

        self.root = tk.Tk()
        self.root.title("KALI-AEGIS")
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.root.minsize(1100, 680)
        self.root.configure(bg=L.to_hex(L.BACKGROUND_TOP))

        self._view = "thread"
        self._busy = False
        self._turns: List[Dict[str, Any]] = []
        self._results: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._agent_rows: Dict[str, Dict[str, Any]] = {}
        self._nav_buttons: Dict[str, Any] = {}
        self._phase = 0.0
        self._after_id: Optional[str] = None

        self._build_background()
        self._build_shell()
        self._show("thread")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._tick()

    # ------------------------------------------------------------------ #
    # background glass
    # ------------------------------------------------------------------ #
    def _build_background(self) -> None:
        tk = self.tk
        self.canvas = tk.Canvas(
            self.root, width=self.WIDTH, height=self.HEIGHT,
            highlightthickness=0, bd=0, bg=L.to_hex(L.BACKGROUND_TOP),
        )
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.liquid = L.LiquidBackground(self.canvas, self.WIDTH, self.HEIGHT)
        self.liquid.paint_static()
        self.liquid.paint_blobs()
        self.root.bind("<Configure>", self._on_resize)

    def _on_resize(self, event) -> None:
        if event.widget is not self.root:
            return
        self.liquid.width = event.width
        self.liquid.height = event.height
        self.canvas.configure(width=event.width, height=event.height)
        self.liquid.paint_static()
        self.liquid.paint_blobs()

    # ------------------------------------------------------------------ #
    # shell layout
    # ------------------------------------------------------------------ #
    def _panel(self, parent, tint, alpha):
        """A frosted Frame: blended fill plus a lit 1px top bevel."""
        tk = self.tk
        bg = L.to_hex(L.blend(L.BACKGROUND_BOTTOM, tint, alpha))
        frame = tk.Frame(parent, bg=bg)
        bevel = tk.Frame(frame, bg=L.to_hex(L.BEVEL_TOP), height=1)
        bevel.pack(fill="x", side="top")
        body = tk.Frame(frame, bg=bg)
        body.pack(fill="both", expand=True)
        frame.body = body  # type: ignore[attr-defined]
        frame.bg = bg  # type: ignore[attr-defined]
        return frame

    def _build_shell(self) -> None:
        self.left = self._panel(self.root, L.SURFACE_HIGH, 0.62)
        self.left.place(x=14, y=14, width=self.RAIL_W, relheight=1.0, height=-28)

        self.center = self._panel(self.root, L.SURFACE_HIGH, 0.5)
        self.center.place(
            x=self.RAIL_W + 28, y=14,
            relwidth=1.0, width=-(self.RAIL_W + self.SIDE_W + 56),
            relheight=1.0, height=-28,
        )

        self.right = self._panel(self.root, L.SURFACE_HIGH, 0.58)
        self.right.place(relx=1.0, x=-(self.SIDE_W + 14), y=14, width=self.SIDE_W,
                         relheight=1.0, height=-28)

        self._build_nav(self.left.body)
        self._build_center(self.center.body)
        self._build_side(self.right.body)

    # -- left rail ------------------------------------------------------ #
    def _build_nav(self, parent) -> None:
        tk = self.tk
        bg = parent.cget("bg")
        head = tk.Frame(parent, bg=bg)
        head.pack(fill="x", padx=16, pady=(18, 6))

        row = tk.Frame(head, bg=bg)
        row.pack(fill="x")
        mark = tk.Canvas(row, width=30, height=30, highlightthickness=0, bd=0, bg=bg)
        mark.pack(side="left")
        L.radial_blob(mark, 15, 15, 15, L.ACCENT, intensity=0.55, rings=8)
        mark.create_oval(7, 7, 23, 23, outline=L.to_hex(L.ACCENT), width=2)
        mark.create_oval(12, 12, 18, 18, fill=L.to_hex(L.ACCENT), outline="")

        title = tk.Frame(row, bg=bg)
        title.pack(side="left", padx=(10, 0))
        tk.Label(title, text="KALI-AEGIS", bg=bg, fg=L.to_hex(L.TEXT),
                 font=L.ui_font(12, "bold")).pack(anchor="w")
        tk.Label(title, text="security engineering", bg=bg, fg=L.to_hex(L.TEXT_FAINT),
                 font=L.ui_font(8)).pack(anchor="w")

        self._nav_buttons.clear()
        for key, label, _ in _VIEWS:
            btn = tk.Label(
                parent, text=f"  {label}", anchor="w", cursor="hand2",
                bg=bg, fg=L.to_hex(L.TEXT_DIM), font=L.ui_font(11), padx=10, pady=7,
            )
            btn.pack(fill="x", padx=10, pady=1)
            btn.bind("<Button-1>", lambda _e, k=key: self._show(k))
            btn.bind("<Enter>", lambda _e, b=btn: b.configure(fg=L.to_hex(L.TEXT)))
            btn.bind("<Leave>", lambda _e: self._paint_nav())
            self._nav_buttons[key] = btn

        tk.Frame(parent, bg=bg).pack(fill="both", expand=True)

        rep = self.runtime.privileges.report() if self.runtime.privileges else None
        if rep is not None:
            badge = tk.Frame(parent, bg=bg)
            badge.pack(fill="x", padx=16, pady=(0, 6))
            if rep.is_root:
                label, color = "root", L.ACCENT_GREEN
            elif rep.allow_root and rep.can_elevate:
                label, color = "elevated", L.ACCENT_GREEN
            elif rep.can_elevate:
                label, color = "available", L.ACCENT_AMBER
            else:
                label, color = "user only", L.TEXT_FAINT
            tk.Label(badge, text="PRIVILEGE", bg=bg, fg=L.to_hex(L.TEXT_FAINT),
                     font=L.ui_font(8)).pack(anchor="w")
            tk.Label(badge, text=label, bg=bg, fg=L.to_hex(color),
                     font=L.ui_font(10, "bold")).pack(anchor="w")

        self.kill_btn = tk.Label(
            parent, text="  Engage killswitch", anchor="w", cursor="hand2",
            bg=bg, fg=L.to_hex(L.ACCENT_RED), font=L.ui_font(10, "bold"),
            padx=10, pady=8,
        )
        self.kill_btn.pack(fill="x", padx=10, pady=(0, 16))
        self.kill_btn.bind("<Button-1>", lambda _e: self._toggle_kill())

    def _paint_nav(self) -> None:
        for key, btn in self._nav_buttons.items():
            active = key == self._view
            btn.configure(
                fg=L.to_hex(L.ACCENT if active else L.TEXT_DIM),
                bg=L.to_hex(L.blend(L.SURFACE_HIGH, L.ACCENT, 0.10)) if active
                else btn.master.cget("bg"),
            )

    # -- center --------------------------------------------------------- #
    def _build_center(self, parent) -> None:
        tk = self.tk
        bg = parent.cget("bg")

        head = tk.Frame(parent, bg=bg)
        head.pack(fill="x", padx=20, pady=(16, 8))
        self.view_title = tk.Label(head, text="Thread", bg=bg, fg=L.to_hex(L.TEXT),
                                   font=L.ui_font(14, "bold"))
        self.view_title.pack(side="left")
        self.view_sub = tk.Label(head, text="", bg=bg, fg=L.to_hex(L.TEXT_FAINT),
                                 font=L.ui_font(9))
        self.view_sub.pack(side="left", padx=(12, 0))
        self.mode_chip = tk.Label(head, text="", bg=bg, fg=L.to_hex(L.TEXT_DIM),
                                  font=L.ui_font(9, "bold"))
        self.mode_chip.pack(side="right")

        wrap = tk.Frame(parent, bg=bg)
        wrap.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        style = L.scrolled_text_style()
        self.thread = tk.Text(wrap, font=L.ui_font(11), **style)
        scroll = tk.Scrollbar(
            wrap, command=self.thread.yview, width=10, bd=0, highlightthickness=0,
            troughcolor=bg, bg=L.to_hex(L.SURFACE_HIGH),
            activebackground=L.to_hex(L.BORDER),
        )
        self.thread.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.thread.pack(side="left", fill="both", expand=True)
        self._configure_tags()

        self._build_composer(parent)

    def _configure_tags(self) -> None:
        t = self.thread
        t.tag_configure("user_label", foreground=L.to_hex(L.ACCENT),
                        font=L.ui_font(9, "bold"), spacing1=10)
        t.tag_configure("user", foreground=L.to_hex(L.TEXT), font=L.ui_font(11),
                        lmargin1=2, lmargin2=2, spacing3=6)
        t.tag_configure("agent_label", foreground=L.to_hex(L.ACCENT_GREEN),
                        font=L.ui_font(9, "bold"), spacing1=14)
        t.tag_configure("body", foreground=L.to_hex(L.TEXT), font=L.ui_font(11),
                        lmargin1=2, lmargin2=2, spacing3=4)
        t.tag_configure("dim", foreground=L.to_hex(L.TEXT_DIM), font=L.ui_font(10))
        t.tag_configure("faint", foreground=L.to_hex(L.TEXT_FAINT), font=L.ui_font(9))
        t.tag_configure("ok", foreground=L.to_hex(L.ACCENT_GREEN), font=L.mono_font(10))
        t.tag_configure("bad", foreground=L.to_hex(L.ACCENT_RED), font=L.mono_font(10))
        t.tag_configure("warn", foreground=L.to_hex(L.ACCENT_AMBER), font=L.mono_font(10))
        t.tag_configure("mono", foreground=L.to_hex(L.TEXT_DIM), font=L.mono_font(10),
                        lmargin1=14, lmargin2=14, spacing1=2)
        t.tag_configure("cmd", foreground=L.to_hex(L.ACCENT), font=L.mono_font(10),
                        lmargin1=16, lmargin2=16, spacing1=4, spacing3=2)
        t.tag_configure("h", foreground=L.to_hex(L.TEXT), font=L.ui_font(12, "bold"),
                        spacing1=8, spacing3=4)
        t.tag_configure("rule", foreground=L.to_hex(L.BORDER), font=L.mono_font(6),
                        spacing1=6, spacing3=6)

    def _build_composer(self, parent) -> None:
        tk = self.tk
        self.composer = tk.Frame(parent, bg=parent.cget("bg"))
        self.composer.pack(fill="x", padx=12, pady=(0, 14))

        box_bg = L.to_hex(L.blend(L.BACKGROUND_TOP, L.SURFACE, 0.72))
        box = tk.Frame(self.composer, bg=box_bg, highlightthickness=1,
                       highlightbackground=L.to_hex(L.BORDER),
                       highlightcolor=L.to_hex(L.ACCENT))
        box.pack(fill="x")
        inner = tk.Frame(box, bg=box_bg)
        inner.pack(fill="x", padx=2, pady=2)

        self.entry = tk.Text(
            inner, height=3, font=L.ui_font(11), bg=box_bg, fg=L.to_hex(L.TEXT),
            insertbackground=L.to_hex(L.ACCENT), relief="flat", bd=0,
            highlightthickness=0, wrap="word", padx=12, pady=10,
        )
        self.entry.pack(fill="x")
        self.entry.bind("<Control-Return>", self._on_ctrl_return)
        self.entry.bind("<Return>", self._on_return)

        foot = tk.Frame(inner, bg=box_bg)
        foot.pack(fill="x", padx=10, pady=(0, 8))
        self.hint = tk.Label(foot, text="Enter to send  ·  Shift+Enter for newline",
                             bg=box_bg, fg=L.to_hex(L.TEXT_FAINT), font=L.ui_font(8))
        self.hint.pack(side="left")
        self.send_btn = tk.Label(
            foot, text="Send", cursor="hand2", padx=14, pady=4,
            bg=L.to_hex(L.blend(L.SURFACE, L.ACCENT, 0.22)), fg=L.to_hex(L.TEXT),
            font=L.ui_font(9, "bold"),
        )
        self.send_btn.pack(side="right")
        self.send_btn.bind("<Button-1>", lambda _e: self._submit())

    def _on_return(self, event):
        if event.state & 0x0001:  # Shift held
            return None
        self._submit()
        return "break"

    def _on_ctrl_return(self, _event):
        self._submit()
        return "break"

    # -- right rail ------------------------------------------------------ #
    def _build_side(self, parent) -> None:
        tk = self.tk
        bg = parent.cget("bg")
        tk.Label(parent, text="AGENTS", bg=bg, fg=L.to_hex(L.TEXT_FAINT),
                 font=L.ui_font(8, "bold")).pack(anchor="w", padx=16, pady=(18, 8))

        for name in ("leader", "builder", "pentester", "executor"):
            card = tk.Frame(parent, bg=bg)
            card.pack(fill="x", padx=12, pady=3)
            dot = tk.Canvas(card, width=12, height=12, highlightthickness=0, bd=0, bg=bg)
            dot.pack(side="left", pady=(4, 0))
            body = tk.Frame(card, bg=bg)
            body.pack(side="left", fill="x", expand=True, padx=(8, 0))
            tk.Label(body, text=name.upper(), bg=bg, fg=L.to_hex(L.TEXT),
                     font=L.ui_font(10, "bold")).pack(anchor="w")
            state = tk.Label(body, text="idle", bg=bg, fg=L.to_hex(L.TEXT_FAINT),
                             font=L.ui_font(9))
            state.pack(anchor="w")
            meta = tk.Label(body, text="", bg=bg, fg=L.to_hex(L.TEXT_FAINT),
                            font=L.mono_font(8))
            meta.pack(anchor="w")
            self._agent_rows[name] = {"dot": dot, "state": state, "meta": meta}

        tk.Frame(parent, bg=L.to_hex(L.BORDER), height=1).pack(fill="x", padx=16, pady=14)

        tk.Label(parent, text="SYSTEM", bg=bg, fg=L.to_hex(L.TEXT_FAINT),
                 font=L.ui_font(8, "bold")).pack(anchor="w", padx=16, pady=(0, 8))
        self.sys_labels: Dict[str, Any] = {}
        for key in ("model", "turns", "scope", "audit", "queue"):
            row = tk.Frame(parent, bg=bg)
            row.pack(fill="x", padx=16, pady=2)
            tk.Label(row, text=key, bg=bg, fg=L.to_hex(L.TEXT_FAINT),
                     font=L.ui_font(9), width=7, anchor="w").pack(side="left")
            val = tk.Label(row, text="-", bg=bg, fg=L.to_hex(L.TEXT_DIM),
                           font=L.ui_font(9), anchor="w")
            val.pack(side="left", fill="x", expand=True)
            self.sys_labels[key] = val

    # ------------------------------------------------------------------ #
    # views
    # ------------------------------------------------------------------ #
    def _show(self, view: str) -> None:
        self._view = view
        self._paint_nav()
        for key, label, sub in _VIEWS:
            if key == view:
                self.view_title.configure(text=label)
                self.view_sub.configure(text=sub)
                break
        self._render_view()

    def _render_view(self) -> None:
        {
            "thread": self._render_thread,
            "team": self._render_team,
            "tasks": self._render_tasks,
            "security": self._render_security,
            "audit": self._render_audit,
            "settings": self._render_settings,
        }.get(self._view, self._render_thread)()
        self._refresh_system()

    def _clear(self) -> None:
        self.thread.configure(state="normal")
        self.thread.delete("1.0", "end")

    def _w(self, text: str, tag: str = "") -> None:
        self.thread.insert("end", text, tag or ())

    def _render_thread(self) -> None:
        self.composer.pack(fill="x", padx=12, pady=(0, 14))
        self._clear()
        if not self._turns:
            self._write_welcome()
        else:
            for turn in self._turns:
                self._write_turn(turn)
        self.thread.configure(state="disabled")
        self.thread.see("end")

    def _write_welcome(self) -> None:
        self._w("KALI-AEGIS\n", "h")
        self._w("Security engineering assistant. Ask for something on this "
                "machine and the team will work on it with real tools.\n\n", "dim")

        self._w("Capabilities\n", "h")
        for line in (
            "  ·  run shell commands, build, and test  (risk-classified, audited)",
            "  ·  read and write files anywhere the policy allows",
            "  ·  scoped reconnaissance of authorized targets",
            "  ·  root/elevated execution when the operator enables it",
        ):
            self._w(line + "\n", "body")

        rep = self.runtime.privileges.report() if self.runtime.privileges else None
        if rep is not None and rep.can_elevate:
            self._w("\nRoot is available on this host. Start with --root to let "
                    "privileged commands run; each one is stamped elevated in the "
                    "audit log.\n", "warn")

        if not self.runtime.llm_available:
            self._w("\nNo model is configured, so objectives fall back to the "
                    "deterministic runner. Set KALI_AEGIS_MODEL_PROVIDER and "
                    "KALI_AEGIS_MODEL_NAME, then export the API key named by "
                    "KALI_AEGIS_MODEL_API_KEY_ENV (default KALI_AEGIS_MODEL_API_KEY) "
                    "to enable the reasoning loop.\n", "warn")

        self._w("\nTry\n", "h")
        for s in ("show me disk usage and the biggest directories under /",
                  "is sshd listening, and what version is it?",
                  "write a python script that lists open TCP ports, then run it"):
            self._w(f"  › {s}\n", "mono")

    def _write_turn(self, turn: Dict[str, Any]) -> None:
        if turn["kind"] == "user":
            self._w("YOU\n", "user_label")
            self._w(turn["text"] + "\n", "user")
            return

        status = turn.get("status", "")
        color = {"COMPLETE": "ok", "BLOCKED": "warn", "FAILED": "bad",
                 "INTERRUPTED": "bad"}.get(status, "dim")
        self._w(f"AEGIS  ·  {turn.get('mode', '')}\n", "agent_label")
        self._w("─" * 68 + "\n", "rule")

        for step in turn.get("steps", []):
            self._write_step(step)

        if turn.get("text"):
            self._w("\n" + turn["text"] + "\n", "body")

        if turn.get("errors"):
            self._w("\nErrors\n", "h")
            for err in turn["errors"]:
                self._w(f"  · {err}\n", "bad")

        self._w(f"\nSTATUS: {status}\n", color)
        if turn.get("next"):
            self._w(f"NEXT: {turn['next']}\n", "dim")

    def _write_step(self, step: Dict[str, Any]) -> None:
        tool = step.get("tool", "")
        ok = step.get("ok", True)
        detail = step.get("detail", "")
        args = step.get("arguments", {})

        if tool == "run_command":
            shown = args.get("executed") or args.get("command", "")
            self._w("  $ ", "faint")
            self._w(str(shown), "cmd")
            if args.get("elevated"):
                self._w("  [elevated]", "warn")
            self._w("\n", "cmd")
        elif tool in {"read_file", "write_file", "list_dir"}:
            self._w(f"  {tool} ", "faint")
            self._w(str(args.get("path", "")), "mono")
            self._w("\n", "mono")

        output = (step.get("output") or "").strip()
        if output:
            lines = output.splitlines()
            shown = lines[:12]
            for line in shown:
                self._w("      " + line[:160] + "\n", "mono")
            if len(lines) > len(shown):
                self._w(f"      … {len(lines) - len(shown)} more lines\n", "faint")
        elif detail and not ok:
            self._w(f"      {detail[:200]}\n", "bad")

    def _render_team(self) -> None:
        self.composer.pack_forget()
        self._clear()
        self._w("The team, and what each actually is\n", "h")
        self._w("A self-model is not a prompt — it is the class's real public "
                "methods, checked against a declared capability list.\n", "dim")
        for agent in self.runtime.agents.values():
            model = agent.introspect()
            self._w(f"\n{model.name.upper()}  ·  {model.role}\n", "h")
            state = model.status
            self._w(f"  state        {state}\n",
                    "bad" if state in {"ERROR", "STOPPED"} else "ok")
            self._w(f"  requires llm {'yes' if model.requires_llm else 'no'}\n", "faint")
            self._w("  capabilities\n", "dim")
            for cap in model.capabilities:
                self._w(f"    · {cap}\n", "body")
            if model.limitations:
                self._w("  limits\n", "dim")
                for lim in model.limitations:
                    self._w(f"    · {lim}\n", "faint")
        self.thread.configure(state="disabled")

    def _render_tasks(self) -> None:
        self.composer.pack_forget()
        self._clear()
        tasks = self.runtime.state.all()
        self._w("Objectives\n", "h")
        if not tasks:
            self._w("No objectives yet. Ask for something in the Thread.\n", "dim")
        for task in tasks:
            color = {"COMPLETE": "ok", "BLOCKED": "warn", "FAILED": "bad",
                     "INTERRUPTED": "bad", "RUNNING": "mono"}.get(task.status, "dim")
            self._w(f"\n{task.id}  {task.objective}\n", "h")
            self._w(f"  status   {task.status}\n", color)
            if task.scope:
                self._w(f"  scope    {task.scope}\n", "faint")
            for sub in task.subtasks:
                mark = "✓" if sub.status == "COMPLETE" else "·"
                self._w(f"    {mark} [{sub.agent}] {sub.description}\n", "body")
        self.thread.configure(state="disabled")

    def _render_security(self) -> None:
        self.composer.pack_forget()
        self._clear()
        scope = self.runtime.pentester.scope
        rep = self.runtime.privileges.report() if self.runtime.privileges else None

        self._w("Authorized scope\n", "h")
        hosts = sorted(scope.authorized_hosts)
        if hosts:
            for host in hosts:
                self._w(f"  · {host}\n", "ok")
        else:
            self._w("  (none configured) — reconnaissance is limited to localhost\n", "warn")

        self._w("\nAlways permitted (this machine)\n", "h")
        for addr in sorted(local_addresses()):
            self._w(f"  · {addr}\n", "dim")

        self._w("\nPosture\n", "h")
        self._w(f"  high-risk auto-approve   "
                f"{'ON' if self.config.auto_approve_high_risk else 'off'}\n",
                "bad" if self.config.auto_approve_high_risk else "body")
        self._w(f"  killswitch               "
                f"{'ENGAGED' if self.runtime.killswitch.is_engaged() else 'released'}\n",
                "bad" if self.runtime.killswitch.is_engaged() else "ok")
        if rep is not None:
            self._w(f"  allow root               "
                    f"{'enabled' if rep.allow_root else 'disabled'}\n",
                    "warn" if rep.allow_root else "body")
            self._w(f"  elevation                {rep.capability}\n", "dim")

        self._w("\nBoundary\n", "h")
        self._w("Root administers this machine. It is not authorization to test "
                "third-party systems. Remote security work needs an explicit scope "
                "above.\n", "dim")
        self.thread.configure(state="disabled")

    def _render_audit(self) -> None:
        self.composer.pack_forget()
        self._clear()
        self._w("Operation log  (append-only, secrets redacted)\n", "h")
        entries = list(self.runtime.log.tail(limit=200))
        if not entries:
            self._w("Nothing recorded yet.\n", "dim")
        for entry in reversed(entries):
            ts = str(entry.get("timestamp", ""))[11:19]
            status = str(entry.get("status", ""))
            color = {"ok": "ok", "failed": "bad", "denied": "warn",
                     "error": "bad"}.get(status, "dim")
            tag = "[elevated] " if entry.get("elevated") else ""
            self._w(f"{ts}  ", "faint")
            self._w(f"{entry.get('agent', ''):<9}", "faint")
            self._w(f"{entry.get('operation', ''):<14}", "dim")
            self._w(f"{tag}{entry.get('command') or entry.get('target') or ''}\n", color)
        self.thread.configure(state="disabled")

    def _render_settings(self) -> None:
        self.composer.pack_forget()
        self._clear()
        self._w("Effective configuration\n", "h")
        data = self.config.to_dict()
        for key in sorted(data):
            value = data[key]
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value) or "(empty)"
            self._w(f"  {key:<26} {value}\n", "body")
        self._w("\nPrivilege\n", "h")
        for line in (self.runtime.privileges.describe() if self.runtime.privileges else []):
            self._w(f"  {line}\n", "dim")
        if self.runtime.ai is not None:
            self._w(f"\n  llm endpoint              {self.runtime.ai.client.endpoint}\n", "dim")
            self._w(f"  llm available             "
                    f"{'yes' if self.runtime.llm_available else 'no'}\n",
                    "ok" if self.runtime.llm_available else "warn")
        self.thread.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # submitting an objective
    # ------------------------------------------------------------------ #
    def _submit(self) -> None:
        if self._busy:
            return
        if not getattr(self.entry, "winfo_exists", lambda: False)():
            return
        objective = self.entry.get("1.0", "end").strip()
        if not objective:
            return
        if self.runtime.killswitch.is_engaged():
            self._flash("killswitch is engaged — release it first")
            return

        self.entry.delete("1.0", "end")
        self._turns.append({"kind": "user", "text": objective})
        self._busy = True
        self._set_busy(True)
        self._render_thread()

        threading.Thread(
            target=self._run_objective, args=(objective,), daemon=True
        ).start()

    def _run_objective(self, objective: str) -> None:
        """Worker thread. Never touches a widget — results go through the queue."""
        try:
            self._results.put(("report", self.runtime.act(objective)))
        except Exception as exc:  # pragma: no cover - defensive
            self._results.put(("error", exc))

    def _set_busy(self, busy: bool) -> None:
        try:
            if busy:
                self.send_btn.configure(
                    text="…", bg=L.to_hex(L.blend(L.SURFACE, L.TEXT_FAINT, 0.2)))
                self.hint.configure(
                    text="working — the team is executing actions",
                    fg=L.to_hex(L.TEXT_DIM))
            else:
                self.send_btn.configure(
                    text="Send", bg=L.to_hex(L.blend(L.SURFACE, L.ACCENT, 0.22)))
                self.hint.configure(
                    text="Enter to send  ·  Shift+Enter for newline",
                    fg=L.to_hex(L.TEXT_FAINT))
        except Exception:  # pragma: no cover - widget gone
            pass

    def _flash(self, message: str) -> None:
        self.hint.configure(text=message, fg=L.to_hex(L.ACCENT_RED))
        self.root.after(2500, lambda: self.hint.configure(
            text="Enter to send  ·  Shift+Enter for newline",
            fg=L.to_hex(L.TEXT_FAINT)))

    # ------------------------------------------------------------------ #
    # main loop
    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        if not self._alive():
            return
        while True:
            try:
                kind, payload = self._results.get_nowait()
            except queue.Empty:
                break
            if kind == "report":
                self._turns.append(self._report_to_turn(payload))
            else:
                self._turns.append({
                    "kind": "assistant",
                    "text": f"the run failed: {payload}",
                    "status": "FAILED", "mode": "error", "steps": [],
                    "errors": [str(payload)], "next": "review the failure and retry",
                })
            self._busy = False
            self._set_busy(False)
            if self._view == "thread":
                self._render_thread()

        self._refresh_agents()
        if self._view == "thread" and not self._busy:
            self._refresh_system()
        self._animate()
        self._after_id = self.root.after(90, self._tick)

    def _alive(self) -> bool:
        """Whether the window still exists; the tick loop outlives teardown."""
        try:
            return bool(self.root.winfo_exists())
        except Exception:  # pragma: no cover - interpreter gone
            return False

    def close(self) -> None:
        """Cancel the pending tick before destroying, so no callback fires late."""
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:  # pragma: no cover
                pass
            self._after_id = None
        try:
            self.root.destroy()
        except Exception:  # pragma: no cover
            pass

    @staticmethod
    def _report_to_turn(report: Dict[str, Any]) -> Dict[str, Any]:
        results = report.get("RESULTS") or []
        text = results[0] if results else ""
        if not text:
            text = "\n".join(report.get("ACTIONS", [])[:8])
        return {
            "kind": "assistant",
            "text": text,
            "status": report.get("STATUS", "UNKNOWN"),
            "mode": report.get("MODE", ""),
            "steps": report.get("STEPS", []),
            "errors": report.get("ERRORS", []),
            "next": report.get("NEXT ACTION", ""),
        }

    def _animate(self) -> None:
        self.liquid.step()
        self._phase = (self._phase + 0.0035) % 1.0
        self.canvas.delete("sweep")
        L.specular_sweep(
            self.canvas, self.RAIL_W + 28, 0,
            self.canvas.winfo_width() or self.WIDTH, 150,
            self._phase, tags=("sweep",),
        )

    def _refresh_agents(self) -> None:
        try:
            monitor = self.runtime.monitor()
        except Exception:  # pragma: no cover - defensive
            return
        by_name = {a["name"]: a for a in monitor.get("agents", [])}
        for name, row in self._agent_rows.items():
            info = by_name.get(name, {})
            state_name = info.get("state", "IDLE")
            color = L.to_hex(_STATE_COLOR.get(state_name, L.TEXT_FAINT))
            row["state"].configure(text=state_name.lower(), fg=color)
            row["dot"].delete("all")
            row["dot"].create_oval(3, 3, 9, 9, fill=color, outline="")
            elapsed = info.get("elapsed_s") or 0
            task = (info.get("current_task") or "")[:20]
            row["meta"].configure(text=f"{elapsed:5.1f}s {task}")

    def _refresh_system(self) -> None:
        available = self.runtime.llm_available
        self.sys_labels["model"].configure(
            text=self.config.model_name if available else "none",
            fg=L.to_hex(L.ACCENT_GREEN if available else L.TEXT_FAINT))
        self.mode_chip.configure(
            text="AI REASONING" if available else "DETERMINISTIC",
            fg=L.to_hex(L.ACCENT_GREEN if available else L.TEXT_FAINT))
        try:
            entries = list(self.runtime.log.tail(limit=1))
            last = entries[-1] if entries else {}
            self.sys_labels["audit"].configure(text=str(last.get("operation", "-")))
        except Exception:  # pragma: no cover
            pass
        scope = self.runtime.pentester.scope
        self.sys_labels["scope"].configure(text=f"{len(scope.authorized_hosts)} host(s)")
        self.sys_labels["queue"].configure(text=str(len(self.runtime.state.queue())))
        self.sys_labels["turns"].configure(text=str(len(self._turns)))

    # ------------------------------------------------------------------ #
    # killswitch
    # ------------------------------------------------------------------ #
    def _toggle_kill(self) -> None:
        if self.runtime.killswitch.is_engaged():
            self._release_kill()
        else:
            self._engage_kill()

    def _engage_kill(self) -> None:
        self.runtime.killswitch.engage("operator engaged killswitch from the console")
        self.kill_btn.configure(text="  Release killswitch", fg=L.to_hex(L.ACCENT_GREEN))
        self._flash("killswitch engaged — new work is refused")

    def _release_kill(self) -> None:
        self.runtime.killswitch.release()
        if self.runtime.killswitch.env_engaged():
            self._flash("sentinel removed, but KALI_AEGIS_KILLSWITCH is set in the environment")
        else:
            self.kill_btn.configure(text="  Engage killswitch", fg=L.to_hex(L.ACCENT_RED))
            self._flash("killswitch released")

    # ------------------------------------------------------------------ #
    # entry point
    # ------------------------------------------------------------------ #
    def run(self) -> None:
        self.root.mainloop()


def launch(config: Config, runtime: Optional[Runtime] = None) -> int:
    """Start the console. Returns a process exit code."""
    try:
        import tkinter as tk  # local import: only needed for the GUI
    except ImportError:
        print(_TK_HELP, file=sys.stderr)
        return 1
    del tk  # presence is all this check needs

    rt = runtime or Runtime.build(config, interactive=False)
    try:
        app = GlassApp(rt, config)
    except Exception as exc:  # pragma: no cover - no display
        print(f"cannot open a window: {exc}", file=sys.stderr)
        print("If you are on a headless machine, run the CLI instead.", file=sys.stderr)
        return 1
    app.run()
    return 0
