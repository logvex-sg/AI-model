"""Tests for scope enforcement and reconnaissance."""

from __future__ import annotations

import unittest

from aegis.errors import ScopeViolation
from aegis.security.recon import COMMON_PORTS, Recon
from aegis.security.scope import Scope, is_local_host, local_addresses


class ScopeTests(unittest.TestCase):
    def test_loopback_is_always_allowed(self) -> None:
        scope = Scope.from_config([])
        self.assertTrue(scope.allows("localhost"))
        self.assertTrue(scope.allows("127.0.0.1"))

    def test_private_lan_is_not_implicitly_allowed(self) -> None:
        # Another host on the LAN is still another host.
        scope = Scope.from_config([])
        self.assertFalse(scope.allows("10.0.0.5"))
        self.assertFalse(scope.allows("192.168.1.50"))

    def test_explicitly_authorized_host_is_allowed(self) -> None:
        scope = Scope.from_config(["lab.example.internal"])
        self.assertTrue(scope.allows("lab.example.internal"))

    def test_require_raises_for_unauthorized_host(self) -> None:
        scope = Scope.from_config([])
        with self.assertRaises(ScopeViolation):
            scope.require("evil.example.com")

    def test_empty_host_is_refused(self) -> None:
        scope = Scope.from_config([])
        self.assertFalse(scope.allows(""))

    def test_local_addresses_include_loopback(self) -> None:
        self.assertIn("127.0.0.1", local_addresses())

    def test_is_local_host_for_loopback(self) -> None:
        self.assertTrue(is_local_host("127.0.0.1"))


class ReconTests(unittest.TestCase):
    def test_scan_of_unauthorized_target_is_refused(self) -> None:
        recon = Recon(Scope.from_config([]), timeout=0.2)
        with self.assertRaises(ScopeViolation):
            recon.scan("example.com")

    def test_scan_of_loopback_runs(self) -> None:
        recon = Recon(Scope.from_config([]), timeout=0.2)
        report = recon.scan("127.0.0.1", ports=[1])
        self.assertEqual(report.target, "127.0.0.1")
        self.assertEqual(len(report.ports), 1)

    def test_check_port_reports_closed_port(self) -> None:
        recon = Recon(Scope.from_config([]), timeout=0.2)
        result = recon.check_port("127.0.0.1", 1)
        self.assertFalse(result.open)

    def test_common_ports_include_http(self) -> None:
        self.assertEqual(COMMON_PORTS[80], "http")


if __name__ == "__main__":
    unittest.main()
