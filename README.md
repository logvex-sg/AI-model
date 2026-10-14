# JARVIS

A local-first Linux assistant. The AI model never touches the OS directly: every action goes
through a typed tool registry, a permission policy, and an audited executor.

```
CLI  ->  Agent  ->  Executor  ->  Policy / Killswitch / Behaviour monitor  ->  Tool  ->  Linux
                        |
                        +-> Audit log (JSONL, secrets redacted)
```

## Install

Requires Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/jarvis doctor
```

Desktop install (venv in `~/.local/share/jarvis`, `jarvis` on PATH, launcher + icon):

```bash
./install.sh
```

## Model backend

Ollama is the default and is expected at `http://127.0.0.1:11434`:

```bash
ollama serve
ollama pull llama3.1
```

`jarvis status` / `jarvis doctor` report the backend as unreachable and the model as
not installed unless the backend actually says otherwise — they never assume.

A remote OpenAI-compatible endpoint can be used instead. The key is read from an environment
variable (`JARVIS_API_KEY` by default); it is never stored in the repo or in config files.

```bash
export JARVIS_PROVIDER=openai_compat
export JARVIS_ENDPOINT=https://api.example.com/v1
export JARVIS_MODEL=some-model
export JARVIS_API_KEY=...
```

## CLI

```bash
jarvis status [--json]        # mode, backend reachability, killswitch, monitor
jarvis doctor                 # environment checks
jarvis tools                  # registered tools with permission levels
jarvis run <tool> --args '{}' # run a single tool through the executor
jarvis chat                   # interactive session (needs a reachable model backend)
jarvis emergency-stop         # engage the persistent killswitch
jarvis resume                 # clear it
jarvis audit [--limit N]      # tail the audit log
jarvis memory remember|search|forget|clear
```

State lives in `~/.local/state/jarvis/`: `memory.db`, `audit.jsonl`, `EMERGENCY_STOP`.
Config is read from `~/.config/jarvis/config.toml` and `JARVIS_*` environment variables.

## Modes and permissions

Modes (`normal`, `code`, `beast`, `op`, `override`) set the maximum permission level and the
tool-call budget per turn. Tools declare their own level (`safe`, `moderate`, `high`,
`destructive`, `blocked`) — the model cannot choose or raise it.

`override` is a configuration mode, **not** a security bypass: destructive operations still
require explicit confirmation, and this is enforced by a test.

## Safety

- Filesystem tools confine paths to `$HOME` by default, resolving symlinks first.
- Writes/copies/moves refuse to clobber existing paths unless `overwrite=true`.
- Archive extraction rejects `..` traversal and symlink members.
- Subprocesses run with a fixed argument vector, `shell=False`, and a timeout.
- Destructive calls require confirmation; without a confirm callback they return
  `needs_confirmation` instead of running.
- `emergency-stop` writes a file, so any other JARVIS process is halted too.
- The behaviour monitor suspends execution on repeated failures, loops, or denial storms.
- Audit records redact secret-looking values; memory refuses to store them.

## Tests

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

## Status

See [PROJECT_STATE.md](PROJECT_STATE.md) for what is implemented and what is not.
