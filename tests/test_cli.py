from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from jarvis.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("JARVIS_CONFIG", str(tmp_path / "nonexistent.toml"))
    monkeypatch.setenv("JARVIS_ENDPOINT", "http://127.0.0.1:59999")


def test_status_reports_unreachable_backend_without_pretending():
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["model"]["reachable"] is False
    assert payload["model"]["error"]
    assert payload["killswitch"]["engaged"] is False


def test_tools_command_lists_registered_tools():
    result = runner.invoke(app, ["tools"])
    assert result.exit_code == 0
    assert "system.metrics" in result.stdout


def test_run_executes_a_safe_tool():
    result = runner.invoke(app, ["run", "system.os_info"])
    assert result.exit_code == 0
    assert '"status": "ok"' in result.stdout


def test_run_rejects_destructive_tool_in_normal_mode(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("x")
    arguments = json.dumps({"path": str(target), "confine_to_home": False})
    result = runner.invoke(app, ["run", "files.delete", "--args", arguments])
    assert result.exit_code == 1
    assert "rejected" in result.stdout
    assert target.exists()


def test_emergency_stop_blocks_subsequent_tool_calls():
    stop = runner.invoke(app, ["emergency-stop", "--reason", "cli test"])
    assert stop.exit_code == 0
    blocked = runner.invoke(app, ["run", "system.os_info"])
    assert blocked.exit_code == 1
    assert "stopped" in blocked.stdout
    assert runner.invoke(app, ["resume"]).exit_code == 0
    assert runner.invoke(app, ["run", "system.os_info"]).exit_code == 0


def test_chat_fails_cleanly_without_a_model_backend():
    result = runner.invoke(app, ["chat", "hello"])
    assert result.exit_code == 1
    assert "unreachable" in result.stdout


def test_memory_roundtrip_and_secret_rejection():
    assert runner.invoke(app, ["memory", "remember", "editor", "neovim"]).exit_code == 0
    search = runner.invoke(app, ["memory", "search", "neovim"])
    assert "editor" in search.stdout
    rejected = runner.invoke(app, ["memory", "remember", "creds", "password: hunter2"])
    assert rejected.exit_code == 1
    assert runner.invoke(app, ["memory", "forget", "editor"]).exit_code == 0


def test_audit_log_records_cli_tool_calls():
    runner.invoke(app, ["run", "system.os_info"])
    result = runner.invoke(app, ["audit", "--limit", "5"])
    assert "system.os_info" in result.stdout


def test_doctor_exit_code_reflects_real_health():
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1  # no model backend on this machine
    assert "model backend" in result.stdout
