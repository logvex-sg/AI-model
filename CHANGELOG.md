# Changelog

All notable changes to KALI-OPS are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-09-30

### Added
- Four-agent team: leader, builder, pentester, executor, each with an
  introspectable self-model (`capabilities`, `limitations`, `requires_llm`).
- Deterministic task planner and decomposer (no LLM required).
- Shell execution engine with risk classification (LOW/MEDIUM/HIGH), audit
  logging, stdout/stderr capture, and timeout handling.
- Global killswitch driven by the `KALI_OPS_KILLSWITCH` environment variable
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
- `kali-ops` CLI covering lifecycle, tasks, execution, introspection, audit,
  repository helpers, pentest, network, and killswitch control.
- 53-test standard-library suite covering risk, scope, redaction, config,
  killswitch, state, and orchestration.
- CI workflow running the suite on Python 3.9–3.13.
