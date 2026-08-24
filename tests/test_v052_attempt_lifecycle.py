from __future__ import annotations

import ast
from pathlib import Path

import pytest

from attempt_lifecycle import AttemptRegistry, AttemptState
from platform_adapters import ADAPTERS
from platform_contract import clear_active_platform_state
from platform_engine import PlatformEngine


@pytest.fixture(autouse=True)
def _isolate_platform_state_binding():
    """Do not leak a synthetic dispatch binding into the next test."""

    clear_active_platform_state()
    try:
        yield
    finally:
        clear_active_platform_state()


ROUTES = {
    "tixcraft": (
        "https://tixcraft.com/ticket/area/25_event/1",
        "https://tixcraft.com/ticket/checkout/25_event",
    ),
    "kktix": (
        "https://example.kktix.cc/events/demo/registrations/new",
        "https://example.kktix.cc/orders/123",
    ),
    "ticketplus": (
        "https://ticketplus.com.tw/order/123/session-a",
        "https://ticketplus.com.tw/confirm/123/session-a",
    ),
    "ibon": (
        "https://ticket.ibon.com.tw/ActivityInfo/Details/1",
        "https://ticket.ibon.com.tw/Order/Checkout/1",
    ),
    "kham": (
        "https://kham.com.tw/application/UTK02/UTK0201_.aspx?PRODUCT_ID=1",
        "https://kham.com.tw/application/UTK02/UTK0206_.aspx?ORDER_ID=1",
    ),
    "famiticket": (
        "https://www.famiticket.com.tw/Activity/Info/1",
        "https://www.famiticket.com.tw/Order/Checkout/1",
    ),
    "funone": (
        "https://tickets.funone.io/events/1",
        "https://tickets.funone.io/orders/1",
    ),
    "fansigo": (
        "https://go.fansi.me/events/1",
        "https://go.fansi.me/orders/1",
    ),
    "cityline": (
        "https://www.cityline.com/Events.html?id=1",
        "https://www.cityline.com/checkout/1",
    ),
    "hkticketing": (
        "https://premier.hkticketing.com/events/1",
        "https://premier.hkticketing.com/payment/1",
    ),
}


class _Tab:
    pass


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda item: item.key)
def test_all_platforms_complete_once_then_safe_route_rearms_new_attempt(adapter) -> None:
    engine = PlatformEngine()
    tab = _Tab()
    safe_url, protected_url = ROUTES[adapter.key]

    first = engine.before_dispatch(tab, safe_url, {})
    first_attempt = engine.current_attempt(tab, adapter)
    assert first_attempt is not None
    assert first.new_attempt_started is True
    assert first.automation_allowed is True

    engine.before_dispatch(tab, protected_url, {})
    completed = engine.mark_attempt_completed(tab, adapter, reason="fixture_success")
    assert completed is not None
    assert completed.state is AttemptState.COMPLETED

    same_attempt = engine.before_dispatch(tab, protected_url + "?render=2#same", {})
    assert same_attempt.attempt_id == first_attempt.attempt_id
    assert same_attempt.attempt_generation == first_attempt.generation
    assert same_attempt.automation_allowed is False

    second = engine.before_dispatch(tab, safe_url, {})
    second_attempt = engine.current_attempt(tab, adapter)
    assert second_attempt is not None
    assert second_attempt.attempt_id != first_attempt.attempt_id
    assert second_attempt.generation == first_attempt.generation + 1
    assert second.new_attempt_started is True
    assert second.automation_allowed is True


def test_submit_ownership_is_attempt_scoped_and_never_duplicates() -> None:
    engine = PlatformEngine()
    tab = _Tab()
    adapter = next(item for item in ADAPTERS if item.key == "ticketplus")
    safe_url, protected_url = ROUTES[adapter.key]

    engine.before_dispatch(tab, safe_url, {})
    first_token = engine.claim_submit(tab, adapter, owner="ticketplus_next")
    assert first_token
    assert engine.claim_submit(tab, adapter, owner="ticketplus_next") is None

    engine.before_dispatch(tab, protected_url, {})
    engine.mark_attempt_completed(tab, adapter, reason="confirmed")
    assert engine.claim_submit(tab, adapter, owner="ticketplus_next") is None

    engine.before_dispatch(tab, safe_url, {})
    second_token = engine.claim_submit(tab, adapter, owner="ticketplus_next")
    assert second_token
    assert second_token != first_token


@pytest.mark.parametrize(
    ("safe_url", "protected_url"),
    (
        (
            "https://www.indievox.com/activity/game/26_iv0404354",
            "https://www.indievox.com/ticket/checkout/26_iv0404354",
        ),
        (
            "https://www.ticketmaster.sg/activity/detail/demo",
            "https://www.ticketmaster.sg/ticket/checkout/demo",
        ),
    ),
    ids=("indievox", "ticketmaster"),
)
def test_tixcraft_family_hosts_rearm_attempt_independently(
    safe_url: str,
    protected_url: str,
) -> None:
    engine = PlatformEngine()
    tab = _Tab()
    adapter = next(item for item in ADAPTERS if item.key == "tixcraft")

    first = engine.before_dispatch(tab, safe_url, {})
    engine.before_dispatch(tab, protected_url, {})
    engine.mark_attempt_completed(tab, adapter, reason="family_fixture_success")
    second = engine.before_dispatch(tab, safe_url, {})

    assert first.attempt_id
    assert second.attempt_id != first.attempt_id
    assert second.attempt_generation == first.attempt_generation + 1
    assert second.automation_allowed is True


def test_submit_outcome_unknown_fails_closed_until_safe_route() -> None:
    engine = PlatformEngine()
    tab = _Tab()
    adapter = next(item for item in ADAPTERS if item.key == "ticketplus")
    safe_url, protected_url = ROUTES[adapter.key]

    engine.before_dispatch(tab, safe_url, {})
    assert engine.claim_submit(tab, adapter, owner="ticketplus_next")
    engine.before_dispatch(tab, protected_url, {})
    unknown = engine.mark_submit_outcome_unknown(tab, adapter, reason="cdp_disconnect")

    assert unknown is not None
    assert unknown.state is AttemptState.SUBMIT_OUTCOME_UNKNOWN
    assert engine.before_dispatch(tab, protected_url, {}).automation_allowed is False
    assert engine.claim_submit(tab, adapter, owner="ticketplus_next") is None

    recovered = engine.before_dispatch(tab, safe_url, {})
    assert recovered.new_attempt_started is True
    assert recovered.automation_allowed is True


def test_attempt_registry_rejects_stale_object_identity_entry() -> None:
    registry = AttemptRegistry()
    tab = object()
    stale_tab = object()
    created = registry.create_for_area(tab, "A", "event")

    registry._fallback_attempts[id(tab)] = (
        stale_tab,
        {"default": created},
    )

    assert registry.get_current(tab) is None


def test_runtime_has_no_process_local_ticketplus_completion_latch() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "src" / "nodriver_tixcraft.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assigned_names = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        )
        if isinstance(target, ast.Name)
    }

    assert "ticketplus_purchase_done" not in assigned_names


def test_attempt_generation_stress_remains_monotonic_and_rearmed() -> None:
    engine = PlatformEngine()
    tab = _Tab()
    adapter = next(item for item in ADAPTERS if item.key == "ticketplus")
    safe_url, protected_url = ROUTES[adapter.key]

    for expected_generation in range(1, 1001):
        decision = engine.before_dispatch(tab, safe_url, {})
        attempt = engine.current_attempt(tab, adapter)
        assert attempt is not None
        assert attempt.generation == expected_generation
        assert decision.automation_allowed is True
        assert engine.claim_submit(tab, adapter, owner="stress")
        engine.before_dispatch(tab, protected_url, {})
        engine.mark_attempt_completed(tab, adapter, reason="stress_success")

    final = engine.before_dispatch(tab, safe_url, {})
    assert final.attempt_generation == 1001
    assert final.automation_allowed is True


def test_tab_churn_does_not_retain_attempt_or_refresh_owners() -> None:
    engine = PlatformEngine()
    adapter = next(item for item in ADAPTERS if item.key == "ticketplus")
    safe_url, _ = ROUTES[adapter.key]

    for _ in range(1000):
        tab = _Tab()
        engine.before_dispatch(tab, safe_url, {})
        assert engine.current_attempt(tab, adapter) is not None
        engine.clear_tab(tab)

    assert engine.state_count == 0
    assert engine.refresh_coordinator_count == 0
