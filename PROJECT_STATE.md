# PROJECT STATE

Last updated: 2026-09-14

## Phase

Core foundation (Phase 1/2). The repository was empty before this work; everything below was
written from scratch.

## Implemented and verified

- Config loading (defaults -> `~/.config/jarvis/config.toml` -> `JARVIS_*` env).
- Permission model: 5 modes, 5 permission levels, `PolicyEngine` (allow / confirm / reject).
- `Executor`: killswitch check, monitor check, policy, confirmation, argument validation,
  timeout, audit write. Every tool call goes through it.
- Audit log: JSONL with recursive secret redaction.
- Killswitch: file-backed in `~/.local/state/jarvis/EMERGENCY_STOP`, so it stops other processes.
- Behaviour monitor: suspends on repeated failures, call-rate spikes, identical-call loops,
  denial storms.
- SQLite memory: remember/get/search/relevant/recent/forget/clear, conversation history,
  rejects secret-looking values.
- Tools (30 registered): filesystem, processes, applications, network diagnostics, system and
  kernel/hardware info, memory tools.
- Providers: Ollama (default, local) and OpenAI-compatible (remote, key from env only).
  Reachability and model presence are reported from the backend, never assumed.
- Agent loop with balanced-brace tool-call parsing, bounded by the mode's tool budget.
- Task model/manager with cancellation.
- CLI: `status`, `doctor`, `tools`, `run`, `chat`, `emergency-stop`, `resume`, `audit`,
  `memory *`.
- Desktop entry + icon + `install.sh`.

Verification: `76 passed` (pytest), `ruff check` clean, and CLI smoke tests for
`tools`, `status --json`, `doctor`, `run system.os_info`, `memory`, `emergency-stop`/`resume`,
`audit`.

## Not implemented

- Desktop GUI (tkinter is not installed in the dev VM; framework choice still open).
- Privileged helper / root operations.
- Voice, vision, web research, plugin system, scheduler/automation, notifications.
- Remote machine administration.
- Context compaction beyond keyword-relevance selection.

## Known limitations

- `jarvis chat` needs a reachable model backend; with none it reports the connection error.
- Hardware inventory depends on `lspci`/`lsusb`; missing tools are reported as unavailable.

## Next objective

GUI shell over the existing core, then the privileged helper with explicit per-action
authorisation and audit.
