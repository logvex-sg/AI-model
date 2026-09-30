# KALI-AEGIS — repository guidance

KALI-AEGIS is a dependency-free Python agent framework. Keep it that way: the
core must run on a bare host with only the standard library.

## Commands

```bash
python3 -m unittest discover -s tests -t .   # run the suite
python3 -m compileall -q aegis            # syntax check
python3 -m aegis doctor                   # smoke-test the runtime
```

Always use `-t .` with `unittest discover`; the tests use package-relative
imports and fail without the correct top-level directory.

## Layout

- `aegis/agents/` — leader, builder, pentester, executor
- `aegis/security/` — scope enforcement and reconnaissance
- `aegis/*.py` — core services (shell, risk, recovery, diagnostics, filesystem, killswitch, …)
- `tests/` — standard-library `unittest` suite
- `docs/` — architecture, install guide, and the original system prompt

## Conventions

- **Standard library only** in `aegis/`. Dependencies belong in an optional
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
- **Runtime states must stay accurate.** Transition with `enter()` / `leave()` /
  `record_error()`; never report a state the agent is not in.
- **Recover through the engine.** Do not hand-roll retry loops. Use
  `RecoveryEngine`, which classifies failures, backs off, and refuses to spin.
- **Inter-agent messages are structured.** Use `Agent.send()`, not ad-hoc
  dictionaries.
- **Exit codes are typed.** Raise an `AegisError` subclass so a policy denial is
  distinguishable from a genuine failure.
- **Tkinter is single-threaded.** Work runs on a worker thread but must never
  touch a widget from it — push onto `GlassApp._results` and let `_tick()`
  drain it. Calling `after()` off the main thread raises
  `main thread is not in main loop`.
- **Views are rebuilt, not persistent.** `_render_view()` destroys the content
  frame, so grab widgets you need to read later via a tracked attribute (see
  `GlassApp._entry`) and check `winfo_exists()` before using them.

## Testing

```bash
python3 -m unittest discover -s tests -t .          # 79 tests
xvfb-run -a python3 -m unittest discover -s tests -t .   # includes GUI tests
```

The GUI tests skip cleanly when Tk or a display is missing, so the suite runs
on a headless builder.

## Safety

This tool performs real shell execution and network reconnaissance. Changes to
`risk.py`, `recovery.py`, `killswitch.py`, `security/scope.py`, `filesystem.py`,
or `errors.py` (exit-code taxonomy) are security-sensitive and need a matching
test.
