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
|    recovery.py    diagnostics.py                              |
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
| Killswitch | `shell.py`, `filesystem.py`, `recovery.py`, task loops | `Killswitch.guard()` |
| Scope | `security/scope.py` | `Scope.require()` before any socket |
| Write confinement | `filesystem.py` | `_guard_write()` against configured roots |
| Secret redaction | `logging.py`, `shell.py` | `secrets.redact()` on all captured output |

Every policy lives in exactly one place. Adding a new agent or interface does
not bypass them because they all share the same core services.

## Desktop console

`gui.py` is a conversation-first surface built on plain Tkinter — no theme
engine, no third-party widgets. The frosted-glass look is composited by hand:
`_blend()` fakes translucency by mixing an accent into a base colour, cards get
a hairline border plus a 1px lit top bevel, and the hero is a per-row gradient
drawn on a canvas.

Two details matter for correctness rather than looks:

- **Threading.** The objective runs on a worker thread, which never touches a
  widget. It pushes the result onto a `queue.Queue`; the main loop drains that
  queue in `_tick()` and only then updates the UI. Calling `after()` from the
  worker would raise `main thread is not in main loop`.
- **Rebuild-on-view.** Switching views destroys and recreates the content
  frame, so the composer's entry widget is recreated too. `_submit()` reads the
  live widget through `_entry` and checks `winfo_exists()` before use.

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

**An LLM planner.** Set `model_provider` in the config and have
`LeaderAgent.plan()` delegate to it. The deterministic template planner stays
as the offline fallback so the system never becomes dependent on a network
call to function.
