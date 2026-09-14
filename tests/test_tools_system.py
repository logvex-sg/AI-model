"""These tests assert against the real machine, not fixtures."""

from __future__ import annotations

import os

from jarvis.tools import build_registry
from jarvis.tools.network import dns_config, interfaces, listening_sockets, resolve_host
from jarvis.tools.processes import find_application, inspect_process, list_processes
from jarvis.tools.system import disk_usage, kernel_modules, os_info, system_metrics


def test_registry_tools_declare_full_metadata():
    registry = build_registry()
    assert registry.names()
    for tool in registry.tools.values():
        schema = tool.schema()
        assert schema["description"]
        assert schema["permission"]
        assert schema["category"]
        assert tool.timeout > 0


def test_system_metrics_are_real():
    metrics = system_metrics()
    assert metrics["cpu_count"] == os.cpu_count()
    assert 0 <= metrics["memory"]["percent"] <= 100
    assert metrics["uptime_seconds"] > 0


def test_disk_usage_lists_root_partition():
    mountpoints = {entry["mountpoint"] for entry in disk_usage()["partitions"]}
    assert "/" in mountpoints


def test_os_info_matches_uname():
    info = os_info()
    assert info["system"] == "Linux"
    assert info["kernel_release"] == os.uname().release


def test_kernel_modules_reports_availability():
    result = kernel_modules(limit=5)
    assert set(result) >= {"available"}
    if result["available"]:
        assert len(result["modules"]) <= 5


def test_process_tools_see_this_process():
    info = inspect_process(os.getpid())
    assert info["pid"] == os.getpid()
    assert list_processes(limit=5)["count"] >= 1


def test_find_application_detects_python():
    result = find_application("python3")
    assert result["installed"] is True
    assert result["path"]


def test_network_interfaces_include_loopback():
    names = {entry["name"] for entry in interfaces()["interfaces"]}
    assert "lo" in names


def test_listening_sockets_shape():
    result = listening_sockets(limit=5)
    assert "available" in result


def test_dns_and_localhost_resolution():
    assert "available" in dns_config()
    assert "127.0.0.1" in resolve_host("localhost")["addresses"]
