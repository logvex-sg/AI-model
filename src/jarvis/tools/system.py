"""Real system metrics and diagnostics (psutil + /proc)."""

from __future__ import annotations

import os
import platform
import time
from pathlib import Path
from typing import Any

import psutil

from jarvis.permissions import PermissionLevel
from jarvis.safety import command_available, run_command
from jarvis.tools.base import Argument, Tool, ToolRegistry


def system_metrics() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    load1, load5, load15 = os.getloadavg()
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "cpu_count": psutil.cpu_count(logical=True),
        "load_average": {"1m": load1, "5m": load5, "15m": load15},
        "memory": {
            "total_mb": round(memory.total / 1048576),
            "used_mb": round(memory.used / 1048576),
            "percent": memory.percent,
        },
        "swap": {"total_mb": round(swap.total / 1048576), "percent": swap.percent},
        "uptime_seconds": round(time.time() - psutil.boot_time()),
    }


def disk_usage() -> dict[str, Any]:
    partitions = []
    for partition in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except (PermissionError, OSError):
            continue
        partitions.append(
            {
                "device": partition.device,
                "mountpoint": partition.mountpoint,
                "fstype": partition.fstype,
                "total_gb": round(usage.total / 1073741824, 2),
                "used_gb": round(usage.used / 1073741824, 2),
                "percent": usage.percent,
            }
        )
    return {"partitions": partitions}


def os_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "system": platform.system(),
        "kernel_release": platform.release(),
        "kernel_version": platform.version(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "hostname": platform.node(),
    }
    os_release = Path("/etc/os-release")
    if os_release.is_file():
        fields = {}
        for line in os_release.read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                fields[key] = value.strip('"')
        info["distribution"] = fields.get("PRETTY_NAME", fields.get("NAME", "unknown"))
    cmdline = Path("/proc/cmdline")
    if cmdline.is_file():
        info["kernel_cmdline"] = cmdline.read_text().strip()
    return info


def kernel_modules(limit: int = 25) -> dict[str, Any]:
    modules_file = Path("/proc/modules")
    if not modules_file.is_file():
        return {"available": False, "reason": "/proc/modules is not readable on this system"}
    modules = []
    for line in modules_file.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 3:
            modules.append({"name": parts[0], "size": int(parts[1]), "used_by": int(parts[2])})
    modules.sort(key=lambda module: module["size"], reverse=True)
    return {"available": True, "count": len(modules), "modules": modules[:limit]}


def hardware_inventory() -> dict[str, Any]:
    """PCI/USB inventory. Reports unavailability instead of faking data."""
    result: dict[str, Any] = {}
    for key, argv in (("pci", ["lspci"]), ("usb", ["lsusb"])):
        if not command_available(argv[0]):
            result[key] = {"available": False, "reason": f"{argv[0]} is not installed"}
            continue
        completed = run_command(argv, timeout=10)
        result[key] = {
            "available": completed["returncode"] == 0,
            "devices": completed["stdout"].splitlines() if completed["returncode"] == 0 else [],
            "error": completed["stderr"] or None,
        }
    return result


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="system.metrics",
            description="Current CPU, memory, swap, load average and uptime.",
            category="SYSTEM",
            permission=PermissionLevel.SAFE,
            handler=system_metrics,
        )
    )
    registry.register(
        Tool(
            name="system.disk_usage",
            description="Disk usage per mounted partition.",
            category="SYSTEM",
            permission=PermissionLevel.SAFE,
            handler=disk_usage,
        )
    )
    registry.register(
        Tool(
            name="system.os_info",
            description="Operating system, distribution and kernel information.",
            category="SYSTEM",
            permission=PermissionLevel.SAFE,
            handler=os_info,
        )
    )
    registry.register(
        Tool(
            name="system.kernel_modules",
            description="Loaded kernel modules read from /proc/modules.",
            category="KERNEL",
            permission=PermissionLevel.SAFE,
            handler=kernel_modules,
            arguments=(
                Argument("limit", int, "Maximum number of modules to return.", False, 25),
            ),
        )
    )
    registry.register(
        Tool(
            name="system.hardware",
            description="PCI and USB device inventory (requires lspci/lsusb).",
            category="HARDWARE",
            permission=PermissionLevel.SAFE,
            handler=hardware_inventory,
            timeout=25.0,
        )
    )
