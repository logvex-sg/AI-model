"""Risk classification for operations.

Every command or action is graded LOW / MEDIUM / HIGH before it runs. HIGH
risk actions are denied unless the operator has explicitly authorized
automatic execution (``auto_approve_high_risk``) or confirms interactively.

The classifier is deliberately conservative: an unknown command is treated as
MEDIUM, never LOW.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional, Sequence


class Risk(IntEnum):
    """Ordered risk levels; higher compares greater."""

    LOW = 0
    MEDIUM = 1
    HIGH = 2

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


# Commands that are safe to run unattended.
_LOW_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "file", "stat", "tree", "pwd", "echo",
    "find", "grep", "rg", "sed", "awk", "sort", "uniq", "cut", "tr", "diff",
    "which", "whereis", "type", "env", "printenv", "id", "whoami", "uname",
    "date", "hostname", "uptime", "free", "df", "du", "ps", "top", "lsof",
    "mkdir", "touch", "cp", "ln", "readlink", "realpath", "basename", "dirname",
    "make", "gcc", "clang", "cc", "python", "python3", "pytest", "go", "cargo",
    "npm", "pnpm", "yarn", "gradle", "mvn", "javac", "java", "node", "tsc",
    "ping", "dig", "host", "nslookup", "traceroute", "netstat", "ss",
    "journalctl", "git",
}

# Commands that mutate the system and need care.
_MEDIUM_COMMANDS = {
    "apt", "apt-get", "dpkg", "pip", "pip3", "snap", "systemctl", "service",
    "ufw", "iptables", "nft", "sysctl", "mount", "umount", "kill", "pkill",
    "docker", "podman", "ssh", "scp", "rsync", "curl", "wget", "chmod",
    "chown", "chgrp", "setfacl", "crontab", "at", "reboot", "shutdown",
}

# Commands that can destroy data or break the host.
_HIGH_COMMANDS = {
    "rm", "rmdir", "mkfs", "fdisk", "parted", "dd", "wipefs", "shred",
    "userdel", "groupdel", "passwd", "chpasswd", "visudo", "grub-install",
    "update-grub", "poweroff", "halt", "init",
}

# Argument fragments that escalate an otherwise-boring command.
_HIGH_MARKERS = (
    "rm -rf /", "rm -rf /*", "dd if=", "mkfs", "> /dev/sd", "of=/dev/",
    ":(){", "chmod -R 777 /", "chown -R /", "iptables -F", "nft flush",
)

# Network tools used offensively — always HIGH unless scope-checked separately.
_OFFENSIVE_TOOLS = {
    "nmap", "masscan", "hydra", "sqlmap", "metasploit", "msfconsole",
    "msfvenom", "nikto", "gobuster", "ffuf", "dirb", "wfuzz", "john",
    "hashcat", "aircrack-ng", "responder", "ettercap", "beef", "armitage",
    "burpsuite", "zap", "wpscan", "nuclei", "amass", "theHarvester",
}

# Commands whose effects cannot be undone by re-running something else.
_IRREVERSIBLE = {
    "rm", "rmdir", "mkfs", "fdisk", "parted", "dd", "wipefs", "shred",
    "userdel", "groupdel", "grub-install", "update-grub", "poweroff", "halt",
}


@dataclass(frozen=True)
class RiskAssessment:
    """The verdict for one operation.

    Carries the pre-execution facts section 20 requires the executor to
    determine: how risky it is, what it will affect, whether it is reversible,
    and whether it needs elevated privileges.
    """

    risk: Risk
    reason: str
    command: str
    needs_confirmation: bool
    category: str = "unknown"
    reversible: bool = True
    requires_root: bool = False

    def to_dict(self) -> dict:
        return {
            "risk": str(self.risk),
            "reason": self.reason,
            "command": self.command,
            "needs_confirmation": self.needs_confirmation,
            "category": self.category,
            "reversible": self.reversible,
            "requires_root": self.requires_root,
        }


#: Commands that only ever affect a single process, never the host.
_CATEGORY_BY_RISK = {
    Risk.LOW: "diagnostic",
    Risk.MEDIUM: "system-change",
    Risk.HIGH: "destructive",
}


def _first_word(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        # Unbalanced quotes — fall back to a naive split.
        parts = command.split()
    if not parts:
        return ""
    # Skip leading `sudo`, `env VAR=x`, `time`, etc.
    idx = 0
    while idx < len(parts) and (
        parts[idx] in {"sudo", "doas", "time", "nohup", "env"}
        or "=" in parts[idx] and parts[idx].split("=", 1)[0].isidentifier()
    ):
        idx += 1
    if idx >= len(parts):
        return ""
    return parts[idx].rsplit("/", 1)[-1]


def classify(command: str, *, base: Optional[Risk] = None) -> RiskAssessment:
    """Classify a shell command string."""
    tool = _first_word(command)
    lowered = command.lower()
    needs_root = "sudo" in lowered.split() or lowered.startswith("sudo ")
    irreversible = tool in _IRREVERSIBLE

    def verdict(
        risk: Risk,
        reason: str,
        *,
        needs_confirmation: bool,
        category: Optional[str] = None,
    ) -> RiskAssessment:
        return RiskAssessment(
            risk=risk,
            reason=reason,
            command=command,
            needs_confirmation=needs_confirmation,
            category=category or _CATEGORY_BY_RISK.get(risk, "unknown"),
            reversible=not (tool in _IRREVERSIBLE),
            requires_root=needs_root,
        )

    for marker in _HIGH_MARKERS:
        if marker in lowered:
            return verdict(
                Risk.HIGH,
                f"destructive pattern detected: {marker!r}",
                needs_confirmation=True,
                category="destructive",
            )

    if tool in _OFFENSIVE_TOOLS:
        return verdict(
            Risk.HIGH,
            f"{tool} is an offensive security tool and requires an authorized scope",
            needs_confirmation=True,
            category="offensive",
        )

    if tool in _HIGH_COMMANDS:
        return verdict(
            Risk.HIGH,
            f"{tool} can destroy data or break the host"
            + (" and cannot be undone" if irreversible else ""),
            needs_confirmation=True,
        )

    if tool in _MEDIUM_COMMANDS:
        return verdict(Risk.MEDIUM, f"{tool} changes system state", needs_confirmation=False)

    if tool in _LOW_COMMANDS:
        risk = base or Risk.LOW
        return verdict(risk, f"{tool} is a read-only or build command", needs_confirmation=False)

    risk = base or Risk.MEDIUM
    return verdict(
        risk,
        f"{tool or 'command'} is unrecognised; treated as {risk.name} by default",
        needs_confirmation=risk is Risk.HIGH,
    )


def classify_sequence(commands: Sequence[str]) -> List[RiskAssessment]:
    return [classify(c) for c in commands]


def highest(assessments: Sequence[RiskAssessment]) -> Risk:
    return max((a.risk for a in assessments), default=Risk.LOW)
