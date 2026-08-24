from __future__ import annotations

import pytest

import settings
from navigation_context import canonicalize_target_url
from platform_adapters import adapter_for_key
from platform_engine import platform_engine
from platforms import ticketplus


class _Tab:
    pass


def test_target_canonicalization_removes_noise_but_preserves_event_identity() -> None:
    first = canonicalize_target_url(
        "HTTPS://TicketPlus.com.tw/activity/ABC/?event=one&utm_source=x#intro"
    )
    equivalent = canonicalize_target_url(
        "https://ticketplus.com.tw/activity/ABC?utm_medium=y&event=one"
    )
    different = canonicalize_target_url(
        "https://ticketplus.com.tw/activity/ABC?event=two"
    )

    assert first == equivalent
    assert first != different
    assert "#" not in first
    assert "utm_" not in first


def test_platform_engine_captures_configured_login_target_per_tab() -> None:
    adapter = adapter_for_key("ticketplus")
    assert adapter is not None
    tabs = (_Tab(), _Tab())
    configs = []
    for event in ("event-a", "event-b"):
        config = settings.get_default_config()
        config["homepage"] = f"https://ticketplus.com.tw/activity/{event}"
        configs.append(config)

    for tab, config in zip(tabs, configs, strict=True):
        platform_engine.clear_tab(tab)
        platform_engine.before_dispatch(tab, "https://ticketplus.com.tw/", config)

    contexts = [platform_engine.target_context_for(tab, adapter) for tab in tabs]
    assert all(context is not None for context in contexts)
    assert contexts[0].intent.normalized_target_url.endswith("/activity/event-a")
    assert contexts[1].intent.normalized_target_url.endswith("/activity/event-b")
    assert contexts[0].intent.tab_identity != contexts[1].intent.tab_identity


@pytest.mark.asyncio
async def test_ticketplus_restore_is_bounded_and_uses_captured_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tab = _Tab()
    config = settings.get_default_config()
    config["homepage"] = "https://ticketplus.com.tw/activity/original"
    adapter = adapter_for_key("ticketplus")
    assert adapter is not None
    platform_engine.clear_tab(tab)
    platform_engine.before_dispatch(tab, "https://ticketplus.com.tw/", config)
    calls: list[str] = []

    async def failed_get(_tab, target, _config, **_kwargs):
        calls.append(target)
        return False

    monkeypatch.setattr(ticketplus, "guarded_get", failed_get)
    debug = type("Debug", (), {"log": lambda self, _message: None})()
    clock = {"now": 100.0}
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: clock["now"])

    for _ in range(6):
        await ticketplus._ticketplus_restore_navigation_target(tab, config, debug)
        clock["now"] += ticketplus.CONST_TICKETPLUS_TARGET_RESTORE_RETRY_SECONDS

    assert calls == [config["homepage"]] * ticketplus.CONST_TICKETPLUS_TARGET_RESTORE_MAX_ATTEMPTS
    context = platform_engine.target_context_for(tab, adapter)
    assert context is not None
    assert context.restore_attempts == ticketplus.CONST_TICKETPLUS_TARGET_RESTORE_MAX_ATTEMPTS
    assert context.restored_at is None


def test_ticketplus_session_expiry_rearms_login_attempt() -> None:
    ticketplus._state.clear()
    ticketplus._ensure_ticketplus_state_defaults()
    ticketplus._state.update(
        {
            "authenticated": True,
            "signin_form_filled": True,
            "login_attempt_started_at": 10.0,
            "login_generation": 4,
        }
    )

    ticketplus._ticketplus_update_login_lifecycle(False, now=20.0)

    assert ticketplus._state["authenticated"] is False
    assert ticketplus._state["signin_form_filled"] is False
    assert ticketplus._state["login_generation"] == 5


@pytest.mark.asyncio
async def test_ticketplus_terminal_cookie_error_is_not_swallowed() -> None:
    class ConnectionClosedError(RuntimeError):
        pass

    class _Cookies:
        async def get_all(self):
            raise ConnectionClosedError("WebSocket connection closed")

    tab = type(
        "Tab",
        (),
        {"browser": type("Browser", (), {"cookies": _Cookies()})()},
    )()

    with pytest.raises(ConnectionClosedError):
        await ticketplus.nodriver_ticketplus_is_signin(tab)
