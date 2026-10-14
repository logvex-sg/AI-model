"""Security tooling: scope enforcement and safe, passive-first recon.

The tools here are deliberately conservative. ``recon`` performs a TCP connect
check against a small set of common ports and a banner grab — both of which are
the same thing a browser does when it connects. Anything more aggressive is
left to operator-supplied tools, which must still pass the scope check and the
risk policy.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .scope import Scope

#: Ports probed by a default recon sweep.
COMMON_PORTS: Dict[int, str] = {
    21: "ftp",
    22: "ssh",
    23: "telnet",
    25: "smtp",
    53: "dns",
    80: "http",
    110: "pop3",
    143: "imap",
    443: "https",
    445: "smb",
    3306: "mysql",
    5432: "postgres",
    6379: "redis",
    8080: "http-alt",
    8443: "https-alt",
}


@dataclass
class PortResult:
    port: int
    service: str
    open: bool
    banner: str = ""

    def to_dict(self) -> dict:
        return {"port": self.port, "service": self.service, "open": self.open, "banner": self.banner}


@dataclass
class ReconReport:
    target: str
    resolved: List[str] = field(default_factory=list)
    ports: List[PortResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def open_ports(self) -> List[int]:
        return [p.port for p in self.ports if p.open]

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "resolved": self.resolved,
            "open_ports": self.open_ports,
            "ports": [p.to_dict() for p in self.ports],
            "errors": self.errors,
        }


class Recon:
    """Passive/lightweight reconnaissance against an authorized target."""

    def __init__(self, scope: Scope, *, timeout: float = 1.0) -> None:
        self.scope = scope
        self.timeout = timeout

    def resolve(self, host: str) -> List[str]:
        self.scope.require(host)
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return []
        return sorted({info[4][0] for info in infos})

    def check_port(self, host: str, port: int, service: str = "") -> PortResult:
        self.scope.require(host)
        name = service or COMMON_PORTS.get(port, "unknown")
        try:
            with socket.create_connection((host, port), timeout=self.timeout) as sock:
                banner = self._grab_banner(sock)
            return PortResult(port=port, service=name, open=True, banner=banner)
        except (OSError, socket.timeout):
            return PortResult(port=port, service=name, open=False)

    def scan(self, host: str, ports: Optional[List[int]] = None) -> ReconReport:
        """Connect-scan the authorized target and collect banners."""
        self.scope.require(host)
        report = ReconReport(target=host, resolved=self.resolve(host))
        for port in ports or sorted(COMMON_PORTS):
            report.ports.append(self.check_port(host, port))
        return report

    @staticmethod
    def _grab_banner(sock: socket.socket) -> str:
        try:
            sock.settimeout(0.5)
            data = sock.recv(256)
            return data.decode("utf-8", errors="replace").strip()[:200]
        except (OSError, socket.timeout):
            return ""
