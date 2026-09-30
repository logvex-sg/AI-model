# Architecture

KALI-OPS is a thin, auditable layer over the operating system. There is no
hidden state and no network service it must phone home to. The whole system
runs in one Python process (or three: CLI, API, GUI) over a shared
`$KALI_OPS_HOME` directory.

## Layers

```
┌──────────────────────────────────────────────────────────────┐
│  Interfaces:  CLI (cli.py)   REST API (api.py)   GUI (gui.py) │
├──────────────────────────────────────────────────────────────┤
│  Runtime / Orchestrator (orchestrator.py)                     │
│    builds shared handles, drives plan → execute → verify      │
├──────────────────────────────────────────────────────────────┤
│  Agents (agents/)                                             │
│    leader      builder      pentester       executor          │
│    · plan      · files      · scoped recon  · run commands    │
│    · verify    · build                                       │
│             each carries an explicit self-model               │
├──────────────────────────────────────────────────────────────┤
│  Core services                                                │
│    shell.py      risk.py       secrets.py                     │
│    filesystem.py killswitch.py logging.py                     │
│    config.py     state.py      errors.py                      │
├──────────────────────────────────────────────────────────────┤
│  Security (security/)                                         │
│    scope.py      recon.py                                     │
├──────────────────────────────────────────────────────────────┤
│  OS: subprocess · filesystem · sockets                        │
└──────────────────────────────────────────────────────────────┘
```

## The self-model

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

This is what "self-aware" means here: the agent's model of itself is data the
rest of the system can query and act on, not a prompt it hopes to follow.

## Request flow

1. An interface calls `Runtime.run_task(objective)`.
2. The **leader** decomposes the objective into subtasks based on intent
   keywords (`_TEMPLATES` in `agents/leader.py`).
3. The orchestrator dispatches each subtask to the assigned agent.
4. Agents call into core services, which enforce the risk and killswitch
   policies *before* anything touches the OS.
5. The **leader** verifies the collected results.
6. A structured report is written to state and returned.

Steps that no agent can perform deterministically come back as `BLOCKED` with
an explanatory reason — never a fabricated success.

## Safety enforcement points

| Policy | Enforced in | Mechanism |
|---|---|---|
| Risk classification | `shell.py` | `risk.classify()` before `subprocess.run` |
| Killswitch | `shell.py`, `filesystem.py`, `state` loops | `Killswitch.guard()` |
| Scope | `security/scope.py` | `Scope.require()` before any socket |
| Write confinement | `filesystem.py` | `_guard_write()` against configured roots |
| Secret redaction | `logging.py`, `shell.py` | `secrets.redact()` on all captured output |

Every policy lives in exactly one place. Adding a new agent or interface does
not bypass them because they all share the same core services.

## Persistence

- `operations.jsonl` — append-only audit log, one JSON object per action.
- `state.json` — tasks, subtasks, and statuses; written atomically.

Both live under `$KALI_OPS_HOME` (default `~/.kali-ops/`).

## Extending

**A new agent.** Subclass `Agent`, declare `name`, `role`, `capabilities`,
`limitations`, implement the actions, and register it in `Runtime.build()`.

**An LLM planner.** Set `model_provider` in the config and have
`LeaderAgent.plan()` delegate to it. The deterministic template planner stays
as the offline fallback so the system never becomes dependent on a network
call to function.
