# KALI-AEGIS

An autonomous security and software engineering platform for systems, networks,
and repositories you own or are explicitly authorized to test. KALI-AEGIS runs
four cooperating agents — **leader**, **builder**, **pentester**, and
**executor** — behind a single CLI, a REST API, and a desktop console.

It is dependency-free: the whole thing runs on the Python standard library, so
it works on a bare Kali host with no `pip install` step.

## What it actually does

This is a working framework, not a proof of concept. What is implemented today:

| Capability | Status | Notes |
|---|---|---|
| Agent team with self-models | Implemented | Each agent declares its real capabilities and limits |
| Agent runtime state | Implemented | IDLE / THINKING / PLANNING / EXECUTING / ERROR / STOPPED, with live counters |
| Structured inter-agent messaging | Implemented | Assignments and verification results with status, error, recommended action |
| Task planning / decomposition | Implemented | Template-based intent routing; no LLM required |
| Recovery engine | Implemented | Classify → strategise → retry with backoff and an anti-spin guard |
| Shell execution with audit trail | Implemented | Every command logs command, cwd, exit code, duration, state |
| Risk policy (LOW/MEDIUM/HIGH) | Implemented | Reversibility, privilege need, and category assessed before execution |
| Killswitch | Implemented | Env var or sentinel file; checked before every action |
| Persistent task + project state | Implemented | Atomic JSON state under `$KALI_AEGIS_HOME` |
| Secret redaction | Implemented | Tokens, keys, JWTs, PEM blocks scrubbed from all output |
| Sandboxed filesystem writes | Implemented | Writes confined to configured roots |
| Scoped reconnaissance | Implemented | TCP connect scan + banner grab, scope-enforced |
| Diagnostics table | Implemented | `aegis doctor` reports each component as OK / WARN / MISSING |
| REST API | Implemented | Stdlib `http.server`, loopback by default |
| Desktop console | Implemented | Tkinter glass UI; thread composer, live agent rail, killswitch |
| Open-ended code generation | **Not implemented** | Needs an LLM; blocked steps are reported honestly |
| Exploitation / payload delivery | **Not implemented** | Deliberately out of scope |

Read the [architecture](docs/ARCHITECTURE.md) for how the pieces fit.

## The important caveat

KALI-AEGIS has **no built-in LLM**. The deterministic core does real work — shell
execution, risk enforcement, filesystem operations, scoped recon, recovery,
audit logging — but it cannot invent code from a natural-language objective on
its own. When a planned step needs reasoning it does not have, the run reports
`STATUS: BLOCKED` with a reason instead of pretending it succeeded.

That honesty is the point. An agent that fabricates success is worse than one
that admits a limit.

## Install

```bash
git clone --branch kali-ops-implementation \
  https://github.com/logvex-sg/AI-model.git
cd AI-model
```

For a full walkthrough — requirements, pipx, venv, verification, uninstall, and
troubleshooting — see the **[install guide](docs/INSTALL.md)**. The short version:

### Just run it (recommended)

No install required at all:

```bash
python3 -m aegis doctor
```

Every command below works with `python3 -m aegis <command>`.

### Install the `aegis` command

Kali and Debian block system-wide `pip install` with
[PEP 668](https://peps.python.org/pep-0668/) (`error: externally-managed-environment`).
Use one of these instead.

**pipx** (cleanest — isolated, puts the command on your PATH):

```bash
sudo apt-get install -y pipx
pipx install .
aegis doctor
```

**Virtual environment** (if you want a normal editable install):

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/aegis doctor
```

**Not recommended:** `pip install -e . --break-system-packages`. It can corrupt
your system Python. The two options above are strictly better.

If you installed with `pip --user` and get `aegis: command not found`, the
script landed in `~/.local/bin`, which is often not on `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Python 3.9+ is required. The desktop console additionally needs the system Tk
libraries:

```bash
sudo apt-get install -y python3-tk      # Kali / Debian / Ubuntu
```

## Quick start

```bash
aegis start                        # banner, health table, readiness
aegis doctor                       # detailed diagnostics
aegis agents                       # inspect the team's self-models
aegis monitor                      # live per-agent state
aegis exec "uname -a"              # run a command through the audit trail
aegis plan "create a python tool"  # decompose without executing
aegis task "create a python tool"  # plan and run
aegis pentest 127.0.0.1            # scoped recon of an authorized target
aegis kill                         # engage the killswitch
aegis resume                       # release it
aegis gui                          # launch the desktop console
```

## Desktop console

`aegis gui` opens a conversation-first assistant surface, not an admin table.
You describe an objective in the composer; the team plans and works on it, and
the thread shows each step in plain language with raw commands kept inside
their own monospace cards.

- **Thread** — the main surface. Ask for something, watch the plan and activity
  stream in, read the result and next action.
- **Team** — each agent's self-model: what it can do and where it stops.
- **Tasks / Audit / Security / Settings** — objectives, the append-only
  operation log, the authorized scope and safety posture, effective config.

A live agent rail sits on the right showing each agent's state, current action,
and elapsed time; the killswitch is always one click away in the top bar or the
left rail. The glass aesthetic is drawn entirely in Tkinter — layered
translucent panels, hairline borders, a lit top bevel on each card, and a soft
gradient hero — with no third-party theme or widget dependency.

## Commands

| Command | Purpose |
|---|---|
| `start` / `stop` / `restart` | Verify readiness / halt / release and re-verify |
| `status` | Runtime, agent, and killswitch status |
| `doctor [--json]` | Component health table |
| `plan <objective>` | Decompose an objective |
| `task <objective>` | Plan and execute an objective |
| `resume-task <id>` | Resume an INTERRUPTED task |
| `tasks` | List known tasks |
| `exec <command>` | Execute a shell command |
| `shell` | Interactive command shell |
| `agents` / `monitor` | Self-models / live state |
| `messages` | Structured inter-agent messages |
| `logs` | Recent audit entries |
| `pentest <target>` / `network <target>` | Scoped reconnaissance |
| `security` | Authorized scope and safety posture |
| `repo init` / `repo status` | Repository helpers |
| `build` / `test` | Build and test a project |
| `kill` / `resume` | Killswitch control |
| `config` | Print effective configuration |
| `api` / `gui` | Run the REST API / desktop console |

Global flags: `--config PATH`, `--home PATH`, `--dry-run`.

### Exit codes

Command failures are typed, so scripts can tell a policy denial from a crash:

| Code | Meaning |
|---|---|
| 1 | General failure |
| 2 | Configuration error |
| 3 | Killswitch engaged |
| 4 | Scope violation |
| 5 | Risk denied |
| 6 | Command failed |
| 7 | Not found |
| 8 | Recovery exhausted |

## Configuration

Precedence: explicit overrides → environment → config file → defaults.

The config file lives at `$KALI_AEGIS_HOME/config.toml` (default `~/.aegis/`):

```toml
log_level = "INFO"
command_timeout = 300
api_host = "127.0.0.1"
api_port = 8765
authorized_hosts = ["lab.internal"]
allowed_write_paths = ["/srv/work"]
auto_approve_high_risk = false
max_retries = 1
retry_backoff_s = 1.0
```

Every field can also be set through the environment:

```bash
export KALI_AEGIS_API_PORT=9000
export KALI_AEGIS_AUTHORIZED_HOSTS="lab.internal,ctf.local"
export KALI_AEGIS_AUTO_APPROVE_HIGH_RISK=1   # disposable environments only
```

## Safety model

Three boundaries are enforced in code, not just documented:

**Risk.** Every command is graded before it runs. `rm -rf /` and friends are
HIGH risk and refused unless you pass `--confirm-high-risk` or explicitly set
`auto_approve_high_risk`. Unknown commands default to MEDIUM, never LOW. The
assessment also records whether the operation is reversible and whether it needs
root.

**Scope.** Owning the machine KALI-AEGIS runs on does not authorize attacking
another host. Only loopback, this machine's own addresses, and hosts listed in
`authorized_hosts` pass the scope check. A bare LAN address such as
`10.0.0.5` is treated as a *foreign* host — same network, different machine,
still needs authorization.

**Killswitch.** Engage with `aegis kill`, `KALI_AEGIS_KILLSWITCH=1`, or the
desktop button. It is checked before every operation and halts new work
immediately, including between recovery attempts.

## Development

```bash
python3 -m unittest discover -s tests -t .   # run the suite (74 tests, no deps)
python3 -m compileall aegis                  # syntax check
```

## License

MIT — see [LICENSE](LICENSE).
