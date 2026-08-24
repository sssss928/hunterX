from __future__ import annotations

import asyncio

import pytest

import settings
from attempt_lifecycle import AttemptState
from platform_adapters import adapter_for_key
from platform_contract import clear_active_platform_state
from platform_engine import platform_engine
from platforms import ticketplus
from runtime_health import RuntimeHealthSupervisor


class _Tab:
    pass


@pytest.fixture(autouse=True)
def _isolate_platform_state_binding():
    """Release the synthetic global-engine dispatch binding at test boundaries."""

    clear_active_platform_state()
    try:
        yield
    finally:
        clear_active_platform_state()


def _activate_ticketplus(tab: _Tab):
    adapter = adapter_for_key("ticketplus")
    assert adapter is not None
    platform_engine.clear_tab(tab)
    platform_engine.before_dispatch(
        tab,
        "https://ticketplus.com.tw/order/event/session",
        settings.get_default_config(),
    )
    ticketplus._ensure_ticketplus_state_defaults()
    return adapter


def test_ticketplus_submit_watch_claims_exact_engine_attempt() -> None:
    tab = _Tab()
    adapter = _activate_ticketplus(tab)
    initial = platform_engine.current_attempt(tab, adapter)
    assert initial is not None

    assert ticketplus._ticketplus_arm_submission_watch(tab) is True
    armed = platform_engine.current_attempt(tab, adapter)
    assert armed is not None
    assert armed.attempt_id == initial.attempt_id
    assert armed.state is AttemptState.SUBMIT_IN_FLIGHT
    assert ticketplus._state["submission_attempt_id"] == initial.attempt_id

    assert ticketplus._ticketplus_arm_submission_watch(tab) is False
    assert platform_engine.current_attempt(tab, adapter).submit_token == armed.submit_token


@pytest.mark.asyncio
async def test_ticketplus_unknown_submit_outcome_never_schedules_duplicate_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tab = _Tab()
    adapter = _activate_ticketplus(tab)
    config = settings.get_default_config()
    debug = type("Debug", (), {"log": lambda self, _message: None})()
    assert ticketplus._ticketplus_arm_submission_watch(tab) is True

    async def unknown_outcome(*_args, **_kwargs):
        return {"status": "unknown", "dialog_text": ""}

    monkeypatch.setattr(ticketplus, "_ticketplus_probe_submission_outcome", unknown_outcome)
    ticketplus._state["submission_deadline"] = 1.0
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: 2.0)

    assert await ticketplus._ticketplus_handle_submission_watch(
        tab,
        config,
        debug,
        force=True,
    ) is True
    attempt = platform_engine.current_attempt(tab, adapter)
    assert attempt is not None
    assert attempt.state is AttemptState.SUBMIT_OUTCOME_UNKNOWN
    assert ticketplus._state["submission_pending"] is False
    assert ticketplus._state["submission_outcome_unknown"] is True
    assert ticketplus._state["failure_retry_pending"] is False


@pytest.mark.asyncio
async def test_stale_submission_watcher_cannot_mutate_rearmed_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delayed attempt-N probe must not consume attempt N+1 ownership."""

    clock = {"now": 100.0}
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: clock["now"])
    tab = _Tab()
    adapter = _activate_ticketplus(tab)
    config = settings.get_default_config()
    debug = type("Debug", (), {"log": lambda self, _message: None})()
    health = RuntimeHealthSupervisor()
    initial = platform_engine.current_attempt(tab, adapter)
    assert initial is not None

    probe_started = asyncio.Event()
    release_old_probe = asyncio.Event()

    async def delayed_unknown(*_args, **_kwargs):
        probe_started.set()
        await release_old_probe.wait()
        return {"status": "unknown", "dialog_text": ""}

    monkeypatch.setattr(
        ticketplus,
        "_ticketplus_probe_submission_outcome",
        delayed_unknown,
    )
    with health.bind_expected_progress(
        tab_identity="ticketplus-stale-tab",
        platform_key="ticketplus",
        attempt_id=initial.attempt_id,
        attempt_generation=initial.generation,
    ):
        assert ticketplus._ticketplus_arm_submission_watch(tab) is True
        old_attempt = platform_engine.current_attempt(tab, adapter)
        assert old_attempt is not None
        old_watcher = asyncio.create_task(
            ticketplus._ticketplus_handle_submission_watch(
                tab,
                config,
                debug,
                force=True,
            )
        )
        await probe_started.wait()

    clock["now"] = 200.0
    platform_engine.before_dispatch(
        tab,
        "https://ticketplus.com.tw/confirm/event/session",
        config,
    )
    completed = platform_engine.mark_attempt_completed(
        tab,
        adapter,
        reason="fixture_success",
    )
    assert completed is not None
    rearmed = platform_engine.before_dispatch(
        tab,
        "https://ticketplus.com.tw/order/event/session",
        config,
    )
    assert rearmed.new_attempt_started is True
    ticketplus._ensure_ticketplus_state_defaults()
    before_new_submit = platform_engine.current_attempt(tab, adapter)
    assert before_new_submit is not None
    with health.bind_expected_progress(
        tab_identity="ticketplus-stale-tab",
        platform_key="ticketplus",
        attempt_id=before_new_submit.attempt_id,
        attempt_generation=before_new_submit.generation,
    ):
        assert ticketplus._ticketplus_arm_submission_watch(tab) is True
        new_attempt = platform_engine.current_attempt(tab, adapter)
        assert new_attempt is not None
        assert new_attempt.attempt_id != old_attempt.attempt_id
        assert new_attempt.state is AttemptState.SUBMIT_IN_FLIGHT
        new_deadline = ticketplus._state["submission_deadline"]
        new_token = ticketplus._state["submission_token"]
        new_progress_tab = ticketplus._state["submission_progress_tab_identity"]

    clock["now"] = new_deadline + 1.0
    release_old_probe.set()
    await old_watcher

    current = platform_engine.current_attempt(tab, adapter)
    assert current is not None
    assert current.attempt_id == new_attempt.attempt_id
    assert current.submit_token == new_attempt.submit_token
    assert current.state is AttemptState.SUBMIT_IN_FLIGHT
    assert ticketplus._state["submission_pending"] is True
    assert ticketplus._state["submission_attempt_id"] == new_attempt.attempt_id
    assert ticketplus._state["submission_token"] == new_token
    assert ticketplus._state["submission_deadline"] == new_deadline
    assert ticketplus._state["submission_outcome_unknown"] is False
    assert health.confirm_expected_progress(
        tab_identity=new_progress_tab,
        attempt_id=new_attempt.attempt_id,
        attempt_generation=new_attempt.generation,
        action_owner="ticketplus_next_button",
        action_token=new_token,
        now=clock["now"],
    ) is True
    assert health.active_expected_progress_count == 1
