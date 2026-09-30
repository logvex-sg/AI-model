"""Desktop console — a frosted-glass AI assistant surface (Tkinter).

This is a conversation-first interface, not an admin table. You type an
objective, an agent team works on it, and the thread shows what each agent is
doing in plain language while raw commands stay tucked inside their own mono
cards. The glass aesthetic is built from layered translucent panels, hairline
borders, and a soft gradient hero — no external theme, no third-party widgets.

Tkinter ships with CPython but needs the system Tk libraries. When they are
missing :func:`launch` reports a precise, actionable error and exits non-zero
instead of crashing with an opaque import failure.
"""

from __future__ import annotations

import platform
import queue
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

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

# --------------------------------------------------------------------------- #
# palette — deep space base, frosted translucent panels, restrained accents
# --------------------------------------------------------------------------- #
_VOID = "#04060c"          # behind everything
_BASE = "#080c16"          # app background
_GLASS = "#0f1626"         # primary frosted panel
_GLASS_LO = "#0b111d"      # sunken surface (code blocks)
_HAIR = "#1e2a42"          # hairline border
_HAIR_HI = "#2c3d5c"       # hairline on raised
_FG = "#e9effb"            # primary text
_FG2 = "#9dafcb"           # secondary text
_FG3 = "#5f7091"           # tertiary / captions
_BLUE = "#63a4ff"
_CYAN = "#57d9e8"
_GREEN = "#5ce08d"
_AMBER = "#f2b455"
_RED = "#ff6f6f"
_VIOLET = "#a98bff"

_STATE_COLOR = {
    "IDLE": _FG3,
    "THINKING": _BLUE,
    "PLANNING": _VIOLET,
    "EXECUTING": _GREEN,
    "TESTING": _AMBER,
    "WAITING": _CYAN,
    "ERROR": _RED,
    "STOPPED": _RED,
}

_STATUS_COLOR = {
    "COMPLETE": _GREEN,
    "RUNNING": _BLUE,
    "PENDING": _FG3,
    "BLOCKED": _AMBER,
    "INTERRUPTED": _AMBER,
    "FAILED": _RED,
}

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_VIEWS = [
    ("thread", "◍", "Thread"),
    ("team", "◈", "Team"),
    ("tasks", "▤", "Tasks"),
    ("security", "⛨", "Security"),
    ("logs", "≡", "Audit"),
    ("settings", "⚙", "Settings"),
]

_SUGGESTIONS = [
    "Create a Python network monitor, test it, and init a git repo",
    "Scan 127.0.0.1 and report open services",
    "Explain what the executor agent can and cannot do",
]


def _import_tk():
    try:
        import tkinter as tk
        from tkinter import font as tkfont
    except ImportError as exc:  # pragma: no cover - depends on host libs
        raise RuntimeError(f"{_TK_HELP}\n\nunderlying error: {exc}") from exc
    return tk, tkfont


def _hex_to_rgb(color: str):
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))


def _lerp(c1: str, c2: str, t: float) -> str:
    a, b = _hex_to_rgb(c1), _hex_to_rgb(c2)
    return "#%02x%02x%02x" % tuple(
        int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3)
    )


def _blend(fg: str, bg: str, alpha: float) -> str:
    """Fake translucency: mix ``fg`` into ``bg`` at ``alpha``."""
    return _lerp(bg, fg, alpha)


class GlassApp:
    """The frosted-glass assistant console."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.tk, self.tkfont = _import_tk()
        self.root = self.tk.Tk()
        self.root.title("KALI-AEGIS")
        self.root.geometry("1180x780")
        self.root.minsize(940, 620)
        self.root.configure(bg=_BASE)

        self._view = "thread"
        self._busy = False
        self._spin = 0
        self._kill_cache: Optional[bool] = None
        self._agent_rows: Dict[str, Any] = {}
        self._rail_btns: Dict[str, Any] = {}
        self._thread_win: Optional[Any] = None
        self._thread_inner: Optional[Any] = None
        self._activity: Optional[Any] = None
        self._spin_lbl: Optional[Any] = None
        self._entry: Optional[Any] = None
        self._results: "queue.Queue[Any]" = queue.Queue()
        self._turns: List[Dict[str, Any]] = []

        self._pick_fonts()
        self._build()
        self._render_view()
        self._tick()

    # -- fonts ---------------------------------------------------------------
    def _pick_fonts(self) -> None:
        available = set(self.tkfont.families())

        def pick(candidates: List[str], fallback: str = "TkDefaultFont") -> str:
            for name in candidates:
                if name in available:
                    return name
            return fallback

        self.f_display = pick(
            ["JetBrains Mono", "IBM Plex Mono", "DejaVu Sans Mono", "Menlo",
             "Consolas", "monospace"]
        )
        self.f_ui = pick(
            ["Inter", "IBM Plex Sans", "Cantarell", "DejaVu Sans", "Segoe UI",
             "Helvetica"]
        )
        self.f_mono = self.f_display

    def F(self, family: str, size: int, weight: str = "normal") -> tuple:
        return (family, size, weight)

    # -- public entry points (used by tests) ---------------------------------
    def _show(self, view: str) -> None:
        self._view = view
        self._render_view()

    def refresh(self) -> None:
        self._render_view()
        self._refresh_agent_rail()

    def _engage_kill(self) -> None:
        self.runtime.killswitch.engage("desktop console")
        self.refresh()

    def _release_kill(self) -> None:
        self.runtime.killswitch.release()
        self.refresh()

    # -- layout --------------------------------------------------------------
    def _build(self) -> None:
        shell = self.tk.Frame(self.root, bg=_BASE)
        shell.pack(fill="both", expand=True)

        self._build_rail(shell)

        body = self.tk.Frame(shell, bg=_BASE)
        body.pack(side="left", fill="both", expand=True)

        self._build_topbar(body)

        stage = self.tk.Frame(body, bg=_BASE)
        stage.pack(fill="both", expand=True, padx=18, pady=(6, 0))

        self._build_agents_rail(stage)

        self.content = self.tk.Frame(stage, bg=_BASE)
        self.content.pack(side="left", fill="both", expand=True, padx=(16, 0))

        self._build_statusbar(body)

    # .. left icon rail .......................................................
    def _build_rail(self, parent: Any) -> None:
        rail = self.tk.Frame(parent, bg=_VOID, width=68)
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)

        mark = self.tk.Canvas(
            rail, width=40, height=40, bg=_VOID, highlightthickness=0, bd=0
        )
        mark.pack(pady=(18, 10))
        self._round_rect(mark, 2, 2, 38, 38, 11, fill=_blend(_BLUE, _VOID, 0.16),
                         outline=_blend(_BLUE, _VOID, 0.45))
        mark.create_text(20, 21, text="Λ", fill=_BLUE,
                         font=self.F(self.f_display, 17, "bold"))

        for key, glyph, _label in _VIEWS:
            btn = self.tk.Label(
                rail, text=glyph, bg=_VOID, fg=_FG3,
                font=self.F(self.f_display, 16), cursor="hand2",
            )
            btn.pack(pady=9)
            btn.bind("<Button-1>", lambda _e, k=key: self._show(k))
            self._rail_btns[key] = btn

        spacer = self.tk.Frame(rail, bg=_VOID)
        spacer.pack(fill="both", expand=True)

        self.rail_kill = self.tk.Label(
            rail, text="⏻", bg=_VOID, fg=_FG3,
            font=self.F(self.f_display, 16), cursor="hand2",
        )
        self.rail_kill.pack(pady=(0, 18))
        self.rail_kill.bind("<Button-1>", lambda _e: self._toggle_kill())

    # .. top bar ..............................................................
    def _build_topbar(self, parent: Any) -> None:
        bar = self.tk.Frame(parent, bg=_BASE, height=58)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        left = self.tk.Frame(bar, bg=_BASE)
        left.pack(side="left", padx=(20, 0))
        self.tk.Label(
            left, text="KALI-AEGIS", bg=_BASE, fg=_FG,
            font=self.F(self.f_ui, 15, "bold"),
        ).pack(anchor="w", pady=(12, 0))
        self.top_sub = self.tk.Label(
            left, text="autonomous security engineering", bg=_BASE, fg=_FG3,
            font=self.F(self.f_ui, 9),
        )
        self.top_sub.pack(anchor="w")

        right = self.tk.Frame(bar, bg=_BASE)
        right.pack(side="right", padx=(0, 20))
        self.status_pill = self._pill(right, "● READY", _GREEN, 11, bold=True)
        self.status_pill.pack(side="right", pady=20)
        self.kill_pill = self._pill(right, "KILLSWITCH ARMED", _FG3, 9)
        self.kill_pill.pack(side="right", padx=(0, 10), pady=20)

    # .. right agent rail .....................................................
    def _build_agents_rail(self, parent: Any) -> None:
        card = self.tk.Frame(parent, bg=_GLASS, width=252,
                             highlightthickness=1, highlightbackground=_HAIR)
        card.pack(side="right", fill="y", pady=(8, 12))
        card.pack_propagate(False)

        head = self.tk.Frame(card, bg=_GLASS)
        head.pack(fill="x", padx=16, pady=(16, 10))
        self.tk.Label(
            head, text="AGENT TEAM", bg=_GLASS, fg=_FG3,
            font=self.F(self.f_ui, 8, "bold"),
        ).pack(side="left")
        self.team_count = self.tk.Label(
            head, text="4", bg=_GLASS, fg=_FG3, font=self.F(self.f_display, 9)
        )
        self.team_count.pack(side="right")

        self.team_body = self.tk.Frame(card, bg=_GLASS)
        self.team_body.pack(fill="both", expand=True, padx=10)

        foot = self.tk.Frame(card, bg=_GLASS)
        foot.pack(fill="x", padx=16, pady=(6, 14))
        self.queue_lbl = self.tk.Label(
            foot, text="queue 0", bg=_GLASS, fg=_FG3, font=self.F(self.f_display, 8)
        )
        self.queue_lbl.pack(side="left")
        self.err_lbl = self.tk.Label(
            foot, text="errors 0", bg=_GLASS, fg=_FG3, font=self.F(self.f_display, 8)
        )
        self.err_lbl.pack(side="right")

    # .. status bar ...........................................................
    def _build_statusbar(self, parent: Any) -> None:
        bar = self.tk.Frame(parent, bg=_BASE, height=30)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.hint = self.tk.Label(
            bar, text="", bg=_BASE, fg=_FG3, font=self.F(self.f_display, 8)
        )
        self.hint.pack(side="left", padx=20)
        self.home_lbl = self.tk.Label(
            bar, text=str(self.runtime.config.home), bg=_BASE, fg=_FG3,
            font=self.F(self.f_display, 8),
        )
        self.home_lbl.pack(side="right", padx=20)

    # -- view routing --------------------------------------------------------
    def _render_view(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self._thread_win = None
        self._thread_inner = None
        self._activity = None

        for key, btn in self._rail_btns.items():
            btn.configure(fg=_BLUE if key == self._view else _FG3)

        renderer: Callable[[Any], None] = {
            "thread": self._view_thread,
            "team": self._view_team,
            "tasks": self._view_tasks,
            "security": self._view_security,
            "logs": self._view_logs,
            "settings": self._view_settings,
        }[self._view]
        renderer(self.content)

    # .. thread (the main assistant surface) .................................
    def _view_thread(self, parent: Any) -> None:
        frame = self.tk.Frame(parent, bg=_BASE)
        frame.pack(fill="both", expand=True)

        scroll, inner = self._scrollframe(frame)
        scroll.pack(fill="both", expand=True, pady=(8, 0))
        self._thread_win = scroll
        self._thread_inner = inner

        if not self._turns:
            self._render_hero(inner)
        for turn in self._turns:
            self._render_turn(inner, turn)

        self._build_composer(frame)

    def _render_hero(self, parent: Any) -> None:
        hero = self.tk.Canvas(parent, height=196, bg=_BASE,
                              highlightthickness=0, bd=0)
        hero.pack(fill="x", pady=(10, 6))
        hero.bind("<Configure>", lambda e: self._paint_hero(hero, e.width))

    def _paint_hero(self, canvas: Any, width: int) -> None:
        canvas.delete("all")
        width = max(width, 200)
        top = _lerp(_BASE, _BLUE, 0.10)
        for y in range(196):
            canvas.create_line(0, y, width, y, fill=_lerp(top, _BASE, y / 195))

        pad = 26
        canvas.create_text(
            pad, 58, anchor="w", text="What should we work on?",
            fill=_FG, font=self.F(self.f_ui, 22, "bold"),
        )
        canvas.create_text(
            pad, 88, anchor="w",
            text="Describe an objective. The team breaks it down, executes what it "
                 "can, and reports honestly what it cannot.",
            fill=_FG2, font=self.F(self.f_ui, 10), width=width - pad * 2,
        )

        x = pad
        for glyph, label, color in [
            ("◈", "4 agents", _VIOLET),
            ("⛨", "scope enforced", _GREEN),
            ("⏻", "killswitch", _AMBER),
            ("≡", "audited", _CYAN),
        ]:
            w = 12 + 13 * len(label)
            self._round_rect(canvas, x, 122, x + w, 148, 13,
                             fill=_blend(color, _BASE, 0.10),
                             outline=_blend(color, _BASE, 0.34))
            canvas.create_text(x + 11, 135, anchor="w", text=glyph, fill=color,
                               font=self.F(self.f_display, 9))
            canvas.create_text(x + 24, 135, anchor="w", text=label, fill=_FG2,
                               font=self.F(self.f_ui, 9))
            x += w + 8

        y = 168
        for text in _SUGGESTIONS[:2]:
            canvas.create_text(
                pad, y, anchor="w", text="›  " + text, fill=_FG3,
                font=self.F(self.f_display, 9), width=width - pad * 2,
            )
            y += 16

    def _build_composer(self, parent: Any) -> None:
        shell = self.tk.Frame(parent, bg=_GLASS, highlightthickness=1,
                              highlightbackground=_HAIR_HI)
        shell.pack(fill="x", pady=(12, 14))

        inner = self.tk.Frame(shell, bg=_GLASS)
        inner.pack(fill="x", padx=12, pady=10)

        self.prompt_lbl = self.tk.Label(
            inner, text="›", bg=_GLASS, fg=_BLUE,
            font=self.F(self.f_display, 15, "bold"),
        )
        self.prompt_lbl.pack(side="left", padx=(2, 8), anchor="n", pady=(4, 0))

        self.entry = self.tk.Text(
            inner, height=2, bg=_GLASS, fg=_FG, insertbackground=_BLUE,
            font=self.F(self.f_ui, 11), bd=0, highlightthickness=0,
            wrap="word", padx=2, pady=4,
        )
        self.entry.pack(side="left", fill="both", expand=True)
        self._entry = self.entry
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<Shift-Return>", lambda _e: None)

        send = self.tk.Label(
            inner, text="  ↑  ", bg=_blend(_BLUE, _GLASS, 0.22), fg=_FG,
            font=self.F(self.f_display, 13, "bold"), cursor="hand2", padx=6, pady=4,
        )
        send.pack(side="right", padx=(8, 2), anchor="s")
        send.bind("<Button-1>", lambda _e: self._submit())

        self.composer_hint = self.tk.Label(
            shell, text="Enter to send  ·  Shift+Enter for a new line  ·  commands run "
                        "through the risk policy and audit log",
            bg=_GLASS, fg=_FG3, font=self.F(self.f_ui, 8),
        )
        self.composer_hint.pack(anchor="w", padx=16, pady=(0, 10))

    # .. render a turn .......................................................
    def _render_turn(self, parent: Any, turn: Dict[str, Any]) -> None:
        if turn["kind"] == "user":
            self._render_user(parent, turn)
        else:
            self._render_assistant(parent, turn)

    def _render_user(self, parent: Any, turn: Dict[str, Any]) -> None:
        row = self.tk.Frame(parent, bg=_BASE)
        row.pack(fill="x", pady=(14, 2))

        bar = self.tk.Frame(row, bg=_BLUE, width=3)
        bar.pack(side="left", fill="y", pady=2)

        body = self.tk.Frame(row, bg=_BASE)
        body.pack(side="left", fill="x", expand=True, padx=(12, 0))
        self.tk.Label(
            body, text="YOU", bg=_BASE, fg=_FG3,
            font=self.F(self.f_ui, 8, "bold"),
        ).pack(anchor="w")
        lbl = self.tk.Label(
            body, text=turn["text"], bg=_BASE, fg=_FG,
            font=self.F(self.f_ui, 12), justify="left", anchor="w",
        )
        lbl.pack(anchor="w", pady=(2, 0))
        self._bind_wrap(lbl, self._thread_win, pad=90)

    def _render_assistant(self, parent: Any, turn: Dict[str, Any]) -> None:
        row = self.tk.Frame(parent, bg=_BASE)
        row.pack(fill="x", pady=(10, 4))

        body = self.tk.Frame(row, bg=_BASE)
        body.pack(fill="x", expand=True)

        head = self.tk.Frame(body, bg=_BASE)
        head.pack(fill="x")
        self.tk.Label(
            head, text="KALI-AEGIS", bg=_BASE, fg=_BLUE,
            font=self.F(self.f_ui, 9, "bold"),
        ).pack(side="left")
        if turn.get("status"):
            color = _STATUS_COLOR.get(turn["status"], _FG3)
            self._pill(head, "  " + turn["status"] + "  ", color, 8,
                       bold=True).pack(side="left", padx=8)

        report = turn.get("report")
        if report is None:
            msg = self.tk.Label(
                body, text=turn.get("text", ""), bg=_BASE, fg=_FG2,
                font=self.F(self.f_ui, 10), justify="left", anchor="w",
            )
            msg.pack(anchor="w", pady=(6, 0))
            self._bind_wrap(msg, self._thread_win, 60)
            return

        self._render_report(body, report)

    def _render_report(self, parent: Any, report: Dict[str, Any]) -> None:
        verification = report.get("VERIFICATION", "")
        if verification:
            lbl = self.tk.Label(
                parent, text=verification, bg=_BASE, fg=_FG2,
                font=self.F(self.f_ui, 10), justify="left", anchor="w",
            )
            lbl.pack(anchor="w", pady=(6, 0))
            self._bind_wrap(lbl, self._thread_win, 60)

        objective = report.get("OBJECTIVE")
        if objective:
            self._kv(parent, "Objective", objective)

        steps = report.get("PLAN") or []
        if steps:
            self._step_list(parent, steps)

        results = report.get("RESULTS") or []
        if results:
            self._section(parent, "ACTIVITY", [
                (r, _GREEN if "BLOCK" not in r.upper() else _AMBER)
                for r in results
            ])

        for cmd in report.get("COMMANDS EXECUTED") or []:
            self._command_card(parent, cmd)

        errors = report.get("ERRORS") or []
        if errors:
            self._section(parent, "ERRORS", [(e, _RED) for e in errors])

        files = report.get("FILES CHANGED") or []
        if files:
            self._section(parent, "FILES CHANGED", [(f, _CYAN) for f in files])

        next_action = report.get("NEXT ACTION")
        if next_action and next_action != "none":
            self._kv(parent, "Next action", next_action, accent=_AMBER)

    def _section(self, parent: Any, title: str, items: List[tuple]) -> None:
        wrap = self.tk.Frame(parent, bg=_BASE)
        wrap.pack(fill="x", pady=(10, 0))
        self.tk.Label(
            wrap, text=title, bg=_BASE, fg=_FG3,
            font=self.F(self.f_ui, 8, "bold"),
        ).pack(anchor="w", pady=(0, 3))
        for text, color in items:
            line = self.tk.Frame(wrap, bg=_BASE)
            line.pack(fill="x", pady=1)
            self.tk.Label(
                line, text="•", bg=_BASE, fg=color,
                font=self.F(self.f_display, 9),
            ).pack(side="left", padx=(2, 8), anchor="n")
            lbl = self.tk.Label(
                line, text=str(text), bg=_BASE, fg=_FG2,
                font=self.F(self.f_ui, 10), justify="left", anchor="w",
            )
            lbl.pack(side="left", fill="x", expand=True)
            self._bind_wrap(lbl, self._thread_win, 120)

    def _step_list(self, parent: Any, steps: List[Dict[str, Any]]) -> None:
        wrap = self.tk.Frame(parent, bg=_BASE)
        wrap.pack(fill="x", pady=(10, 0))
        self.tk.Label(
            wrap, text="PLAN", bg=_BASE, fg=_FG3,
            font=self.F(self.f_ui, 8, "bold"),
        ).pack(anchor="w", pady=(0, 4))

        for idx, step in enumerate(steps, 1):
            row = self.tk.Frame(wrap, bg=_BASE)
            row.pack(fill="x", pady=2)
            self.tk.Label(
                row, text=f"{idx:02d}", bg=_BASE, fg=_FG3,
                font=self.F(self.f_display, 9),
            ).pack(side="left", padx=(2, 10), anchor="n")
            lbl = self.tk.Label(
                row, text=step.get("description", ""), bg=_BASE, fg=_FG2,
                font=self.F(self.f_ui, 10), justify="left", anchor="w",
            )
            lbl.pack(side="left", fill="x", expand=True)
            self._bind_wrap(lbl, self._thread_win, 150)
            agent = step.get("agent", "")
            color = _VIOLET if agent == "leader" else _BLUE
            self._pill(row, " " + agent + " ", color, 8).pack(side="right")

    def _command_card(self, parent: Any, command: str) -> None:
        card = self.tk.Frame(parent, bg=_GLASS_LO, highlightthickness=1,
                             highlightbackground=_HAIR)
        card.pack(fill="x", pady=(8, 0))
        head = self.tk.Frame(card, bg=_GLASS_LO)
        head.pack(fill="x", padx=12, pady=(8, 0))
        self.tk.Label(
            head, text="COMMAND", bg=_GLASS_LO, fg=_FG3,
            font=self.F(self.f_ui, 7, "bold"),
        ).pack(side="left")
        body = self.tk.Frame(card, bg=_GLASS_LO)
        body.pack(fill="x", padx=12, pady=(4, 10))
        self.tk.Label(
            body, text="$", bg=_GLASS_LO, fg=_GREEN,
            font=self.F(self.f_mono, 10, "bold"),
        ).pack(side="left", padx=(0, 8), anchor="n")
        lbl = self.tk.Label(
            body, text=command, bg=_GLASS_LO, fg=_FG,
            font=self.F(self.f_mono, 10), justify="left", anchor="w",
        )
        lbl.pack(side="left", fill="x", expand=True)
        self._bind_wrap(lbl, self._thread_win, 150)

    def _kv(self, parent: Any, key: str, value: str,
            accent: str = _FG3) -> None:
        wrap = self.tk.Frame(parent, bg=_BASE)
        wrap.pack(fill="x", pady=(10, 0))
        self.tk.Label(
            wrap, text=key.upper(), bg=_BASE, fg=accent,
            font=self.F(self.f_ui, 8, "bold"),
        ).pack(anchor="w", pady=(0, 2))
        lbl = self.tk.Label(
            wrap, text=value, bg=_BASE, fg=_FG2,
            font=self.F(self.f_ui, 10), justify="left", anchor="w",
        )
        lbl.pack(anchor="w")
        self._bind_wrap(lbl, self._thread_win, 70)

    # -- activity row (streaming feel) ---------------------------------------
    def _show_activity(self, agent: str, action: str) -> None:
        if self._thread_inner is None:
            return
        if self._activity is not None:
            self._activity.destroy()
        row = self.tk.Frame(self._thread_inner, bg=_BASE)
        row.pack(fill="x", pady=(10, 0))
        self._spin_lbl = self.tk.Label(
            row, text=_SPINNER[0], bg=_BASE, fg=_BLUE,
            font=self.F(self.f_display, 11),
        )
        self._spin_lbl.pack(side="left", padx=(2, 8))
        self.tk.Label(
            row, text=f"{agent} is {action}", bg=_BASE, fg=_FG2,
            font=self.F(self.f_ui, 10),
        ).pack(side="left")
        self._activity = row
        self._scroll_bottom()

    def _clear_activity(self) -> None:
        if self._activity is not None:
            self._activity.destroy()
            self._activity = None

    # -- other views ---------------------------------------------------------
    def _view_team(self, parent: Any) -> None:
        self._page_title(parent, "Team", "What each agent can and cannot do.")
        for model in self.runtime.introspect()["team"]:
            card = self._card(parent)
            head = self.tk.Frame(card, bg=_GLASS)
            head.pack(fill="x", padx=18, pady=(16, 4))
            self.tk.Label(
                head, text=model["name"].upper(), bg=_GLASS, fg=_FG,
                font=self.F(self.f_ui, 12, "bold"),
            ).pack(side="left")
            color = _RED if model["status"] == "STOPPED" else _GREEN
            self._pill(head, " " + model["status"] + " ", color, 8,
                       bold=True).pack(side="right")
            self.tk.Label(
                card, text=model["role"], bg=_GLASS, fg=_FG2,
                font=self.F(self.f_ui, 10),
            ).pack(anchor="w", padx=18)

            grid = self.tk.Frame(card, bg=_GLASS)
            grid.pack(fill="x", padx=18, pady=(10, 4))
            self._chips(grid, "CAPABILITIES", model["capabilities"], _GREEN)
            self._chips(grid, "LIMITATIONS", model["limitations"], _AMBER)

            foot = self.tk.Frame(card, bg=_GLASS)
            foot.pack(fill="x", padx=18, pady=(6, 16))
            self.tk.Label(
                foot, text=f"{len(model['implemented_actions'])} implemented actions",
                bg=_GLASS, fg=_FG3, font=self.F(self.f_display, 8),
            ).pack(side="left")
            if model["requires_llm"]:
                self.tk.Label(
                    foot, text="requires LLM", bg=_GLASS, fg=_AMBER,
                    font=self.F(self.f_display, 8),
                ).pack(side="right")

    def _chips(self, parent: Any, title: str, items: List[str], color: str) -> None:
        wrap = self.tk.Frame(parent, bg=_GLASS)
        wrap.pack(fill="x", pady=3)
        self.tk.Label(
            wrap, text=title, bg=_GLASS, fg=_FG3,
            font=self.F(self.f_ui, 7, "bold"),
        ).pack(side="left", padx=(0, 10), anchor="n", pady=(3, 0))
        box = self.tk.Frame(wrap, bg=_GLASS)
        box.pack(side="left", fill="x", expand=True)
        for item in items:
            self._pill(box, " " + item + " ",
                       _blend(color, _GLASS, 0.9), 8,
                       bg=_blend(color, _GLASS, 0.13)).pack(side="left",
                                                             padx=2, pady=1)

    def _view_tasks(self, parent: Any) -> None:
        self._page_title(parent, "Tasks", "Objectives this session and their status.")
        tasks = self.runtime.state.all()
        if not tasks:
            self._empty(parent, "No tasks yet. Describe an objective in the Thread view.")
            return
        for task in reversed(tasks):
            card = self._card(parent)
            head = self.tk.Frame(card, bg=_GLASS)
            head.pack(fill="x", padx=18, pady=(14, 2))
            self.tk.Label(
                head, text=task.id, bg=_GLASS, fg=_FG3,
                font=self.F(self.f_display, 9),
            ).pack(side="left")
            color = _STATUS_COLOR.get(task.status, _FG3)
            self._pill(head, " " + task.status + " ", color, 8,
                       bold=True).pack(side="right")
            lbl = self.tk.Label(
                card, text=task.objective, bg=_GLASS, fg=_FG,
                font=self.F(self.f_ui, 10), justify="left", anchor="w",
            )
            lbl.pack(anchor="w", padx=18, pady=(0, 6))
            self._bind_wrap(lbl, self.content, 260)

            if task.subtasks:
                box = self.tk.Frame(card, bg=_GLASS)
                box.pack(fill="x", padx=18, pady=(0, 14))
                for sub in task.subtasks:
                    row = self.tk.Frame(box, bg=_GLASS)
                    row.pack(fill="x", pady=1)
                    dot_color = _STATUS_COLOR.get(sub.status, _FG3)
                    self.tk.Label(
                        row, text="●", bg=_GLASS, fg=dot_color,
                        font=self.F(self.f_display, 8),
                    ).pack(side="left", padx=(0, 8))
                    self.tk.Label(
                        row, text=sub.description, bg=_GLASS, fg=_FG2,
                        font=self.F(self.f_ui, 9),
                    ).pack(side="left")
                    self._pill(row, " " + sub.agent + " ", _BLUE, 7).pack(
                        side="right")
            else:
                self.tk.Frame(card, bg=_GLASS, height=10).pack()

    def _view_security(self, parent: Any) -> None:
        self._page_title(parent, "Security", "Authorized scope and enforcement posture.")
        cfg = self.runtime.config
        scope = self.runtime.pentester.scope
        card = self._card(parent, expand=False)
        rows = [
            ("Authorized hosts",
             ", ".join(sorted(scope.authorized_hosts)) or "none configured"),
            ("Always in scope",
             ", ".join(sorted(local_addresses())) or "loopback only"),
            ("High-risk auto-approve",
             "ENABLED — disposable environments only" if cfg.auto_approve_high_risk
             else "disabled (confirmation required)"),
            ("Root permitted", "yes" if cfg.allow_root else "no"),
            ("Write roots", ", ".join(str(p) for p in cfg.write_roots()) or "none"),
            ("Max retries", str(cfg.max_retries)),
            ("Killswitch",
             "ENGAGED" if self.runtime.killswitch.is_engaged() else "armed / ready"),
        ]
        for key, val in rows:
            line = self.tk.Frame(card, bg=_GLASS)
            line.pack(fill="x", padx=18, pady=4)
            self.tk.Label(
                line, text=key, bg=_GLASS, fg=_FG3, width=22, anchor="w",
                font=self.F(self.f_ui, 9),
            ).pack(side="left")
            self.tk.Label(
                line, text=val, bg=_GLASS, fg=_FG2, anchor="w",
                font=self.F(self.f_display, 9),
            ).pack(side="left", fill="x", expand=True)

        self.tk.Frame(card, bg=_GLASS, height=12).pack()

        findings = getattr(self.runtime.pentester, "findings", [])
        if findings:
            self._page_title(parent, "Findings", "")
            for finding in findings:
                self._section(parent, "FINDING", [(str(finding), _AMBER)])

    def _view_logs(self, parent: Any) -> None:
        self._page_title(parent, "Audit", "Append-only record of every operation.")
        card = self._card(parent)
        entries = self.runtime.log.read(limit=200)
        if not entries:
            self._empty(card, "No operations recorded yet.")
            return
        for entry in reversed(entries):
            row = self.tk.Frame(card, bg=_GLASS)
            row.pack(fill="x", padx=16, pady=2)
            state = str(entry.get("state", ""))
            color = _GREEN if state in {"ok", "complete"} else (
                _RED if state in {"error", "failed"} else _FG3
            )
            self.tk.Label(
                row, text="●", bg=_GLASS, fg=color,
                font=self.F(self.f_display, 8),
            ).pack(side="left", padx=(0, 8))
            self.tk.Label(
                row,
                text=time.strftime("%H:%M:%S", time.localtime(entry.get("ts", 0))),
                bg=_GLASS, fg=_FG3, font=self.F(self.f_display, 8),
            ).pack(side="left", padx=(0, 10))
            self.tk.Label(
                row, text=str(entry.get("agent", "")), bg=_GLASS, fg=_BLUE,
                font=self.F(self.f_display, 8), width=10, anchor="w",
            ).pack(side="left")
            cmd = str(entry.get("command", entry.get("action", "")))
            self.tk.Label(
                row, text=cmd[:90], bg=_GLASS, fg=_FG2, anchor="w",
                font=self.F(self.f_mono, 8),
            ).pack(side="left", fill="x", expand=True)
        self.tk.Frame(card, bg=_GLASS, height=12).pack()

    def _view_settings(self, parent: Any) -> None:
        self._page_title(parent, "Settings", "Effective configuration for this runtime.")
        cfg = self.runtime.config
        card = self._card(parent, expand=False)
        rows = {
            "home": str(cfg.home),
            "config file": str(cfg.home / "config.toml"),
            "log level": cfg.log_level,
            "command timeout": f"{cfg.command_timeout}s",
            "api": f"{cfg.api_host}:{cfg.api_port}",
            "model provider": cfg.model_provider or "none (deterministic core)",
            "python": platform.python_version(),
            "platform": platform.platform(),
        }
        for key, val in rows.items():
            line = self.tk.Frame(card, bg=_GLASS)
            line.pack(fill="x", padx=18, pady=4)
            self.tk.Label(
                line, text=key, bg=_GLASS, fg=_FG3, width=18, anchor="w",
                font=self.F(self.f_ui, 9),
            ).pack(side="left")
            self.tk.Label(
                line, text=str(val), bg=_GLASS, fg=_FG2, anchor="w",
                font=self.F(self.f_display, 9),
            ).pack(side="left", fill="x", expand=True)
        self.tk.Frame(card, bg=_GLASS, height=12).pack()

    # -- shared widgets ------------------------------------------------------
    def _page_title(self, parent: Any, title: str, subtitle: str) -> None:
        wrap = self.tk.Frame(parent, bg=_BASE)
        wrap.pack(fill="x", pady=(16, 4))
        self.tk.Label(
            wrap, text=title, bg=_BASE, fg=_FG,
            font=self.F(self.f_ui, 17, "bold"),
        ).pack(anchor="w")
        if subtitle:
            self.tk.Label(
                wrap, text=subtitle, bg=_BASE, fg=_FG3,
                font=self.F(self.f_ui, 9),
            ).pack(anchor="w", pady=(2, 0))

    def _card(self, parent: Any, expand: bool = True) -> Any:
        outer = self.tk.Frame(parent, bg=_GLASS, highlightthickness=1,
                              highlightbackground=_HAIR)
        outer.pack(fill="both" if expand else "x", expand=expand, pady=(8, 0))
        # 1px lit top edge — the detail that reads as a glass bevel.
        self.tk.Frame(outer, bg=_HAIR_HI, height=1).pack(fill="x")
        return outer

    def _empty(self, parent: Any, text: str) -> None:
        self.tk.Label(
            parent, text=text, bg=_GLASS, fg=_FG3,
            font=self.F(self.f_ui, 10),
        ).pack(anchor="w", padx=18, pady=18)

    def _pill(self, parent: Any, text: str, color: str, size: int,
              bold: bool = False, bg: Optional[str] = None) -> Any:
        bg = bg or _blend(color, _BASE, 0.14)
        return self.tk.Label(
            parent, text=text, bg=bg, fg=color,
            font=self.F(self.f_display, size, "bold" if bold else "normal"),
            padx=4, pady=2,
        )

    def _round_rect(self, canvas: Any, x1, y1, x2, y2, r, **kw) -> None:
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        canvas.create_polygon(pts, smooth=True, **kw)

    def _bind_wrap(self, label: Any, container: Any, pad: int) -> None:
        if container is None:
            return

        def _on_resize(event: Any) -> None:
            label.configure(wraplength=max(event.width - pad, 120))

        container.bind("<Configure>", _on_resize, add="+")

    # -- scrolling -----------------------------------------------------------
    def _scrollframe(self, parent: Any):
        canvas = self.tk.Canvas(parent, bg=_BASE, highlightthickness=0, bd=0)
        vbar = self.tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = self.tk.Frame(canvas, bg=_BASE)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")

        canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def _resize(_e: Any) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(win, width=canvas.winfo_width())

        inner.bind("<Configure>", _resize)
        canvas.bind("<Configure>", _resize)

        def _wheel(event: Any) -> None:
            canvas.yview_scroll(int(-event.delta / 40), "units")

        canvas.bind_all("<MouseWheel>", _wheel)
        canvas.bind_all("<Button-4>", lambda _e: canvas.yview_scroll(-2, "units"))
        canvas.bind_all("<Button-5>", lambda _e: canvas.yview_scroll(2, "units"))
        return canvas, inner

    def _scroll_bottom(self) -> None:
        if self._thread_win is not None:
            self._thread_win.update_idletasks()
            self._thread_win.yview_moveto(1.0)

    # -- interaction ---------------------------------------------------------
    def _on_return(self, _event: Any) -> str:
        self._submit()
        return "break"

    def _submit(self) -> None:
        if self._busy:
            return
        entry = self._entry
        if entry is None or not entry.winfo_exists():
            return
        text = entry.get("1.0", "end").strip()
        if not text:
            return
        entry.delete("1.0", "end")
        self._turns.append({"kind": "user", "text": text})
        self._turns.append({"kind": "assistant", "text": "", "pending": True})
        self._view = "thread"
        self._render_view()
        self._scroll_bottom()

        self._busy = True
        self._set_status("● WORKING", _BLUE)
        self._show_activity("leader", "planning the objective")
        threading.Thread(target=self._run_objective, args=(text,),
                         daemon=True).start()

    def _run_objective(self, objective: str) -> None:
        """Worker thread: run the objective and hand the result to the UI queue.

        Tkinter is not thread-safe, so nothing here touches a widget — the main
        loop picks the result up in :meth:`_drain_results`.
        """
        try:
            report = self.runtime.run_task(objective)
        except Exception as exc:  # pragma: no cover - defensive
            self._results.put((None, str(exc)))
        else:
            self._results.put((report, None))

    def _drain_results(self) -> None:
        while True:
            try:
                report, error = self._results.get_nowait()
            except queue.Empty:
                return
            self._finish_turn(report, error)

    def _finish_turn(self, report: Optional[Dict[str, Any]],
                     error: Optional[str]) -> None:
        self._busy = False
        self._clear_activity()
        if self._turns and self._turns[-1].get("pending"):
            self._turns.pop()
        if report is not None:
            status = report.get("STATUS", "")
            self._turns.append({
                "kind": "assistant", "status": status, "report": report,
            })
            self._set_status("● " + status, _STATUS_COLOR.get(status, _FG3))
        else:
            self._turns.append({
                "kind": "assistant", "text": f"Runtime error: {error}",
            })
            self._set_status("● ERROR", _RED)
        self._render_view()
        self._scroll_bottom()

    def _toggle_kill(self) -> None:
        if self.runtime.killswitch.is_engaged():
            self._release_kill()
        else:
            self._engage_kill()

    def _set_status(self, text: str, color: str) -> None:
        self.status_pill.configure(text=text, fg=color,
                                   bg=_blend(color, _BASE, 0.14))

    # -- live refresh --------------------------------------------------------
    def _refresh_agent_rail(self) -> None:
        monitor = self.runtime.monitor()
        self.team_count.configure(text=str(len(monitor["agents"])))
        self.queue_lbl.configure(text=f"queue {monitor['queue_length']}")
        errors = sum(a.get("error_count", 0) for a in monitor["agents"])
        self.err_lbl.configure(text=f"errors {errors}")

        for child in self.team_body.winfo_children():
            child.destroy()
        for agent in monitor["agents"]:
            row = self.tk.Frame(self.team_body, bg=_GLASS)
            row.pack(fill="x", pady=3, padx=4)
            state = agent.get("state", "IDLE")
            color = _STATE_COLOR.get(state, _FG3)
            self.tk.Label(row, text="●", bg=_GLASS, fg=color,
                          font=self.F(self.f_display, 9)).pack(
                side="left", padx=(0, 8))
            box = self.tk.Frame(row, bg=_GLASS)
            box.pack(side="left", fill="x", expand=True)
            self.tk.Label(
                box, text=agent["name"], bg=_GLASS, fg=_FG,
                font=self.F(self.f_ui, 9, "bold"),
            ).pack(anchor="w")
            detail = agent.get("current_task") or agent.get("last_action") or state.lower()
            self.tk.Label(
                box, text=str(detail)[:28], bg=_GLASS, fg=_FG3,
                font=self.F(self.f_ui, 8),
            ).pack(anchor="w")
            self.tk.Label(
                row, text=str(round(agent.get("elapsed_s", 0))) + "s",
                bg=_GLASS, fg=_FG3, font=self.F(self.f_display, 8),
            ).pack(side="right")
            self._agent_rows[agent["name"]] = row

        engaged = monitor["killswitch"]
        if engaged != self._kill_cache:
            self._kill_cache = engaged
            if engaged:
                self.kill_pill.configure(text="KILLSWITCH ENGAGED", fg=_RED,
                                         bg=_blend(_RED, _BASE, 0.16))
                self.rail_kill.configure(fg=_RED)
                self._set_status("● HALTED", _RED)
            else:
                self.kill_pill.configure(text="KILLSWITCH ARMED", fg=_FG3,
                                         bg=_blend(_FG3, _BASE, 0.14))
                self.rail_kill.configure(fg=_GREEN)
                if not self._busy:
                    self._set_status("● READY", _GREEN)

        self.hint.configure(
            text=f"{len(self.runtime.state.all())} tasks · "
                 f"{len(self.runtime.messages())} messages · "
                 f"home {self.runtime.config.home}"
        )

    def _tick(self) -> None:
        self._spin = (self._spin + 1) % len(_SPINNER)
        if self._activity is not None and self._spin_lbl is not None:
            try:
                self._spin_lbl.configure(text=_SPINNER[self._spin])
            except Exception:  # pragma: no cover - widget race
                pass
        try:
            self._drain_results()
            self._refresh_agent_rail()
        except Exception:  # pragma: no cover - defensive
            pass
        self.root.after(250, self._tick)


def launch(config: Config) -> int:
    """Start the desktop console. Returns a process exit code."""
    try:
        import tkinter  # noqa: F401
    except ImportError as exc:
        print(_TK_HELP, file=sys.stderr)
        print(f"\nunderlying error: {exc}", file=sys.stderr)
        return 1

    runtime = Runtime.build(config)
    app = GlassApp(runtime)
    app.root.mainloop()
    return 0
