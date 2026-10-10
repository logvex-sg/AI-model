"""Privilege detection and elevation policy tests.

These exercise the real decisions the shell makes. Nothing here mocks sudo —
the tests assert on the *command string and privilege verdict* the manager
produces, which is the part that can actually be wrong.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from aegis.privilege import (
    Privilege,
    PrivilegeManager,
    is_root,
)
from tests import RuntimeTestCase


class PrivilegeManagerTest(unittest.TestCase):
    def test_unprivileged_command_is_untouched(self) -> None:
        mgr = PrivilegeManager(allow_root=True)
        command, privilege = mgr.elevate("ls -la", requires_root=False)
        self.assertEqual(command, "ls -la")
        self.assertIn(privilege, {Privilege.USER, Privilege.ROOT})

    def test_root_required_is_refused_when_policy_is_off(self) -> None:
        mgr = PrivilegeManager(allow_root=False)
        with self.assertRaises(PermissionError) as ctx:
            mgr.elevate("apt-get update", requires_root=True)
        self.assertIn("allow_root is disabled", str(ctx.exception))

    def test_root_required_denied_when_sudo_missing(self) -> None:
        mgr = PrivilegeManager(allow_root=True)
        with mock.patch("aegis.privilege.sudo_path", return_value=None):
            with self.assertRaises(PermissionError) as ctx:
                mgr.elevate("apt-get update", requires_root=True)
        self.assertIn("sudo is not installed", str(ctx.exception))

    def test_non_interactive_needs_passwordless_sudo(self) -> None:
        mgr = PrivilegeManager(allow_root=True, interactive=False)
        with mock.patch("aegis.privilege.is_root", return_value=False), \
             mock.patch("aegis.privilege.sudo_path", return_value="/usr/bin/sudo"), \
             mock.patch("aegis.privilege.sudo_nopasswd", return_value=False):
            with self.assertRaises(PermissionError) as ctx:
                mgr.elevate("apt-get update", requires_root=True)
        self.assertIn("passwordless sudo", str(ctx.exception))

    def test_non_interactive_prefixes_with_sudo_n(self) -> None:
        mgr = PrivilegeManager(allow_root=True, interactive=False)
        with mock.patch("aegis.privilege.is_root", return_value=False), \
             mock.patch("aegis.privilege.sudo_path", return_value="/usr/bin/sudo"), \
             mock.patch("aegis.privilege.sudo_nopasswd", return_value=True):
            command, privilege = mgr.elevate("apt-get update", requires_root=True)
        self.assertEqual(command, "sudo -n apt-get update")
        self.assertEqual(privilege, Privilege.ELEVATED)

    def test_interactive_omits_the_non_interactive_flag(self) -> None:
        mgr = PrivilegeManager(allow_root=True, interactive=True)
        with mock.patch("aegis.privilege.is_root", return_value=False), \
             mock.patch("aegis.privilege.sudo_path", return_value="/usr/bin/sudo"):
            command, privilege = mgr.elevate("apt-get update", requires_root=True)
        self.assertEqual(command, "sudo apt-get update")
        self.assertEqual(privilege, Privilege.ELEVATED)

    def test_already_sudo_is_not_double_prefixed(self) -> None:
        mgr = PrivilegeManager(allow_root=True, interactive=True)
        with mock.patch("aegis.privilege.is_root", return_value=False):
            command, privilege = mgr.elevate("sudo ls", requires_root=True)
        self.assertEqual(command, "sudo ls")
        self.assertEqual(privilege, Privilege.ELEVATED)

    def test_bare_sudo_is_refused_when_policy_is_off(self) -> None:
        mgr = PrivilegeManager(allow_root=False, interactive=True)
        with mock.patch("aegis.privilege.is_root", return_value=False):
            with self.assertRaises(PermissionError):
                mgr.elevate("sudo ls", requires_root=False)

    def test_report_shape(self) -> None:
        report = PrivilegeManager(allow_root=False).report()
        data = report.to_dict()
        for key in ("is_root", "user", "uid", "sudo_installed",
                    "sudo_passwordless", "allow_root", "can_elevate", "capability"):
            self.assertIn(key, data)
        self.assertIsInstance(data["can_elevate"], bool)

    def test_is_root_matches_euid(self) -> None:
        if hasattr(os, "geteuid"):
            self.assertEqual(is_root(), os.geteuid() == 0)


class ShellPrivilegeTest(RuntimeTestCase):
    """The shell must deny, not silently run, a command needing root."""

    def test_root_command_denied_without_policy(self) -> None:
        from aegis.errors import PrivilegeDenied

        rt = self.build_runtime(allow_root=False)
        with self.assertRaises(PrivilegeDenied):
            rt.shell.run("apt-get update")

    def test_denied_command_is_recorded_in_the_audit_log(self) -> None:
        from aegis.errors import PrivilegeDenied

        rt = self.build_runtime(allow_root=False)
        with self.assertRaises(PrivilegeDenied):
            rt.shell.run("apt-get update")
        entries = rt.log.read()
        self.assertTrue(entries, "denial should be audited")
        self.assertEqual(entries[-1]["status"], "denied")

    def test_unprivileged_command_still_runs(self) -> None:
        rt = self.build_runtime(allow_root=False)
        result = rt.shell.run("echo hello")
        self.assertTrue(result.ok, result.stderr)
        self.assertEqual(result.stdout.strip(), "hello")
        self.assertFalse(result.elevated)

    def test_result_records_the_privilege_used(self) -> None:
        rt = self.build_runtime(allow_root=False)
        result = rt.shell.run("echo hi")
        self.assertEqual(result.privilege, Privilege.USER.value)
        self.assertIn("privilege", result.to_dict())
        self.assertIn("elevated", result.to_dict())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
