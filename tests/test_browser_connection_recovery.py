from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

import runtime_health

_REAL_PLATFORM = sys.platform
try:
    sys.platform = "test"
    import nodriver_tixcraft
finally:
    sys.platform = _REAL_PLATFORM


def _args() -> SimpleNamespace:
    return SimpleNamespace(instance=None, input=None)


def test_safe_browser_recovery_url_requires_same_origin_and_safe_page() -> None:
    homepage = "https://tixcraft.com/activity/detail/26_event"

    assert nodriver_tixcraft._is_safe_browser_recovery_url(
        "https://tixcraft.com/ticket/area/26_event/123",
        homepage,
    )
    assert not nodriver_tixcraft._is_safe_browser_recovery_url(
        "https://tixcraft.com/ticket/ticket/26_event/123/1",
        homepage,
    )
    assert not nodriver_tixcraft._is_safe_browser_recovery_url(
        "https://queue-it.net/queue",
        homepage,
    )
    assert not nodriver_tixcraft._is_safe_browser_recovery_url(
        "https://example.com/ticket/area/26_event/123",
        homepage,
    )


def test_empty_url_watchdog_requires_previous_url_and_full_grace_period() -> None:
    grace = nodriver_tixcraft.BROWSER_CONNECTION_EMPTY_URL_GRACE_SECONDS

    assert not nodriver_tixcraft._has_empty_url_watchdog_expired(
        "",
        10.0,
        10.0 + grace,
    )
    assert not nodriver_tixcraft._has_empty_url_watchdog_expired(
        "https://tixcraft.com/ticket/area/26_event/123",
        10.0,
        10.0 + grace - 0.001,
    )
    assert nodriver_tixcraft._has_empty_url_watchdog_expired(
        "https://tixcraft.com/ticket/area/26_event/123",
        10.0,
        10.0 + grace,
    )


@pytest.mark.asyncio
async def test_recovery_navigation_uses_exact_url_without_mutating_config() -> None:
    area_url = "https://tixcraft.com/ticket/area/26_event/123"
    config = {"homepage": "https://tixcraft.com/activity/detail/26_event"}

    class Driver:
        def __init__(self) -> None:
            self.requested_url = ""

        async def get(self, url: str):
            self.requested_url = url
            return object()

    driver = Driver()

    assert (
        await nodriver_tixcraft._navigate_browser_start(
            driver,
            config,
            area_url,
        )
        is not None
    )
    assert driver.requested_url == area_url
    assert config["homepage"] == "https://tixcraft.com/activity/detail/26_event"


@pytest.mark.asyncio
async def test_zendriver_listener_failure_sets_recovery_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = []
    monkeypatch.setattr(
        runtime_health,
        "runtime_log",
        lambda message, *_args, **_kwargs: recorded.append(message),
    )
    event = asyncio.Event()

    class Listener:
        async def listener_loop(self) -> None:
            return None

    class FakeTask:
        def __init__(self, coro) -> None:
            self.coro = coro

        def get_coro(self):
            return self.coro

    coro = Listener().listener_loop()
    try:
        loop = asyncio.get_running_loop()
        handler = runtime_health.create_browser_loop_exception_handler(
            {},
            event,
        )
        handler(
            loop,
            {
                "message": "Task exception was never retrieved",
                "exception": asyncio.InvalidStateError("invalid state"),
                "future": FakeTask(coro),
            },
        )
    finally:
        coro.close()

    assert event.is_set()
    assert recorded == ["[BROWSER] listener_failed"]


@pytest.mark.asyncio
async def test_main_restarts_safe_page_and_resumes_last_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    area_url = "https://tixcraft.com/ticket/area/26_event/123"

    async def fake_run_once(_args, resources):
        calls.append(dict(resources))
        if len(calls) == 1:
            resources["last_url"] = area_url
            resources["config_dict"] = {"homepage": area_url}
            raise runtime_health.BrowserConnectionLost(
                "current_url",
                "ConnectionClosedError",
            )
        return "recovered"

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(nodriver_tixcraft, "_run_main_once", fake_run_once)
    monkeypatch.setattr(nodriver_tixcraft.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(runtime_health, "runtime_log", lambda *_a, **_k: None)

    assert await nodriver_tixcraft.main(_args()) == "recovered"
    assert len(calls) == 2
    assert calls[1]["resume_url"] == area_url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://tixcraft.com/ticket/ticket/26_event/123/1",
        "https://tixcraft.com/ticket/order",
        "https://tixcraft.com/ticket/checkout",
        "https://queue-it.net/queue",
    ],
)
async def test_main_never_restarts_protected_or_queue_page(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    calls = 0

    async def fake_run_once(_args, resources):
        nonlocal calls
        calls += 1
        resources["last_url"] = url
        resources["config_dict"] = {"homepage": url}
        raise runtime_health.BrowserConnectionLost(
            "current_url",
            "ConnectionClosedError",
        )

    monkeypatch.setattr(nodriver_tixcraft, "_run_main_once", fake_run_once)
    monkeypatch.setattr(runtime_health, "runtime_log", lambda *_a, **_k: None)

    with pytest.raises(runtime_health.BrowserConnectionLost):
        await nodriver_tixcraft.main(_args())

    assert calls == 1


@pytest.mark.asyncio
async def test_main_never_restarts_cross_origin_area_shaped_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_run_once(_args, resources):
        nonlocal calls
        calls += 1
        resources["last_url"] = "https://example.com/ticket/area/26_event/123"
        resources["configured_homepage"] = (
            "https://tixcraft.com/activity/detail/26_event"
        )
        resources["config_dict"] = {
            "homepage": "https://tixcraft.com/activity/detail/26_event"
        }
        raise runtime_health.BrowserConnectionLost(
            "current_url",
            "ConnectionClosedError",
        )

    monkeypatch.setattr(nodriver_tixcraft, "_run_main_once", fake_run_once)
    monkeypatch.setattr(runtime_health, "runtime_log", lambda *_a, **_k: None)

    with pytest.raises(runtime_health.BrowserConnectionLost):
        await nodriver_tixcraft.main(_args())

    assert calls == 1


@pytest.mark.asyncio
async def test_main_stops_after_bounded_restart_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    area_url = "https://tixcraft.com/ticket/area/26_event/123"

    async def fake_run_once(_args, resources):
        nonlocal calls
        calls += 1
        resources["last_url"] = area_url
        resources["config_dict"] = {"homepage": area_url}
        raise runtime_health.BrowserConnectionLost(
            "current_url",
            "ConnectionClosedError",
        )

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(nodriver_tixcraft, "_run_main_once", fake_run_once)
    monkeypatch.setattr(nodriver_tixcraft.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(runtime_health, "runtime_log", lambda *_a, **_k: None)
    monkeypatch.setattr(
        nodriver_tixcraft,
        "BROWSER_CONNECTION_MAX_RESTARTS",
        2,
    )

    with pytest.raises(runtime_health.BrowserConnectionLost):
        await nodriver_tixcraft.main(_args())

    assert calls == 3
