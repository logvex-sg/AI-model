# KALI-OPS

An autonomous cybersecurity engineering assistant for systems, networks, and
repositories you own or are explicitly authorized to test. KALI-OPS runs four
cooperating agents — **leader**, **builder**, **pentester**, and **executor** —
behind a single CLI, a REST API, and a desktop application.

It is dependency-free: the whole thing runs on the Python standard library, so
it works on a bare Kali host with no `pip install` step.

## What it actually does

This is a working framework, not a proof of concept. What is implemented today:

| Capability | Status | Notes |
|---|---|---|
| Agent team with self-models | Implemented | Each agent declares its real capabilities and limits |
| Task planning / decomposition | Implemented | Template-based intent routing; no LLM required |
| Shell execution with audit trail | Implemented | Every command logged, exit codes captured, secrets redacted |
| Risk policy (LOW/MEDIUM/HIGH) | Implemented | HIGH risk denied unless confirmed |
| Killswitch | Implemented | Env var or sentinel file; checked before every action |
| Persistent task state | Implemented | Atomic JSON state under `$KALI_OPS_HOME` |
| Secret redaction | Implemented | Tokens, keys, JWTs, PEM blocks scrubbed from all output |
| Sandboxed filesystem writes | Implemented | Writes confined to configured roots |
| Scoped reconnaissance | Implemented | TCP connect scan + banner grab, scope-enforced |
| REST API | Implemented | Stdlib `http.server`, loopback by default |
| Desktop GUI | Implemented | Tkinter; 5 tabs incl. killswitch |
| Open-ended code generation | **Not implemented** | Needs an LLM; blocked steps are reported honestly |
| Exploitation / payload delivery | **Not implemented** | Deliberately out of scope |

Read the [architecture](docs/ARCHITECTURE.md) for how the pieces fit.

## The important caveat

KALI-OPS has **no built-in LLM**. The deterministic core does real work — shell
execution, risk enforcement, filesystem operations, scoped recon, audit
logging — but it cannot invent code from a natural-language objective on its
own. When a planned step needs reasoning it does not have, the run reports
`STATUS: BLOCKED` with a reason instead of pretending it succeeded.

That honesty is the point. An agent that fabricates success is worse than one
that admits a limit.

## Install

The implementation lives on the `kali-ops-implementation` branch.

```bash
git clone --branch kali-ops-implementation \
  https://github.com/logvex-sg/AI-model.git
cd AI-model
```

You can run it immediately from the source tree with `python3 -m kali_ops` — no
install required.

To get the `kali-ops` command instead:

```bash
python3 -m pip install -e .
kali-ops doctor
```

If you see `kali-ops: command not found` after installing, pip put the script in
`~/.local/bin`, which is often not on `PATH`. Fix it with:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Python 3.9+ is required. The GUI additionally needs the system Tk libraries:

```bash
sudo apt-get install -y python3-tk      # Kali / Debian / Ubuntu
```

## Quick start

```bash
kali-ops doctor                       # verify the environment
kali-ops agents                       # inspect the team's self-models
kali-ops exec "uname -a"              # run a command through the audit trail
kali-ops plan "create a python tool"  # decompose without executing
kali-ops task "create a python tool"  # plan and run
kali-ops pentest 127.0.0.1            # scoped recon of an authorized target
kali-ops kill                         # engage the killswitch
kali-ops resume                       # release it
kali-ops api                          # serve the REST API
kali-ops gui                          # launch the desktop app
```

## Commands

| Command | Purpose |
|---|---|
| `start` / `stop` | Verify readiness / engage the killswitch |
| `status` | Runtime, agent, and killswitch status |
| `doctor` | Diagnose the environment |
| `plan <objective>` | Decompose an objective |
| `task <objective>` | Plan and execute an objective |
| `exec <command>` | Execute a shell command |
| `agents` | Per-agent self-models |
| `logs` | Recent audit entries |
| `pentest <target>` / `network <target>` | Scoped reconnaissance |
| `repo init` / `repo build` | Repository helpers |
| `kill` / `resume` | Killswitch control |
| `config` | Print effective configuration |
| `api` / `gui` | Run the REST API / desktop app |

Global flags: `--config PATH`, `--home PATH`, `--dry-run`.

## Configuration

Precedence: explicit overrides → environment → config file → defaults.

The config file lives at `$KALI_OPS_HOME/config.toml` (default `~/.kali-ops/`):

```toml
log_level = "INFO"
command_timeout = 300
api_host = "127.0.0.1"
api_port = 8765
authorized_hosts = ["lab.internal"]
allowed_write_paths = ["/srv/work"]
auto_approve_high_risk = false
```

Every field can also be set through the environment:

```bash
export KALI_OPS_API_PORT=9000
export KALI_OPS_AUTHORIZED_HOSTS="lab.internal,ctf.local"
export KALI_OPS_AUTO_APPROVE_HIGH_RISK=1   # disposable environments only
```

## Safety model

Two boundaries are enforced in code, not just documented:

**Risk.** Every command is graded before it runs. `rm -rf /` and friends are
HIGH risk and refused unless you pass `--confirm-high-risk` or explicitly set
`auto_approve_high_risk`. Unknown commands default to MEDIUM, never LOW.

**Scope.** Owning the machine KALI-OPS runs on does not authorize attacking
another host. Only loopback, this machine's own addresses, and hosts listed in
`authorized_hosts` pass the scope check. A bare LAN address such as
`10.0.0.5` is treated as a *foreign* host — same network, different machine,
still needs authorization.

**Killswitch.** Engage with `kali-ops kill`, `KALI_OPS_KILLSWITCH=1`, or the
GUI button. It is checked before every operation and halts new work
immediately.

## Development

```bash
python3 -m unittest discover -s tests -t .   # run the suite (53 tests, no deps)
python3 -m compileall kali_ops               # syntax check
```

## License

MIT — see [LICENSE](LICENSE).
