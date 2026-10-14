# Changelog

All notable changes to KALI-AEGIS are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Optional LLM reasoning layer (`llm.py`, `ai.py`): when a model is configured
  the planner asks it to reason over the objective and produces a plan; with no
  model configured the deterministic planner still runs, so the tool never
  depends on a network service. `aegis doctor` reports the model row and the
  core degrades cleanly when it is absent.
- Privilege layer (`privilege.py`): detects root vs. passwordless `sudo`,
  gates elevation behind an explicit `allow_root` setting, and refuses to
  elevate when the policy is off. Elevated commands stay audited like any other.
- `aegis doctor` now reports a **GUI** row: `OK` with a usable display, `WARN`
  when tkinter is present but headless (with the `xvfb-run` hint), and `MISSING`
  when tkinter is not installed (with the platform install command). A missing
  console toolkit is diagnosable instead of discovered on first launch.

### Changed
- Rebalanced the console palette so the glass is actually visible. The panel
  fill previously landed within ~1 luminance level of the background, so the
  three-column layout read as a single flat field. Panels now sit ~25 levels
  above the backdrop, and the liquid background composites blobs against the
  gradient beneath them (plus bloom and a vignette) rather than a constant.
- Panel fills are blended against the background gradient at the panel's own
  vertical position, so top and bottom cards no longer render identically.

### Fixed
- Removed genuinely unused imports in `diagnostics.py`, `filesystem.py`,
  `state.py`, and two test modules; the lint run is now clean rather than
  filtered.

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
- Rebuilt the desktop console as a conversation-first glass UI: a thread
  composer that streams plan and activity in plain language, monospace command
  cards, a live agent rail with per-agent state and elapsed time, and an
  always-present killswitch. Glass is drawn in pure Tkinter — translucent
  panels, hairline borders, a lit top bevel per card, and a gradient hero.
- Expanded suite to 74 tests covering recovery, diagnostics, runtime state,
  messaging, and risk metadata; the desktop console adds 5 widget tests that
  skip cleanly when Tk or a display is unavailable.
- Added a full [install guide](docs/INSTALL.md): requirements, pipx, venv,
  run-in-place, verification, uninstall, and PEP 668 troubleshooting.

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
