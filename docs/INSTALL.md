# Install guide

Everything here is verified against a clean checkout. KALI-AEGIS has **no Python
dependencies** — it runs on the standard library — so installation is small. The
only friction on Kali/Debian is [PEP 668](https://peps.python.org/pep-0668/),
which blocks system-wide `pip install`. This guide covers every way around it.

## Requirements

| Requirement | Needed for | Check |
|---|---|---|
| Python 3.9+ | everything | `python3 --version` |
| git | cloning, `repo` commands | `git --version` |
| `python3-tk` | the desktop console only | `python3 -c "import tkinter"` |

Nothing else. No `requirements.txt`, no compiler, no network at runtime.

### Confirm Python and Tk

```bash
python3 --version                 # expect 3.9 or newer
python3 -c "import tkinter; print('tk ok')"
```

If `tkinter` fails, the CLI and API still work; only `aegis gui` needs it:

```bash
sudo apt-get update
sudo apt-get install -y python3-tk
```

## Step 1 — Get the code

```bash
git clone --branch kali-ops-implementation \
  https://github.com/logvex-sg/AI-model.git
cd AI-model
```

If you already have a checkout, `cd` into it instead. Verify what you got:

```bash
python3 -m aegis --version      # aegis 0.2.0
```

## Step 2 — Choose how to run it

You have three options. **Option A needs no install at all** and is the best
default.

---

### Option A — Run directly (no install)

Nothing to install. Run the package straight from the checkout:

```bash
python3 -m aegis doctor
```

Every command works this way:

```bash
python3 -m aegis start
python3 -m aegis task "create a python tool"
python3 -m aegis gui
```

Downside: you must be inside the checkout directory (or set `PYTHONPATH`), and
you type `python3 -m aegis` every time.

To get the short `aegis` command from a shell, add an alias:

```bash
echo "alias aegis='python3 -m aegis'" >> ~/.bashrc
source ~/.bashrc
aegis doctor
```

---

### Option B — pipx (recommended for a real install)

[pipx](https://pipx.pypa.io/) installs the app into its own isolated
environment and puts the `aegis` command on your `PATH`. It is immune to PEP 668
and cannot damage your system Python.

```bash
# Install pipx if you do not have it
sudo apt-get update
sudo apt-get install -y pipx

# Install KALI-AEGIS
cd /path/to/AI-model
pipx install .
```

Verify:

```bash
aegis --version      # aegis 0.2.0
aegis doctor
```

To upgrade after pulling new code:

```bash
pipx install --force .
```

To remove:

```bash
pipx uninstall aegis
```

> **If `pipx` reports `No apps associated with package`**, confirm `pyproject.toml`
> contains the `[project.scripts]` entry `aegis = "aegis.cli:main"` — that is what
> creates the command.

---

### Option C — Virtual environment (for development)

Use this when you want to edit the code and have changes take effect
immediately (an editable install).

```bash
cd /path/to/AI-model

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install --upgrade pip
pip install -e .
```

Verify:

```bash
aegis --version
aegis doctor
```

`-e` means *editable*: the `aegis` command points at your checkout, so edits to
`aegis/*.py` apply without reinstalling.

Deactivate when done:

```bash
deactivate
```

---

### What about a plain `pip install`?

On Kali and Debian, this fails by design:

```
error: externally-managed-environment
```

Do **not** work around it with `--break-system-packages` — that writes into your
distribution's Python and can break system tools. Options A, B, and C above are
strictly better. If you genuinely have an unmanaged Python (a self-built 3.11+,
a container, a VM you control), then a plain install is fine:

```bash
pip install .
```

## Step 3 — Verify the install

Run the health check. It reports each component as `OK`, `WARN`, or `MISSING`:

```bash
aegis doctor
```

Then a quick end-to-end sanity check:

```bash
aegis start                      # banner + readiness
aegis agents                     # the four agents and their self-models
aegis exec "uname -a"            # runs a command through the audit trail
aegis plan "create a python tool"  # decompose an objective (no execution)
aegis kill                       # engage the killswitch
aegis resume                     # release it
```

A `WARN` on an *optional* component (docker, a compiler, a language runtime you
do not use) is expected and harmless. Only a failed *required* check is a
problem — usually Python or git.

## Step 4 — First run

```bash
aegis start
```

Then pick an interface:

```bash
aegis task "create a python tool, test it, and init a git repo"   # CLI
aegis api                                                          # REST API on 127.0.0.1:8765
aegis gui                                                          # desktop console
```

## Where state lives

Everything KALI-AEGIS writes lives under one directory:

```
$KALI_AEGIS_HOME            (default ~/.aegis/)
├── config.toml             effective configuration
├── operations.jsonl        append-only audit log
├── state.json              tasks, subtasks, projects
└── KILLSWITCH              sentinel file when engaged
```

To keep a run isolated (tests, demos), point it elsewhere:

```bash
export KALI_AEGIS_HOME=/tmp/aegis-scratch
aegis start
```

To uninstall *and remove state*:

```bash
pipx uninstall aegis        # or: deactivate && rm -rf .venv
rm -rf ~/.aegis
```

## Configuration

Edit `~/.aegis/config.toml`, or override with environment variables:

```bash
export KALI_AEGIS_API_PORT=9000
export KALI_AEGIS_AUTHORIZED_HOSTS="lab.internal,ctf.local"
export KALI_AEGIS_AUTO_APPROVE_HIGH_RISK=1    # disposable labs only
export KALI_AEGIS_ALLOW_ROOT=1                # permit sudo/elevated commands
```

Precedence: explicit flags → environment → config file → defaults.

### Connecting a model

To enable the reasoning loop, point KALI-AEGIS at any OpenAI-compatible
endpoint. Without this the assistant still runs, but objectives that need
reasoning come back `BLOCKED` instead of being attempted.

```bash
# hosted (DeepSeek shown; openai/groq/together/openrouter/xai/mistral all work)
export KALI_AEGIS_MODEL_PROVIDER=deepseek
export KALI_AEGIS_MODEL_NAME=deepseek-chat
export KALI_AEGIS_MODEL_API_KEY=sk-...

# local, no key needed
export KALI_AEGIS_MODEL_PROVIDER=ollama
export KALI_AEGIS_MODEL_NAME=llama3.1
```

Verify with `aegis doctor` — the `MODEL` row should read `OK`. For any other
endpoint use `KALI_AEGIS_MODEL_PROVIDER=custom` plus
`KALI_AEGIS_MODEL_BASE_URL=https://your.host/v1`.

## Troubleshooting

### `aegis: command not found`

The script is installed but its directory is not on `PATH`. Find it and add it:

```bash
# pipx installs to ~/.local/bin by default
export PATH="$HOME/.local/bin:$PATH"

# or locate it
python3 -c "import site; print(site.USER_BASE + '/bin')"
```

Persist it:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### `error: externally-managed-environment`

PEP 668. You ran a plain `pip install`. Use Option A, B, or C. Do not reach for
`--break-system-packages`.

### `ModuleNotFoundError: No module named 'tkinter'`

The GUI needs system Tk:

```bash
sudo apt-get install -y python3-tk
```

The CLI and API are unaffected.

### `ModuleNotFoundError: No module named 'aegis'`

You are outside the checkout and did not install. Either `cd` into the repo, or
install with Option B or C.

### `aegis gui` prints "no display name and no $DISPLAY"

The desktop console needs a graphical session. Over SSH, forward X
(`ssh -X`) or use the CLI/API instead.

### Exit codes look unexpected

They are typed on purpose. See the table in the [README](../README.md#exit-codes):
`5` means a risk denial, `3` means the killswitch is engaged. These are not
crashes.

### Port already in use

```bash
python3 -m aegis api --port 9000
# or
aegis config          # inspect effective settings
```

## Uninstall

```bash
# pipx
pipx uninstall aegis

# venv
deactivate
rm -rf .venv

# remove runtime state (audit log, tasks, config)
rm -rf ~/.aegis
```

## Next steps

- [README](../README.md) — commands, safety model, exit codes
- [Architecture](ARCHITECTURE.md) — how the agents, recovery engine, and
  enforcement points fit together
- [System prompt](SYSTEM_PROMPT.md) — the spec, with an implementation-status table
