from __future__ import annotations

from types import SimpleNamespace

import pytest

import nodriver_tixcraft as runtime
from attempt_lifecycle import AttemptState
from browser_session import (
    BrowserBootstrapResult,
    BrowserExitState,
    BrowserRecoveryResult,
    BrowserSessionManager,
)
from page_classifier import PageClass
from platform_engine import platform_engine
from runtime_health import RecoveryLevel, RuntimeHealthSupervisor
from settings import get_default_config


LOGIN_URL = "https://tixcraft.com/login"


class ConnectionClosedError(ConnectionError):
    """Faithful classifier shape used by the Windows Zendriver traceback."""


ConnectionClosedError.__module__ = "websockets.exceptions"


class _Target:
    def __init__(self, url: str, target_id: str) -> None:
        self.url = url
        self.target_id = target_id


class _DeadPlatformTab:
    def __init__(self) -> None:
        self.target = _Target(LOGIN_URL, "owned-tixcraft-tab")
        self.query_calls = 0

    async def query_selector(self, selector: str):
        assert selector == "#onetrust-accept-btn-handler"
        self.query_calls += 1
        raise ConnectionClosedError("no close frame received or sent")


class _LiveReplacementTab:
    def __init__(self) -> None:
        self.target = _Target(LOGIN_URL, "owned-tixcraft-tab")
        self.proof_calls = 0

    async def send(self, _command):
        self.proof_calls += 1
        return _Target(LOGIN_URL, "owned-tixcraft-tab")


class _Process:
    returncode = None

    @staticmethod
    def poll():
        return None


class _Driver:
    def __init__(self, replacement: _LiveReplacementTab) -> None:
        self.tabs = [replacement]
        self.main_tab = replacement
        self.browser_process = _Process()
        self.update_calls = 0
        self.stop_calls = 0

    async def update_targets(self) -> None:
        self.update_calls += 1

    async def stop(self) -> None:
        self.stop_calls += 1


class _RecoverySession:
    def __init__(
        self,
        responses: list[tuple[bool, str, bool]],
        *,
        exit_state: BrowserExitState = BrowserExitState.ALIVE,
        recovery_error: Exception | None = None,
    ) -> None:
        self.responses = list(responses)
        self.exit_state = exit_state
        self.recovery_error = recovery_error
        self.recover_calls: list[dict[str, object]] = []
        self.attach_calls: list[tuple[object, object | None]] = []

    def browser_exit_state(self) -> BrowserExitState:
        return self.exit_state

    def attach(self, driver: object, tab: object | None = None) -> None:
        self.attach_calls.append((driver, tab))

    async def recover(self, level: RecoveryLevel, **kwargs):
        self.recover_calls.append({"level": level, **kwargs})
        if self.recovery_error is not None:
            raise self.recovery_error
        success, reason, restarted = self.responses.pop(0)
        tab = _LiveReplacementTab() if success else None
        return BrowserRecoveryResult(
            success,
            reason,
            level,
            driver=SimpleNamespace(tabs=[tab] if tab else []),
            tab=tab,
            restarted=restarted,
        )


def _config() -> dict:
    config = get_default_config()
    config["homepage"] = "https://tixcraft.com/ticket/area/26_khalid/22908"
    config["ocr_captcha"]["enable"] = False
    config["advanced"]["show_timestamp"] = False
    return config


def _context(
    url: str,
    page_class: PageClass,
    session: _RecoverySession,
    *,
    health: RuntimeHealthSupervisor | None = None,
) -> runtime.RuntimeIterationContext:
    tab = _LiveReplacementTab()
    tab.target.url = url
    context = runtime.RuntimeIterationContext(
        config_dict=_config(),
        driver=SimpleNamespace(tabs=[tab]),
        tab=tab,
        session_manager=session,
        health_supervisor=health
        or RuntimeHealthSupervisor(recovery_cooldown_seconds=0.0),
        refresh_datetime_state={"target_str": "", "reached": False},
    )
    context.last_url = url
    context.last_safe_url = "https://tixcraft.com/ticket/area/26_khalid/22908"
    context.last_platform_key = "tixcraft"
    context.last_page_class = page_class
    platform_engine.before_dispatch(tab, url, context.config_dict)
    return context


@pytest.mark.asyncio
async def test_run_main_owns_tixcraft_terminal_failure_and_rebinds_without_process_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real RC2 gap: helper escalation must terminate at the outer lifecycle owner."""

    config = _config()
    dead_tab = _DeadPlatformTab()
    replacement = _LiveReplacementTab()
    driver = _Driver(replacement)
    manager = BrowserSessionManager(config)
    manager.target_probe_timeout_seconds = 0.1
    recover_calls: list[dict[str, object]] = []
    original_recover = manager.recover

    async def tracked_recover(level, **kwargs):
        recover_calls.append({"level": level, **kwargs})
        return await original_recover(level, **kwargs)

    manager.recover = tracked_recover  # type: ignore[method-assign]

    async def bootstrap(*_args, **_kwargs):
        manager.attach(driver, dead_tab)
        return BrowserBootstrapResult(driver=driver, tab=dead_tab)

    quit_checks = 0

    async def quit_after_recovery(_config_dict):
        nonlocal quit_checks
        quit_checks += 1
        return quit_checks >= 2

    async def current_url(_tab, _config_dict, **_kwargs):
        return LOGIN_URL, False

    async def platform_main(tab, *_args, **_kwargs):
        await runtime.tixcraft_platform.nodriver_tixcraft_home_close_window(tab)
        return False

    async def no_pause(_config_dict):
        return False

    async def no_gate(*_args, **_kwargs):
        return False

    async def no_cloudflare(*_args, **_kwargs):
        return False

    async def no_reload(config_dict, config_mtime, _config_path):
        return config_dict, config_mtime

    monkeypatch.setattr(runtime, "get_config_dict", lambda _args: config)
    monkeypatch.setattr(runtime, "create_browser_session_manager", lambda *_args: manager)
    monkeypatch.setattr(runtime, "bootstrap_owned_browser", bootstrap)
    monkeypatch.setattr(runtime, "check_and_handle_quit", quit_after_recovery)
    monkeypatch.setattr(runtime, "check_and_handle_pause", no_pause)
    monkeypatch.setattr(runtime, "check_refresh_datetime_gate", no_gate)
    monkeypatch.setattr(runtime, "detect_cloudflare_challenge", no_cloudflare)
    monkeypatch.setattr(runtime, "nodriver_current_url", current_url)
    monkeypatch.setattr(runtime, "nodriver_tixcraft_main", platform_main)
    monkeypatch.setattr(runtime, "reload_config", no_reload)
    monkeypatch.setattr(runtime, "write_last_url_to_file", lambda _url: None)
    monkeypatch.setattr(runtime.runtime_health, "touch_heartbeat", lambda *_args: None)
    monkeypatch.setattr(runtime.runtime_health, "runtime_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime.util, "force_remove_file", lambda *_args: None)

    args = SimpleNamespace(instance="", input=None, mcp_debug=False)

    # This call reaches main -> _run_main -> real run_runtime_iteration ->
    # real TixCraft cookie helper -> terminal classifier.  Unmodified RC2 lets
    # the ConnectionClosedError escape this boundary and fails here.
    await runtime.main(args)

    assert dead_tab.query_calls == 1
    assert replacement.proof_calls == 1
    assert driver.update_calls == 1
    assert driver.stop_calls == 1
    assert recover_calls == [
        {
            "level": RecoveryLevel.TRANSPORT_REBIND,
            "target_url": LOGIN_URL,
            "platform_key": "tixcraft",
            "allow_restart": False,
        }
    ]


@pytest.mark.asyncio
async def test_terminal_owner_rethrows_non_browser_programming_error() -> None:
    session = _RecoverySession([])
    context = _context(LOGIN_URL, PageClass.UNKNOWN, session)

    with pytest.raises(ValueError, match="production bug"):
        await runtime._handle_terminal_iteration_failure(
            context,
            ValueError("production bug"),
        )

    assert session.recover_calls == []


@pytest.mark.asyncio
async def test_manual_browser_close_stops_without_recovery_or_restart() -> None:
    session = _RecoverySession([], exit_state=BrowserExitState.CLEAN_EXIT)
    context = _context(
        "https://tixcraft.com/ticket/area/26_khalid/22908",
        PageClass.AREA,
        session,
    )

    result = await runtime._handle_terminal_iteration_failure(
        context,
        ConnectionClosedError("no close frame received or sent"),
    )

    assert result.action == "stop"
    assert result.reason == "manual_target_close"
    assert session.recover_calls == []


@pytest.mark.asyncio
async def test_safe_area_browser_crash_escalates_rebind_then_full_bootstrap() -> None:
    session = _RecoverySession(
        [
            (False, "transport_probe_failed", False),
            (True, "browser_restarted", True),
        ],
        exit_state=BrowserExitState.CRASHED,
    )
    area_url = "https://tixcraft.com/ticket/area/26_khalid/22908"
    context = _context(area_url, PageClass.AREA, session)

    result = await runtime._handle_terminal_iteration_failure(
        context,
        ConnectionClosedError("no close frame received or sent"),
    )

    assert result.action == "continue"
    assert result.reason == "browser_restarted"
    assert [call["level"] for call in session.recover_calls] == [
        RecoveryLevel.TRANSPORT_REBIND,
        RecoveryLevel.SAFE_RESTART,
    ]
    assert session.recover_calls[0]["allow_restart"] is False
    assert session.recover_calls[1] == {
        "level": RecoveryLevel.SAFE_RESTART,
        "target_url": area_url,
        "platform_key": "tixcraft",
        "allow_restart": True,
    }
    assert context.pending_restart_target_url == area_url


@pytest.mark.asyncio
async def test_submit_in_flight_disconnect_never_restarts_or_releases_owner() -> None:
    session = _RecoverySession([(False, "transport_probe_failed", False)])
    ticket_url = "https://tixcraft.com/ticket/ticket/26_khalid/22908/1/54"
    context = _context(ticket_url, PageClass.TICKET, session)
    token = platform_engine.claim_submit(
        context.tab,
        "tixcraft",
        owner="final-layer-submit-owner",
    )
    assert token
    before = platform_engine.current_attempt(context.tab, "tixcraft")
    assert before is not None

    result = await runtime._handle_terminal_iteration_failure(
        context,
        ConnectionClosedError("no close frame received or sent"),
    )

    after = platform_engine.current_attempt(context.tab, "tixcraft")
    assert result.action == "monitor"
    assert [call["level"] for call in session.recover_calls] == [
        RecoveryLevel.TRANSPORT_REBIND
    ]
    assert after is not None
    assert after.attempt_id == before.attempt_id
    assert after.state is AttemptState.SUBMIT_IN_FLIGHT
    assert after.submit_token == token
    assert (
        platform_engine.claim_submit(
            context.tab,
            "tixcraft",
            owner="forbidden-duplicate-owner",
        )
        is None
    )


@pytest.mark.asyncio
async def test_submit_unknown_disconnect_is_immediate_zero_recovery_fail_closed() -> None:
    session = _RecoverySession([])
    ticket_url = "https://tixcraft.com/ticket/ticket/26_khalid/22908/1/54"
    context = _context(ticket_url, PageClass.TICKET, session)
    token = platform_engine.claim_submit(
        context.tab,
        "tixcraft",
        owner="final-layer-unknown-owner",
    )
    attempt = platform_engine.current_attempt(context.tab, "tixcraft")
    assert token and attempt is not None
    unknown = platform_engine.mark_submit_outcome_unknown_if_owned(
        context.tab,
        "tixcraft",
        attempt_id=attempt.attempt_id,
        token=token,
        reason="final_layer_fault_injection",
    )
    assert unknown is not None

    result = await runtime._handle_terminal_iteration_failure(
        context,
        ConnectionClosedError("no close frame received or sent"),
    )

    assert result.action == "fail_closed"
    assert result.reason == "protected_submit_outcome"
    assert session.recover_calls == []
    assert (
        platform_engine.current_attempt(context.tab, "tixcraft").state
        is AttemptState.SUBMIT_OUTCOME_UNKNOWN
    )


@pytest.mark.asyncio
async def test_failed_terminal_recovery_is_bounded_then_controlled_fail_closed() -> None:
    health = RuntimeHealthSupervisor(
        max_recovery_attempts=3,
        recovery_cooldown_seconds=0.0,
    )
    session = _RecoverySession(
        [(False, "transport_probe_failed", False)] * 3,
    )
    context = _context(LOGIN_URL, PageClass.UNKNOWN, session, health=health)

    results = [
        await runtime._handle_terminal_iteration_failure(
            context,
            ConnectionClosedError("no close frame received or sent"),
        )
        for _ in range(4)
    ]

    assert [result.action for result in results] == [
        "monitor",
        "monitor",
        "fail_closed",
        "fail_closed",
    ]
    assert len(session.recover_calls) == 3
    assert results[-1].reason == "terminal_recovery_attempts_exhausted"


@pytest.mark.asyncio
async def test_execution_context_loss_uses_reacquire_without_restart() -> None:
    session = _RecoverySession([(True, "target_reacquired", False)])
    area_url = "https://tixcraft.com/ticket/area/26_khalid/22908"
    context = _context(area_url, PageClass.AREA, session)

    result = await runtime._handle_terminal_iteration_failure(
        context,
        RuntimeError("execution context was destroyed during navigation"),
    )

    assert result.action == "continue"
    assert [call["level"] for call in session.recover_calls] == [
        RecoveryLevel.REACQUIRE
    ]
    assert session.recover_calls[0]["allow_restart"] is False


@pytest.mark.asyncio
async def test_terminal_failure_during_recovery_is_owned_and_does_not_escape() -> None:
    session = _RecoverySession(
        [],
        recovery_error=ConnectionClosedError("rebind transport also closed"),
    )
    context = _context(LOGIN_URL, PageClass.UNKNOWN, session)

    result = await runtime._handle_terminal_iteration_failure(
        context,
        ConnectionClosedError("platform transport closed"),
    )

    assert result.action == "monitor"
    assert result.reason == "terminal_recovery_transport_failed"
    assert len(session.recover_calls) == 1
