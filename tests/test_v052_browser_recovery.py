from __future__ import annotations

import pytest

from attempt_lifecycle import AttemptState
from browser_session import BrowserBootstrapResult, BrowserSessionManager
from browser_session import BrowserExitState
from nodriver_tixcraft import terminal_url_failure_is_recoverable
from page_classifier import PageClass
from runtime_health import (
    BrowserFailureKind,
    RecoveryLevel,
    RuntimeHealthSupervisor,
    classify_browser_exception,
)


def test_browser_exception_taxonomy_separates_target_transport_and_context() -> None:
    assert classify_browser_exception(RuntimeError("Target closed")) is BrowserFailureKind.TARGET_CLOSED
    assert classify_browser_exception(RuntimeError("WebSocket connection closed")) is BrowserFailureKind.TRANSPORT_CLOSED
    assert classify_browser_exception(RuntimeError("Execution context was destroyed")) is BrowserFailureKind.EXECUTION_CONTEXT_LOST
    assert classify_browser_exception(TimeoutError()) is BrowserFailureKind.TIMEOUT


def test_main_loop_never_recovers_manual_or_unknown_browser_close() -> None:
    assert not terminal_url_failure_is_recoverable(
        BrowserFailureKind.TARGET_CLOSED,
        BrowserExitState.ALIVE,
    )
    assert not terminal_url_failure_is_recoverable(
        BrowserFailureKind.TRANSPORT_CLOSED,
        BrowserExitState.CLEAN_EXIT,
    )
    assert not terminal_url_failure_is_recoverable(
        BrowserFailureKind.TRANSPORT_CLOSED,
        BrowserExitState.UNKNOWN,
    )
    assert not terminal_url_failure_is_recoverable(
        BrowserFailureKind.TRANSPORT_CLOSED,
        BrowserExitState.CRASHED,
        quit_requested=True,
    )
    assert terminal_url_failure_is_recoverable(
        BrowserFailureKind.TRANSPORT_CLOSED,
        BrowserExitState.CRASHED,
    )


def test_health_supervisor_uses_bounded_state_aware_recovery() -> None:
    health = RuntimeHealthSupervisor()
    health.record_url_failure(BrowserFailureKind.TRANSIENT_URL_MISS, now=1.0)
    assert health.plan_recovery(PageClass.AREA, AttemptState.AREA_READY, now=1.0).level is RecoveryLevel.NORMAL_RETRY
    health.record_url_failure(BrowserFailureKind.TRANSIENT_URL_MISS, now=2.0)
    health.record_url_failure(BrowserFailureKind.TRANSIENT_URL_MISS, now=3.0)
    assert health.plan_recovery(PageClass.AREA, AttemptState.AREA_READY, now=3.0).level is RecoveryLevel.REACQUIRE

    health.record_url_failure(BrowserFailureKind.TRANSPORT_CLOSED, now=4.0)
    assert health.plan_recovery(PageClass.AREA, AttemptState.AREA_READY, now=4.0).level is RecoveryLevel.TRANSPORT_REBIND
    assert health.plan_recovery(PageClass.ORDER, AttemptState.SUBMIT_OUTCOME_UNKNOWN, now=4.0).level is RecoveryLevel.FAIL_CLOSED
    assert health.plan_recovery(PageClass.AREA, AttemptState.AREA_READY, manual_close=True, now=4.0).level is RecoveryLevel.STOP


def test_health_supervisor_recovery_budget_prevents_storm() -> None:
    health = RuntimeHealthSupervisor()
    for generation in range(3):
        assert health.begin_recovery(generation=generation, now=10.0 + generation * 10.0)
        health.complete_recovery(False, now=10.0 + generation * 10.0)
    assert health.begin_recovery(generation=4, now=100.0) is False


@pytest.mark.asyncio
async def test_session_manager_reacquires_matching_replacement_without_restart() -> None:
    async def evaluate_live_url(self, _script):
        return self.target.url

    old = type("Tab", (), {"target": type("Target", (), {"url": ""})()})()
    unrelated = type("Tab", (), {"target": type("Target", (), {"url": "https://example.org/"})()})()
    replacement = type(
        "Tab",
        (),
        {
            "target": type("Target", (), {"url": "https://ticketplus.com.tw/activity/a"})(),
            "evaluate": evaluate_live_url,
        },
    )()
    driver = type("Driver", (), {"tabs": [old, unrelated, replacement], "main_tab": old})()
    manager = BrowserSessionManager({"advanced": {}})
    manager.attach(driver, old)

    result = await manager.recover(
        RecoveryLevel.REACQUIRE,
        target_url="https://ticketplus.com.tw/activity/a",
        platform_key="ticketplus",
        allow_restart=False,
    )

    assert result.success is True
    assert result.tab is replacement
    assert result.restarted is False


@pytest.mark.asyncio
async def test_clean_manual_browser_exit_never_restarts() -> None:
    class _Process:
        returncode = 0

        def poll(self):
            return self.returncode

    restart_calls: list[int] = []

    async def restart():
        restart_calls.append(1)
        return object()

    manager = BrowserSessionManager({"advanced": {}})
    manager.attach(type("Driver", (), {"process": _Process(), "tabs": []})())
    manager.set_restart_factory(restart)
    result = await manager.recover(
        RecoveryLevel.SAFE_RESTART,
        target_url="https://ticketplus.com.tw/activity/a",
        platform_key="ticketplus",
        allow_restart=True,
    )

    assert result.success is False
    assert result.reason == "manual_browser_close"
    assert restart_calls == []


@pytest.mark.asyncio
async def test_crashed_browser_restarts_only_when_safe_restart_is_authorized() -> None:
    class _Process:
        returncode = 1

        def poll(self):
            return self.returncode

    class _NewDriver:
        def __init__(self, tab):
            self.tabs = [tab]
            self.main_tab = tab
            self.targets: list[str] = []

        async def get(self, target):
            self.targets.append(target)
            raise AssertionError("SAFE_RESTART must not navigate outside shared bootstrap")

    restored_tab = type(
        "Tab",
        (),
        {"target": type("Target", (), {"url": "https://ticketplus.com.tw/activity/a"})()},
    )()
    new_driver = _NewDriver(restored_tab)
    restart_contexts: list[tuple[str, str]] = []

    async def restart(*, target_url, platform_key):
        restart_contexts.append((target_url, platform_key))
        return BrowserBootstrapResult(new_driver, restored_tab)

    manager = BrowserSessionManager({"advanced": {}})
    manager.attach(type("Driver", (), {"process": _Process(), "tabs": []})())
    manager.set_restart_factory(restart)

    refused = await manager.recover(
        RecoveryLevel.SAFE_RESTART,
        target_url="https://ticketplus.com.tw/activity/a",
        platform_key="ticketplus",
        allow_restart=False,
    )
    assert refused.success is False
    assert new_driver.targets == []

    recovered = await manager.recover(
        RecoveryLevel.SAFE_RESTART,
        target_url="https://ticketplus.com.tw/activity/a",
        platform_key="ticketplus",
        allow_restart=True,
    )
    assert recovered.success is True
    assert recovered.restarted is True
    assert recovered.tab is restored_tab
    assert restart_contexts == [("https://ticketplus.com.tw/activity/a", "ticketplus")]
    assert new_driver.targets == []
