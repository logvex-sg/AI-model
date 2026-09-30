"""Authorized-scope enforcement for security testing.

This module is the boundary between "the agent has root on its own box" and
"the agent may attack a remote host". Only targets that are this machine
(loopback or one of its own interface addresses) or explicitly listed in
``authorized_hosts`` may be actively tested. Everything else is refused.

Note the deliberate omission: a bare RFC1918 address such as ``10.0.0.5`` is
**not** treated as local. Another host on the same private network is still
another host, and testing it needs explicit authorization.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Iterable, List, Set

from ..errors import ScopeViolation

_LOOPBACK_NAMES = {"localhost", "ip6-localhost", "ip6-loopback"}


def local_addresses() -> Set[str]:
    """Addresses belonging to this machine (best effort)."""
    addrs: Set[str] = {"127.0.0.1", "::1"}
    try:
        _, _, ips = socket.gethostbyname_ex(socket.gethostname())
        addrs.update(ips)
    except OSError:
        pass
    return addrs


@dataclass
class Scope:
    """The set of hosts authorized for active security testing."""

    authorized_hosts: Set[str]

    @classmethod
    def from_config(cls, authorized_hosts: Iterable[str]) -> "Scope":
        normalized = {h.strip().lower() for h in authorized_hosts if h and h.strip()}
        return cls(authorized_hosts=normalized)

    def allows(self, host: str) -> bool:
        """Return True only if *host* is this machine or explicitly authorized."""
        target = (host or "").strip().lower()
        if not target:
            return False
        if target in _LOOPBACK_NAMES or target in self.authorized_hosts:
            return True
        local = local_addresses()
        for addr in self._resolve(target):
            if addr in local:
                return True
        return False

    def require(self, host: str) -> None:
        """Raise :class:`ScopeViolation` unless *host* is authorized."""
        if not self.allows(host):
            raise ScopeViolation(
                f"host {host!r} is outside the authorized scope. Add it to "
                f"authorized_hosts only if you own it or have written permission "
                f"to test it."
            )

    @staticmethod
    def _resolve(host: str) -> List[str]:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return []
        return list({info[4][0] for info in infos})


def is_local_host(host: str) -> bool:
    """True if *host* refers to this machine."""
    return Scope.from_config([]).allows(host)
