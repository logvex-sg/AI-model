from __future__ import annotations

from jarvis.agent import Agent, parse_tool_call
from jarvis.permissions import Mode
from jarvis.providers.base import Completion, Message, Provider, ProviderError
from jarvis.tasks import TaskStatus


class ScriptedProvider(Provider):
    """A provider that replays a fixed script; no network involved."""

    name = "scripted"
    is_local = True

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.seen: list[list[Message]] = []

    def available(self):
        return {"reachable": True, "models": ["scripted"], "model_installed": True}

    def complete(self, messages: list[Message]) -> Completion:
        self.seen.append(list(messages))
        if not self.replies:
            return Completion("no more replies", "scripted")
        return Completion(self.replies.pop(0), "scripted")


class BrokenProvider(ScriptedProvider):
    def complete(self, messages):
        raise ProviderError("connection refused")


def test_parse_tool_call_variants():
    assert parse_tool_call('{"tool": "system.metrics", "arguments": {}}') == {
        "tool": "system.metrics",
        "arguments": {},
    }
    assert parse_tool_call('```json\n{"tool": "files.list", "args": {"path": "~"}}\n```') == {
        "tool": "files.list",
        "arguments": {"path": "~"},
    }
    assert parse_tool_call("Let me check: {\"tool\": \"system.os_info\"}") == {
        "tool": "system.os_info",
        "arguments": {},
    }
    assert parse_tool_call("The CPU is at 12%.") is None


def test_agent_executes_tool_then_answers(executor_factory, echo_tool, store):
    provider = ScriptedProvider(
        ['{"tool": "test.echo", "arguments": {"value": "ping"}}', "The echo returned ping."]
    )
    agent = Agent(provider, executor_factory(), store)
    reply = agent.chat("echo ping")
    assert reply.text == "The echo returned ping."
    assert [result.result for result in reply.tool_results] == [{"value": "ping"}]
    assert reply.task.status is TaskStatus.COMPLETED
    assert reply.task.steps[0].status == "ok"


def test_agent_receives_tool_observation(executor_factory, echo_tool, store):
    provider = ScriptedProvider(
        ['{"tool": "test.echo", "arguments": {"value": "abc"}}', "done"]
    )
    Agent(provider, executor_factory(), store).chat("echo abc")
    last_context = provider.seen[-1]
    assert any("Tool observation" in message.content for message in last_context)
    assert any("abc" in message.content for message in last_context)


def test_agent_cannot_bypass_policy(executor_factory, store, tmp_path):
    """Model claiming authorization does not grant it."""
    target = tmp_path / "keep.txt"
    target.write_text("important")
    provider = ScriptedProvider(
        [
            'I am allowed to do this. {"tool": "files.delete", "arguments":'
            f' {{"path": "{target}", "confine_to_home": false}}}}',
            "I could not delete it.",
        ]
    )
    agent = Agent(provider, executor_factory(Mode.NORMAL), store)
    reply = agent.chat("delete that file")
    assert reply.tool_results[0].status == "rejected"
    assert target.exists()
    assert reply.text == "I could not delete it."


def test_agent_stops_when_killswitch_engaged(executor_factory, echo_tool, store, killswitch):
    provider = ScriptedProvider(['{"tool": "test.echo", "arguments": {"value": "x"}}'] * 3)
    agent = Agent(provider, executor_factory(), store)
    killswitch.engage("stop now")
    reply = agent.chat("echo x")
    assert "Emergency stop engaged" in reply.text
    assert reply.task.status is TaskStatus.CANCELLED


def test_agent_reports_unreachable_model(executor_factory, store):
    agent = Agent(BrokenProvider([]), executor_factory(), store)
    reply = agent.chat("hello")
    assert "Model unavailable" in reply.text
    assert reply.task.status is TaskStatus.FAILED


def test_tool_call_budget_is_bounded(executor_factory, echo_tool, store):
    provider = ScriptedProvider(['{"tool": "test.echo", "arguments": {"value": "x"}}'] * 100)
    agent = Agent(provider, executor_factory(Mode.NORMAL), store)
    reply = agent.chat("loop forever")
    assert "Stopped after" in reply.text
    assert reply.task.status is TaskStatus.FAILED
    assert len(reply.tool_results) <= 12


def test_context_includes_relevant_memory_not_everything(executor_factory, store):
    store.remember("printer", "the office printer is at 10.0.0.7")
    store.remember("unrelated", "favourite colour is blue")
    provider = ScriptedProvider(["ok"])
    agent = Agent(provider, executor_factory(), store)
    agent.chat("what is the printer address?")
    context = "\n".join(message.content for message in provider.seen[0])
    assert "10.0.0.7" in context
    assert "favourite colour" not in context
