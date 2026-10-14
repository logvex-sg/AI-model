"""JARVIS command line interface."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from jarvis.app import Jarvis
from jarvis.config import load_config
from jarvis.permissions import Mode

app = typer.Typer(help="JARVIS - local-first Linux assistant", no_args_is_help=True)
memory_app = typer.Typer(help="Inspect and edit persistent memory", no_args_is_help=True)
app.add_typer(memory_app, name="memory")
console = Console()


def _format_time(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return str(value)
    return datetime.fromtimestamp(value).isoformat(timespec="seconds")


def _confirm(tool: str, arguments: dict[str, Any], reason: str) -> bool:
    console.print(f"[yellow]Confirmation required[/yellow] {tool} {arguments} - {reason}")
    return typer.confirm("Allow this tool call?", default=False)


def _build(mode: str | None = None, interactive: bool = False) -> Jarvis:
    config = load_config()
    jarvis = Jarvis(config, confirm=_confirm if interactive else None)
    if mode:
        jarvis.set_mode(Mode(mode))
    return jarvis


@app.command()
def status(json_output: bool = typer.Option(False, "--json")) -> None:
    """Show mode, model, killswitch and monitor state."""
    jarvis = _build()
    data = jarvis.status()
    jarvis.close()
    if json_output:
        console.print_json(json.dumps(data, default=str))
        return
    model = data["model"]
    console.print(
        f"[bold]Mode:[/bold] {data['mode']}   [bold]Privilege:[/bold] {data['privilege']}"
    )
    console.print(
        f"[bold]Model:[/bold] {model['provider']}/{model['model']} "
        f"({model['location']}) reachable={model['reachable']}"
    )
    if model.get("error"):
        console.print(f"[red]Model backend error:[/red] {model['error']}")
    killswitch = data["killswitch"]
    state = f"ENGAGED - {killswitch['reason']}" if killswitch["engaged"] else "clear"
    console.print(f"[bold]Killswitch:[/bold] {state}")
    console.print(f"[bold]Tools:[/bold] {data['tools']}   [bold]Audit:[/bold] {data['audit_log']}")


@app.command()
def doctor() -> None:
    """Verify what actually works on this machine."""
    jarvis = _build()
    report = jarvis.doctor()
    jarvis.close()
    table = Table("check", "status", "detail")
    for check in report["checks"]:
        table.add_row(
            check["check"],
            "[green]ok[/green]" if check["ok"] else "[red]FAIL[/red]",
            str(check["detail"]),
        )
    console.print(table)
    raise typer.Exit(code=0 if report["healthy"] else 1)


@app.command("tools")
def list_tools() -> None:
    """List registered tools with their permission levels."""
    jarvis = _build()
    table = Table("tool", "permission", "category", "description")
    for category, tools in jarvis.registry.by_category().items():
        for tool in tools:
            table.add_row(tool.name, tool.permission.value, category, tool.description)
    jarvis.close()
    console.print(table)


@app.command("run")
def run_tool(
    name: str,
    arguments: str = typer.Option("{}", "--args", help="JSON object of arguments"),
    mode: str = typer.Option("normal", "--mode"),
) -> None:
    """Execute a single tool through the permission executor."""
    jarvis = _build(mode, interactive=True)
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as exc:
        console.print(f"[red]--args must be valid JSON:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    result = jarvis.executor.call(name, parsed)
    jarvis.close()
    console.print_json(
        json.dumps(
            {
                "tool": result.tool,
                "status": result.status,
                "result": result.result,
                "error": result.error,
                "reason": result.reason,
                "duration": round(result.duration, 3),
            },
            default=str,
        )
    )
    raise typer.Exit(code=0 if result.ok else 1)


@app.command()
def chat(
    message: str = typer.Argument(None, help="Single message; omitted for an interactive session"),
    mode: str = typer.Option("normal", "--mode", help="normal|code|beast|op|override"),
) -> None:
    """Talk to JARVIS. Requires a reachable model backend."""
    jarvis = _build(mode, interactive=True)
    status = jarvis.provider.available()
    if not status.get("reachable"):
        console.print(f"[red]Model backend unreachable:[/red] {status.get('error')}")
        console.print(
            "Start Ollama or configure a provider, then retry. Tools still work: jarvis run"
        )
        jarvis.close()
        raise typer.Exit(code=1)

    location = "LOCAL MODEL" if jarvis.provider.is_local else "REMOTE MODEL"
    console.print(f"[bold]{jarvis.mode.indicator}[/bold] | {location} | Ctrl-C to exit")

    def exchange(text: str) -> None:
        reply = jarvis.agent.chat(text)
        for turn in reply.turns:
            if turn.tool_call is None:
                continue
            result = turn.tool_result
            marker = "[green]OK[/green]" if result and result.ok else "[red]FAIL[/red]"
            console.print(f"  {marker} {turn.tool_call['tool']} {turn.tool_call['arguments']}")
        console.print(f"[bold cyan]JARVIS[/bold cyan] {reply.text}")

    try:
        if message:
            exchange(message)
        else:
            while True:
                text = console.input("[bold]you>[/bold] ").strip()
                if text in {"exit", "quit"}:
                    break
                if text:
                    exchange(text)
    except (KeyboardInterrupt, EOFError):
        console.print("\nbye")
    finally:
        jarvis.close()


@app.command("emergency-stop")
def emergency_stop(reason: str = typer.Option("manual emergency stop", "--reason")) -> None:
    """Engage the killswitch: stop tasks and block all further tool calls."""
    jarvis = _build()
    result = jarvis.emergency_stop(reason)
    jarvis.close()
    console.print(
        f"[red bold]EMERGENCY STOP ENGAGED[/red bold] "
        f"({result['cancelled_tasks']} task(s) cancelled)"
    )
    console.print(f"Audit log preserved at {result['audit_log']}")


@app.command()
def resume() -> None:
    """Clear the killswitch and re-enable autonomous execution."""
    jarvis = _build()
    jarvis.resume()
    jarvis.close()
    console.print("[green]Killswitch cleared[/green]")


@app.command()
def audit(limit: int = typer.Option(20, "--limit")) -> None:
    """Show recent audited tool calls."""
    jarvis = _build()
    records = jarvis.audit.tail(limit)
    jarvis.close()
    table = Table("time", "mode", "tool", "permission", "status")
    for record in records:
        table.add_row(
            _format_time(record.get("timestamp")),
            str(record.get("mode")),
            str(record.get("tool")),
            str(record.get("permission")),
            str(record.get("status")),
        )
    console.print(table)


@memory_app.command("remember")
def memory_remember(key: str, value: str, tags: str = typer.Option("", "--tags")) -> None:
    jarvis = _build()
    result = jarvis.executor.call("memory.remember", {"key": key, "value": value, "tags": tags})
    jarvis.close()
    if not result.ok:
        console.print(f"[red]{result.error or result.reason}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]remembered[/green] {key}")


@memory_app.command("search")
def memory_search(query: str, limit: int = typer.Option(10, "--limit")) -> None:
    jarvis = _build()
    results = jarvis.memory.search(query, limit)
    jarvis.close()
    table = Table("key", "value", "tags")
    for memory in results:
        table.add_row(memory.key, memory.value, memory.tags)
    console.print(table)


@memory_app.command("forget")
def memory_forget(key: str) -> None:
    jarvis = _build()
    deleted = jarvis.memory.forget(key)
    jarvis.close()
    console.print("[green]forgotten[/green]" if deleted else "[yellow]no such memory[/yellow]")


@memory_app.command("clear")
def memory_clear(yes: bool = typer.Option(False, "--yes", help="Skip confirmation")) -> None:
    if not yes and not typer.confirm("Delete all memories?", default=False):
        raise typer.Exit(code=1)
    jarvis = _build()
    count = jarvis.memory.clear()
    jarvis.close()
    console.print(f"[green]deleted {count} memories[/green]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
