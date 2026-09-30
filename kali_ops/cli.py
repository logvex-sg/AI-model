"""``kali-ops`` command-line interface.

Subcommands mirror the specification: lifecycle (start/stop/status/doctor),
task execution (task/plan/exec), introspection (agents/introspect), audit
(logs), repository helpers, pentest, network, and the killswitch controls.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import socket
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .config import Config, load_config
from .errors import KaliOpsError, KillswitchActive
from .killswitch import Killswitch
from .orchestrator import Runtime
from .secrets import redact


def _print(data: Any) -> None:
    if isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, indent=2, default=str))


def _build_runtime(args: argparse.Namespace) -> Runtime:
    overrides: Dict[str, Any] = {}
    if getattr(args, "home", None):
        overrides["home"] = Path(args.home)
    config = load_config(getattr(args, "config", None), overrides)
    return Runtime.build(config, dry_run=getattr(args, "dry_run", False))


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_doctor(args: argparse.Namespace) -> int:
    """Verify the runtime environment and report degraded components."""
    config = load_config(getattr(args, "config", None))
    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    check("python", True, platform.python_version())
    check("platform", True, f"{platform.system()} {platform.release()}")
    check("filesystem", True, str(config.home))
    try:
        config.ensure_home()
        check("home_writable", True, str(config.home))
    except OSError as exc:
        check("home_writable", False, str(exc))
    for tool in ("git", "bash", "python3"):
        path = shutil.which(tool)
        check(tool, bool(path), path or "not found")
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=2).close()
        check("network", True, "outbound TCP reachable")
    except OSError as exc:
        check("network", False, f"no outbound connectivity: {exc}")
    ks = Killswitch(config)
    check("killswitch", True, ks.reason() or "disengaged")

    ok = all(c["ok"] for c in checks)
    _print({"ok": ok, "checks": checks})
    return 0 if ok else 1


def cmd_status(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    _print(
        {
            "version": __version__,
            "home": str(rt.config.home),
            "killswitch": {
                "engaged": rt.killswitch.is_engaged(),
                "reason": rt.killswitch.reason(),
                "path": str(rt.killswitch.path),
            },
            "agents": rt.agent_status(),
            "tasks": len(rt.state.all()),
        }
    )
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    _print(rt.introspect())
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    _print(rt.plan(args.objective, args.scope or ""))
    return 0


def cmd_task(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    report = rt.run_task(args.objective, args.scope or "")
    _print(report)
    return 0 if report.get("STATUS") == "COMPLETE" else 1


def cmd_exec(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    result = rt.executor.execute(
        args.command,
        confirmed=args.confirm_high_risk,
        retries=args.retries,
    )
    _print(result.to_dict())
    return 0 if result.ok else result.data.get("returncode", 1)


def cmd_logs(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    for entry in rt.log.tail(limit=args.limit):
        print(json.dumps(entry, default=str))
    return 0


def cmd_pentest(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    result = rt.pentester.recon(args.target)
    _print(result.to_dict())
    return 0 if result.ok else 1


def cmd_network(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    report = rt.pentester.recon(args.target)
    _print(report.to_dict())
    return 0 if report.ok else 1


def cmd_kill(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    path = rt.killswitch.engage(args.reason or "operator engaged killswitch")
    _print({"killswitch": "engaged", "sentinel": str(path)})
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    removed = rt.killswitch.release()
    still_env = rt.killswitch.env_engaged()
    _print(
        {
            "sentinel_removed": removed,
            "env_still_set": still_env,
            "engaged": rt.killswitch.is_engaged(),
            "note": "unset KALI_OPS_KILLSWITCH to fully release" if still_env else "released",
        }
    )
    return 0 if not rt.killswitch.is_engaged() else 1


def cmd_config(args: argparse.Namespace) -> int:
    config = load_config(getattr(args, "config", None))
    _print(config.to_dict())
    return 0


def cmd_repo_init(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    target = Path(args.path).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    result = rt.executor.execute(f"git init {target}")
    _print(result.to_dict())
    return 0 if result.ok else 1


def cmd_repo_build(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    result = rt.builder.build(args.command or "make", confirmed=args.confirm_high_risk)
    _print(result.to_dict())
    return 0 if result.ok else 1


def cmd_api(args: argparse.Namespace) -> int:
    from .api import serve

    config = load_config(getattr(args, "config", None))
    serve(config, host=args.host, port=args.port)
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from .gui import launch

    config = load_config(getattr(args, "config", None))
    return launch(config)


def cmd_start(args: argparse.Namespace) -> int:
    return cmd_doctor(args)


def cmd_stop(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    path = rt.killswitch.engage("kali-ops stop")
    _print({"stopped": True, "sentinel": str(path)})
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kali-ops", description="KALI-OPS security engineering assistant")
    parser.add_argument("--version", action="version", version=f"kali-ops {__version__}")
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("--home", help="override $KALI_OPS_HOME")
    parser.add_argument("--dry-run", action="store_true", help="plan without executing")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, fn, help_text: str, **kwargs) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, **kwargs)
        p.set_defaults(func=fn)
        return p

    add("start", cmd_start, "verify the environment and report readiness")
    add("stop", cmd_stop, "engage the killswitch and halt new work")
    add("status", cmd_status, "show runtime, agents, and killswitch state")
    add("doctor", cmd_doctor, "diagnose the runtime environment")

    p = add("task", cmd_task, "plan and execute an objective")
    p.add_argument("objective")
    p.add_argument("--scope", help="authorized scope for security steps")

    p = add("plan", cmd_plan, "decompose an objective without executing")
    p.add_argument("objective")
    p.add_argument("--scope", help="authorized scope")

    p = add("exec", cmd_exec, "execute a shell command")
    p.add_argument("command", metavar="command")
    p.add_argument("--confirm-high-risk", action="store_true")
    p.add_argument("--retries", type=int, default=0)

    add("agents", cmd_agents, "show each agent's self-model")
    add("introspect", cmd_agents, "alias for agents")

    p = add("logs", cmd_logs, "show recent audit-log entries")
    p.add_argument("--limit", type=int, default=20)

    p = add("pentest", cmd_pentest, "scoped reconnaissance of an authorized target")
    p.add_argument("target")

    p = add("network", cmd_network, "network diagnostics for an authorized target")
    p.add_argument("target")

    p = add("kill", cmd_kill, "engage the killswitch")
    p.add_argument("--reason")

    add("resume", cmd_resume, "release the killswitch")

    add("config", cmd_config, "print the effective configuration")

    p = add("repo", None, "repository helpers")
    repo_sub = p.add_subparsers(dest="repo_command", required=True)
    ri = repo_sub.add_parser("init", help="initialise a git repository")
    ri.add_argument("path")
    ri.set_defaults(func=cmd_repo_init)
    rb = repo_sub.add_parser("build", help="build a repository")
    rb.add_argument("command", nargs="?", default="make")
    rb.add_argument("--confirm-high-risk", action="store_true")
    rb.set_defaults(func=cmd_repo_build)

    p = add("api", cmd_api, "run the REST API")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)

    add("gui", cmd_gui, "launch the desktop application")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KillswitchActive as exc:
        print(f"STATUS: INTERRUPTED\nREASON: {exc}", file=sys.stderr)
        return exc.exit_code
    except KaliOpsError as exc:
        print(f"error: {redact(str(exc))}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
