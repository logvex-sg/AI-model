# KALI-OPS — repository guidance

KALI-OPS is a dependency-free Python agent framework. Keep it that way: the
core must run on a bare host with only the standard library.

## Commands

```bash
python3 -m unittest discover -s tests -t .   # run the suite
python3 -m compileall -q kali_ops            # syntax check
python3 -m kali_ops doctor                   # smoke-test the runtime
```

Always use `-t .` with `unittest discover`; the tests use package-relative
imports and fail without the correct top-level directory.

## Layout

- `kali_ops/agents/` — leader, builder, pentester, executor
- `kali_ops/security/` — scope enforcement and reconnaissance
- `kali_ops/*.py` — core services (shell, risk, filesystem, killswitch, …)
- `tests/` — standard-library `unittest` suite
- `docs/` — architecture and the original system prompt

## Conventions

- **Standard library only** in `kali_ops/`. Dependencies belong in an optional
  extra and must degrade gracefully when absent.
- **Every side effect is audited.** New operations go through `Shell` or
  `FileSystem`, not directly through `subprocess` or `pathlib`.
- **Policies live in one place.** Risk, scope, killswitch, and redaction are
  each enforced by a single module. Do not duplicate the checks.
- **Never claim success without verification.** If a step cannot be performed,
  return a failed result with a reason; the orchestrator turns it into
  `BLOCKED`. Fabricated success is a bug.
- **Self-models must stay accurate.** If you add a capability, add it to the
  class's `capabilities` tuple and a test asserting it is implemented.

## Safety

This tool performs real shell execution and network reconnaissance. Changes to
`risk.py`, `killswitch.py`, `security/scope.py`, or `filesystem.py` are
security-sensitive and need a matching test.
