# Architecture

KALI-AEGIS is a thin, auditable layer over the operating system. There is no
hidden state and no network service it must phone home to. The whole system
runs in one Python process (or three: CLI, API, desktop console) over a shared
`$KALI_AEGIS_HOME` directory.

## Layers

```
+--------------------------------------------------------------+
|  Interfaces:  CLI (cli.py)   REST API (api.py)   GUI (gui.py) |
+--------------------------------------------------------------+
|  Runtime / Orchestrator (orchestrator.py)                     |
|    builds shared handles, drives plan -> execute -> verify     |
|    exposes monitor(), messages(), diagnostics(), resume       |
+--------------------------------------------------------------+
|  Agents (agents/)                                             |
|    leader      builder      pentester       executor          |
|    - plan      - files      - scoped recon  - run commands    |
|    - verify    - build      - findings      - recover         |
|    each carries a self-model and a runtime state              |
+--------------------------------------------------------------+
|  Core services                                                |
|    shell.py       risk.py        secrets.py                   |
|    filesystem.py  killswitch.py  logging.py                   |
|    config.py      state.py       errors.py                    |
|    recovery.py    diagnostics.py privilege.py                 |
+--------------------------------------------------------------+
|  Reasoning (optional)                                         |
|    llm.py   provider-agnostic chat client (stdlib urllib)     |
|    ai.py    tool-calling loop: run_command/read/write/list    |
+--------------------------------------------------------------+
|  Security (security/)                                         |
|    scope.py      recon.py                                     |
+--------------------------------------------------------------+
|  OS: subprocess - filesystem - sockets                        |
+--------------------------------------------------------------+
```

## The self-model and runtime state

Every agent declares, in code, what it can and cannot do:

```python
class BuilderAgent(Agent):
    name = "builder"
    capabilities = ("create_file", "scaffold_project", "build", "run_tests", ...)
    limitations = ("writes only within the configured sandbox roots", ...)
    requires_llm = False
```

`Agent.introspect()` returns this as a `SelfModel`, and `implemented_actions()`
reflects over the class to list real public methods. A test asserts that every
declared capability is actually implemented — an agent cannot claim a power it
does not have.

Alongside the self-model each agent carries a `RuntimeStats` record with a state
(`IDLE`, `THINKING`, `PLANNING`, `EXECUTING`, `TESTING`, `WAITING`, `ERROR`,
`STOPPED`), the current task and tool, elapsed time, error count, and queue
length. `Runtime.monitor()` reads these directly, so the monitor command and the
desktop agent bar show what an agent is *actually* doing rather than guessing.

This is what "self-aware" means here: the agent's model of itself is data the
rest of the system can query and act on, not a prompt it hopes to follow.

## Structured messaging

Agents do not pass free text to each other. `Agent.send()` emits an
`AgentMessage` with `from`, `to`, `type`, `status`, and optional `error`,
`recommended_action`, and `payload`. The leader emits an `assignment` message
per delegated subtask and a `verification` message when a run fails. Messages
are mirrored into the audit log, so the chain of decisions is reconstructable.

## Recovery engine

`recovery.py` implements the bounded failure loop:

```
capture -> classify -> strategise -> apply -> retry -> verify -> record
```

A failure is mapped to an `ErrorClass`, which selects a strategy:

| Error class | Strategy | Behaviour |
|---|---|---|
| `transient` | `retry` | Re-run after exponential backoff |
| `environment` | `replan` | A prerequisite is missing; the caller must change the plan |
| `permission` | `abort` | Re-raise the original typed exception |
| `input` / `logic` | `abort` | Not automatically recoverable |
| `unknown` | `replan` | No safe automatic action |

Two safeguards keep it honest: an `abort` re-raises the *original* exception so
its typed exit code survives, and a spin-guard stops the loop if an identical
failure repeats with nothing changed. The killswitch is checked between
attempts and during backoff.

## Diagnostics

`diagnostics.py` produces the `aegis doctor` table. Each component —
OS, kernel, architecture, CPU, memory, filesystem, DNS, network, Python, git,
docker, compiler, language runtimes, package manager, root, killswitch, model —
is reported as `OK`, `WARN`, or `MISSING`. A missing *optional* component is a
`WARN`; only a failed *required* check makes the report fail. This is how the
platform degrades instead of refusing to start.

## Request flow

1. An interface calls `Runtime.run_task(objective)`.
2. The **leader** decomposes the objective into subtasks based on intent
   keywords (`_TEMPLATES` in `agents/leader.py`).
3. The orchestrator dispatches each subtask to the assigned agent.
4. Agents call into core services, which enforce the risk and killswitch
   policies *before* anything touches the OS.
5. Transient failures are handled by the recovery engine.
6. The **leader** verifies the collected results.
7. A structured report is written to state and returned.

Steps that no agent can perform deterministically come back as `BLOCKED` with
an explanatory reason — never a fabricated success. A task stopped by the
killswitch is marked `INTERRUPTED` and can be resumed with `resume-task`.

## Safety enforcement points

| Policy | Enforced in | Mechanism |
|---|---|---|
| Risk classification | `shell.py` | `risk.classify()` before `subprocess.run` |
| Privilege | `shell.py` | `privilege.elevate()` refuses before spawn when root is needed but not allowed |
| Killswitch | `shell.py`, `filesystem.py`, `recovery.py`, task loops, `ai.py` | `Killswitch.guard()` |
| Scope | `security/scope.py` | `Scope.require()` before any socket |
| Write confinement | `filesystem.py` | `_guard_write()` against configured roots |
| Secret redaction | `logging.py`, `shell.py` | `secrets.redact()` on all captured output |

Every policy lives in exactly one place. Adding a new agent or interface does
not bypass them because they all share the same core services — and this is
what makes the reasoning loop safe to enable: `ai.py` holds no execution path
of its own. It calls the same `shell.run` the CLI does, so a model-issued
command is classified, privilege-checked, killswitch-guarded, and audited
exactly like a typed one.

## The reasoning loop

`llm.py` is a complete OpenAI-compatible client in the standard library: it
normalises the base URL (`.../v1/chat/completions`), attaches a bearer token
when the provider needs one, and parses content, tool calls, and token usage.
`resolve_base_url()` maps a provider name to a known endpoint; `custom` uses the
configured URL directly. Ollama and LM Studio are in `KEYLESS_PROVIDERS`, so
they are ready without a token.

`ai.py` implements the loop. The model is handed a system prompt, the objective,
and five tools — `run_command`, `read_file`, `write_file`, `list_dir`, `finish`.
Each iteration it either calls a tool or answers in prose. Tool calls are
executed through injected callables (the runtime passes `shell.run` and the
`FileSystem` methods), results are appended as `tool` messages, and the loop
continues until `finish`, a prose answer, or the iteration budget.

Two properties are deliberate:

- **The loop cannot execute anything itself.** Execution is injected as
  callables from `Runtime.build()`, so the model has exactly the powers the
  runtime grants and nothing more.
- **A refusal is informative, not fatal.** If a command is denied by the risk or
  privilege policy, that comes back to the model as a failed step with the
  reason attached. The model gets to reason about it rather than the run
  silently dying.

`Runtime.act()` is the entry point. It checks the killswitch, then dispatches to
the reasoning loop when `llm_available` is true and to the deterministic runner
otherwise, tagging the report with `MODE` so the caller always knows which
engine produced it.

## Privilege

`privilege.py` decides *how* a command runs, separate from *whether* it may run.
`PrivilegeManager.report()` probes the host once — euid, sudo presence, whether
sudo is passwordless — and `elevate()` applies the policy:

| Situation | Result |
|---|---|
| Command needs no root | Run as-is |
| Root needed, `allow_root` off | `PrivilegeDenied` (exit 9), logged `denied` |
| Root needed, already root | Run as-is, marked root |
| Root needed, interactive caller | Prefix `sudo`, may prompt |
| Root needed, non-interactive caller | Prefix `sudo -n`, fail fast if no passwordless sudo |

The distinction between interactive and non-interactive callers is not
cosmetic: the CLI can prompt for a password, but the REST API and the GUI
worker thread cannot, so they must not try.

## Desktop console

`gui.py` is a conversation-first surface built on plain Tkinter — no theme
engine, no third-party widgets, no Pillow. `liquid.py` supplies the glass
toolkit: gradient fills, soft radial blobs for the animated background, and
panel drawing with a hairline border, a lit top bevel, and a shaded bottom edge.

Real frosted glass needs per-pixel alpha and a compositor, which Tkinter does
not expose for child widgets. The look is therefore reconstructed from
primitives: panel fills are *blended* from the colour behind them so the
translucency reads correctly even though each pixel is opaque, and the specular
sweep is a series of widening translucent lines whose intensity follows a
sine envelope so the highlight enters and leaves instead of popping.

Two details matter for correctness rather than looks:

- **Threading.** The objective runs on a worker thread, which never touches a
  widget. It pushes the result onto a `queue.Queue`; the main loop drains that
  queue in `_tick()` and only then updates the UI. Calling `after()` from the
  worker would raise `main thread is not in main loop`.
- **Teardown.** `_tick()` re-arms itself with `after()`. On close the pending
  callback is cancelled first (`close()`), and `_alive()` guards the loop
  anyway, so a late timer cannot fire against a destroyed interpreter.

The right-hand agent rail reads `Runtime.monitor()` on every tick, so the states
shown are the agents' real runtime state, not a decorative animation.

## Persistence

- `operations.jsonl` — append-only audit log, one JSON object per action, with
  command, working directory, exit code, duration, and state.
- `state.json` — tasks, subtasks, projects, and statuses; written atomically
  (temp file + rename) so an interrupted run cannot corrupt it.

Both live under `$KALI_AEGIS_HOME` (default `~/.aegis/`).

## Extending

**A new agent.** Subclass `Agent`, declare `name`, `role`, `capabilities`,
`limitations`, implement the actions, and register it in `Runtime.build()`.

**A new model provider.** Add the base URL to `PROVIDERS` in `llm.py` (and to
`KEYLESS_PROVIDERS` if it needs no token). Anything OpenAI-compatible works
without further changes; for a non-compatible API, implement `_raw_post` and
`_parse` in a subclass.

**A new tool for the reasoning loop.** Add the spec to `TOOL_SPECS` in `ai.py`,
a `_do_*` handler, and a branch in `_dispatch`. Route any real effect through an
injected callable so it inherits the policy layers rather than sidestepping them.
