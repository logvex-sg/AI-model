"""``aegis`` command-line interface.

Implements the full command set from operating-model section 13: lifecycle,
diagnostics, task execution, execution, introspection, audit, monitoring,
repository helpers, security, network, configuration, and killswitch control.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .config import load_config
from .errors import AegisError, AegisHaltedError
from .orchestrator import Runtime
from .secrets import redact

BANNER = r"""
+----------------------------------------------------------------------+
|                            K A L I // A E G I S                       |
|         AUTONOMOUS SECURITY & SOFTWARE ENGINEERING PLATFORM           |
+----------------------------------------------------------------------+
"""


def _print(data: Any) -> None:
    if isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, indent=2, default=str))


def _build_runtime(args: argparse.Namespace, *, dry_run: Optional[bool] = None) -> Runtime:
    overrides: Dict[str, Any] = {}
    if getattr(args, "home", None):
        overrides["home"] = Path(args.home)
    config = load_config(getattr(args, "config", None), overrides)
    if dry_run is None:
        dry_run = getattr(args, "dry_run", False)
    return Runtime.build(config, dry_run=dry_run)


# --------------------------------------------------------------------------- #
# lifecycle
# --------------------------------------------------------------------------- #
def cmd_start(args: argparse.Namespace) -> int:
    """Verify readiness and print the system identity."""
    rt = _build_runtime(args)
    report = rt.diagnostics()
    print(BANNER)
    print(report.render())
    print()
    _print(
        {
            "version": __version__,
            "home": str(rt.config.home),
            "llm_configured": rt.config.model_provider not in {"", "none"},
            "killswitch": "ENGAGED" if rt.killswitch.is_engaged() else "READY",
            "agents": sorted(rt.agents),
            "ready": report.ok,
            "degraded": report.degraded,
        }
    )
    return 0 if report.ok else 1


def cmd_stop(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    path = rt.killswitch.engage("aegis stop")
    _print({"stopped": True, "killswitch": "engaged", "sentinel": str(path)})
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    rt.killswitch.release()
    report = rt.diagnostics()
    print(BANNER)
    print(report.render())
    _print({"restarted": True, "ready": report.ok})
    return 0 if report.ok else 1


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
            "queue": len(rt.state.queue()),
            "interrupted": len(rt.state.interrupted()),
            "projects": [p.path for p in rt.state.projects()],
        }
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Run the health assessment and print the component table."""
    rt = _build_runtime(args)
    report = rt.diagnostics()
    if getattr(args, "json", False):
        _print(report.to_dict())
    else:
        print(report.render())
    return 0 if report.ok else 1


# --------------------------------------------------------------------------- #
# introspection & monitoring
# --------------------------------------------------------------------------- #
def cmd_agents(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    _print(rt.introspect())
    return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    _print(rt.monitor())
    return 0


def cmd_messages(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    _print(rt.messages(limit=args.limit))
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    for entry in rt.log.tail(limit=args.limit):
        print(json.dumps(entry, default=str))
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    _print(load_config(getattr(args, "config", None)).to_dict())
    return 0


# --------------------------------------------------------------------------- #
# tasks
# --------------------------------------------------------------------------- #
def cmd_plan(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    _print(rt.plan(args.objective, args.scope or ""))
    return 0


def cmd_task(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    report = rt.run_task(args.objective, args.scope or "")
    _print(report)
    return 0 if report.get("STATUS") == "COMPLETE" else 1


def cmd_resume_task(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    report = rt.resume_task(args.task_id)
    _print(report)
    return 0 if report.get("STATUS") == "COMPLETE" else 1


def cmd_tasks(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    _print(
        [
            {
                "id": t.id,
                "objective": t.objective,
                "status": t.status,
                "subtasks": len(t.subtasks),
            }
            for t in rt.state.all()
        ]
    )
    return 0


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #
def cmd_exec(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    result = rt.executor.execute(
        args.command,
        confirmed=args.confirm_high_risk,
        retries=args.retries,
        cwd=args.cwd,
    )
    _print(result.to_dict())
    if result.ok:
        return 0
    return int(result.exit_code or result.data.get("returncode", 1) or 1)


def cmd_shell(args: argparse.Namespace) -> int:
    """Interactive REPL. Every command goes through the risk policy."""
    rt = _build_runtime(args)
    print(BANNER)
    print("interactive shell - blank line or 'exit' to leave")
    while True:
        try:
            line = input("aegis> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line or line in {"exit", "quit"}:
            break
        result = rt.executor.execute(line, confirmed=False)
        data = result.data or {}
        if data.get("stdout"):
            print(data["stdout"], end="")
        if data.get("stderr"):
            print(data["stderr"], end="", file=sys.stderr)
        if not result.ok:
            print(f"[exit {data.get('returncode')}: {result.summary}]", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- #
# security & network
# --------------------------------------------------------------------------- #
def cmd_pentest(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    result = rt.pentester.recon(args.target)
    _print(result.to_dict())
    return 0 if result.ok else 1


def cmd_network(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    result = rt.pentester.recon(args.target)
    _print(result.to_dict())
    return 0 if result.ok else 1


def cmd_security(args: argparse.Namespace) -> int:
    """Report the authorized scope and the safety posture."""
    rt = _build_runtime(args)
    from .security.scope import Scope, local_addresses

    scope = Scope.from_config(rt.config.authorized_hosts)
    _print(
        {
            "authorized_hosts": sorted(scope.authorized_hosts),
            "authorized_count": len(scope.authorized_hosts),
            "always_allowed": sorted(local_addresses()),
            "auto_approve_high_risk": rt.config.auto_approve_high_risk,
            "max_retries": rt.config.max_retries,
            "killswitch": rt.killswitch.is_engaged(),
            "allow_root": rt.config.allow_root,
        }
    )
    return 0


# --------------------------------------------------------------------------- #
# repository helpers
# --------------------------------------------------------------------------- #
def cmd_repo_init(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    target = Path(args.path).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    result = rt.executor.execute(f"git init {target}")
    _print(result.to_dict())
    rt.state.add_project(str(target), "git")
    return 0 if result.ok else 1


def cmd_repo_status(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    target = Path(args.path).expanduser()
    result = rt.executor.execute(f"git -C {target} status --short --branch")
    _print(result.to_dict())
    return 0 if result.ok else 1


def cmd_build(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    result = rt.builder.build(args.command or "make", confirmed=args.confirm_high_risk, cwd=args.cwd)
    _print(result.to_dict())
    if args.command or args.cwd:
        rt.state.record_build(args.cwd or ".", result.summary)
    return 0 if result.ok else 1


def cmd_test(args: argparse.Namespace) -> int:
    rt = _build_runtime(args)
    result = rt.builder.run_tests(args.command, cwd=args.cwd)
    _print(result.to_dict())
    rt.state.record_test(args.cwd or ".", result.summary)
    return 0 if result.ok else 1


# --------------------------------------------------------------------------- #
# killswitch
# --------------------------------------------------------------------------- #
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
            "note": "unset KALI_AEGIS_KILLSWITCH to fully release" if still_env else "released",
        }
    )
    return 0 if not rt.killswitch.is_engaged() else 1


# --------------------------------------------------------------------------- #
# interfaces
# --------------------------------------------------------------------------- #
def cmd_api(args: argparse.Namespace) -> int:
    from .api import serve

    config = load_config(getattr(args, "config", None))
    serve(config, host=args.host, port=args.port)
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from .gui import launch

    config = load_config(getattr(args, "config", None))
    return launch(config)


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aegis",
        description="KALI-AEGIS autonomous security & software engineering platform",
    )
    parser.add_argument("--version", action="version", version=f"aegis {__version__}")
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("--home", help="override $KALI_AEGIS_HOME")
    parser.add_argument("--dry-run", action="store_true", help="plan without executing")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, fn, help_text: str, **kwargs) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, **kwargs)
        p.set_defaults(func=fn)
        return p

    # lifecycle
    add("start", cmd_start, "verify readiness and report system identity")
    add("stop", cmd_stop, "engage the killswitch and halt new work")
    add("restart", cmd_restart, "release the killswitch and re-verify")
    add("status", cmd_status, "show runtime, agents, and killswitch state")

    p = add("doctor", cmd_doctor, "run the environment health assessment")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")

    # introspection
    add("agents", cmd_agents, "show each agent's self-model")
    add("introspect", cmd_agents, "alias for agents")
    add("monitor", cmd_monitor, "show live per-agent state")
    p = add("messages", cmd_messages, "show structured inter-agent messages")
    p.add_argument("--limit", type=int, default=50)
    p = add("logs", cmd_logs, "show recent audit-log entries")
    p.add_argument("--limit", type=int, default=20)
    add("config", cmd_config, "print the effective configuration")

    # tasks
    p = add("task", cmd_task, "plan and execute an objective")
    p.add_argument("objective")
    p.add_argument("--scope", help="authorized scope for security steps")

    p = add("plan", cmd_plan, "decompose an objective without executing")
    p.add_argument("objective")
    p.add_argument("--scope", help="authorized scope")

    p = add("resume-task", cmd_resume_task, "resume an INTERRUPTED task")
    p.add_argument("task_id")

    add("tasks", cmd_tasks, "list known tasks")

    # execution
    p = add("exec", cmd_exec, "execute a shell command")
    p.add_argument("command", metavar="command")
    p.add_argument("--confirm-high-risk", action="store_true")
    p.add_argument("--retries", type=int, default=None)
    p.add_argument("--cwd", default=None)

    add("shell", cmd_shell, "interactive command shell")

    # security & network
    p = add("pentest", cmd_pentest, "scoped reconnaissance of an authorized target")
    p.add_argument("target")

    p = add("network", cmd_network, "network diagnostics for an authorized target")
    p.add_argument("target")

    add("security", cmd_security, "show the authorized scope and safety posture")

    # repository
    p = add("repo", None, "repository helpers")
    repo_sub = p.add_subparsers(dest="repo_command", required=True)
    ri = repo_sub.add_parser("init", help="initialise a git repository")
    ri.add_argument("path")
    ri.set_defaults(func=cmd_repo_init)
    rs = repo_sub.add_parser("status", help="show repository status")
    rs.add_argument("path", nargs="?", default=".")
    rs.set_defaults(func=cmd_repo_status)

    p = add("build", cmd_build, "run a build command")
    p.add_argument("command", nargs="?", default="make")
    p.add_argument("--confirm-high-risk", action="store_true")
    p.add_argument("--cwd", default=None)

    p = add("test", cmd_test, "run the test suite")
    p.add_argument("command", nargs="?", default="python3 -m unittest discover -s tests -t .")
    p.add_argument("--cwd", default=None)

    # killswitch
    p = add("kill", cmd_kill, "engage the killswitch")
    p.add_argument("--reason")

    add("resume", cmd_resume, "release the killswitch")

    # interfaces
    p = add("api", cmd_api, "run the REST API")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)

    add("gui", cmd_gui, "launch the desktop console")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    try:
        return func(args)
    except AegisHaltedError as exc:
        print(f"STATUS: INTERRUPTED\nREASON: {exc}", file=sys.stderr)
        return exc.exit_code
    except AegisError as exc:
        print(f"error: {redact(str(exc))}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
