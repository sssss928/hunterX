from __future__ import annotations

import asyncio

import pytest

from page_classifier import PageClass
from platform_adapters import adapter_for_key
from platform_engine import platform_engine
from platforms import ticketplus, tixcraft
from refresh_coordinator import RefreshCoordinator
from reload_guard import ReloadGuard
from runtime_health import (
    ExpectedProgressKind,
    ExpectedProgressOutcome,
    RuntimeHealthSupervisor,
    arm_bound_expected_progress,
    cancel_bound_expected_progress,
)


TICKETPLUS_ORDER = "https://ticketplus.com.tw/order/EVENT/GAME"
TIXCRAFT_AREA = "https://tixcraft.com/ticket/area/EVENT/GAME"
TIXCRAFT_DATE = "https://tixcraft.com/activity/game/EVENT/GAME"
TIXCRAFT_TICKET = "https://tixcraft.com/ticket/ticket/EVENT/GAME"
TIXCRAFT_ORDER = "https://tixcraft.com/ticket/order/EVENT/GAME"


class _Target:
    def __init__(self, url: str, target_id: str) -> None:
        self.url = url
        self.target_id = target_id


class _ReloadTab:
    def __init__(self, outcome: str = "success") -> None:
        self.target = _Target(TIXCRAFT_AREA, "reload-tab")
        self.outcome = outcome

    async def reload(self) -> None:
        if self.outcome == "timeout":
            await asyncio.Event().wait()
        if self.outcome == "error":
            raise RuntimeError("reload transport failed")


class _Tab:
    def __init__(self, url: str, target_id: str) -> None:
        self.target = _Target(url, target_id)


class _Debug:
    enabled = False

    def log(self, _message: str) -> None:
        return None


def test_task_local_helper_rejects_cross_attempt_override() -> None:
    health = RuntimeHealthSupervisor()
    with health.bind_expected_progress(
        tab_identity="tab-1",
        platform_key="ticketplus",
        attempt_id="attempt-1",
        attempt_generation=1,
    ):
        assert arm_bound_expected_progress(
            action_owner="owner",
            action_token="token-1",
            kind=ExpectedProgressKind.NAVIGATION,
            deadline=10.0,
            now=0.0,
        ) is not None

    with health.bind_expected_progress(
        tab_identity="tab-1",
        platform_key="ticketplus",
        attempt_id="attempt-2",
        attempt_generation=2,
    ):
        assert not cancel_bound_expected_progress(
            tab_identity="tab-1",
            attempt_id="attempt-1",
            attempt_generation=1,
            action_owner="owner",
            action_token="token-1",
            reason="stale_callback",
        )

    assert health.active_expected_progress_count == 1


@pytest.mark.asyncio
async def test_reload_arms_only_after_dispatch_and_confirms_exact_generation() -> None:
    tab = _ReloadTab()
    health = RuntimeHealthSupervisor()
    coordinator = RefreshCoordinator(tab_identity=id(tab))

    with health.bind_expected_progress(
        tab_identity="reload-tab",
        platform_key="tixcraft",
        attempt_id="attempt-1",
        attempt_generation=1,
    ):
        assert await ReloadGuard().reload(
            tab,
            reason="inventory",
            coordinator=coordinator,
        )

    decision = health.expected_progress_history[-1]
    assert coordinator.generation == 1
    assert decision.outcome is ExpectedProgressOutcome.CONFIRMED
    assert decision.expectation.kind is ExpectedProgressKind.RELOAD
    assert decision.expectation.action_token == "1"
    assert decision.expectation.minimum_refresh_generation == 1
    assert health.active_expected_progress_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "raises", "reason"),
    [
        ("timeout", None, "reload_dispatch_timeout"),
        ("error", RuntimeError, "reload_dispatch_error"),
    ],
)
async def test_reload_timeout_and_error_fail_the_exact_dispatch(
    outcome: str,
    raises: type[BaseException] | None,
    reason: str,
) -> None:
    tab = _ReloadTab(outcome)
    health = RuntimeHealthSupervisor()
    coordinator = RefreshCoordinator(tab_identity=id(tab))

    with health.bind_expected_progress(
        tab_identity="reload-tab",
        platform_key="tixcraft",
        attempt_id="attempt-1",
        attempt_generation=1,
    ):
        operation = ReloadGuard().reload(
            tab,
            reason="inventory",
            timeout_seconds=0.01,
            coordinator=coordinator,
        )
        if raises is None:
            assert await operation is False
        else:
            with pytest.raises(raises):
                await operation

    decision = health.expected_progress_history[-1]
    assert coordinator.generation == 0
    assert coordinator.in_flight_token is None
    assert decision.outcome is ExpectedProgressOutcome.STALLED_ACTION
    assert decision.reason == reason
    assert decision.expectation.action_token == "1"


@pytest.mark.asyncio
async def test_suppressed_reload_never_arms_expected_progress() -> None:
    tab = _ReloadTab()
    health = RuntimeHealthSupervisor()
    coordinator = RefreshCoordinator(tab_identity=id(tab), purchase_guard=True)

    with health.bind_expected_progress(
        tab_identity="reload-tab",
        platform_key="tixcraft",
        attempt_id="attempt-1",
        attempt_generation=1,
    ):
        assert not await ReloadGuard().reload(
            tab,
            reason="inventory",
            coordinator=coordinator,
        )

    assert health.active_expected_progress_count == 0
    assert health.expected_progress_history == ()


def _bind_ticketplus_attempt(tab: _Tab):
    adapter = adapter_for_key("ticketplus")
    assert adapter is not None
    platform_engine.clear_tab(tab)
    decision = platform_engine.before_dispatch(tab, TICKETPLUS_ORDER, {})
    data = platform_engine.state_for(tab, adapter).platform_data
    state_token = ticketplus._state.bind(data)
    ticketplus._ensure_ticketplus_state_defaults()
    return adapter, decision, data, state_token


def test_ticketplus_submit_watch_cancel_and_unknown_are_exactly_fenced() -> None:
    tab = _Tab(TICKETPLUS_ORDER, "ticketplus-tab")
    health = RuntimeHealthSupervisor()
    _adapter, decision, _data, state_token = _bind_ticketplus_attempt(tab)
    try:
        with health.bind_expected_progress(
            tab_identity="ticketplus-tab",
            platform_key="ticketplus",
            attempt_id=decision.attempt_id,
            attempt_generation=decision.attempt_generation,
        ):
            assert ticketplus._ticketplus_arm_submission_watch(tab)
            armed = next(iter(health._expected_progress.values()))
            assert armed.kind is ExpectedProgressKind.SUBMIT
            assert armed.action_token == ticketplus._state["submission_token"]
            assert ticketplus._ticketplus_release_definitely_unsubmitted(tab)
            assert health.expected_progress_history[-1].outcome is (
                ExpectedProgressOutcome.CANCELLED
            )

            platform_engine.mark_attempt_failed(
                tab,
                "ticketplus",
                reason="test_rearm",
            )
            platform_engine.before_dispatch(
                tab,
                "https://ticketplus.com.tw/activity/EVENT",
                {},
            )
            tab.target.url = TICKETPLUS_ORDER
            next_decision = platform_engine.before_dispatch(
                tab,
                TICKETPLUS_ORDER,
                {},
            )
        with health.bind_expected_progress(
            tab_identity="ticketplus-tab",
            platform_key="ticketplus",
            attempt_id=next_decision.attempt_id,
            attempt_generation=next_decision.attempt_generation,
        ):
            assert ticketplus._ticketplus_arm_submission_watch(tab)
            assert ticketplus._ticketplus_mark_submit_outcome_unknown(
                tab,
                "synthetic_ambiguous_submit",
            )
    finally:
        ticketplus._state.reset_binding(state_token)
        platform_engine.clear_tab(tab)

    fault = health.expected_progress_history[-1]
    assert fault.outcome is ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN
    assert fault.reason == "synthetic_ambiguous_submit"
    assert health.unresolved_expected_progress_count == 1


@pytest.mark.asyncio
async def test_ticketplus_queue_confirms_progress_but_keeps_submit_watcher(
    monkeypatch,
) -> None:
    tab = _Tab(TICKETPLUS_ORDER, "ticketplus-tab")
    health = RuntimeHealthSupervisor()
    _adapter, decision, _data, state_token = _bind_ticketplus_attempt(tab)

    async def queue_outcome(*_args, **_kwargs):
        return {"status": "queue", "dialog_text": ""}

    monkeypatch.setattr(ticketplus, "_ticketplus_probe_submission_outcome", queue_outcome)
    try:
        with health.bind_expected_progress(
            tab_identity="ticketplus-tab",
            platform_key="ticketplus",
            attempt_id=decision.attempt_id,
            attempt_generation=decision.attempt_generation,
        ):
            assert ticketplus._ticketplus_arm_submission_watch(tab)
            assert await ticketplus._ticketplus_handle_submission_watch(
                tab,
                {},
                _Debug(),
                force=True,
            )
            assert ticketplus._state["submission_pending"] is True
            assert ticketplus._state["queue_active"] is True
    finally:
        ticketplus._state.reset_binding(state_token)
        platform_engine.clear_tab(tab)

    assert health.expected_progress_history[-1].outcome is (
        ExpectedProgressOutcome.CONFIRMED
    )
    assert health.active_expected_progress_count == 0


def _bind_tixcraft_state(tab: _Tab):
    adapter = adapter_for_key("tixcraft")
    assert adapter is not None
    platform_engine.clear_tab(tab)
    decision = platform_engine.before_dispatch(tab, tab.target.url, {})
    data = platform_engine.state_for(tab, adapter).platform_data
    state_token = tixcraft._state.bind(data)
    tixcraft._ensure_tixcraft_state_defaults()
    tixcraft._state.update(
        {
            "current_event_id": "EVENT",
            "current_game_id": "GAME",
            "last_valid_area_url": TIXCRAFT_AREA,
        }
    )
    return decision, data, state_token


def test_tixcraft_area_candidate_arms_confirms_and_expires_exact_token() -> None:
    tab = _Tab(TIXCRAFT_AREA, "tixcraft-tab")
    health = RuntimeHealthSupervisor()
    decision, _data, state_token = _bind_tixcraft_state(tab)
    try:
        with health.bind_expected_progress(
            tab_identity="tixcraft-tab",
            platform_key="tixcraft",
            attempt_id=decision.attempt_id,
            attempt_generation=decision.attempt_generation,
        ):
            first = tixcraft._set_pending_area_navigation(
                tab,
                TIXCRAFT_AREA,
                "A區",
                {},
            )
            assert tixcraft._reconcile_tixcraft_pending_navigation(
                tab,
                TIXCRAFT_TICKET,
                PageClass.TICKET,
                {},
            )
            assert health.expected_progress_history[-1].outcome is (
                ExpectedProgressOutcome.CONFIRMED
            )
            assert health.expected_progress_history[-1].expectation.action_token == str(
                first.token
            )

            second = tixcraft._set_pending_area_navigation(
                tab,
                TIXCRAFT_AREA,
                "B區",
                {},
                now=0.0,
            )
            assert not tixcraft._reconcile_tixcraft_pending_navigation(
                tab,
                TIXCRAFT_AREA,
                PageClass.AREA,
                {},
            )
    finally:
        tixcraft._state.reset_binding(state_token)
        platform_engine.clear_tab(tab)

    fault = health.expected_progress_history[-1]
    assert fault.outcome is ExpectedProgressOutcome.STALLED_ACTION
    assert fault.expectation.action_token == str(second.token)
    assert fault.reason == "tixcraft_area_click_not_navigated"


def test_tixcraft_date_navigation_to_area_is_confirmed_not_ambiguous() -> None:
    tab = _Tab(TIXCRAFT_DATE, "tixcraft-tab")
    health = RuntimeHealthSupervisor()
    decision, _data, state_token = _bind_tixcraft_state(tab)
    try:
        with health.bind_expected_progress(
            tab_identity="tixcraft-tab",
            platform_key="tixcraft",
            attempt_id=decision.attempt_id,
            attempt_generation=decision.attempt_generation,
        ):
            pending = tixcraft._set_pending_date_navigation(
                tab,
                TIXCRAFT_DATE,
                TIXCRAFT_AREA,
                {},
            )
            tixcraft._reconcile_tixcraft_pending_navigation(
                tab,
                TIXCRAFT_AREA,
                PageClass.AREA,
                {},
            )
    finally:
        tixcraft._state.reset_binding(state_token)
        platform_engine.clear_tab(tab)

    confirmation = health.expected_progress_history[-1]
    assert confirmation.outcome is ExpectedProgressOutcome.CONFIRMED
    assert confirmation.expectation.action_token == str(pending.token)
    assert confirmation.reason == "tixcraft_date_navigation_confirmed"


@pytest.mark.asyncio
async def test_tixcraft_submit_progress_uses_central_token_and_exact_outcome() -> None:
    tab = _Tab(TIXCRAFT_AREA, "tixcraft-tab")
    health = RuntimeHealthSupervisor()
    _decision, data, state_token = _bind_tixcraft_state(tab)
    tab.target.url = TIXCRAFT_TICKET
    submit_decision = platform_engine.before_dispatch(tab, TIXCRAFT_TICKET, {})
    try:
        with health.bind_expected_progress(
            tab_identity="tixcraft-tab",
            platform_key="tixcraft",
            attempt_id=submit_decision.attempt_id,
            attempt_generation=submit_decision.attempt_generation,
        ):
            tixcraft._begin_tixcraft_purchase_attempt("ticket_page", TIXCRAFT_TICKET)
            assert tixcraft._mark_tixcraft_submit_started(TIXCRAFT_TICKET, tab)
            context = data["submit_in_flight"]
            armed = next(iter(health._expected_progress.values()))
            assert armed.kind is ExpectedProgressKind.SUBMIT
            assert armed.action_token == context.central_submit_token
            assert await tixcraft._reconcile_tixcraft_submit_ownership(
                tab,
                PageClass.ORDER,
                TIXCRAFT_ORDER,
                {},
            )
    finally:
        tixcraft._state.reset_binding(state_token)
        platform_engine.clear_tab(tab)

    confirmation = health.expected_progress_history[-1]
    assert confirmation.outcome is ExpectedProgressOutcome.CONFIRMED
    assert confirmation.reason == "tixcraft_submit_order_observed"


def test_tixcraft_ambiguous_submit_fails_only_after_central_owner_accepts() -> None:
    tab = _Tab(TIXCRAFT_AREA, "tixcraft-tab")
    health = RuntimeHealthSupervisor()
    _decision, data, state_token = _bind_tixcraft_state(tab)
    tab.target.url = TIXCRAFT_TICKET
    submit_decision = platform_engine.before_dispatch(tab, TIXCRAFT_TICKET, {})
    try:
        with health.bind_expected_progress(
            tab_identity="tixcraft-tab",
            platform_key="tixcraft",
            attempt_id=submit_decision.attempt_id,
            attempt_generation=submit_decision.attempt_generation,
        ):
            tixcraft._begin_tixcraft_purchase_attempt("ticket_page", TIXCRAFT_TICKET)
            assert tixcraft._mark_tixcraft_submit_started(TIXCRAFT_TICKET, tab)
            assert tixcraft._mark_tixcraft_central_submit_unknown(
                tab,
                "synthetic_tixcraft_ambiguous_submit",
            )
            context = data["submit_in_flight"]
    finally:
        tixcraft._state.reset_binding(state_token)
        platform_engine.clear_tab(tab)

    fault = health.expected_progress_history[-1]
    assert fault.outcome is ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN
    assert fault.expectation.action_token == context.central_submit_token
    assert fault.reason == "synthetic_tixcraft_ambiguous_submit"
