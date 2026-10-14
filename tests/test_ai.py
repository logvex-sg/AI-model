"""LLM client and reasoning-loop tests.

The client is tested against a stub that speaks the same wire protocol, so the
request shape, URL normalisation, and response parsing are all exercised for
real. The agent loop is tested by feeding it scripted tool calls — no network.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from aegis.ai import AIAgent, AgentRun, Step
from aegis.llm import (
    KEYLESS_PROVIDERS,
    LLMClient,
    LLMError,
    LLMResponse,
    parse_json_object,
    resolve_base_url,
)


class LLMClientTest(unittest.TestCase):
    def test_unavailable_without_config(self) -> None:
        client = LLMClient()
        self.assertFalse(client.available)
        with self.assertRaises(LLMError):
            client.complete([{"role": "user", "content": "hi"}])

    def test_endpoint_normalisation_adds_v1(self) -> None:
        self.assertEqual(
            LLMClient(base_url="https://api.example.com", model="m").endpoint,
            "https://api.example.com/v1/chat/completions",
        )

    def test_endpoint_normalisation_keeps_existing_v1(self) -> None:
        self.assertEqual(
            LLMClient(base_url="https://api.example.com/v1", model="m").endpoint,
            "https://api.example.com/v1/chat/completions",
        )

    def test_provider_lookup(self) -> None:
        self.assertEqual(resolve_base_url("openai"), "https://api.openai.com/v1")
        self.assertIn("127.0.0.1", resolve_base_url("ollama"))
        self.assertEqual(resolve_base_url("nonexistent-provider"), "")
        # An explicit URL always wins.
        self.assertEqual(
            resolve_base_url("openai", "http://my.host/v1"), "http://my.host/v1"
        )

    def test_local_providers_need_no_api_key(self) -> None:
        for provider in ("ollama", "lmstudio"):
            self.assertIn(provider, KEYLESS_PROVIDERS)

    def test_completion_parses_a_response(self) -> None:
        payload = {
            "model": "test-model",
            "choices": [{"message": {"content": "  hello  "}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }
        client = LLMClient(base_url="http://x", model="test-model", api_key="k")
        with mock.patch.object(client, "_raw_post", return_value=payload):
            response = client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(response.text, "hello")
        self.assertEqual(response.total_tokens, 18)
        self.assertEqual(response.model, "test-model")

    def test_no_choices_raises(self) -> None:
        client = LLMClient(base_url="http://x", model="m")
        with mock.patch.object(client, "_raw_post", return_value={"choices": []}):
            with self.assertRaises(LLMError):
                client.complete([{"role": "user", "content": "hi"}])


class JSONParsingTest(unittest.TestCase):
    def test_plain_object(self) -> None:
        self.assertEqual(parse_json_object('{"a": 1}'), {"a": 1})

    def test_fenced_object(self) -> None:
        self.assertEqual(
            parse_json_object('```json\n{"a": 1}\n```'), {"a": 1}
        )

    def test_bare_fence(self) -> None:
        self.assertEqual(parse_json_object('```\n{"a": 2}\n```'), {"a": 2})

    def test_invalid_raises(self) -> None:
        with self.assertRaises(LLMError):
            parse_json_object("not json at all")

    def test_non_object_raises(self) -> None:
        with self.assertRaises(LLMError):
            parse_json_object("[1, 2, 3]")


class _ScriptedClient:
    """A client that returns canned responses in order."""

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = []

    @property
    def available(self) -> bool:
        return True

    def complete(self, messages, tools=None, json_mode=False):
        self.calls.append(messages)
        raw = self.responses.pop(0)
        return LLMResponse(
            text=raw.get("text", ""),
            prompt_tokens=1,
            completion_tokens=1,
            raw=raw.get("raw", {}),
        )


def _tool_response(name, arguments, call_id="call_1"):
    return {
        "raw": {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }],
                }
            }]
        }
    }


class _StubResult:
    """Mimics the executor's AgentResult for the command tool."""

    def __init__(self, ok=True, stdout="", stderr="", returncode=0) -> None:
        self.data = {
            "ok": ok, "stdout": stdout, "stderr": stderr, "returncode": returncode,
        }


class AIAgentTest(unittest.TestCase):
    def _agent(self, responses, **kwargs):
        client = _ScriptedClient(responses)
        recorded = []

        def run_command(command, cwd=None):
            recorded.append(command)
            return _StubResult(stdout=f"ran {command}")

        agent = AIAgent(
            client=client,
            execute_command=run_command,
            read_file=lambda p: f"contents of {p}",
            write_file=lambda p, c: p,
            list_dir=lambda p, recursive=False: ["a", "b"],
            max_iterations=kwargs.get("max_iterations", 6),
        )
        agent.recorded = recorded
        return agent

    def test_finish_tool_returns_the_answer(self) -> None:
        agent = self._agent([_tool_response("finish", {"answer": "all done"})])
        run = agent.run("do a thing")
        self.assertTrue(run.ok)
        self.assertEqual(run.answer, "all done")
        self.assertEqual(run.iterations, 1)

    def test_command_tool_executes_and_feeds_result_back(self) -> None:
        agent = self._agent([
            _tool_response("run_command", {"command": "uname -a"}),
            _tool_response("finish", {"answer": "kernel checked"}),
        ])
        run = agent.run("check the kernel")
        self.assertEqual(agent.recorded, ["uname -a"])
        self.assertTrue(run.ok)
        tools = [s.tool for s in run.steps]
        self.assertEqual(tools, ["run_command", "finish"])

    def test_prose_answer_without_a_tool_finishes(self) -> None:
        agent = self._agent([{"text": "here is my answer", "raw": {
            "choices": [{"message": {"content": "here is my answer"}}]}}])
        run = agent.run("explain something")
        self.assertTrue(run.ok)
        self.assertEqual(run.answer, "here is my answer")

    def test_iteration_budget_is_enforced(self) -> None:
        # Always asks for another command; never finishes.
        agent = self._agent(
            [_tool_response("run_command", {"command": "echo x"})] * 20,
            max_iterations=3,
        )
        run = agent.run("loop forever")
        self.assertFalse(run.ok)
        self.assertIn("without finishing", run.error)
        self.assertEqual(run.iterations, 3)

    def test_denied_command_is_reported_as_a_failed_step(self) -> None:
        client = _ScriptedClient([
            _tool_response("run_command", {"command": "rm -rf /"}),
            _tool_response("finish", {"answer": "blocked"}),
        ])

        def refuse(command, cwd=None):
            raise PermissionError("denied by risk policy")

        agent = AIAgent(
            client=client, execute_command=refuse,
            read_file=lambda p: "", write_file=lambda p, c: p,
            list_dir=lambda p, recursive=False: [],
        )
        run = agent.run("delete everything")
        failed = [s for s in run.steps if not s.ok]
        self.assertTrue(failed, "the refusal must appear as a failed step")
        self.assertIn("denied", failed[0].detail)

    def test_unavailable_client_reports_no_model(self) -> None:
        client = LLMClient()
        agent = AIAgent(
            client=client, execute_command=lambda *a, **k: None,
            read_file=lambda p: "", write_file=lambda p, c: p,
            list_dir=lambda p, recursive=False: [],
        )
        run = agent.run("anything")
        self.assertFalse(run.ok)
        self.assertIn("no LLM", run.error)

    def test_read_and_write_tools_hit_the_filesystem_layer(self) -> None:
        agent = self._agent([
            _tool_response("read_file", {"path": "/tmp/x"}),
            _tool_response("write_file", {"path": "/tmp/y", "content": "data"}),
            _tool_response("finish", {"answer": "done"}),
        ])
        run = agent.run("touch a file")
        outputs = {s.tool: s.output for s in run.steps}
        self.assertIn("contents of /tmp/x", outputs["read_file"])
        self.assertIn("/tmp/y", outputs["write_file"])

    def test_elevated_command_is_flagged_on_the_step(self) -> None:
        client = _ScriptedClient([
            _tool_response("run_command", {"command": "apt-get update"}),
            _tool_response("finish", {"answer": "updated"}),
        ])
        agent = AIAgent(
            client=client,
            execute_command=lambda c, cwd=None: _StubResult(stdout="ok"),
            read_file=lambda p: "", write_file=lambda p, c: p,
            list_dir=lambda p, recursive=False: [],
        )
        # The stub result carries no elevation, so the step must not claim any.
        run = agent.run("update packages")
        step = run.steps[0]
        self.assertFalse(step.arguments["elevated"])
        self.assertEqual(step.arguments["executed"], "apt-get update")


class ElevationReportingTest(unittest.TestCase):
    """An elevated command must be visibly marked in the report."""

    def test_report_marks_elevated_commands(self) -> None:
        import tempfile
        from pathlib import Path

        from aegis.config import Config
        from aegis.orchestrator import Runtime

        with tempfile.TemporaryDirectory() as tmp:
            rt = Runtime.build(Config(home=Path(tmp)))
            run = AgentRun(objective="update packages", steps=[
                Step(kind="tool", tool="run_command", ok=True,
                     arguments={"command": "apt-get update",
                                "executed": "sudo -n apt-get update",
                                "elevated": True},
                     output="hit", detail="exit 0 (MEDIUM risk), elevated"),
                Step(kind="tool", tool="finish", ok=True, arguments={}),
            ], answer="done", iterations=2, tokens=5)
            report = rt._ai_report("update packages", "", run)
            self.assertEqual(report["ELEVATED"], ["sudo -n apt-get update"])
            self.assertEqual(report["STATUS"], "COMPLETE")


class RuntimeAITest(unittest.TestCase):
    """The runtime must not claim AI capability when none is configured."""

    def test_report_is_honest_without_a_model(self) -> None:
        import tempfile
        from pathlib import Path

        from aegis.config import Config
        from aegis.orchestrator import Runtime

        with tempfile.TemporaryDirectory() as tmp:
            config = Config(home=Path(tmp))
            rt = Runtime.build(config)
            self.assertFalse(rt.llm_available)
            report = rt.act("do something")
            self.assertIn("no LLM configured", report["MODE"])
            self.assertIn(report["STATUS"], {"BLOCKED", "FAILED", "INTERRUPTED"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
