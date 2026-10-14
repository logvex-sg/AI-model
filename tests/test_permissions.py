from __future__ import annotations

import pytest

from jarvis.permissions import Decision, Mode, PermissionLevel, PolicyEngine


@pytest.mark.parametrize(
    ("mode", "level", "expected"),
    [
        (Mode.NORMAL, PermissionLevel.SAFE, Decision.ALLOW),
        (Mode.NORMAL, PermissionLevel.MODERATE, Decision.CONFIRM),
        (Mode.NORMAL, PermissionLevel.HIGH, Decision.CONFIRM),
        (Mode.NORMAL, PermissionLevel.DESTRUCTIVE, Decision.REJECT),
        (Mode.CODE, PermissionLevel.MODERATE, Decision.ALLOW),
        (Mode.BEAST, PermissionLevel.DESTRUCTIVE, Decision.CONFIRM),
        (Mode.OVERRIDE, PermissionLevel.HIGH, Decision.ALLOW),
        (Mode.OVERRIDE, PermissionLevel.DESTRUCTIVE, Decision.CONFIRM),
    ],
)
def test_policy_decisions(mode, level, expected):
    assert PolicyEngine(mode).evaluate(level).decision is expected


@pytest.mark.parametrize("mode", list(Mode))
def test_blocked_is_always_rejected(mode):
    assert PolicyEngine(mode).evaluate(PermissionLevel.BLOCKED).decision is Decision.REJECT


def test_override_mode_never_auto_allows_destructive():
    """DAN/OVERRIDE is a configuration mode, not a security bypass."""
    engine = PolicyEngine(Mode.OVERRIDE)
    assert engine.evaluate(PermissionLevel.DESTRUCTIVE).decision is Decision.CONFIRM
