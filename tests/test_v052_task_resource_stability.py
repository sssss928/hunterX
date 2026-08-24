from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

import pytest

import runtime_diagnostics
import runtime_health
from runtime_diagnostics import collect_runtime_diagnostics
from runtime_health import RuntimeHealthSupervisor
from task_registry import TaskRegistry


@pytest.mark.asyncio
async def test_task_registry_harvests_completion_failure_and_cancellation() -> None:
    registry = TaskRegistry(history_capacity=8)

    async def complete():
        return 7

    async def fail():
        raise RuntimeError("synthetic terminal exception")

    async def wait_forever():
        import asyncio

        await asyncio.Event().wait()

    completed = registry.create(complete(), owner="test", purpose="complete")
    failed = registry.create(fail(), owner="test", purpose="fail")
    cancelled = registry.create(wait_forever(), owner="cancel", purpose="wait")
    assert await completed == 7
    with pytest.raises(RuntimeError, match="synthetic terminal"):
        await failed
    assert await registry.cancel_owner("cancel") == 1
    assert cancelled.cancelled()
    assert registry.active_count == 0
    assert {record.status for record in registry.history} == {
        "completed",
        "failed",
        "cancelled",
    }


@pytest.mark.asyncio
async def test_task_registry_1000_task_churn_is_bounded() -> None:
    registry = TaskRegistry(history_capacity=32)

    async def noop():
        return None

    for _ in range(1000):
        await registry.create(noop(), owner="stress", purpose="noop")
    assert registry.active_count == 0
    assert len(registry.history) == 32
    assert all(record.status == "completed" for record in registry.history)


def test_all_production_create_task_calls_are_owned_by_task_registry() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    violations: list[str] = []
    for path in source_root.rglob("*.py"):
        if path.name == "task_registry.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == "asyncio"
                and function.attr == "create_task"
            ):
                violations.append(f"{path.name}:{node.lineno}")
    assert violations == []


def test_low_frequency_resource_snapshot_is_bounded(monkeypatch, tmp_path) -> None:
    process = type("Process", (), {"pid": 12345})()
    driver = type("Driver", (), {"tabs": [object(), object()], "process": process})()
    manager = type(
        "Manager",
        (),
        {"driver": driver, "browser_pid": lambda self: process.pid},
    )()
    monkeypatch.setattr(
        "runtime_diagnostics._process_metrics",
        lambda pid: ((pid or 0) * 10, 0.0),
    )
    monkeypatch.setattr(
        "runtime_diagnostics.runtime_health._instance_log_path",
        lambda: str(tmp_path / "missing.log"),
    )
    health = RuntimeHealthSupervisor()
    health.record_url_failure(now=1.0)
    snapshot = collect_runtime_diagnostics(manager, health)
    assert snapshot.tab_count == 2
    assert snapshot.browser_rss_bytes == 123450
    assert snapshot.cdp_timeout_count == 0
    assert snapshot.runtime_log_bytes is None


def test_windows_process_api_binding_is_cached_across_resource_samples() -> None:
    first = runtime_diagnostics._windows_process_api()
    before = runtime_diagnostics._windows_process_api.cache_info()

    for _ in range(1000):
        assert runtime_diagnostics._windows_process_api() is first

    after = runtime_diagnostics._windows_process_api.cache_info()
    assert after.misses == before.misses
    assert after.hits >= before.hits + 1000
    if sys.platform == "win32":
        assert first is not None
        assert runtime_diagnostics._windows_rss_bytes(os.getpid())
        assert os.getpid() in runtime_diagnostics._windows_process_tree_pids(
            os.getpid()
        )
    else:
        assert first is None


def test_debug_trace_is_bounded_exportable_and_redacted(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        runtime_health,
        "_instance_log_path",
        lambda: str(tmp_path / "runtime.log"),
    )
    with runtime_health._CRITICAL_TRACE_LOCK:
        runtime_health._CRITICAL_TRACE.clear()
    for index in range(1005):
        runtime_health.runtime_log(
            "synthetic_trace",
            {},
            attempt_id=f"attempt-{index}",
            current_url=f"https://ticketplus.com.tw/activity/demo?token=secret-{index}",
            password="never-export-this",
            auth_code="123456",
        )
    output = runtime_health.export_debug_trace(str(tmp_path / "trace.json"))
    payload = json.loads(Path(output).read_text(encoding="utf-8"))
    rendered = json.dumps(payload, ensure_ascii=False)
    assert payload["capacity"] == 1000
    assert len(payload["events"]) == 1000
    assert payload["events"][0]["attempt_id"] == "attempt-5"
    assert "never-export-this" not in rendered
    assert "123456" not in rendered
    assert "secret-" not in rendered
    assert payload["events"][-1]["password"] == "[REDACTED]"
