"""Permission levels, operating modes and the policy engine.

The policy engine is the only component that decides whether a tool call may
run. The model never decides its own permissions: it can only request a tool,
and the decision is computed here from the tool's declared permission level and
the active mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PermissionLevel(str, Enum):
    SAFE = "safe"
    MODERATE = "moderate"
    HIGH = "high"
    DESTRUCTIVE = "destructive"
    BLOCKED = "blocked"


_ORDER = {
    PermissionLevel.SAFE: 0,
    PermissionLevel.MODERATE: 1,
    PermissionLevel.HIGH: 2,
    PermissionLevel.DESTRUCTIVE: 3,
    PermissionLevel.BLOCKED: 4,
}


class Mode(str, Enum):
    NORMAL = "normal"
    CODE = "code"
    BEAST = "beast"
    OP = "op"
    OVERRIDE = "override"

    @property
    def indicator(self) -> str:
        return {
            Mode.NORMAL: "NORMAL",
            Mode.CODE: "CODE",
            Mode.BEAST: "BEAST",
            Mode.OP: "OP MODE ACTIVE",
            Mode.OVERRIDE: "DAN / OVERRIDE",
        }[self]


class Decision(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    REJECT = "reject"


@dataclass(frozen=True)
class ModePolicy:
    """How a mode treats each permission level.

    ``auto_allow_up_to`` is the highest level executed without confirmation.
    ``max_level`` is the highest level that may run at all; anything above it is
    rejected outright.
    """

    auto_allow_up_to: PermissionLevel
    max_level: PermissionLevel
    max_tool_calls: int
    planning_depth: int


MODE_POLICIES: dict[Mode, ModePolicy] = {
    Mode.NORMAL: ModePolicy(PermissionLevel.SAFE, PermissionLevel.HIGH, 12, 1),
    Mode.CODE: ModePolicy(PermissionLevel.MODERATE, PermissionLevel.HIGH, 24, 2),
    Mode.BEAST: ModePolicy(PermissionLevel.MODERATE, PermissionLevel.DESTRUCTIVE, 64, 4),
    Mode.OP: ModePolicy(PermissionLevel.MODERATE, PermissionLevel.DESTRUCTIVE, 32, 3),
    Mode.OVERRIDE: ModePolicy(PermissionLevel.HIGH, PermissionLevel.DESTRUCTIVE, 64, 4),
}


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    reason: str


class PolicyEngine:
    """Decides allow / confirm / reject for a tool invocation."""

    def __init__(self, mode: Mode = Mode.NORMAL) -> None:
        self.mode = mode

    @property
    def policy(self) -> ModePolicy:
        return MODE_POLICIES[self.mode]

    def set_mode(self, mode: Mode) -> None:
        self.mode = mode

    def evaluate(self, level: PermissionLevel) -> PolicyResult:
        if level is PermissionLevel.BLOCKED:
            return PolicyResult(Decision.REJECT, "tool is blocked")
        policy = self.policy
        if _ORDER[level] > _ORDER[policy.max_level]:
            return PolicyResult(
                Decision.REJECT,
                f"permission '{level.value}' exceeds maximum for mode '{self.mode.value}'",
            )
        if _ORDER[level] <= _ORDER[policy.auto_allow_up_to]:
            return PolicyResult(Decision.ALLOW, "within auto-allow level for mode")
        return PolicyResult(Decision.CONFIRM, f"'{level.value}' requires explicit confirmation")
