from __future__ import annotations

import asyncio

import pytest

from page_classifier import PageClass
from platform_adapters import ADAPTERS, adapter_for_url
from platform_contract import (
    CapabilityStatus,
    PlatformRuntimeState,
    clear_active_platform_state,
)
from platform_engine import PlatformEngine
from reload_guard import guarded_reload


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
        "https://ticketplus.com.tw/order/123",
        "https://ticketplus.com.tw/confirm/123",
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


@pytest.fixture(autouse=True)
def _isolate_dispatch_context():
    """Do not leak a test engine's task-local binding into legacy direct tests."""

    clear_active_platform_state()
    try:
        yield
    finally:
        clear_active_platform_state()


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda item: item.key)
def test_every_adapter_satisfies_complete_capability_contract(adapter):
    capabilities = adapter.capabilities()
    assert capabilities.supports_leak_watch
    assert all(
        value is CapabilityStatus.COMPLETE
        for value in (
            capabilities.onsale,
            capabilities.leak_watch,
            capabilities.inventory_scan,
            capabilities.candidate_selection,
            capabilities.protected_pages,
            capabilities.recovery,
            capabilities.watchdogs,
        )
    )


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda item: item.key)
def test_every_adapter_has_safe_protected_and_fail_closed_unknown_routes(adapter):
    safe_url, protected_url = ROUTES[adapter.key]
    assert adapter.matches_url(safe_url)
    assert adapter.is_safe_watch_page(safe_url)
    assert not adapter.is_protected_page(safe_url)
    assert adapter.is_protected_page(protected_url)
    assert not adapter.is_safe_watch_page(protected_url)

    unknown_url = f"https://{adapter.hosts[0]}/unrecognized-sensitive-route"
    assert adapter.classify_page(unknown_url) is PageClass.UNKNOWN
    assert adapter.is_protected_page(unknown_url)


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda item: item.key)
def test_adapter_rejects_hostname_spoofing(adapter):
    assert not adapter.matches_url(f"https://{adapter.hosts[0]}.attacker.invalid/event/1")
    assert adapter_for_url(f"https://{adapter.hosts[0]}.attacker.invalid/event/1") is None


def test_runtime_state_backfill_and_reset_are_owned_and_bounded():
    state = PlatformRuntimeState()
    state.leak_scheduler.history.extend(str(index) for index in range(1000))
    state.reload_guard.history.extend(
        state.reload_guard.can_reload("https://example.invalid", str(index))
        for index in range(1000)
    )
    state.current_page = "invalid"  # type: ignore[assignment]
    state.cycle_count = -10
    state.backfill()

    assert state.current_page is PageClass.UNKNOWN
    assert state.cycle_count == 0
    assert len(state.leak_scheduler.history) <= 256
    assert len(state.reload_guard.history) <= 256
    state.reset_attempt()
    assert state.recovery_count == 1


class _Target:
    def __init__(self, url: str) -> None:
        self.url = url


class _Tab:
    def __init__(self, url: str) -> None:
        self.target = _Target(url)
        self.reload_count = 0

    async def reload(self):
        self.reload_count += 1


def _leak_config(interval: float = 30.0):
    return {
        "advanced": {
            "run_mode": "leak_watch",
            "leak_refresh_interval_seconds": interval,
        }
    }


def test_non_tixcraft_reload_uses_shared_single_flight_scheduler():
    async def scenario():
        tab = _Tab("https://example.kktix.cc/events/demo/registrations/new")
        first = await guarded_reload(tab, reason="contract", config_dict=_leak_config())
        second = await guarded_reload(tab, reason="contract", config_dict=_leak_config())
        return tab, first, second

    tab, first, second = asyncio.run(scenario())
    assert first is True
    assert second is False
    assert tab.reload_count == 1


def test_engine_state_is_separated_by_tab_and_platform():
    engine = PlatformEngine()
    first_tab = _Tab("https://example.kktix.cc/events/demo/registrations/new")
    second_tab = _Tab("https://ticketplus.com.tw/activity/1")
    first = engine.before_dispatch(first_tab, first_tab.target.url, _leak_config())
    second = engine.before_dispatch(second_tab, second_tab.target.url, _leak_config())

    assert first.allowed and second.allowed
    assert first.adapter is not None and second.adapter is not None
    assert engine.state_for(first_tab, first.adapter) is not engine.state_for(second_tab, second.adapter)
    assert engine.state_count == 2


def test_external_queue_preserves_exact_per_tab_submission_owner():
    engine = PlatformEngine()
    owner_tab = _Tab("https://ticketplus.com.tw/order/event/session")
    order = engine.before_dispatch(owner_tab, owner_tab.target.url, _leak_config())
    assert order.adapter is not None and order.adapter.key == "ticketplus"
    owner_state = engine.state_for(owner_tab, order.adapter)
    owner_state.platform_data.update(
        {
            "submission_pending": True,
            "submission_deadline": 130.0,
            "submission_next_probe_at": 101.0,
        }
    )

    queue_url = "https://tenant.queue-it.net/?queue=waiting"
    queue = engine.before_dispatch(owner_tab, queue_url, _leak_config())
    assert queue.allowed is True
    assert queue.page_class is PageClass.QUEUE
    assert queue.platform_key == "ticketplus"
    assert engine.state_for(owner_tab, queue.adapter) is owner_state
    assert owner_state.platform_data["submission_pending"] is True

    unrelated_tab = _Tab(queue_url)
    unsupported = engine.before_dispatch(unrelated_tab, queue_url, _leak_config())
    assert unsupported.allowed is False
    assert unsupported.adapter is None
    assert unsupported.platform_key is None
    assert owner_state.platform_data["submission_pending"] is True


def test_external_queue_preserves_guarded_retry_until_provider_return():
    engine = PlatformEngine()
    tab = _Tab("https://ticketplus.com.tw/order/event/session")
    order = engine.before_dispatch(tab, tab.target.url, _leak_config())
    assert order.adapter is not None
    state = engine.state_for(tab, order.adapter)
    state.platform_data["failure_retry_pending"] = True

    queue = engine.before_dispatch(
        tab,
        "https://tenant.queue-it.net/?queue=waiting",
        _leak_config(),
    )
    assert queue.platform_key == "ticketplus"
    assert state.platform_data["failure_retry_pending"] is True

    returned = engine.before_dispatch(
        tab,
        "https://ticketplus.com.tw/order/event/session",
        _leak_config(),
    )
    assert returned.platform_key == "ticketplus"
    assert state.platform_data["failure_retry_pending"] is True
