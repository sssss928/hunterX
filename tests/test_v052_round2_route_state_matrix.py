from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType, SimpleNamespace
from typing import Callable

import pytest

from attempt_lifecycle import AttemptState
from page_classifier import PageClass
from platform_adapters import adapter_for_key, adapter_for_url
from platform_engine import PlatformEngine
from platforms import (
    cityline,
    famiticket,
    fansigo,
    funone,
    hkticketing,
    ibon,
    kham,
    kktix,
    ticketplus,
    tixcraft,
)


SAFE_CLASSES = frozenset(
    {PageClass.ACTIVITY, PageClass.DATE, PageClass.AREA}
)
PROTECTED_CLASSES = frozenset(
    {
        PageClass.TICKET,
        PageClass.ORDER,
        PageClass.CHECKOUT,
        PageClass.PAYMENT,
        PageClass.QUEUE,
        PageClass.UNKNOWN,
    }
)
LEAK_CONFIG = {
    "advanced": {
        "run_mode": "leak_watch",
        "auto_reload_page_interval": 3.0,
        "leak_refresh_interval_seconds": 3.0,
    }
}


PLATFORM_HOSTS = {
    "tixcraft": "https://tixcraft.com",
    "kktix": "https://example.kktix.cc",
    "ticketplus": "https://ticketplus.com.tw",
    "ibon": "https://ticket.ibon.com.tw",
    "kham": "https://kham.com.tw",
    "famiticket": "https://www.famiticket.com.tw",
    "funone": "https://tickets.funone.io",
    "fansigo": "https://go.fansi.me",
    "cityline": "https://www.cityline.com",
    "hkticketing": "https://premier.hkticketing.com",
}


EXPLICIT_ROUTE_CASES = (
    ("tixcraft", "https://tixcraft.com/activity/detail/E", PageClass.ACTIVITY),
    ("tixcraft", "https://tixcraft.com/activity/game/E", PageClass.DATE),
    ("tixcraft", "https://tixcraft.com/ticket/area/E/G", PageClass.AREA),
    ("tixcraft", "https://tixcraft.com/ticket/ticket/E/G", PageClass.TICKET),
    ("tixcraft", "https://tixcraft.com/ticket/verify/E/G", PageClass.TICKET),
    ("tixcraft", "https://tixcraft.com/ticket/order/E", PageClass.ORDER),
    ("tixcraft", "https://tixcraft.com/ticket/checkout/E", PageClass.CHECKOUT),
    ("tixcraft", "https://tixcraft.com/payment/E", PageClass.PAYMENT),
    ("kktix", "https://example.kktix.cc/events/demo", PageClass.DATE),
    (
        "kktix",
        "https://example.kktix.cc/events/demo/registrations/new",
        PageClass.AREA,
    ),
    ("kktix", "https://example.kktix.cc/orders/1", PageClass.ORDER),
    ("kktix", "https://example.kktix.cc/checkout/1", PageClass.CHECKOUT),
    ("kktix", "https://example.kktix.cc/payment/1", PageClass.PAYMENT),
    ("kktix", "https://example.kktix.cc/#/booking", PageClass.HOME),
    (
        "ticketplus",
        "https://ticketplus.com.tw/activity/demo",
        PageClass.DATE,
    ),
    (
        "ticketplus",
        "https://ticketplus.com.tw/order/demo/session",
        PageClass.AREA,
    ),
    (
        "ticketplus",
        "https://ticketplus.com.tw/ticket/demo/session",
        PageClass.TICKET,
    ),
    (
        "ticketplus",
        "https://ticketplus.com.tw/confirm/demo/session",
        PageClass.CHECKOUT,
    ),
    ("ticketplus", "https://ticketplus.com.tw/payment/1", PageClass.PAYMENT),
    (
        "ibon",
        "https://ticket.ibon.com.tw/ActivityInfo/Details/1",
        PageClass.DATE,
    ),
    ("ibon", "https://ticket.ibon.com.tw/Performance/1", PageClass.AREA),
    ("ibon", "https://ticket.ibon.com.tw/Ticket/1", PageClass.AREA),
    ("ibon", "https://ticket.ibon.com.tw/Order/1", PageClass.ORDER),
    ("ibon", "https://ticket.ibon.com.tw/Checkout/1", PageClass.CHECKOUT),
    ("ibon", "https://ticket.ibon.com.tw/Payment/1", PageClass.PAYMENT),
    ("kham", "https://kham.com.tw/event/demo", PageClass.ACTIVITY),
    ("kham", "https://kham.com.tw/product/demo", PageClass.DATE),
    ("kham", "https://kham.com.tw/performance/demo", PageClass.AREA),
    ("kham", "https://kham.com.tw/ticketseat/demo", PageClass.TICKET),
    (
        "kham",
        "https://kham.com.tw/application/UTK02/UTK0206_.aspx?ORDER_ID=1",
        PageClass.ORDER,
    ),
    ("kham", "https://kham.com.tw/checkout/1", PageClass.CHECKOUT),
    ("kham", "https://kham.com.tw/payment/1", PageClass.PAYMENT),
    (
        "famiticket",
        "https://www.famiticket.com.tw/Activity/Info/1",
        PageClass.DATE,
    ),
    (
        "famiticket",
        "https://www.famiticket.com.tw/Home/Activity/1",
        PageClass.AREA,
    ),
    ("famiticket", "https://www.famiticket.com.tw/Ticket/1", PageClass.AREA),
    ("famiticket", "https://www.famiticket.com.tw/Order/1", PageClass.ORDER),
    (
        "famiticket",
        "https://www.famiticket.com.tw/Checkout/1",
        PageClass.CHECKOUT,
    ),
    (
        "famiticket",
        "https://www.famiticket.com.tw/Payment/1",
        PageClass.PAYMENT,
    ),
    ("funone", "https://tickets.funone.io/events/1", PageClass.DATE),
    ("funone", "https://tickets.funone.io/sales/1", PageClass.AREA),
    ("funone", "https://tickets.funone.io/ticket/1", PageClass.AREA),
    ("funone", "https://tickets.funone.io/orders/1", PageClass.ORDER),
    ("funone", "https://tickets.funone.io/checkout/1", PageClass.CHECKOUT),
    ("funone", "https://tickets.funone.io/payment/1", PageClass.PAYMENT),
    ("fansigo", "https://go.fansi.me/events/1", PageClass.DATE),
    ("fansigo", "https://go.fansi.me/ticket/1", PageClass.AREA),
    ("fansigo", "https://go.fansi.me/orders/1", PageClass.ORDER),
    ("fansigo", "https://go.fansi.me/checkout/1", PageClass.CHECKOUT),
    ("fansigo", "https://go.fansi.me/payment/1", PageClass.PAYMENT),
    ("cityline", "https://www.cityline.com/Events.html?id=1", PageClass.DATE),
    (
        "cityline",
        "https://www.cityline.com/utsvInternet/demo/performance",
        PageClass.AREA,
    ),
    (
        "cityline",
        "https://www.cityline.com/secure/selection",
        PageClass.TICKET,
    ),
    ("cityline", "https://www.cityline.com/order/1", PageClass.ORDER),
    ("cityline", "https://www.cityline.com/checkout/1", PageClass.CHECKOUT),
    ("cityline", "https://www.cityline.com/payment/1", PageClass.PAYMENT),
    (
        "hkticketing",
        "https://premier.hkticketing.com/events/1",
        PageClass.DATE,
    ),
    (
        "hkticketing",
        "https://premier.hkticketing.com/performance/1",
        PageClass.AREA,
    ),
    (
        "hkticketing",
        "https://premier.hkticketing.com/secure/selection",
        PageClass.AREA,
    ),
    (
        "hkticketing",
        "https://premier.hkticketing.com/order/1",
        PageClass.ORDER,
    ),
    (
        "hkticketing",
        "https://premier.hkticketing.com/checkout/1",
        PageClass.CHECKOUT,
    ),
    (
        "hkticketing",
        "https://premier.hkticketing.com/payment/1",
        PageClass.PAYMENT,
    ),
)


ROUTE_CASES = EXPLICIT_ROUTE_CASES + tuple(
    route_case
    for key, host in PLATFORM_HOSTS.items()
    for route_case in (
        (key, f"{host}/queue", PageClass.QUEUE),
        (key, f"{host}/round2-unclassified", PageClass.UNKNOWN),
    )
)


class _Tab:
    def __init__(self, identity: str = "round2-matrix") -> None:
        self.target = SimpleNamespace(target_id=identity, url="")


@pytest.mark.parametrize(
    ("platform_key", "url", "expected"),
    ROUTE_CASES,
    ids=[
        f"{key}-{expected.value}-{index}"
        for index, (key, _url, expected) in enumerate(ROUTE_CASES)
    ],
)
def test_real_adapter_route_classification_matrix(
    platform_key: str,
    url: str,
    expected: PageClass,
) -> None:
    adapter = adapter_for_key(platform_key)
    assert adapter is not None
    assert adapter_for_url(url) is adapter
    assert adapter.classify_page(url) is expected
    assert adapter.is_safe_watch_page(url) is (expected in SAFE_CLASSES)
    assert adapter.is_protected_page(url) is (expected in PROTECTED_CLASSES)


@pytest.mark.parametrize(
    ("safe_url", "expected"),
    (
        ("https://tixcraft.com/activity/detail/E", PageClass.ACTIVITY),
        ("https://tixcraft.com/activity/game/E", PageClass.DATE),
        ("https://tixcraft.com/ticket/area/E/G", PageClass.AREA),
    ),
    ids=("activity", "date", "area"),
)
def test_platform_engine_rearms_only_safe_page_classes(
    safe_url: str,
    expected: PageClass,
) -> None:
    engine = PlatformEngine()
    tab = _Tab(expected.value)
    adapter = adapter_for_key("tixcraft")
    assert adapter is not None
    protected_url = "https://tixcraft.com/ticket/order/E"

    first = engine.before_dispatch(tab, protected_url, LEAK_CONFIG)
    assert first.page_class is PageClass.ORDER
    coordinator = engine.refresh_coordinator_for(tab)
    assert coordinator.purchase_guard is True
    completed = engine.mark_attempt_completed(tab, adapter, reason="matrix_success")
    assert completed is not None
    assert completed.state is AttemptState.COMPLETED

    second = engine.before_dispatch(tab, safe_url, LEAK_CONFIG)
    assert second.page_class is expected
    assert second.new_attempt_started is True
    assert second.attempt_generation == first.attempt_generation + 1
    assert second.automation_allowed is True
    assert coordinator.purchase_guard is False
    assert engine.state_for(tab, adapter).reload_guard.can_reload(
        safe_url,
        page_class=expected,
    ).allowed is True


@pytest.mark.parametrize(
    ("url", "expected"),
    (
        ("https://tixcraft.com/ticket/ticket/E/G", PageClass.TICKET),
        ("https://tixcraft.com/ticket/order/E", PageClass.ORDER),
        ("https://tixcraft.com/ticket/checkout/E", PageClass.CHECKOUT),
        ("https://tixcraft.com/payment/E", PageClass.PAYMENT),
        ("https://tixcraft.com/queue", PageClass.QUEUE),
        ("https://tixcraft.com/round2-unclassified", PageClass.UNKNOWN),
    ),
    ids=("ticket", "order", "checkout", "payment", "queue", "unknown"),
)
def test_completed_attempt_never_rearms_on_protected_page_classes(
    url: str,
    expected: PageClass,
) -> None:
    engine = PlatformEngine()
    tab = _Tab(expected.value)
    adapter = adapter_for_key("tixcraft")
    assert adapter is not None
    safe_url = "https://tixcraft.com/ticket/area/E/G"

    first = engine.before_dispatch(tab, safe_url, LEAK_CONFIG)
    completed = engine.mark_attempt_completed(tab, adapter, reason="matrix_success")
    assert completed is not None
    decision = engine.before_dispatch(tab, url, LEAK_CONFIG)

    assert decision.page_class is expected
    assert decision.reason == "protected_no_reload"
    assert decision.new_attempt_started is False
    assert decision.attempt_id == first.attempt_id
    assert decision.attempt_generation == first.attempt_generation
    assert decision.automation_allowed is False
    assert engine.state_for(tab, adapter).reload_guard.can_reload(
        url,
        page_class=expected,
    ).allowed is False


@pytest.mark.parametrize(
    ("platform_key", "safe_url", "protected_url"),
    (
        (
            "ibon",
            "https://ticket.ibon.com.tw/Ticket/1",
            "https://ticket.ibon.com.tw/Order/1",
        ),
        (
            "famiticket",
            "https://www.famiticket.com.tw/Ticket/1",
            "https://www.famiticket.com.tw/Order/1",
        ),
        (
            "funone",
            "https://tickets.funone.io/ticket/1",
            "https://tickets.funone.io/orders/1",
        ),
        (
            "fansigo",
            "https://go.fansi.me/ticket/1",
            "https://go.fansi.me/orders/1",
        ),
        (
            "hkticketing",
            "https://premier.hkticketing.com/secure/selection",
            "https://premier.hkticketing.com/order/1",
        ),
    ),
)
def test_ticket_named_routes_rearm_only_when_adapter_classifies_them_as_area(
    platform_key: str,
    safe_url: str,
    protected_url: str,
) -> None:
    engine = PlatformEngine()
    tab = _Tab(platform_key)
    adapter = adapter_for_key(platform_key)
    assert adapter is not None
    assert adapter.classify_page(safe_url) is PageClass.AREA

    first = engine.before_dispatch(tab, protected_url, LEAK_CONFIG)
    assert engine.mark_attempt_completed(
        tab,
        adapter,
        reason="ticket_named_area_fixture",
    ) is not None
    second = engine.before_dispatch(tab, safe_url, LEAK_CONFIG)

    assert second.new_attempt_started is True
    assert second.attempt_generation == first.attempt_generation + 1
    assert second.automation_allowed is True


def test_kktix_fragment_only_booking_is_not_safe_rearm_proof() -> None:
    engine = PlatformEngine()
    tab = _Tab("kktix-fragment")
    adapter = adapter_for_key("kktix")
    assert adapter is not None
    safe_url = "https://example.kktix.cc/events/demo/registrations/new"

    first = engine.before_dispatch(tab, safe_url, LEAK_CONFIG)
    assert engine.mark_attempt_completed(
        tab,
        adapter,
        reason="kktix_fragment_fixture",
    ) is not None
    fragment = engine.before_dispatch(
        tab,
        "https://example.kktix.cc/#/booking",
        LEAK_CONFIG,
    )

    assert fragment.page_class is PageClass.HOME
    assert fragment.new_attempt_started is False
    assert fragment.attempt_id == first.attempt_id
    assert fragment.attempt_generation == first.attempt_generation
    assert fragment.automation_allowed is False


@dataclass(frozen=True)
class StateResetCase:
    key: str
    module: ModuleType
    initializer: Callable[[], None]
    safe_url: str
    protected_url: str
    sticky: dict[str, object]
    expected_defaults: dict[str, object]


STATE_RESET_CASES = (
    StateResetCase(
        "tixcraft",
        tixcraft,
        tixcraft._ensure_tixcraft_state_defaults,
        "https://tixcraft.com/ticket/area/E/G",
        "https://tixcraft.com/ticket/checkout/E",
        {
            "purchase_attempt": object(),
            "submit_in_flight": object(),
            "printed_completed": True,
        },
        {
            "purchase_attempt": None,
            "submit_in_flight": None,
            "printed_completed": False,
        },
    ),
    StateResetCase(
        "kktix",
        kktix,
        kktix._ensure_kktix_state_defaults,
        "https://example.kktix.cc/events/demo/registrations/new",
        "https://example.kktix.cc/orders/1",
        {
            "got_ticket_detected": True,
            "success_actions_done": True,
            "printed_completed": True,
        },
        {
            "got_ticket_detected": False,
            "success_actions_done": False,
            "printed_completed": False,
        },
    ),
    StateResetCase(
        "ticketplus",
        ticketplus,
        ticketplus._ensure_ticketplus_state_defaults,
        "https://ticketplus.com.tw/order/demo/session",
        "https://ticketplus.com.tw/confirm/demo/session",
        {
            "purchase_completed": True,
            "submission_pending": True,
            "is_ticket_assigned": True,
        },
        {
            "purchase_completed": False,
            "submission_pending": False,
            "is_ticket_assigned": False,
        },
    ),
    StateResetCase(
        "ibon",
        ibon,
        ibon._ensure_state,
        "https://ticket.ibon.com.tw/ActivityInfo/Details/1",
        "https://ticket.ibon.com.tw/Order/Checkout/1",
        {
            "is_popup_checkout": True,
            "played_sound_order": True,
            "shown_checkout_message": True,
        },
        {
            "is_popup_checkout": False,
            "played_sound_order": False,
            "shown_checkout_message": False,
        },
    ),
    StateResetCase(
        "kham",
        kham,
        kham._ensure_kham_state_defaults,
        "https://kham.com.tw/application/UTK02/UTK0201_.aspx?PRODUCT_ID=1",
        "https://kham.com.tw/application/UTK02/UTK0206_.aspx?ORDER_ID=1",
        {
            "is_popup_checkout": True,
            "udn_quick_buy_submitted": True,
            "kham_auto_submit_clicked": True,
        },
        {
            "is_popup_checkout": False,
            "udn_quick_buy_submitted": False,
            "kham_auto_submit_clicked": False,
        },
    ),
    StateResetCase(
        "famiticket",
        famiticket,
        famiticket._ensure_famiticket_state_defaults,
        "https://www.famiticket.com.tw/Activity/Info/1",
        "https://www.famiticket.com.tw/Order/Checkout/1",
        {
            "order_notified": True,
            "payment_logged": True,
            "payment_notified": True,
        },
        {
            "order_notified": False,
            "payment_logged": False,
            "payment_notified": False,
        },
    ),
    StateResetCase(
        "funone",
        funone,
        funone._ensure_funone_state_defaults,
        "https://tickets.funone.io/events/1",
        "https://tickets.funone.io/orders/1",
        {
            "next_button_clicked": True,
            "checkout_printed": True,
            "is_ticket_selecting": True,
        },
        {
            "next_button_clicked": False,
            "checkout_printed": False,
            "is_ticket_selecting": False,
        },
    ),
    StateResetCase(
        "fansigo",
        fansigo,
        fansigo._ensure_fansigo_state_defaults,
        "https://go.fansi.me/events/1",
        "https://go.fansi.me/orders/1",
        {
            "notified_checkout": True,
            "qty_set_url": "old-attempt",
            "played_sound_ticket": True,
        },
        {
            "notified_checkout": False,
            "qty_set_url": None,
            "played_sound_ticket": False,
        },
    ),
    StateResetCase(
        "cityline",
        cityline,
        cityline._ensure_cityline_state_defaults,
        "https://www.cityline.com/Events.html?id=1",
        "https://www.cityline.com/checkout/1",
        {
            "purchase_button_pressed": True,
            "modal_handled": True,
            "played_sound_order": True,
        },
        {
            "purchase_button_pressed": False,
            "modal_handled": False,
            "played_sound_order": False,
        },
    ),
    StateResetCase(
        "hkticketing",
        hkticketing,
        hkticketing._ensure_hkticketing_state_defaults,
        "https://premier.hkticketing.com/events/1",
        "https://premier.hkticketing.com/payment/1",
        {
            "shown_checkout_message": True,
            "played_sound_order": True,
            "is_date_submiting": True,
        },
        {
            "shown_checkout_message": False,
            "played_sound_order": False,
            "is_date_submiting": False,
        },
    ),
)


@pytest.mark.parametrize("case", STATE_RESET_CASES, ids=lambda case: case.key)
def test_real_platform_sticky_state_resets_on_new_attempt(
    case: StateResetCase,
) -> None:
    engine = PlatformEngine()
    tab = _Tab(f"reset-{case.key}")
    adapter = adapter_for_key(case.key)
    assert adapter is not None
    first = engine.before_dispatch(tab, case.safe_url, LEAK_CONFIG)
    state = engine.state_for(tab, adapter)
    mapping = state.platform_data
    mapping_identity = id(mapping)
    state_scheduler = state.leak_scheduler
    state_reload_guard = state.reload_guard
    token = case.module._state.bind(mapping)
    try:
        case.initializer()
        for key, expected in case.expected_defaults.items():
            assert mapping[key] == expected
        mapping.update(case.sticky)
        state_scheduler.reload_pending = True
        state_scheduler.dom_scan_pending = True
        state_scheduler.submit_pending = True

        coordinator = engine.refresh_coordinator_for(tab)
        assert coordinator.arm_scheduled("old-attempt", 999_000_000)
        protected = engine.before_dispatch(tab, case.protected_url, LEAK_CONFIG)
        assert protected.page_class in {
            PageClass.TICKET,
            PageClass.ORDER,
            PageClass.CHECKOUT,
            PageClass.PAYMENT,
        }
        assert coordinator.purchase_guard is True
        completed = engine.mark_attempt_completed(
            tab,
            adapter,
            reason="real_state_matrix_success",
        )
        assert completed is not None
        assert completed.state is AttemptState.COMPLETED
        for key, value in case.sticky.items():
            assert mapping[key] is value or mapping[key] == value

        second = engine.before_dispatch(tab, case.safe_url, LEAK_CONFIG)
        assert second.new_attempt_started is True
        assert second.attempt_generation == first.attempt_generation + 1
        assert second.automation_allowed is True
        assert id(state.platform_data) == mapping_identity
        assert mapping == {}
        assert state.leak_scheduler is state_scheduler
        assert state.reload_guard is state_reload_guard
        assert state_scheduler.reload_pending is False
        assert state_scheduler.dom_scan_pending is False
        assert state_scheduler.submit_pending is False
        assert coordinator is engine.refresh_coordinator_for(tab)
        assert coordinator.purchase_guard is False
        assert coordinator.scheduled_token == ""

        case.initializer()
        for key, expected in case.expected_defaults.items():
            assert mapping[key] == expected
    finally:
        case.module._state.reset_binding(token)
        engine.clear_tab(tab)
