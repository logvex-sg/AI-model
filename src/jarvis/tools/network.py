"""Network diagnostics. Read-only, local-host oriented."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import psutil

from jarvis.permissions import PermissionLevel
from jarvis.safety import SafetyError, command_available, run_command
from jarvis.tools.base import Argument, Tool, ToolRegistry


def interfaces() -> dict[str, Any]:
    stats = psutil.net_if_stats()
    result = []
    for name, addresses in psutil.net_if_addrs().items():
        entry: dict[str, Any] = {"name": name, "addresses": [], "up": False, "speed_mbps": None}
        if name in stats:
            entry["up"] = stats[name].isup
            entry["speed_mbps"] = stats[name].speed or None
        for address in addresses:
            family = {
                socket.AF_INET: "ipv4",
                socket.AF_INET6: "ipv6",
                psutil.AF_LINK: "mac",
            }.get(address.family)
            if family:
                entry["addresses"].append({"family": family, "address": address.address})
        result.append(entry)
    return {"interfaces": result}


def listening_sockets(limit: int = 50) -> dict[str, Any]:
    sockets = []
    try:
        connections = psutil.net_connections(kind="inet")
    except psutil.AccessDenied:
        return {"available": False, "reason": "listing sockets requires elevated privileges"}
    for connection in connections:
        if connection.status != psutil.CONN_LISTEN or connection.laddr is None:
            continue
        sockets.append(
            {
                "address": connection.laddr.ip,
                "port": connection.laddr.port,
                "pid": connection.pid,
                "family": "ipv6" if connection.family == socket.AF_INET6 else "ipv4",
            }
        )
    sockets.sort(key=lambda item: item["port"])
    return {"available": True, "count": len(sockets), "sockets": sockets[:limit]}


def dns_config() -> dict[str, Any]:
    resolv = Path("/etc/resolv.conf")
    if not resolv.is_file():
        return {"available": False, "reason": "/etc/resolv.conf not present"}
    nameservers = [
        line.split()[1]
        for line in resolv.read_text().splitlines()
        if line.startswith("nameserver") and len(line.split()) > 1
    ]
    return {"available": True, "nameservers": nameservers}


def routes() -> dict[str, Any]:
    if not command_available("ip"):
        return {"available": False, "reason": "'ip' (iproute2) is not installed"}
    completed = run_command(["ip", "route", "show"], timeout=10)
    if completed["returncode"] != 0:
        return {"available": False, "reason": completed["stderr"]}
    return {"available": True, "routes": completed["stdout"].splitlines()}


def resolve_host(host: str) -> dict[str, Any]:
    """Resolve a hostname through the system resolver."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SafetyError(f"could not resolve '{host}': {exc.strerror}") from exc
    addresses = sorted({info[4][0] for info in infos})
    return {"host": host, "addresses": addresses}


def check_connectivity(host: str = "127.0.0.1", port: int = 22, timeout: float = 3.0):
    """Attempt a TCP connection to an explicitly named host and port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"host": host, "port": port, "reachable": True}
    except OSError as exc:
        return {"host": host, "port": port, "reachable": False, "error": str(exc)}


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            "network.interfaces",
            "List network interfaces with addresses and link state.",
            "NETWORK",
            PermissionLevel.SAFE,
            interfaces,
        )
    )
    registry.register(
        Tool(
            "network.listening",
            "List listening TCP/UDP sockets.",
            "NETWORK",
            PermissionLevel.SAFE,
            listening_sockets,
            (Argument("limit", int, "Maximum sockets to return.", False, 50),),
        )
    )
    registry.register(
        Tool(
            "network.dns",
            "Show configured DNS nameservers.",
            "NETWORK",
            PermissionLevel.SAFE,
            dns_config,
        )
    )
    registry.register(
        Tool(
            "network.routes",
            "Show the kernel routing table.",
            "NETWORK",
            PermissionLevel.SAFE,
            routes,
        )
    )
    registry.register(
        Tool(
            "network.resolve",
            "Resolve a hostname to IP addresses.",
            "NETWORK",
            PermissionLevel.MODERATE,
            resolve_host,
            (Argument("host", str, "Hostname to resolve."),),
        )
    )
    registry.register(
        Tool(
            "network.connectivity",
            "Test a TCP connection to an explicitly specified host and port.",
            "NETWORK",
            PermissionLevel.MODERATE,
            check_connectivity,
            (
                Argument("host", str, "Target host.", False, "127.0.0.1"),
                Argument("port", int, "Target port.", False, 22),
                Argument("timeout", float, "Connection timeout in seconds.", False, 3.0),
            ),
        )
    )
