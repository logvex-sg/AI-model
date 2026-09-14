"""Process and application tools."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from typing import Any

import psutil

from jarvis.permissions import PermissionLevel
from jarvis.safety import SafetyError
from jarvis.tools.base import Argument, Tool, ToolRegistry

PROTECTED_PIDS = {0, 1}


def list_processes(limit: int = 15, sort_by: str = "cpu") -> dict[str, Any]:
    if sort_by not in {"cpu", "memory"}:
        raise SafetyError("sort_by must be 'cpu' or 'memory'")
    processes = []
    fields = ["pid", "name", "username", "cpu_percent", "memory_percent"]
    for process in psutil.process_iter(fields):
        info = process.info
        processes.append(
            {
                "pid": info["pid"],
                "name": info["name"],
                "user": info["username"],
                "cpu_percent": info["cpu_percent"] or 0.0,
                "memory_percent": round(info["memory_percent"] or 0.0, 2),
            }
        )
    key = "cpu_percent" if sort_by == "cpu" else "memory_percent"
    processes.sort(key=lambda item: item[key], reverse=True)
    return {"count": len(processes), "processes": processes[:limit]}


def inspect_process(pid: int) -> dict[str, Any]:
    try:
        process = psutil.Process(pid)
        with process.oneshot():
            return {
                "pid": pid,
                "name": process.name(),
                "status": process.status(),
                "user": process.username(),
                "cmdline": process.cmdline(),
                "created": process.create_time(),
                "cpu_percent": process.cpu_percent(interval=0.1),
                "memory_mb": round(process.memory_info().rss / 1048576, 2),
                "num_threads": process.num_threads(),
            }
    except psutil.NoSuchProcess as exc:
        raise SafetyError(f"no process with pid {pid}") from exc
    except psutil.AccessDenied as exc:
        raise SafetyError(f"access denied for pid {pid}") from exc


def terminate_process(pid: int, force: bool = False) -> dict[str, Any]:
    if pid in PROTECTED_PIDS or pid == os.getpid():
        raise SafetyError(f"refusing to signal protected pid {pid}")
    try:
        process = psutil.Process(pid)
        name = process.name()
        process.send_signal(signal.SIGKILL if force else signal.SIGTERM)
        gone, _alive = psutil.wait_procs([process], timeout=5)
    except psutil.NoSuchProcess as exc:
        raise SafetyError(f"no process with pid {pid}") from exc
    except psutil.AccessDenied as exc:
        raise SafetyError(f"access denied for pid {pid}") from exc
    return {
        "pid": pid,
        "name": name,
        "signal": "SIGKILL" if force else "SIGTERM",
        "exited": bool(gone),
    }


def find_application(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    running = [
        process.info["pid"]
        for process in psutil.process_iter(["pid", "name"])
        if process.info["name"] == name
    ]
    return {"name": name, "installed": path is not None, "path": path, "running_pids": running}


def launch_application(name: str, detach: bool = True) -> dict[str, Any]:
    executable = shutil.which(name)
    if executable is None:
        raise SafetyError(f"application '{name}' is not installed")
    process = subprocess.Popen(  # noqa: S603 - resolved executable, no shell
        [executable],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=detach,
    )
    return {"name": name, "path": executable, "pid": process.pid}


def close_application(name: str, force: bool = False) -> dict[str, Any]:
    signalled = []
    for process in psutil.process_iter(["pid", "name"]):
        if process.info["name"] != name or process.info["pid"] in PROTECTED_PIDS:
            continue
        try:
            process.send_signal(signal.SIGKILL if force else signal.SIGTERM)
            signalled.append(process.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {"name": name, "signalled_pids": signalled, "count": len(signalled)}


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            "process.list",
            "List running processes sorted by CPU or memory usage.",
            "PROCESSES",
            PermissionLevel.SAFE,
            list_processes,
            (
                Argument("limit", int, "Number of processes to return.", False, 15),
                Argument("sort_by", str, "'cpu' or 'memory'.", False, "cpu"),
            ),
        )
    )
    registry.register(
        Tool(
            "process.inspect",
            "Inspect a single process by pid.",
            "PROCESSES",
            PermissionLevel.SAFE,
            inspect_process,
            (Argument("pid", int, "Process id."),),
        )
    )
    registry.register(
        Tool(
            "process.terminate",
            "Send SIGTERM (or SIGKILL) to a process.",
            "PROCESSES",
            PermissionLevel.DESTRUCTIVE,
            terminate_process,
            (
                Argument("pid", int, "Process id."),
                Argument("force", bool, "Use SIGKILL instead of SIGTERM.", False, False),
            ),
        )
    )
    registry.register(
        Tool(
            "app.find",
            "Check whether an application is installed and running.",
            "APPLICATIONS",
            PermissionLevel.SAFE,
            find_application,
            (Argument("name", str, "Executable name."),),
        )
    )
    registry.register(
        Tool(
            "app.launch",
            "Launch a desktop application by executable name.",
            "APPLICATIONS",
            PermissionLevel.MODERATE,
            launch_application,
            (
                Argument("name", str, "Executable name."),
                Argument("detach", bool, "Start in a new session.", False, True),
            ),
        )
    )
    registry.register(
        Tool(
            "app.close",
            "Terminate all processes of an application by name.",
            "APPLICATIONS",
            PermissionLevel.HIGH,
            close_application,
            (
                Argument("name", str, "Executable name."),
                Argument("force", bool, "Use SIGKILL instead of SIGTERM.", False, False),
            ),
        )
    )
