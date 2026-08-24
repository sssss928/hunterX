from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect

import pytest

from attempt_lifecycle import AttemptState
from page_classifier import PageClass
from platform_adapters import adapter_for_key
from platform_engine import platform_engine
from platforms import tixcraft
from settings import get_default_config


_TICKET_URL = "https://tixcraft.com/ticket/ticket/red-team/1"
_AREA_URL = "https://tixcraft.com/ticket/area/red-team/1"


@dataclass
class _Target:
    url: str
    target_id: str = "tixcraft-authoritative-rejection"


class _Tab:
    def __init__(self, url: str = _TICKET_URL) -> None:
        self.target = _Target(url)


@pytest.fixture
def submitted_tixcraft_attempt():
    tab = _Tab()
    config = get_default_config()
    adapter = adapter_for_key("tixcraft")
    assert adapter is not None
    platform_engine.clear_tab(tab)
    decision = platform_engine.before_dispatch(tab, tab.target.url, config)
    assert decision.page_class is PageClass.TICKET
    state = platform_engine.state_for(tab, adapter)
    binding = tixcraft._state.bind(state.platform_data)
    try:
        tixcraft._ensure_tixcraft_state_defaults()
        assert tixcraft._mark_tixcraft_submit_started(tab.target.url, tab)
        local_context = state.platform_data["submit_in_flight"]
        central_attempt = platform_engine.current_attempt(tab, adapter)
        assert central_attempt is not None
        assert central_attempt.state is AttemptState.SUBMIT_IN_FLIGHT
        yield tab, config, adapter, state, local_context, central_attempt
    finally:
        tixcraft._state.reset_binding(binding)
        platform_engine.clear_tab(tab)


def _authoritatively_reject(tab, local_context) -> bool:
    assert local_context is tixcraft._state.get("submit_in_flight")
    return tixcraft._reset_tixcraft_submit_state(
        tab=tab,
        confirmed_rejection_reason="confirmed_retryable_alert",
    )


def test_authoritative_rejection_allows_same_ticket_submit_retry(
    submitted_tixcraft_attempt,
) -> None:
    tab, _config, adapter, state, local_context, first = submitted_tixcraft_attempt

    assert _authoritatively_reject(tab, local_context)
    released = platform_engine.current_attempt(tab, adapter)
    assert released is not None
    assert released.attempt_id == first.attempt_id
    assert released.generation == first.generation
    assert released.state is AttemptState.TICKET_FORM_ACTIVE
    assert released.submit_token == ""
    assert state.platform_data.get("_central_safe_rearm_proof") is None
    assert state.platform_data.get("submit_in_flight") is None

    assert tixcraft._mark_tixcraft_submit_started(tab.target.url, tab)
    retried = platform_engine.current_attempt(tab, adapter)
    assert retried is not None
    assert retried.attempt_id == first.attempt_id
    assert retried.submit_token
    assert retried.submit_token != first.submit_token


def test_authoritative_rejection_then_area_starts_new_generation(
    submitted_tixcraft_attempt,
) -> None:
    tab, config, adapter, _state, local_context, first = submitted_tixcraft_attempt

    assert _authoritatively_reject(tab, local_context)
    tab.target.url = _AREA_URL
    decision = platform_engine.before_dispatch(tab, tab.target.url, config)
    rearmed = platform_engine.current_attempt(tab, adapter)

    assert decision.new_attempt_started is True
    assert rearmed is not None
    assert rearmed.generation == first.generation + 1
    assert rearmed.attempt_id != first.attempt_id
    assert rearmed.state is AttemptState.AREA_READY
    assert rearmed.submit_token == ""


def test_stale_rejection_token_cannot_release_new_attempt_owner_or_proof(
    submitted_tixcraft_attempt,
) -> None:
    tab, config, adapter, state, old_context, old_attempt = submitted_tixcraft_attempt

    assert _authoritatively_reject(tab, old_context)
    tab.target.url = _AREA_URL
    platform_engine.before_dispatch(tab, tab.target.url, config)
    tab.target.url = _TICKET_URL
    platform_engine.before_dispatch(tab, tab.target.url, config)
    tixcraft._ensure_tixcraft_state_defaults()
    assert tixcraft._mark_tixcraft_submit_started(_TICKET_URL, tab)
    new_context = state.platform_data["submit_in_flight"]
    new_attempt = platform_engine.current_attempt(tab, adapter)
    new_proof = state.platform_data.get("_central_safe_rearm_proof")
    assert new_attempt is not None

    assert platform_engine.release_rejected_submit_if_owned(
        tab,
        adapter,
        attempt_id=old_attempt.attempt_id,
        attempt_generation=old_attempt.generation,
        token=old_attempt.submit_token,
        reason="stale_old_rejection",
    ) is None
    after_stale = platform_engine.current_attempt(tab, adapter)
    assert after_stale == new_attempt
    assert state.platform_data.get("submit_in_flight") == new_context
    assert state.platform_data.get("_central_safe_rearm_proof") == new_proof


@pytest.mark.asyncio
async def test_delayed_production_alert_callback_cannot_release_new_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tab = _Tab()
    config = get_default_config()
    adapter = adapter_for_key("tixcraft")
    assert adapter is not None
    platform_engine.clear_tab(tab)
    decision = platform_engine.before_dispatch(tab, tab.target.url, config)
    assert decision.page_class is PageClass.TICKET
    state = platform_engine.state_for(tab, adapter)
    binding = tixcraft._state.bind(state.platform_data)
    captured: dict[str, object] = {}

    async def no_pause(*_args, **_kwargs):
        return False

    async def no_close(*_args, **_kwargs):
        return None

    async def no_submit(*_args, **_kwargs):
        return False

    async def stop_after_registration(*_args, **_kwargs):
        return True

    async def send(_command):
        return None

    def add_handler(_event_type, callback):
        captured["callback"] = callback

    tab.send = send
    tab.add_handler = add_handler
    monkeypatch.setattr(tixcraft, "check_and_handle_pause", no_pause)
    monkeypatch.setattr(tixcraft, "nodriver_tixcraft_home_close_window", no_close)
    monkeypatch.setattr(tixcraft, "_reconcile_tixcraft_submit_ownership", no_submit)
    monkeypatch.setattr(
        tixcraft,
        "nodriver_ticketmaster_check_ip_block",
        stop_after_registration,
    )

    try:
        tixcraft._ensure_tixcraft_state_defaults()
        await tixcraft._nodriver_tixcraft_main_impl(
            tab,
            tab.target.url,
            config,
            None,
            None,
        )
        delayed_callback = captured.get("callback")
        assert callable(delayed_callback)
        assert asyncio.iscoroutinefunction(delayed_callback)

        assert tixcraft._mark_tixcraft_submit_started(tab.target.url, tab)
        old_context = state.platform_data["submit_in_flight"]
        event = type("Alert", (), {"message": "verification code incorrect"})()
        # Zendriver invokes coroutine handlers with ``(event, connection)``.
        # Calling that exact production shape proves the synchronous factory
        # snapshots attempt N before Zendriver schedules the returned coroutine.
        delayed_alert = delayed_callback(event, object())
        assert inspect.isawaitable(delayed_alert)
        unknown_event = type("Alert", (), {"message": "unexpected warning"})()
        delayed_unknown_alert = delayed_callback(unknown_event, object())
        assert inspect.isawaitable(delayed_unknown_alert)

        assert _authoritatively_reject(tab, old_context)
        tab.target.url = _AREA_URL
        platform_engine.before_dispatch(tab, tab.target.url, config)
        tab.target.url = _TICKET_URL
        platform_engine.before_dispatch(tab, tab.target.url, config)
        tixcraft._ensure_tixcraft_state_defaults()
        assert tixcraft._mark_tixcraft_submit_started(tab.target.url, tab)
        new_attempt = platform_engine.current_attempt(tab, adapter)
        new_context = state.platform_data.get("submit_in_flight")
        new_proof = state.platform_data.get("_central_safe_rearm_proof")
        assert new_attempt is not None and new_attempt.submit_token

        await delayed_alert
        await delayed_unknown_alert

        assert platform_engine.current_attempt(tab, adapter) == new_attempt
        assert state.platform_data.get("submit_in_flight") == new_context
        assert state.platform_data.get("_central_safe_rearm_proof") == new_proof
        assert state.platform_data.get("captcha_alert_detected") is False
        assert state.platform_data.get("manual_intervention_required") is False

        current_alert = delayed_callback(event, object())
        assert inspect.isawaitable(current_alert)
        await current_alert
        released = platform_engine.current_attempt(tab, adapter)
        assert released is not None
        assert released.attempt_id == new_attempt.attempt_id
        assert released.generation == new_attempt.generation
        assert released.state is AttemptState.TICKET_FORM_ACTIVE
        assert released.submit_token == ""
        assert state.platform_data.get("submit_in_flight") is None
        assert state.platform_data.get("_central_safe_rearm_proof") is None
        assert state.platform_data.get("captcha_alert_detected") is True
    finally:
        tixcraft._state.reset_binding(binding)
        platform_engine.clear_tab(tab)
