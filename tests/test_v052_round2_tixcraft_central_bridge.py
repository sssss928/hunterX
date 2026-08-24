from __future__ import annotations

import asyncio

import pytest

from attempt_lifecycle import AttemptState
from page_classifier import PageClass
from platform_adapters import adapter_for_key
from platform_engine import platform_engine
from platforms import tixcraft


AREA = "https://tixcraft.com/ticket/area/EVENT/GAME"
TICKET = "https://tixcraft.com/ticket/ticket/EVENT/GAME"
ORDER = "https://tixcraft.com/ticket/order/EVENT/GAME"
CHECKOUT = "https://tixcraft.com/ticket/checkout"


class _Target:
    def __init__(self, url: str) -> None:
        self.url = url
        self.target_id = "tixcraft-central-bridge"


class _Tab:
    def __init__(self, url: str = AREA) -> None:
        self.target = _Target(url)

    async def send(self, _command):
        return None


def _confirmed_area_health() -> dict[str, object]:
    return {
        "readyState": "complete",
        "hasBody": True,
        "bodyText": "area inventory",
        "title": "event",
        "knownAreaContent": True,
        "hasKnownContent": True,
        "probeFailed": False,
        "blocked": False,
        "whiteOverlay": False,
    }


def _arm_submit(tab: _Tab):
    adapter = adapter_for_key("tixcraft")
    assert adapter is not None
    platform_engine.clear_tab(tab)
    platform_engine.before_dispatch(tab, AREA, {})
    tab.target.url = TICKET
    platform_engine.before_dispatch(tab, TICKET, {})
    data = platform_engine.state_for(tab, adapter).platform_data
    token = tixcraft._state.bind(data)
    try:
        tixcraft._ensure_tixcraft_state_defaults()
        tixcraft._state.update(
            {
                "current_event_id": "EVENT",
                "current_game_id": "GAME",
                "last_valid_area_url": AREA,
            }
        )
        tixcraft._begin_tixcraft_purchase_attempt("ticket_page", TICKET)
        assert tixcraft._mark_tixcraft_submit_started(TICKET, tab)
        context = tixcraft._state["submit_in_flight"]
    finally:
        tixcraft._state.reset_binding(token)
    central = platform_engine.current_attempt(tab, adapter)
    assert central is not None
    assert central.state is AttemptState.SUBMIT_IN_FLIGHT
    assert central.submit_token == context.central_submit_token
    return adapter, data, context, central


def test_area_url_alone_cannot_erase_live_central_or_inner_submit() -> None:
    tab = _Tab()
    adapter, data, context, before = _arm_submit(tab)

    tab.target.url = AREA
    decision = platform_engine.before_dispatch(tab, AREA, {})
    after = platform_engine.current_attempt(tab, adapter)

    assert decision.new_attempt_started is False
    assert after is not None
    assert after.attempt_id == before.attempt_id
    assert after.submit_token == before.submit_token
    assert data["submit_in_flight"] is context


@pytest.mark.asyncio
async def test_interactive_area_proof_rearms_both_lifecycles_once(monkeypatch) -> None:
    tab = _Tab()
    adapter, data, context, before = _arm_submit(tab)
    tab.target.url = AREA
    platform_engine.before_dispatch(tab, AREA, {})
    monkeypatch.setattr(
        tixcraft,
        "_read_tixcraft_page_health",
        lambda *_a, **_k: asyncio.sleep(0, result=_confirmed_area_health()),
    )

    state_token = tixcraft._state.bind(data)
    try:
        assert not await tixcraft._reconcile_tixcraft_submit_ownership(
            tab,
            PageClass.AREA,
            AREA,
            {},
        )
    finally:
        tixcraft._state.reset_binding(state_token)

    after = platform_engine.current_attempt(tab, adapter)
    assert after is not None
    assert after.attempt_id != before.attempt_id
    assert after.generation == before.generation + 1
    assert after.state is AttemptState.AREA_READY
    assert not after.submit_token
    assert data.get("submit_in_flight") is None
    assert context.central_attempt_id == before.attempt_id
    assert (
        platform_engine.mark_submit_outcome_unknown_if_owned(
            tab,
            adapter,
            attempt_id=context.central_attempt_id,
            token=context.central_submit_token,
            reason="stale_old_watcher",
        )
        is None
    )
    assert platform_engine.current_attempt(tab, adapter) == after


def test_second_inner_submit_cannot_claim_or_replace_central_owner() -> None:
    tab = _Tab()
    adapter, data, context, before = _arm_submit(tab)
    state_token = tixcraft._state.bind(data)
    try:
        assert not tixcraft._mark_tixcraft_submit_started(TICKET, tab)
        assert tixcraft._state["submit_in_flight"] is context
    finally:
        tixcraft._state.reset_binding(state_token)
    assert platform_engine.current_attempt(tab, adapter) == before


def test_ambiguous_local_reset_cannot_drop_a_live_central_submit_fence() -> None:
    """A soft-block/reset without an exact browser owner must remain fail-closed."""

    tab = _Tab()
    adapter, data, context, before = _arm_submit(tab)
    state_token = tixcraft._state.bind(data)
    try:
        tixcraft._reset_tixcraft_submit_state()
        assert tixcraft._state.get("submit_in_flight") is context
    finally:
        tixcraft._state.reset_binding(state_token)

    assert platform_engine.current_attempt(tab, adapter) == before


def test_order_and_checkout_preserve_the_exact_cross_lifecycle_owner() -> None:
    tab = _Tab()
    adapter, data, context, before = _arm_submit(tab)
    for url, page in ((ORDER, PageClass.ORDER), (CHECKOUT, PageClass.CHECKOUT)):
        tab.target.url = url
        platform_engine.before_dispatch(tab, url, {})
        state_token = tixcraft._state.bind(data)
        try:
            tixcraft._track_tixcraft_attempt_page(page, url)
            assert tixcraft._state["submit_in_flight"] is context
            assert tixcraft._is_tixcraft_submit_in_flight(tab)
        finally:
            tixcraft._state.reset_binding(state_token)

    central = platform_engine.current_attempt(tab, adapter)
    assert central is not None
    assert central.attempt_id == before.attempt_id
    assert central.submit_token == before.submit_token
    assert central.state is AttemptState.CHECKOUT_REACHED


@pytest.mark.asyncio
async def test_terminal_keydown_marks_exact_central_attempt_unknown() -> None:
    class _DeadTab(_Tab):
        async def send(self, _command):
            raise RuntimeError("websocket is not connected")

    tab = _DeadTab()
    adapter = adapter_for_key("tixcraft")
    assert adapter is not None
    platform_engine.before_dispatch(tab, AREA, {})
    tab.target.url = TICKET
    platform_engine.before_dispatch(tab, TICKET, {})
    data = platform_engine.state_for(tab, adapter).platform_data
    state_token = tixcraft._state.bind(data)
    try:
        tixcraft._ensure_tixcraft_state_defaults()
        tixcraft._begin_tixcraft_purchase_attempt("ticket_page", TICKET)
        guard = tixcraft._state["submit_guard"]
        with pytest.raises(RuntimeError, match="websocket is not connected"):
            await tixcraft._dispatch_tixcraft_enter_submit(tab, TICKET, guard)
    finally:
        tixcraft._state.reset_binding(state_token)

    central = platform_engine.current_attempt(tab, adapter)
    assert central is not None
    assert central.state is AttemptState.SUBMIT_OUTCOME_UNKNOWN
