"""Composition root: wires configuration, security core, tools and the agent."""

from __future__ import annotations

from typing import Any

from jarvis.agent import Agent
from jarvis.audit import AuditLog
from jarvis.config import Config, load_config
from jarvis.executor import ConfirmCallback, Executor
from jarvis.killswitch import KillSwitch
from jarvis.memory import MemoryStore
from jarvis.monitor import BehaviorMonitor
from jarvis.permissions import Mode, PolicyEngine
from jarvis.providers import build_provider
from jarvis.providers.base import Provider
from jarvis.tasks import TaskManager
from jarvis.tools import build_registry


class Jarvis:
    """The JARVIS core. The CLI and any GUI are clients of this object."""

    def __init__(
        self,
        config: Config | None = None,
        provider: Provider | None = None,
        confirm: ConfirmCallback | None = None,
    ) -> None:
        self.config = config or load_config()
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        self.mode = Mode(self.config.mode)
        self.policy = PolicyEngine(self.mode)
        self.audit = AuditLog(self.config.audit_log_path)
        self.killswitch = KillSwitch(self.config.killswitch_path)
        self.monitor = BehaviorMonitor()
        self.memory = MemoryStore(self.config.memory_db_path)
        self.registry = build_registry(self.memory)
        self.tasks = TaskManager()
        self.executor = Executor(
            registry=self.registry,
            policy=self.policy,
            audit=self.audit,
            killswitch=self.killswitch,
            monitor=self.monitor,
            confirm=confirm,
        )
        self.provider = provider or build_provider(self.config.model)
        self.agent = Agent(self.provider, self.executor, self.memory, self.tasks)

    def set_mode(self, mode: Mode) -> None:
        self.mode = mode
        self.policy.set_mode(mode)

    def emergency_stop(self, reason: str = "manual emergency stop") -> dict[str, Any]:
        """Stop everything. Independent of the model and of the agent loop."""
        self.killswitch.engage(reason)
        cancelled = self.tasks.cancel_all()
        self.monitor.suspend(reason)
        return {
            "engaged": True,
            "reason": reason,
            "cancelled_tasks": cancelled,
            "audit_log": str(self.audit.path),
        }

    def resume(self) -> dict[str, Any]:
        self.killswitch.release()
        self.monitor.resume()
        return {"engaged": False}

    def status(self) -> dict[str, Any]:
        provider_status = self.provider.available()
        return {
            "mode": self.mode.indicator,
            "model": {
                "provider": self.config.model.provider,
                "model": self.config.model.model,
                "location": "LOCAL MODEL" if self.provider.is_local else "REMOTE MODEL",
                **provider_status,
            },
            "killswitch": {
                "engaged": self.killswitch.engaged,
                "reason": self.killswitch.reason(),
            },
            "monitor": self.monitor.snapshot(),
            "tools": len(self.registry.tools),
            "active_tasks": [task.snapshot() for task in self.tasks.active()],
            "memory_db": str(self.config.memory_db_path),
            "audit_log": str(self.config.audit_log_path),
            "privilege": "ADMIN" if self.mode is Mode.OP else "USER",
        }

    def doctor(self) -> dict[str, Any]:
        """Report what actually works on this machine."""
        checks: list[dict[str, Any]] = []
        provider_status = self.provider.available()
        checks.append(
            {
                "check": "model backend",
                "ok": bool(provider_status.get("reachable")),
                "detail": provider_status.get("error")
                or f"{len(provider_status.get('models', []))} model(s) installed",
            }
        )
        checks.append(
            {
                "check": f"model '{self.config.model.model}' installed",
                "ok": bool(provider_status.get("model_installed")),
                "detail": "verified via provider API" if provider_status.get("reachable")
                else "backend unreachable, cannot verify",
            }
        )
        metrics = self.executor.call("system.metrics")
        checks.append(
            {"check": "system metrics", "ok": metrics.ok, "detail": metrics.error or "ok"}
        )
        try:
            self.memory.remember("__doctor__", "memory write check")
            self.memory.forget("__doctor__")
            checks.append({"check": "memory store", "ok": True, "detail": str(self.memory.path)})
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            checks.append({"check": "memory store", "ok": False, "detail": str(exc)})
        checks.append(
            {
                "check": "audit log writable",
                "ok": self.audit.path.exists(),
                "detail": str(self.audit.path),
            }
        )
        checks.append(
            {
                "check": "killswitch clear",
                "ok": not self.killswitch.engaged,
                "detail": self.killswitch.reason() or "not engaged",
            }
        )
        return {"checks": checks, "healthy": all(check["ok"] for check in checks)}

    def close(self) -> None:
        self.executor.shutdown()
        self.memory.close()
