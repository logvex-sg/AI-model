"""Desktop application (Tkinter).

A single window with tabs for the agent status, a command console, the task
manager, the audit log, the pentest workspace, and the killswitch. Tkinter
ships with CPython but needs the system Tk libraries; when they are missing
:func:`launch` reports a precise, actionable error and exits non-zero instead
of crashing with an opaque import failure.
"""

from __future__ import annotations

import json
import platform
import shutil
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
    "Then re-run `kali-ops gui`."
)


def _import_tk():
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - depends on host libs
        raise RuntimeError(f"{_TK_HELP}\n\nunderlying error: {exc}") from exc
    return tk, ttk


class DesktopApp:
    """The Tkinter desktop application."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.tk, self.ttk = _import_tk()
        self.root = self.tk.Tk()
        self.root.title("KALI-OPS")
        self.root.geometry("960x640")
        self._build()

    # -- layout --------------------------------------------------------------
    def _build(self) -> None:
        notebook = self.ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)

        self.status_text = self._add_text_tab(notebook, "Agents")
        self.console_out = self._add_text_tab(notebook, "Console")
        self.tasks_text = self._add_text_tab(notebook, "Tasks")
        self.log_text = self._add_text_tab(notebook, "Logs")
        self.pentest_text = self._add_text_tab(notebook, "Pentest")

        self._build_console_controls(notebook)
        self._build_killswitch()

        self.refresh()
        self.root.after(3000, self._auto_refresh)

    def _add_text_tab(self, notebook: Any, title: str) -> Any:
        frame = self.ttk.Frame(notebook)
        notebook.add(frame, text=title)
        text = self.tk.Text(frame, wrap="none", font=("monospace", 10))
        text.pack(fill="both", expand=True)
        return text

    def _build_console_controls(self, notebook: Any) -> None:
        frame = self.ttk.Frame(self.root)
        frame.pack(fill="x")
        self.entry = self.ttk.Entry(frame)
        self.entry.pack(side="left", fill="x", expand=True, padx=4, pady=4)
        self.entry.bind("<Return>", lambda _event: self._run_command())
        self.ttk.Button(frame, text="Run", command=self._run_command).pack(side="left", padx=4)

    def _build_killswitch(self) -> None:
        bar = self.ttk.Frame(self.root)
        bar.pack(fill="x")
        self.ttk.Button(bar, text="KILLSWITCH", command=self._engage_kill).pack(side="right", padx=4, pady=4)
        self.ttk.Button(bar, text="Resume", command=self._release_kill).pack(side="right", padx=4, pady=4)
        self.kill_label = self.ttk.Label(bar, text="")
        self.kill_label.pack(side="left", padx=8)

    # -- data views ----------------------------------------------------------
    def refresh(self) -> None:
        self._set(self.status_text, json.dumps(self.runtime.introspect(), indent=2, default=str))
        self._set(self.tasks_text, json.dumps([t.__dict__ for t in self.runtime.state.all()], indent=2, default=str))
        self._set(self.log_text, json.dumps(self.runtime.log.read(limit=50), indent=2, default=str))
        engaged = self.runtime.killswitch.is_engaged()
        self.kill_label.configure(
            text=f"KILLSWITCH: {'ENGAGED' if engaged else 'disengaged'}"
            + (f" ({self.runtime.killswitch.reason()})" if engaged else "")
        )

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
        output = result.data.get("stdout") or result.data.get("stderr") or result.summary
        self._append(self.console_out, output + "\n")

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
    """Entry point used by ``kali-ops gui``."""
    runtime = Runtime.build(config)
    try:
        return DesktopApp(runtime).run()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
