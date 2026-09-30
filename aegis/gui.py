"""Desktop application — KALI-AEGIS security console (Tkinter).

A dense, dark operations console rather than a chat window: a sidebar of
workspaces, a main pane, and a persistent agent bar with the killswitch.

Tkinter ships with CPython but needs the system Tk libraries. When they are
missing :func:`launch` reports a precise, actionable error and exits non-zero
instead of crashing with an opaque import failure.
"""

from __future__ import annotations

import json
import platform
import sys
import threading
from typing import Any, Callable, Dict, List, Optional

from .config import Config
from .orchestrator import Runtime

_TK_HELP = (
    "Tkinter is unavailable. Install the Tk libraries for your platform, e.g.\n"
    "  Debian/Ubuntu/Kali:  sudo apt-get install -y python3-tk\n"
    "  Fedora:              sudo dnf install -y python3-tkinter\n"
    "  macOS (Homebrew):    brew install python-tk\n"
    "Then re-run `aegis gui`."
)

# Dark security-console palette.
_BG = "#0d1117"
_BG_PANEL = "#161b22"
_BG_BAR = "#1c2128"
_FG = "#c9d1d9"
_FG_DIM = "#8b949e"
_ACCENT = "#58a6ff"
_OK = "#3fb950"
_WARN = "#d29922"
_ERR = "#f85149"

_AGENT_COLORS = {
    "IDLE": _FG_DIM,
    "THINKING": _ACCENT,
    "PLANNING": _ACCENT,
    "EXECUTING": _OK,
    "TESTING": _WARN,
    "WAITING": _FG_DIM,
    "ERROR": _ERR,
    "STOPPED": _ERR,
}

_PANELS = [
    "Dashboard", "Agents", "Terminal", "Tasks",
    "Security", "Network", "Logs", "Settings",
]


def _import_tk():
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - depends on host libs
        raise RuntimeError(f"{_TK_HELP}\n\nunderlying error: {exc}") from exc
    return tk, ttk


class DesktopApp:
    """The KALI-AEGIS Tkinter console."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.tk, self.ttk = _import_tk()
        self.root = self.tk.Tk()
        self.root.title("KALI-AEGIS")
        self.root.geometry("1100x700")
        self.root.configure(bg=_BG)
        self._panels: Dict[str, Any] = {}
        self._agent_labels: Dict[str, Any] = {}
        self._build()

    # -- layout --------------------------------------------------------------
    def _build(self) -> None:
        self._build_header()
        body = self.tk.Frame(self.root, bg=_BG)
        body.pack(fill="both", expand=True)
        self._build_sidebar(body)
        self._build_main(body)
        self._build_agent_bar()
        self.refresh()
        self.root.after(3000, self._auto_refresh)

    def _build_header(self) -> None:
        header = self.tk.Frame(self.root, bg=_BG_BAR, height=44)
        header.pack(fill="x")
        self.tk.Label(
            header, text="  KALI // AEGIS", bg=_BG_BAR, fg=_ACCENT,
            font=("monospace", 14, "bold"),
        ).pack(side="left", pady=8)
        self.header_status = self.tk.Label(
            header, text="SYSTEM ONLINE", bg=_BG_BAR, fg=_OK,
            font=("monospace", 10),
        )
        self.header_status.pack(side="right", padx=14)

    def _build_sidebar(self, parent: Any) -> None:
        side = self.tk.Frame(parent, bg=_BG_PANEL, width=150)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        for name in _PANELS:
            btn = self.tk.Button(
                side, text=name, anchor="w", bd=0, bg=_BG_PANEL, fg=_FG,
                activebackground=_BG_BAR, activeforeground=_ACCENT,
                font=("monospace", 10), padx=12, pady=6,
                command=lambda n=name: self._show(n),
            )
            btn.pack(fill="x")

    def _build_main(self, parent: Any) -> None:
        self.main = self.tk.Frame(parent, bg=_BG)
        self.main.pack(side="right", fill="both", expand=True)

        for name in _PANELS:
            frame = self.tk.Frame(self.main, bg=_BG)
            self._panels[name] = frame

        self.dash_text = self._add_text(self._panels["Dashboard"])
        self.agents_text = self._add_text(self._panels["Agents"])
        self.tasks_text = self._add_text(self._panels["Tasks"])
        self.security_text = self._add_text(self._panels["Security"])
        self.network_text = self._add_text(self._panels["Network"])
        self.logs_text = self._add_text(self._panels["Logs"])
        self.settings_text = self._add_text(self._panels["Settings"])
        self._build_terminal(self._panels["Terminal"])
        self._show("Dashboard")

    def _add_text(self, frame: Any) -> Any:
        text = self.tk.Text(
            frame, wrap="none", bg=_BG, fg=_FG, insertbackground=_FG,
            font=("monospace", 10), bd=0, highlightthickness=0,
        )
        text.pack(fill="both", expand=True, padx=8, pady=8)
        return text

    def _build_terminal(self, frame: Any) -> None:
        controls = self.tk.Frame(frame, bg=_BG)
        controls.pack(side="bottom", fill="x", padx=8, pady=8)
        self.entry = self.tk.Entry(
            controls, bg=_BG_PANEL, fg=_FG, insertbackground=_ACCENT,
            font=("monospace", 10), bd=0,
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.entry.bind("<Return>", lambda _e: self._run_command())
        self.tk.Button(
            controls, text="Run", bg=_BG_BAR, fg=_ACCENT, bd=0,
            activebackground=_BG_PANEL, activeforeground=_ACCENT,
            font=("monospace", 10), padx=14, command=self._run_command,
        ).pack(side="left", padx=6)
        self.console_out = self._add_text(frame)

    def _build_agent_bar(self) -> None:
        bar = self.tk.Frame(self.root, bg=_BG_BAR, height=34)
        bar.pack(fill="x", side="bottom")
        for name in ("leader", "builder", "pentester", "executor"):
            label = self.tk.Label(
                bar, text=f"{name.upper()} o", bg=_BG_BAR, fg=_FG_DIM,
                font=("monospace", 10),
            )
            label.pack(side="left", padx=10, pady=6)
            self._agent_labels[name] = label
        self.ttk.Button(bar, text="KILLSWITCH", command=self._engage_kill).pack(
            side="right", padx=4, pady=4
        )
        self.ttk.Button(bar, text="Resume", command=self._release_kill).pack(
            side="right", padx=4, pady=4
        )
        self.kill_label = self.tk.Label(
            bar, text="", bg=_BG_BAR, fg=_FG_DIM, font=("monospace", 10)
        )
        self.kill_label.pack(side="right", padx=10)

    def _show(self, name: str) -> None:
        for frame in self._panels.values():
            frame.pack_forget()
        self._panels[name].pack(fill="both", expand=True)

    # -- data views ----------------------------------------------------------
    def refresh(self) -> None:
        rt = self.runtime
        model = rt.introspect()
        monitor = rt.monitor()

        self._set(self.dash_text, self._render_dashboard(model, monitor))
        self._set(self.agents_text, json.dumps(model["team"], indent=2, default=str))
        self._set(
            self.tasks_text,
            json.dumps(
                [
                    {"id": t.id, "objective": t.objective, "status": t.status,
                     "subtasks": len(t.subtasks)}
                    for t in rt.state.all()
                ],
                indent=2, default=str,
            ),
        )
        self._set(self.security_text, self._render_security())
        self._set(self.network_text, json.dumps(monitor, indent=2, default=str))
        self._set(self.logs_text, json.dumps(rt.log.read(limit=50), indent=2, default=str))
        self._set(self.settings_text, json.dumps(rt.config.to_dict(), indent=2, default=str))

        for agent in monitor["agents"]:
            label = self._agent_labels.get(agent["name"])
            if label is None:
                continue
            state = agent["state"]
            label.configure(
                text=f"{agent['name'].upper()} \u25cf {state}",
                fg=_AGENT_COLORS.get(state, _FG_DIM),
            )

        engaged = rt.killswitch.is_engaged()
        self.kill_label.configure(
            text=f"KILLSWITCH: {'ENGAGED' if engaged else 'disengaged'}",
            fg=_ERR if engaged else _FG_DIM,
        )
        self.header_status.configure(
            text="HALTED" if engaged else "SYSTEM ONLINE",
            fg=_ERR if engaged else _OK,
        )

    def _render_dashboard(self, model: Dict[str, Any], monitor: Dict[str, Any]) -> str:
        lines = [
            "KALI-AEGIS  //  operations console",
            "=" * 52,
            f"  platform      {platform.system()} {platform.release()}",
            f"  python        {platform.python_version()}",
            f"  home          {self.runtime.config.home}",
            f"  model         {model['model_provider']}"
            + ("" if model["llm_configured"] else "  (deterministic core only)"),
            f"  dry-run       {model['dry_run']}",
            f"  killswitch    {'ENGAGED' if model['killswitch_engaged'] else 'ready'}",
            "",
            "AGENTS",
            "-" * 52,
        ]
        for agent in monitor["agents"]:
            lines.append(
                f"  {agent['name']:<10} {agent['state']:<10} "
                f"done={agent['completed']:<3} err={agent['error_count']:<3} "
                f"{agent['last_action'][:28]}"
            )
        lines += [
            "",
            "WORK QUEUE",
            "-" * 52,
            f"  queued        {monitor['queue_length']}",
            f"  interrupted   {len(monitor['interrupted'])}",
        ]
        return "\n".join(lines)

    def _render_security(self) -> str:
        from .security.scope import Scope, local_addresses

        rt = self.runtime
        scope = Scope.from_config(rt.config.authorized_hosts)
        lines = [
            "SECURITY POSTURE",
            "=" * 52,
            "  authorized hosts",
        ]
        if scope.authorized_hosts:
            lines += [f"    {h}" for h in sorted(scope.authorized_hosts)]
        else:
            lines.append("    (none configured)")
        lines += ["", "  always permitted (this machine)"]
        lines += [f"    {a}" for a in sorted(local_addresses())]
        lines += [
            "",
            f"  auto-approve high risk   {rt.config.auto_approve_high_risk}",
            f"  max retries              {rt.config.max_retries}",
            f"  allow root               {rt.config.allow_root}",
            "",
            "  Note: root on this machine does not authorize testing",
            "  any other host. Add targets to authorized_hosts explicitly.",
        ]
        return "\n".join(lines)

    def _auto_refresh(self) -> None:
        self.refresh()
        self.root.after(3000, self._auto_refresh)

    @staticmethod
    def _set(widget: Any, content: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", content)

    # -- actions -------------------------------------------------------------
    def _run_command(self) -> None:
        command = self.entry.get().strip()
        if not command:
            return
        self.entry.delete(0, "end")
        self._append(self.console_out, f"$ {command}\n")
        threading.Thread(target=self._execute, args=(command,), daemon=True).start()

    def _execute(self, command: str) -> None:
        result = self.runtime.executor.execute(command)
        data = result.data or {}
        output = data.get("stdout") or data.get("stderr") or result.summary
        self._append(self.console_out, output)
        if not output.endswith("\n"):
            self._append(self.console_out, "\n")

    def _engage_kill(self) -> None:
        self.runtime.killswitch.engage("desktop killswitch button")
        self.refresh()

    def _release_kill(self) -> None:
        self.runtime.killswitch.release()
        self.refresh()

    def _append(self, widget: Any, text: str) -> None:
        widget.insert("end", text)
        widget.see("end")

    def run(self) -> int:
        self.root.mainloop()
        return 0


def launch(config: Config) -> int:
    """Entry point used by ``aegis gui``."""
    runtime = Runtime.build(config)
    try:
        return DesktopApp(runtime).run()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
