"""Environment diagnostics.

Implements the startup health assessment from operating-model section 12 and
backs the ``aegis doctor`` command. Every check reports OK / WARN / MISSING and
a concrete detail string. A missing optional component is WARN, never a hard
failure — the platform degrades rather than refusing to start.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import Config
from .killswitch import Killswitch

OK = "OK"
WARN = "WARN"
MISSING = "MISSING"


@dataclass
class Check:
    """One diagnostic result."""

    component: str
    status: str
    detail: str
    required: bool = False

    @property
    def ok(self) -> bool:
        return self.status in {OK, WARN}

    def to_dict(self) -> Dict[str, object]:
        return {
            "component": self.component,
            "status": self.status,
            "detail": self.detail,
            "ok": self.ok,
            "required": self.required,
        }


@dataclass
class DiagnosticsReport:
    """The full health assessment."""

    checks: List[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True unless a *required* check failed."""
        return all(c.ok for c in self.checks if c.required)

    @property
    def degraded(self) -> bool:
        """True if optional components are missing."""
        return any(c.status in {WARN, MISSING} for c in self.checks)

    def to_dict(self) -> Dict[str, object]:
        return {
            "ok": self.ok,
            "degraded": self.degraded,
            "checks": [c.to_dict() for c in self.checks],
        }

    def render(self) -> str:
        """Plain-text table, as shown in the operating model."""
        width = max((len(c.component) for c in self.checks), default=9)
        lines = [f"{'COMPONENT'.ljust(width)}  STATUS   DETAIL", "-" * (width + 30)]
        for c in self.checks:
            lines.append(f"{c.component.ljust(width)}  {c.status:<7}  {c.detail}")
        return "\n".join(lines)


def _tool(name: str) -> Optional[str]:
    return shutil.which(name)


def _check_tool(component: str, *names: str, required: bool = False) -> Check:
    for name in names:
        path = _tool(name)
        if path:
            return Check(component, OK, path, required)
    return Check(component, MISSING, f"none of {', '.join(names)} on PATH", required)


def _dns_ok() -> bool:
    try:
        socket.gethostbyname("localhost")
        return True
    except OSError:
        return False


def _outbound_ok(timeout: float = 2.0) -> bool:
    for host, port in (("1.1.1.1", 53), ("8.8.8.8", 53)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def _run(argv: List[str], timeout: float = 3.0) -> str:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (proc.stdout or proc.stderr).strip().splitlines()[0] if (proc.stdout or proc.stderr) else ""


def _memory_detail() -> str:
    try:
        info: Dict[str, str] = {}
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                key, _, value = line.partition(":")
                info[key.strip()] = value.strip()
        total_kb = int(info.get("MemTotal", "0").split()[0])
        return f"{total_kb / 1024 / 1024:.1f} GiB"
    except (OSError, ValueError, IndexError):
        return "unknown"


def _disk_detail(path: Path) -> str:
    try:
        usage = shutil.disk_usage(path)
        free_gib = usage.free / 1024 ** 3
        return f"{free_gib:.1f} GiB free on {path}"
    except OSError as exc:
        return f"unavailable ({exc})"


def _root_detail() -> Tuple[str, str]:
    if os.geteuid() == 0:
        return OK, "running as root"
    if _tool("sudo"):
        return WARN, "not root; sudo available"
    return WARN, "not root; sudo not found"


def run_diagnostics(config: Optional[Config] = None) -> DiagnosticsReport:
    """Execute the full health assessment."""
    report = DiagnosticsReport()
    add = report.checks.append

    add(Check("OS", OK, f"{platform.system()} {platform.release()}"))
    add(Check("KERNEL", OK, platform.release()))
    add(Check("ARCH", OK, platform.machine()))
    add(Check("CPU", OK, f"{os.cpu_count() or 0} cores"))
    add(Check("MEMORY", OK, _memory_detail()))

    if config is not None:
        add(Check("FILESYSTEM", OK, str(config.home)))

    dns = _dns_ok()
    add(Check("DNS", OK if dns else WARN, "resolver reachable" if dns else "resolution failed"))

    net = _outbound_ok()
    add(Check("NETWORK", OK if net else WARN, "outbound TCP reachable" if net else "no outbound TCP"))

    add(Check("PYTHON", OK, platform.python_version()))
    add(_check_tool("GIT", "git", required=True))
    add(_check_tool("DOCKER", "docker"))
    add(_check_tool("COMPILER", "gcc", "clang", "cc"))
    add(_check_tool("JAVA", "java"))
    add(_check_tool("NODE", "node"))
    add(_check_tool("PKGMGR", "apt-get", "apt", "dnf", "pacman", "apk"))
    add(_check_tool("GITLAB", "glab"))
    add(_check_tool("GITHUB", "gh"))

    if config is not None:
        writable = os.access(config.home, os.W_OK)
        add(Check("HOME_WRITABLE", OK if writable else MISSING, str(config.home), required=True))
        add(Check("DISK", OK, _disk_detail(config.home)))

    root_status, root_detail = _root_detail()
    add(Check("ROOT", root_status, root_detail))

    if config is not None:
        ks = Killswitch(config)
        engaged = ks.is_engaged()
        add(Check("KILLSWITCH", OK, "ENGAGED" if engaged else "READY"))
        add(Check("MODEL", OK if config.model_provider != "none" else WARN,
                  config.model_provider if config.model_provider != "none"
                  else "no LLM configured; deterministic core only"))

    return report
