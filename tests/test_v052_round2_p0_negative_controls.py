from __future__ import annotations

import inspect

import pytest

import nodriver_tixcraft
from attempt_lifecycle import AttemptState
from browser_session import BrowserSessionManager
from page_classifier import PageClass
from runtime_health import (
    BrowserFailureKind,
    ExpectedProgressKind,
    ExpectedProgressOutcome,
    RecoveryLevel,
    RuntimeHealthSupervisor,
)


class _Target:
    def __init__(self, url: str, target_id: str = "") -> None:
        self.url = url
        self.target_id = target_id


class _Tab:
    def __init__(self, url: str, target_id: str = "") -> None:
        self.target = _Target(url, target_id)


def _driver_with(*tabs: _Tab):
    main_tab = tabs[0] if tabs else None
    return type("Driver", (), {"tabs": list(tabs), "main_tab": main_tab})()


def test_expected_refresh_stall_requires_an_explicit_owner_expectation() -> None:
    """P0-R2-01: idle URL health is inert; an armed owner can detect a stall."""

    health = RuntimeHealthSupervisor()
    health.record_refresh_attempt(now=1.0)
    health.record_url_success(now=60.0)

    idle_plan = health.plan_recovery(
        PageClass.AREA,
        AttemptState.AREA_READY,
        now=60.0,
    )
    assert health.last_failure_kind is BrowserFailureKind.NONE
    assert idle_plan.reason != "expected_action_stalled"

    expectation = health.arm_expected_progress(
        tab_identity="tab-a",
        platform_key="ticketplus",
        attempt_id="attempt-a",
        attempt_generation=1,
        action_owner="refresh_coordinator",
        action_token="refresh-1",
        kind=ExpectedProgressKind.RELOAD,
        deadline=120.0,
        acceptable_routes=("https://ticketplus.com.tw/activity/next",),
        now=60.0,
    )
    assert expectation is not None
    decisions = health.observe_expected_progress(
        tab_identity="tab-a",
        attempt_id="attempt-a",
        attempt_generation=1,
        current_route="https://ticketplus.com.tw/activity/current",
        page_state=AttemptState.AREA_READY.value,
        now=120.0,
    )

    assert [item.outcome for item in decisions] == [
        ExpectedProgressOutcome.STALLED_ACTION
    ]
    plan = health.plan_recovery(
        PageClass.AREA,
        AttemptState.AREA_READY,
        now=120.0,
    )
    assert health.last_failure_kind is BrowserFailureKind.RENDERER_STALLED
    assert plan.reason == "expected_action_stalled"


def test_round1_has_no_authoritative_production_iteration_entrypoint() -> None:
    """Negative control for P0-R2-02: soak cannot call the production iteration."""

    assert hasattr(nodriver_tixcraft, "run_runtime_iteration")
    outer_loop = inspect.getsource(nodriver_tixcraft._run_main)
    assert "await run_runtime_iteration(" in outer_loop


def test_round1_has_no_explicit_owned_tab_contract_diagnostic() -> None:
    """Negative control for P0-R2-03: extra same-browser tabs are silent."""

    owned = _Tab("https://ticketplus.com.tw/activity/A", "owned")
    extras = [
        _Tab("https://ticketplus.com.tw/activity/A", "extra-1"),
        _Tab("https://ticketplus.com.tw/activity/A", "extra-2"),
    ]
    manager = BrowserSessionManager({"advanced": {}})
    manager.attach(_driver_with(owned, *extras), owned)

    diagnostic = manager.owned_tab_diagnostic("ticketplus")

    assert diagnostic.supports_same_browser_multi_tab is False
    assert diagnostic.extra_tab_count == 2
    assert diagnostic.automated_tab is owned


def test_round1_reacquires_only_wrong_event_same_platform_tab() -> None:
    """Confirmed negative control for P0-R2-04."""

    wrong = _Tab("https://ticketplus.com.tw/activity/OTHER", "wrong")
    manager = BrowserSessionManager({"advanced": {}})
    manager.attach(_driver_with(wrong))

    result = manager.reacquire_tab(
        "https://ticketplus.com.tw/activity/TARGET",
        "ticketplus",
    )

    assert result is None
    assert manager.active_tab is None


def test_round1_restart_factory_is_not_the_shared_full_bootstrap() -> None:
    """Negative control for P0-R2-05: restart only starts a browser process."""

    outer_loop = inspect.getsource(nodriver_tixcraft._run_main)

    assert "bootstrap_owned_browser(" in outer_loop
    assert "set_restart_factory" in outer_loop
    assert "return await bootstrap_owned_browser(" in outer_loop


@pytest.mark.asyncio
async def test_round1_transport_rebind_accepts_dead_cached_target() -> None:
    """Negative control for P1-R2-06: cached URL is not active transport proof."""

    class _DeadTab(_Tab):
        async def evaluate(self, _script: str):
            raise RuntimeError("WebSocket connection closed")

    dead = _DeadTab("https://ticketplus.com.tw/activity/TARGET", "dead")

    class _Driver:
        tabs = [dead]
        main_tab = dead

        async def update_targets(self) -> None:
            return None

    manager = BrowserSessionManager({"advanced": {}})
    manager.attach(_Driver(), dead)

    result = await manager.recover(
        RecoveryLevel.TRANSPORT_REBIND,
        target_url="https://ticketplus.com.tw/activity/TARGET",
        platform_key="ticketplus",
        allow_restart=False,
    )

    assert result.success is False
    assert result.reason == "transport_probe_failed"
