"""Minimal REST API over the agent runtime.

Built on :mod:`http.server` so it needs no third-party dependency. It binds to
loopback by default; exposing it on a public interface is the operator's
explicit choice (``KALI_AEGIS_API_HOST``).

Endpoints::

    GET  /health              liveness + killswitch state
    GET  /v1/agents           per-agent self-models
    GET  /v1/status           runtime status
    GET  /v1/monitor          live per-agent state
    GET  /v1/diagnostics      environment health assessment
    GET  /v1/messages         structured inter-agent messages
    GET  /v1/logs?limit=N     recent audit entries
    POST /v1/plan             {"objective": ..., "scope": ...}
    POST /v1/task             {"objective": ..., "scope": ...}
    POST /v1/resume-task      {"task_id": ...}
    POST /v1/exec             {"command": ..., "confirmed": bool}
    POST /v1/kill             engage the killswitch
    POST /v1/resume           release the killswitch
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import Config
from .errors import AegisError
from .orchestrator import Runtime

MAX_BODY = 1_000_000


class Handler(BaseHTTPRequestHandler):
    """Routes requests to the runtime. ``runtime`` is injected by :func:`serve`."""

    runtime: Runtime
    server_version = f"aegis/{__version__}"

    # -- helpers -------------------------------------------------------------
    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # Keep the audit log as the single source of truth; silence stdlib noise.
        return

    # -- routes --------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        routes: Dict[str, Callable[[], Any]] = {
            "/health": lambda: {
                "ok": True,
                "version": __version__,
                "killswitch": self.runtime.killswitch.is_engaged(),
                "llm_available": self.runtime.llm_available,
            },
            "/v1/agents": self.runtime.introspect,
            "/v1/privilege": lambda: (
                self.runtime.privileges.report().to_dict()
                if self.runtime.privileges else {}
            ),
            "/v1/status": lambda: {
                "agents": self.runtime.agent_status(),
                "tasks": len(self.runtime.state.all()),
                "queue": len(self.runtime.state.queue()),
                "interrupted": len(self.runtime.state.interrupted()),
                "llm": {
                    "provider": self.runtime.config.model_provider,
                    "model": self.runtime.config.model_name,
                    "available": self.runtime.llm_available,
                },
                "privilege": (
                    self.runtime.privileges.report().to_dict()
                    if self.runtime.privileges else {}
                ),
            },
            "/v1/monitor": self.runtime.monitor,
            "/v1/diagnostics": lambda: self.runtime.diagnostics().to_dict(),
            "/v1/messages": lambda: self.runtime.messages(
                limit=int(query.get("limit", ["50"])[0])
            ),
            "/v1/logs": lambda: self.runtime.log.read(
                limit=int(query.get("limit", ["20"])[0])
            ),
        }
        handler = routes.get(parsed.path)
        if handler is None:
            self._send(404, {"error": "not found", "path": parsed.path})
            return
        self._send(200, handler())

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": f"invalid JSON: {exc}"})
            return

        try:
            status, payload = self._dispatch_post(parsed.path, body)
        except AegisError as exc:
            self._send(409, {"error": str(exc), "type": type(exc).__name__})
            return
        except (KeyError, ValueError) as exc:
            self._send(400, {"error": str(exc)})
            return
        self._send(status, payload)

    def _dispatch_post(self, path: str, body: Dict[str, Any]) -> Tuple[int, Any]:
        if path == "/v1/plan":
            objective = self._require(body, "objective")
            return 200, self.runtime.plan(objective, body.get("scope", ""))
        if path == "/v1/task":
            objective = self._require(body, "objective")
            return 200, self.runtime.run_task(objective, body.get("scope", ""))
        if path == "/v1/resume-task":
            task_id = self._require(body, "task_id")
            return 200, self.runtime.resume_task(task_id)
        if path == "/v1/exec":
            command = self._require(body, "command")
            result = self.runtime.executor.execute(
                command, confirmed=bool(body.get("confirmed", False))
            )
            if result.ok:
                return 200, result.to_dict()
            # Map the typed error class onto an HTTP status so a denial is not
            # indistinguishable from a command that merely exited non-zero.
            status_by_class = {
                "permission": 403,
                "transient": 503,
                "environment": 500,
                "input": 400,
            }
            return status_by_class.get(result.error_class, 422), result.to_dict()
        if path == "/v1/kill":
            return 200, {"sentinel": str(self.runtime.killswitch.engage(body.get("reason", "api request")))}
        if path == "/v1/resume":
            return 200, {"released": self.runtime.killswitch.release()}
        return 404, {"error": "not found", "path": path}

    @staticmethod
    def _require(body: Dict[str, Any], key: str) -> Any:
        if key not in body:
            raise KeyError(f"missing required field: {key}")
        return body[key]


def make_server(config: Config, host: Optional[str] = None, port: Optional[int] = None) -> ThreadingHTTPServer:
    """Build (but do not start) the HTTP server."""
    runtime = Runtime.build(config)
    handler = type("BoundHandler", (Handler,), {"runtime": runtime})
    return ThreadingHTTPServer((host or config.api_host, port or config.api_port), handler)


def serve(config: Config, host: Optional[str] = None, port: Optional[int] = None) -> None:
    """Run the API until interrupted."""
    httpd = make_server(config, host, port)
    bound_host, bound_port = httpd.server_address[:2]
    print(f"aegis API listening on http://{bound_host}:{bound_port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("\nshutting down")
    finally:
        httpd.server_close()
