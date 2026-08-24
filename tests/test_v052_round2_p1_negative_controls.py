from __future__ import annotations

import time

import pytest

import nodriver_tixcraft
from platform_adapters import adapter_for_key
from platform_engine import platform_engine
from platforms import cityline, famiticket, ticketplus, tixcraft
from tab_ownership import TabTransition


class _Target:
    def __init__(self, url: str, target_id: str) -> None:
        self.url = url
        self.target_id = target_id


class _Tab:
    def __init__(self, url: str, target_id: str) -> None:
        self.target = _Target(url, target_id)


class _Debug:
    def log(self, *_args, **_kwargs) -> None:
        return None


def test_round1_tixcraft_dispatch_state_prefers_nonempty_process_fallback() -> None:
    """Negative control for P1-R2-10: a real tab must never use fallback state."""

    tab = _Tab("https://tixcraft.com/activity/detail/E", "fallback-a")
    real = tixcraft._state_for_tab(tab)
    real.clear()
    tixcraft._default_state.clear()
    tixcraft._default_state["legacy"] = "process-global"
    try:
        selected = tixcraft._dispatch_state_for_tab(tab)
        trigger_selected = nodriver_tixcraft._get_trigger_runtime_state(
            tab.target.url,
            tab,
        )
        assert selected is real
        assert trigger_selected is real
    finally:
        tixcraft._default_state.clear()


@pytest.mark.asyncio
async def test_round1_cityline_popup_continues_with_old_tab_state(monkeypatch) -> None:
    """Negative control: bot-created popup adoption must switch state ownership."""

    old = _Tab("https://venue.cityline.com/utsvInternet/demo/eventDetail", "city-old")
    new = _Tab("https://venue.cityline.com/utsvInternet/demo/performance", "city-new")
    browser = type("Browser", (), {"tabs": [old, new]})()
    old.browser = browser
    new.browser = browser
    new.activations = 0

    async def activate() -> None:
        new.activations += 1

    new.activate = activate

    async def current_url(tab, *_args, **_kwargs):
        return tab.target.url, False

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(cityline, "nodriver_current_url", current_url)
    monkeypatch.setattr(cityline.asyncio, "sleep", no_sleep)

    adapter = adapter_for_key("cityline")
    assert adapter is not None
    old_state = platform_engine.state_for(old, adapter).platform_data
    new_state = platform_engine.state_for(new, adapter).platform_data
    old_state.clear()
    new_state.clear()
    old_state.update(
        {
            "pending_owned_tab_baseline_ids": (id(old),),
            "pending_owned_tab_deadline": time.monotonic() + 10.0,
        }
    )
    token = cityline._state.bind(old_state)
    try:
        selected = await cityline.nodriver_cityline_close_second_tab(
            old,
            old.target.url,
        )
        assert isinstance(selected, TabTransition)
        assert selected.previous_tab is old
        assert selected.tab is new
        platform_engine.before_dispatch(new, selected.url, {})
        cityline._state["round2_owner"] = "new-tab-flow"
        assert new_state["round2_owner"] == "new-tab-flow"
        assert "round2_owner" not in old_state
    finally:
        cityline._state.reset_binding(token)
        old_state.clear()
        new_state.clear()


def test_round1_tixcraft_inner_submit_has_no_central_owner_and_is_erased() -> None:
    """Negative control for P1-R2-11 central/inner lifecycle synchronization."""

    tab = _Tab("https://tixcraft.com/ticket/ticket/E/G", "tix-bridge")
    adapter = adapter_for_key("tixcraft")
    assert adapter is not None
    area = "https://tixcraft.com/ticket/area/E/G"
    ticket = tab.target.url

    platform_engine.before_dispatch(tab, area, {})
    platform_engine.before_dispatch(tab, ticket, {})
    state = platform_engine.state_for(tab, adapter)
    token = tixcraft._state.bind(state.platform_data)
    try:
        tixcraft._ensure_tixcraft_state_defaults()
        tixcraft._begin_tixcraft_purchase_attempt("ticket_page", ticket)
        tixcraft._mark_tixcraft_submit_started(ticket, tab)
        central_before = platform_engine.current_attempt(tab, adapter)
        assert central_before is not None
        assert central_before.submit_token

        decision = platform_engine.before_dispatch(tab, area, {})
        central_after = platform_engine.current_attempt(tab, adapter)
        assert decision.new_attempt_started is False
        assert central_after is not None
        assert central_after.attempt_id == central_before.attempt_id
        assert tixcraft._state.get("submit_in_flight") is not None
    finally:
        tixcraft._state.reset_binding(token)


@pytest.mark.asyncio
async def test_round1_ticketplus_refresh_swallows_terminal_transport() -> None:
    class _DeadTab:
        async def evaluate(self, _script):
            raise RuntimeError("websocket is not connected")

    with pytest.raises(RuntimeError, match="websocket is not connected"):
        await ticketplus._ticketplus_click_refresh_button(_DeadTab(), _Debug())


@pytest.mark.asyncio
async def test_round1_cityline_action_swallows_terminal_transport(monkeypatch) -> None:
    class _DeadTab:
        async def evaluate(self, _script):
            raise RuntimeError("websocket is not connected")

    monkeypatch.setattr(cityline.util, "create_debug_logger", lambda *_a, **_k: _Debug())
    monkeypatch.setattr(cityline, "get_auto_reload_interval", lambda *_a, **_k: 0)
    with pytest.raises(RuntimeError, match="websocket is not connected"):
        await cityline.nodriver_cityline_auto_retry_access(_DeadTab(), "", {})


@pytest.mark.asyncio
async def test_round1_famiticket_click_swallows_terminal_transport(monkeypatch) -> None:
    class _DeadTab:
        async def evaluate(self, _script):
            raise RuntimeError("websocket is not connected")

    monkeypatch.setattr(famiticket.util, "create_debug_logger", lambda *_a, **_k: _Debug())
    with pytest.raises(RuntimeError, match="websocket is not connected"):
        await famiticket.nodriver_fami_activity(_DeadTab(), {})
