# Changelog

All notable changes to KALI-AEGIS are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-09-30

### Added
- Renamed the project and package from KALI-OPS to **KALI-AEGIS**; CLI command
  is now `aegis`.
- Agent runtime state machine (`IDLE`, `THINKING`, `PLANNING`, `EXECUTING`,
  `TESTING`, `WAITING`, `ERROR`, `STOPPED`) with live counters, surfaced by
  `aegis monitor`, the REST API, and the desktop agent bar.
- Structured inter-agent messaging (`AgentMessage`: from/to/type/status/error/
  recommended_action/payload), with leader assignments and verification
  results mirrored into the audit log.
- Recovery engine (`recovery.py`): classifies failures, selects a strategy,
  retries transient ones with exponential backoff, re-raises non-retryable
  typed exceptions, and refuses to spin on identical failures.
- Diagnostics module (`diagnostics.py`) backing a component health table
  (`OK` / `WARN` / `MISSING`), shown by `aegis start` and `aegis doctor`.
- Typed error hierarchy with distinct exit codes (2 config, 3 halt, 4 scope,
  5 risk, 6 command, 7 not found, 8 recovery exhausted).
- Richer risk assessment: reversibility, required privileges, and category.
- Project tracking and task queue/interrupted queries in persistent state.
- Task resume (`aegis resume-task`) for killswitch-interrupted runs.
- New CLI surface: `restart`, `monitor`, `messages`, `tasks`, `security`,
  `build`, `test`, `repo status`, `resume-task`, `shell`.
- New REST endpoints: `/v1/monitor`, `/v1/diagnostics`, `/v1/messages`,
  `/v1/resume-task`.
- Rebuilt desktop console: sidebar workspaces, live per-agent status bar,
  dashboard, and a dark security-console theme.
- Expanded suite to 74 tests covering recovery, diagnostics, runtime state,
  messaging, and risk metadata.

### Changed
- Command execution now records working directory, duration, and state.
- Executor retries are driven by the recovery engine instead of an ad-hoc loop.
- Exit codes are typed so a policy denial is distinguishable from a crash.

## [0.1.0] - 2026-09-30

### Added
- Four-agent team: leader, builder, pentester, executor, each with an
  introspectable self-model (`capabilities`, `limitations`, `requires_llm`).
- Deterministic task planner and decomposer (no LLM required).
- Shell execution engine with risk classification (LOW/MEDIUM/HIGH), audit
  logging, stdout/stderr capture, and timeout handling.
- Global killswitch driven by the `KALI_AEGIS_KILLSWITCH` environment variable
  or a sentinel file, checked before every operation.
- Secret redaction (GitHub/AWS/Slack tokens, JWTs, PEM key blocks, bearer
  tokens, credential assignments) applied to all captured output.
- Sandboxed filesystem operations confined to configured write roots.
- Append-only JSONL audit log and atomic persistent task state.
- Scope enforcement for security testing: loopback and this machine's own
  addresses only, plus explicitly `authorized_hosts`.
- Scoped reconnaissance: TCP connect scan and banner grab.
- REST API on the standard library `http.server`, loopback by default.
- Tkinter desktop application with agent status, console, tasks, logs,
  pentest workspace, and killswitch controls.
- `aegis` CLI covering lifecycle, tasks, execution, introspection, audit,
  repository helpers, pentest, network, and killswitch control.
- 53-test standard-library suite covering risk, scope, redaction, config,
  killswitch, state, and orchestration.
- CI workflow running the suite on Python 3.9–3.13.
