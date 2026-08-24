from __future__ import annotations

from pathlib import Path

import pytest

import settings
from attempt_lifecycle import AttemptState
from platform_adapters import adapter_for_key
from platform_engine import platform_engine
from platforms import ticketplus
from runtime_health import ExpectedProgressOutcome, RuntimeHealthSupervisor
from scripts.audit_browser_exception_handlers import REPO_ROOT, audit_source


def test_ticketplus_browser_interaction_handlers_never_hide_terminal_errors() -> None:
    findings = audit_source(
        Path(REPO_ROOT, "src", "platforms", "ticketplus.py"),
        repo_root=REPO_ROOT,
    )

    assert len(findings) >= 30
    assert all(finding.disposition == "terminal_classifier" for finding in findings)


@pytest.mark.asyncio
async def test_ticketplus_terminal_submit_error_is_exactly_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Target:
        url = "https://ticketplus.com.tw/order/event/session"
        target_id = "ticketplus-terminal-submit"

    class _DeadTab:
        target = _Target()

        async def evaluate(self, _script):
            raise RuntimeError("websocket is not connected")

    async def no_pause(*_args, **_kwargs):
        return False

    tab = _DeadTab()
    config = settings.get_default_config()
    adapter = adapter_for_key("ticketplus")
    assert adapter is not None
    platform_engine.clear_tab(tab)
    decision = platform_engine.before_dispatch(tab, tab.target.url, config)
    data = platform_engine.state_for(tab, adapter).platform_data
    state_token = ticketplus._state.bind(data)
    health = RuntimeHealthSupervisor()
    central_after = None
    monkeypatch.setattr(ticketplus, "sleep_with_pause_check", no_pause)
    try:
        ticketplus._ensure_ticketplus_state_defaults()
        with health.bind_expected_progress(
            tab_identity=tab.target.target_id,
            platform_key="ticketplus",
            attempt_id=decision.attempt_id,
            attempt_generation=decision.attempt_generation,
        ):
            assert ticketplus._ticketplus_arm_submission_watch(tab)
            with pytest.raises(RuntimeError, match="websocket is not connected"):
                await ticketplus.nodriver_ticketplus_click_next_button_unified(
                    tab,
                    config,
                )
            central_after = platform_engine.current_attempt(tab, adapter)
    finally:
        ticketplus._state.reset_binding(state_token)
        platform_engine.clear_tab(tab)

    assert data["submission_outcome_unknown"] is True
    assert central_after is not None
    assert central_after.state is AttemptState.SUBMIT_OUTCOME_UNKNOWN
    fault = health.expected_progress_history[-1]
    assert fault.outcome is ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN
    assert fault.expectation.action_token == data["submission_token"]
