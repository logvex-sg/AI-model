"""Tests for the risk classifier and the shell execution policy."""

from __future__ import annotations

import unittest

from aegis.risk import Risk, classify
from aegis.shell import Shell

from . import RuntimeTestCase


class RiskClassifierTests(unittest.TestCase):
    def test_read_only_command_is_low(self) -> None:
        self.assertIs(classify("ls -la").risk, Risk.LOW)

    def test_build_command_is_low(self) -> None:
        self.assertIs(classify("gcc -o out main.c").risk, Risk.LOW)

    def test_package_install_is_medium(self) -> None:
        self.assertIs(classify("apt-get install nmap").risk, Risk.MEDIUM)

    def test_recursive_delete_is_high(self) -> None:
        self.assertIs(classify("rm -rf /").risk, Risk.HIGH)

    def test_offensive_tool_is_high(self) -> None:
        assessment = classify("nmap -sV 10.0.0.1")
        self.assertIs(assessment.risk, Risk.HIGH)
        self.assertTrue(assessment.needs_confirmation)

    def test_unknown_command_defaults_to_medium(self) -> None:
        self.assertIs(classify("frobnicate --all").risk, Risk.MEDIUM)

    def test_sudo_prefix_is_skipped(self) -> None:
        self.assertIs(classify("sudo ls /root").risk, Risk.LOW)

    def test_destructive_marker_escalates(self) -> None:
        self.assertIs(classify("echo x > /dev/sda").risk, Risk.HIGH)


class ShellPolicyTests(RuntimeTestCase):
    def test_low_risk_command_runs(self) -> None:
        shell = Shell(self.config, self.killswitch, self.log)
        result = shell.run("echo hello")
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout.strip(), "hello")

    def test_high_risk_command_denied(self) -> None:
        shell = Shell(self.config, self.killswitch, self.log)
        from aegis.errors import RiskDenied

        with self.assertRaises(RiskDenied):
            shell.run("rm -rf /")

    def test_high_risk_allowed_when_confirmed(self) -> None:
        shell = Shell(self.config, self.killswitch, self.log)
        result = shell.run("rm -rf /tmp/kaliops-definitely-absent", confirmed=True)
        self.assertTrue(result.ok)

    def test_dry_run_does_not_execute(self) -> None:
        marker = self.home / "marker.txt"
        shell = Shell(self.config, self.killswitch, self.log, dry_run=True)
        shell.run(f"touch {marker}")
        self.assertFalse(marker.exists())

    def test_killswitch_blocks_execution(self) -> None:
        shell = Shell(self.config, self.killswitch, self.log)
        self.killswitch.engage()
        from aegis.errors import AegisHaltedError

        with self.assertRaises(AegisHaltedError):
            shell.run("echo nope")

    def test_failure_raises_when_check_requested(self) -> None:
        shell = Shell(self.config, self.killswitch, self.log)
        from aegis.errors import CommandError

        with self.assertRaises(CommandError):
            shell.run("exit 3", check=True)

    def test_command_is_audited(self) -> None:
        shell = Shell(self.config, self.killswitch, self.log)
        shell.run("echo audited")
        entries = self.log.read()
        self.assertTrue(any(e["command"] == "echo audited" for e in entries))


if __name__ == "__main__":
    unittest.main()
