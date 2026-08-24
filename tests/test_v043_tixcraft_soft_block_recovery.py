from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from leak_watch import LeakWatchScheduler
from platforms import tixcraft
from run_modes import RunMode


# LeakWatchScheduler recovery-landing contract


def _leak_watch_config(interval: float) -> dict:
    return {
        "advanced": {
            "run_mode": RunMode.LEAK_WATCH.value,
            "leak_refresh_interval_seconds": interval,
        }
    }


def test_leak_watch_recovery_landing_clears_pending_and_defers_reload() -> None:
    config = _leak_watch_config(6.5)
    area_url = "https://tixcraft.com/ticket/area/event/game"
    scheduler = LeakWatchScheduler(
        reload_pending=True,
        dom_scan_pending=True,
        area_click_pending=True,
        ticket_form_pending=True,
        submit_pending=True,
        next_cycle_at=0.0,
        last_clicked_url=area_url,
    )

    scheduler.mark_recovery_landed(config, now=100.0)

    assert scheduler.reload_pending is False
    assert scheduler.dom_scan_pending is False
    assert scheduler.area_click_pending is False
    assert scheduler.ticket_form_pending is False
    assert scheduler.submit_pending is False
    assert scheduler.last_clicked_url == ""
    assert scheduler.next_cycle_at == 106.5
    assert scheduler.can_reload(config, area_url, now=100.0) == (False, "interval_wait")
    assert scheduler.can_reload(config, area_url, now=106.499) == (False, "interval_wait")
    assert scheduler.can_reload(config, area_url, now=106.5) == (True, "ready")
    assert scheduler.history[-1] == "recovery_landed"


def test_leak_watch_recovery_landing_guards_explicit_zero_interval() -> None:
    config = _leak_watch_config(0.0)
    area_url = "https://tixcraft.com/ticket/area/event/game"
    scheduler = LeakWatchScheduler()

    scheduler.mark_recovery_landed(config, now=250.0)

    assert scheduler.next_cycle_at == 251.0
    assert scheduler.can_reload(config, area_url, now=250.999) == (
        False,
        "interval_wait",
    )
    assert scheduler.can_reload(config, area_url, now=251.0) == (True, "ready")


def test_reset_for_recovery_retains_pre_landing_retry_behavior() -> None:
    config = _leak_watch_config(6.5)
    area_url = "https://tixcraft.com/ticket/area/event/game"
    scheduler = LeakWatchScheduler(
        reload_pending=True,
        dom_scan_pending=True,
        next_cycle_at=999.0,
    )

    scheduler.reset_for_recovery()

    assert scheduler.next_cycle_at == 0.0
    assert scheduler.can_reload(config, area_url, now=100.0) == (True, "ready")
    assert scheduler.history[-1] == "reset_for_recovery"


AREA_URL = "https://tixcraft.com/ticket/area/26_plave/5300"


def _runtime_config(run_mode: str, delay: int = 7) -> dict:
    return {
        "homepage": "https://tixcraft.com/activity/detail/26_plave",
        "ticket_number": 1,
        "tixcraft": {"allow_less_tickets": False},
        "area_auto_select": {
            "enable": True,
            "area_keyword": '"Y1B-1區"',
            "mode": "from top to bottom",
        },
        "area_auto_fallback": False,
        "keyword_exclude": "",
        "advanced": {
            "run_mode": run_mode,
            "auto_reload_page_interval": 5,
            "leak_refresh_interval_seconds": 5,
            "tixcraft_soft_block_delay": str(delay),
        },
    }


def test_area_url_validation_priority_and_no_synthetic_path() -> None:
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "last_valid_area_url": AREA_URL,
            "recent_area_route_url": "https://tixcraft.com/ticket/area/recent/game",
            "current_event_id": "must-not-be-concatenated",
            "current_game_id": "must-not-be-concatenated",
        }
    )
    config = _runtime_config("onsale")

    assert tixcraft._get_tixcraft_soft_block_recovery_url(
        config,
        "https://tixcraft.com/ticket/area/current/game?session=redacted",
        "https://tixcraft.com/ticket/area/original/game",
    ) == AREA_URL

    tixcraft._state["last_valid_area_url"] = ""
    assert tixcraft._get_tixcraft_soft_block_recovery_url(
        config,
        "https://tixcraft.com/ticket/area/current/game",
        "https://evil.example/ticket/area/original/game",
    ) == "https://tixcraft.com/ticket/area/recent/game"

    tixcraft._state["recent_area_route_url"] = ""
    assert tixcraft._get_tixcraft_soft_block_recovery_url(
        config,
        "https://tixcraft.com/activity/detail/26_plave",
        "",
    ) == ""
    assert tixcraft._normalize_tixcraft_area_url("about:blank") == ""
    assert tixcraft._normalize_tixcraft_area_url(
        "https://tixcraft.com.evil.example/ticket/area/event/game"
    ) == ""
    assert tixcraft._normalize_tixcraft_area_url(
        "HTTPS://TIXCRAFT.COM/ticket/area/event/game/"
    ) == "https://tixcraft.com/ticket/area/event/game"
    assert tixcraft._normalize_tixcraft_area_url(
        "https://www.ticketmaster.sg/ticket/area/event/game/"
    ) == "https://www.ticketmaster.sg/ticket/area/event/game"
    assert tixcraft._get_tixcraft_controlled_routing_url(config) == (
        "https://tixcraft.com/activity/detail/26_plave"
    )


def test_blank_page_requires_stable_grace_and_normal_page_resets_state() -> None:
    tixcraft._state.clear()
    blank = {
        "readyState": "complete",
        "hasBody": True,
        "bodyText": "",
        "elementCount": 0,
        "hasKnownContent": False,
    }
    normal = {
        "readyState": "complete",
        "hasBody": True,
        "bodyText": "",
        "elementCount": 1,
        "hasKnownContent": True,
    }
    assert not tixcraft._update_tixcraft_blank_page_state(
        AREA_URL,
        blank,
        now=10.0,
        grace_seconds=1.5,
    )
    assert not tixcraft._update_tixcraft_blank_page_state(
        AREA_URL,
        blank,
        now=11.49,
        grace_seconds=1.5,
    )
    assert tixcraft._update_tixcraft_blank_page_state(
        AREA_URL,
        blank,
        now=11.5,
        grace_seconds=1.5,
    )
    assert not tixcraft._update_tixcraft_blank_page_state(
        AREA_URL,
        normal,
        now=12.0,
        grace_seconds=1.5,
    )
    assert tixcraft._state["soft_block_blank_since"] == 0.0
    assert not tixcraft._update_tixcraft_blank_page_state(
        "https://example.com/",
        blank,
        now=20.0,
        grace_seconds=0,
    )


@pytest.mark.asyncio
async def test_full_viewport_white_overlay_is_stable_blank_even_with_large_dom(
    monkeypatch,
) -> None:
    tixcraft._state.clear()
    clock = {"now": 20.0}
    overlay = {
        "blocked": False,
        "readyState": "complete",
        "hasBody": True,
        "bodyText": "normal page content remains behind the overlay",
        "elementCount": 800,
        "hasKnownContent": True,
        "whiteOverlay": True,
        "knownOrderProcessing": False,
    }

    async def read_overlay(*_args, **_kwargs):
        return overlay

    monkeypatch.setattr(tixcraft, "_read_tixcraft_page_health", read_overlay)
    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])

    first_detection = await tixcraft._detect_tixcraft_soft_block(
        object(),
        AREA_URL,
        {},
    )
    assert first_detection["blocked"] is False
    assert first_detection["inconclusive"] is True
    clock["now"] += tixcraft._TIXCRAFT_BLANK_PAGE_GRACE_SECONDS
    detected = await tixcraft._detect_tixcraft_soft_block(object(), AREA_URL, {})
    assert detected["blocked"] is False
    assert detected["inconclusive"] is True
    assert detected["kind"] == "stable_blank"


def test_legitimate_order_processing_overlay_is_not_a_blank_page() -> None:
    snapshot = {
        "readyState": "complete",
        "hasBody": True,
        "bodyText": "",
        "elementCount": 800,
        "hasKnownContent": True,
        "whiteOverlay": True,
        "knownOrderProcessing": True,
    }
    assert not tixcraft._is_tixcraft_blank_page_snapshot(snapshot)


def test_non_marker_error_html_is_not_a_confirmed_recovery_landing() -> None:
    snapshot = {
        "blocked": False,
        "readyState": "complete",
        "hasBody": True,
        "bodyText": "Access denied. Reference 12345",
        "title": "Error",
        "elementCount": 80,
        "hasKnownContent": False,
        "whiteOverlay": False,
        "knownOrderProcessing": False,
    }

    assert not tixcraft._is_tixcraft_recovery_health_confirmed(snapshot)


def test_area_recovery_rejects_stale_ticket_document_marker() -> None:
    snapshot = {
        "blocked": False,
        "readyState": "complete",
        "hasBody": True,
        "bodyText": "ticket form from the previous document",
        "title": "",
        "elementCount": 30,
        "hasKnownContent": True,
        "knownAreaContent": False,
        "knownActivityContent": False,
        "knownTicketContent": True,
        "knownOrderContent": False,
        "whiteOverlay": False,
        "knownOrderProcessing": False,
    }

    assert tixcraft._is_tixcraft_recovery_health_confirmed(snapshot)
    assert not tixcraft._is_tixcraft_recovery_health_confirmed(
        snapshot,
        tixcraft.PageClass.AREA,
    )


def test_health_probe_timeout_requires_consecutive_failures_and_grace() -> None:
    tixcraft._state.clear()
    assert not tixcraft._update_tixcraft_probe_failure_state(
        AREA_URL,
        True,
        now=10.0,
        grace_seconds=1.5,
    )
    assert not tixcraft._update_tixcraft_probe_failure_state(
        AREA_URL,
        True,
        now=11.49,
        grace_seconds=1.5,
    )
    assert tixcraft._update_tixcraft_probe_failure_state(
        AREA_URL,
        True,
        now=11.5,
        grace_seconds=1.5,
    )
    assert not tixcraft._update_tixcraft_probe_failure_state(
        AREA_URL,
        False,
        now=12.0,
        grace_seconds=1.5,
    )
    assert tixcraft._state["soft_block_probe_failure_count"] == 0


@pytest.mark.asyncio
async def test_probe_failure_sequence_is_reset_after_leaving_tixcraft_scope(
    monkeypatch,
) -> None:
    tixcraft._state.clear()
    clock = {"now": 10.0}

    async def failed_probe(*_args, **_kwargs):
        return {"probeFailed": True}

    monkeypatch.setattr(tixcraft, "_read_tixcraft_page_health", failed_probe)
    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])

    first = await tixcraft._detect_tixcraft_soft_block(object(), AREA_URL, {})
    assert first["inconclusive"] is True
    assert tixcraft._state["soft_block_probe_failure_count"] == 1

    clock["now"] = 20.0
    outside = await tixcraft._detect_tixcraft_soft_block(
        object(),
        "https://example.com/",
        {},
    )
    assert outside == {"blocked": False, "health_confirmed": True}
    assert tixcraft._state["soft_block_probe_failure_count"] == 0

    clock["now"] = 30.0
    returned = await tixcraft._detect_tixcraft_soft_block(
        object(),
        AREA_URL,
        {},
    )
    assert returned["blocked"] is False
    assert returned["inconclusive"] is True
    assert tixcraft._state["soft_block_probe_failure_count"] == 1


def test_soft_block_grace_defaults_never_read_wall_clock(monkeypatch) -> None:
    clock = {"now": 100.0}
    blank = {
        "readyState": "complete",
        "hasBody": True,
        "bodyText": "",
        "elementCount": 0,
        "hasKnownContent": False,
    }

    def wall_clock_must_not_be_used() -> float:
        raise AssertionError("soft-block grace must use monotonic time")

    monkeypatch.setattr(tixcraft.time, "time", wall_clock_must_not_be_used)
    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])

    tixcraft._state.clear()
    assert not tixcraft._update_tixcraft_blank_page_state(
        AREA_URL,
        blank,
        grace_seconds=1.5,
    )
    clock["now"] = 101.5
    assert tixcraft._update_tixcraft_blank_page_state(
        AREA_URL,
        blank,
        grace_seconds=1.5,
    )

    tixcraft._state.clear()
    clock["now"] = 200.0
    assert not tixcraft._update_tixcraft_probe_failure_state(
        AREA_URL,
        True,
        grace_seconds=1.5,
    )
    clock["now"] = 201.5
    assert tixcraft._update_tixcraft_probe_failure_state(
        AREA_URL,
        True,
        grace_seconds=1.5,
    )


@pytest.mark.asyncio
async def test_soft_block_backoff_deadline_uses_monotonic_clock(monkeypatch) -> None:
    config = _runtime_config("leak_watch", delay=7)
    clock = {"now": 300.0}
    wait_calls: list[int] = []
    backoff_calls: list[float] = []
    tab = _RecoveryTab()
    tab_state = tixcraft._state_for_tab(tab)
    tab_state.clear()
    tab_state.update(
        {
            "last_valid_area_url": AREA_URL,
            "recent_area_route_url": AREA_URL,
            "soft_block_recovery_in_progress": False,
            "leak_scheduler": LeakWatchScheduler(),
        }
    )

    def wall_clock_must_not_be_used() -> float:
        raise AssertionError("soft-block backoff must use monotonic time")

    async def stop_during_backoff(*_args, **_kwargs) -> str:
        backoff_calls.append(_args[0])
        return "stop"

    async def record_remaining_wait(_tab, seconds, _config) -> None:
        wait_calls.append(seconds)

    monkeypatch.setattr(tixcraft.time, "time", wall_clock_must_not_be_used)
    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "sleep_with_heartbeat",
        stop_during_backoff,
    )
    monkeypatch.setattr(
        tixcraft,
        "sleep_with_pause_check",
        record_remaining_wait,
    )

    with tixcraft._bind_tixcraft_tab_state(tab):
        assert await tixcraft._handle_tixcraft_soft_block(
            tab,
            config,
            AREA_URL,
            {"kind": "stable_blank", "original_url": "", "client_ip": "redacted"},
        )
        assert tixcraft._state["ip_block_until"] == 307.0
        assert backoff_calls == [7]

        clock["now"] = 302.0
        assert await tixcraft.nodriver_ticketmaster_check_ip_block(
            tab,
            config,
            current_url=AREA_URL,
        )
        assert wait_calls == [5]

    async def blocked_again(*_args, **_kwargs):
        return {
            "blocked": True,
            "kind": "stable_blank",
            "original_url": "",
            "client_ip": "redacted",
        }

    async def navigate_to_area(_tab, url, *_args, **_kwargs):
        _tab.target.url = url
        return True

    clock["now"] = 307.0
    monkeypatch.setattr(tixcraft, "_detect_tixcraft_soft_block", blocked_again)
    monkeypatch.setattr(tixcraft.runtime_health, "guarded_get", navigate_to_area)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(
        tixcraft,
        "_read_tixcraft_page_health",
        _healthy_area_page_health,
    )
    with tixcraft._bind_tixcraft_tab_state(tab):
        assert await tixcraft.nodriver_ticketmaster_check_ip_block(
            tab,
            config,
            current_url=AREA_URL,
        )
        assert backoff_calls == [7]
        assert tixcraft._state["soft_block_recovery_scan_pending"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remaining", "expected_sleep"),
    [(0.1, 0.1), (4.9, 4.9), (8.0, 5.0)],
)
async def test_soft_block_wait_never_oversleeps_deadline(
    monkeypatch,
    remaining: float,
    expected_sleep: float,
) -> None:
    clock = 100.0
    sleeps: list[float] = []
    tab = _RecoveryTab()
    tab_state = tixcraft._state_for_tab(tab)
    tab_state.clear()
    tab_state["ip_block_until"] = clock + remaining

    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock)

    async def record_sleep(_tab, seconds, _config) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(tixcraft, "sleep_with_pause_check", record_sleep)

    assert await tixcraft.nodriver_ticketmaster_check_ip_block(
        tab,
        _runtime_config("leak_watch", delay=7),
        current_url=AREA_URL,
    )
    assert sleeps == [pytest.approx(expected_sleep)]


@pytest.mark.asyncio
async def test_inconclusive_recovery_probe_preserves_completed_backoff(
    monkeypatch,
) -> None:
    clock = {"now": 100.0}
    detections = iter(
        [
            {
                "blocked": False,
                "health_confirmed": False,
                "inconclusive": True,
            },
            {
                "blocked": True,
                "kind": "stable_blank",
                "original_url": "",
                "client_ip": "redacted",
            },
        ]
    )
    full_waits: list[float] = []
    tab = _RecoveryTab()
    tab_state = tixcraft._state_for_tab(tab)
    tab_state.clear()
    tab_state.update(
        {
            "last_valid_area_url": AREA_URL,
            "recent_area_route_url": AREA_URL,
            "soft_block_phase": "recovering",
            "soft_block_backoff_until": 99.0,
            "soft_block_recovery_retry_at": 0.0,
            "ip_block_until": 0.0,
            "soft_block_recovery_in_progress": False,
            "leak_scheduler": LeakWatchScheduler(),
        }
    )

    async def next_detection(*_args, **_kwargs):
        return next(detections)

    async def record_full_wait(seconds, *_args, **_kwargs):
        full_waits.append(seconds)
        return "done"

    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(tixcraft, "_detect_tixcraft_soft_block", next_detection)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "sleep_with_heartbeat",
        record_full_wait,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "guarded_get",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=False),
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=False),
    )

    config = _runtime_config("leak_watch", delay=7)
    assert await tixcraft.nodriver_ticketmaster_check_ip_block(
        tab,
        config,
        current_url=AREA_URL,
    )
    assert tab_state["soft_block_phase"] == "recovering"
    assert tab_state["soft_block_backoff_until"] == 99.0

    clock["now"] = tab_state["soft_block_recovery_retry_at"]
    assert await tixcraft.nodriver_ticketmaster_check_ip_block(
        tab,
        config,
        current_url=AREA_URL,
    )
    assert full_waits == []
    assert tab_state["soft_block_backoff_until"] == 99.0


@pytest.mark.asyncio
async def test_eps_and_text_markers_detect_but_non_tixcraft_fast_rejects(
    monkeypatch,
) -> None:
    calls: list[str] = []

    async def eps_evaluate(_tab, script, *_args, **_kwargs):
        calls.append(script)
        return json.dumps(
            {
                "blocked": True,
                "kind": "eps_js",
                "rr": AREA_URL,
                "client_ip": "redacted",
            }
        )

    monkeypatch.setattr(tixcraft.runtime_health, "evaluate_with_timeout", eps_evaluate)
    detected = await tixcraft._detect_tixcraft_soft_block(
        object(),
        AREA_URL,
        {},
    )
    assert detected["blocked"] is False
    detected = await tixcraft._detect_tixcraft_soft_block(
        object(),
        AREA_URL,
        {},
    )
    assert detected["blocked"] is True
    assert detected["kind"] == "eps_js"
    assert len(calls) == 2

    detected = await tixcraft._detect_tixcraft_soft_block(
        object(),
        "https://www.ticketmaster.sg/ticket/area/event/game",
        {},
    )
    assert detected["blocked"] is False
    detected = await tixcraft._detect_tixcraft_soft_block(
        object(),
        "https://www.ticketmaster.sg/ticket/area/event/game",
        {},
    )
    assert detected["blocked"] is True
    assert detected["kind"] == "eps_js"
    assert len(calls) == 4

    calls.clear()
    non_tixcraft = await tixcraft._detect_tixcraft_soft_block(
        object(),
        "about:blank",
        {},
    )
    assert non_tixcraft == {"blocked": False, "health_confirmed": True}
    assert calls == []

    async def text_evaluate(_tab, script, *_args, **_kwargs):
        return json.dumps(
            {
                "blocked": False,
                "readyState": "complete",
                "hasBody": True,
                "bodyText": "Your browsing activity has been paused",
                "title": "",
                "elementCount": 3,
                "hasKnownContent": False,
            }
        )

    monkeypatch.setattr(tixcraft.runtime_health, "evaluate_with_timeout", text_evaluate)
    detected = await tixcraft._detect_tixcraft_soft_block(object(), AREA_URL, {})
    assert detected["blocked"] is False
    detected = await tixcraft._detect_tixcraft_soft_block(object(), AREA_URL, {})
    assert detected["blocked"] is True
    assert detected["kind"] == "text_marker"


@pytest.mark.asyncio
async def test_repeated_page_health_timeout_becomes_soft_block(
    monkeypatch,
) -> None:
    tixcraft._state.clear()
    clock = iter([10.0, 11.5])

    async def timed_out_evaluate(_tab, _script, *_args, **kwargs):
        return kwargs.get("default")

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "evaluate_with_timeout",
        timed_out_evaluate,
    )
    update_probe_state = tixcraft._update_tixcraft_probe_failure_state

    def update_probe_state_at_test_time(url, probe_failed, *args, **kwargs):
        if probe_failed and kwargs.get("now") is None:
            kwargs["now"] = next(clock)
        return update_probe_state(url, probe_failed, *args, **kwargs)

    monkeypatch.setattr(
        tixcraft,
        "_update_tixcraft_probe_failure_state",
        update_probe_state_at_test_time,
    )

    first_detection = await tixcraft._detect_tixcraft_soft_block(
        object(),
        AREA_URL,
        {},
    )
    assert first_detection["blocked"] is False
    assert first_detection["inconclusive"] is True
    detected = await tixcraft._detect_tixcraft_soft_block(object(), AREA_URL, {})
    assert detected["blocked"] is False
    assert detected["inconclusive"] is True


class _RecoveryTab:
    def __init__(self, url: str = AREA_URL) -> None:
        self.target = SimpleNamespace(url=url)
        self.get_calls: list[str] = []
        self.reload_calls = 0

    async def get(self, url: str) -> None:
        self.get_calls.append(url)
        self.target.url = url

    async def reload(self) -> None:
        self.reload_calls += 1


async def _healthy_area_page_health(*_args, **_kwargs):
    return {
        "blocked": False,
        "readyState": "complete",
        "hasBody": True,
        "bodyText": "area content",
        "title": "",
        "elementCount": 30,
        "hasKnownContent": True,
        "knownAreaContent": True,
        "knownActivityContent": False,
        "knownTicketContent": False,
        "knownOrderContent": False,
        "whiteOverlay": False,
        "knownOrderProcessing": False,
    }


async def _healthy_activity_page_health(*_args, **_kwargs):
    return {
        "blocked": False,
        "readyState": "complete",
        "hasBody": True,
        "bodyText": "activity content",
        "title": "",
        "elementCount": 30,
        "hasKnownContent": True,
        "knownAreaContent": False,
        "knownActivityContent": True,
        "knownTicketContent": False,
        "knownOrderContent": False,
        "whiteOverlay": False,
        "knownOrderProcessing": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("run_mode", ["onsale", "leak_watch"])
async def test_soft_block_wait_has_no_requests_then_navigates_to_area_once(
    monkeypatch,
    run_mode: str,
) -> None:
    config = _runtime_config(run_mode, delay=7)
    tab = _RecoveryTab()
    sleeps: list[float] = []
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "last_valid_area_url": AREA_URL,
            "recent_area_route_url": AREA_URL,
            "soft_block_recovery_in_progress": False,
            "leak_scheduler": LeakWatchScheduler(),
        }
    )

    async def no_control_request(_config=None):
        return False

    async def fake_sleep(seconds, *_args, **_kwargs):
        assert tab.get_calls == []
        assert tab.reload_calls == 0
        sleeps.append(seconds)
        return "done"

    async def fake_get(_tab, url, *_args, **_kwargs):
        await _tab.get(url)
        # Real servers may canonicalize the same area route with a trailing slash.
        _tab.target.url = f"{url}/"
        return True

    async def ready(*_args, **_kwargs):
        return True

    monkeypatch.setattr(tixcraft, "check_and_handle_pause", no_control_request)
    monkeypatch.setattr(tixcraft, "check_and_handle_quit", no_control_request)
    monkeypatch.setattr(tixcraft.runtime_health, "sleep_with_heartbeat", fake_sleep)
    monkeypatch.setattr(tixcraft.runtime_health, "guarded_get", fake_get)
    monkeypatch.setattr(tixcraft.runtime_health, "wait_for_interactive_ready", ready)
    monkeypatch.setattr(
        tixcraft,
        "_read_tixcraft_page_health",
        _healthy_area_page_health,
    )
    monkeypatch.setattr(tixcraft.runtime_health, "runtime_log", lambda *_args, **_kwargs: None)

    assert await tixcraft._handle_tixcraft_soft_block(
        tab,
        config,
        AREA_URL,
        {"kind": "stable_blank", "original_url": "", "client_ip": "redacted"},
    )
    assert sleeps == [7]
    assert tab.get_calls == [AREA_URL]
    assert tab.reload_calls == 0
    assert tixcraft._state["soft_block_recovery_scan_pending"] is True
    assert tixcraft._state["tixcraft_area_reload_next_at"] > 0
    assert tixcraft._state["leak_scheduler"].next_cycle_at > 0


@pytest.mark.asyncio
async def test_soft_block_navigation_timeout_still_confirms_landed_area(
    monkeypatch,
) -> None:
    config = _runtime_config("leak_watch", delay=7)
    tab = _RecoveryTab("https://tixcraft.com/activity/game/26_plave")
    scheduler = LeakWatchScheduler()
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "last_valid_area_url": AREA_URL,
            "recent_area_route_url": AREA_URL,
            "soft_block_recovery_in_progress": False,
            "leak_scheduler": scheduler,
        }
    )

    async def navigation_timed_out_after_landing(_tab, url, *_args, **_kwargs):
        _tab.target.url = url
        return False

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "sleep_with_heartbeat",
        lambda *_args, **_kwargs: asyncio.sleep(0, result="done"),
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "guarded_get",
        navigation_timed_out_after_landing,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(
        tixcraft,
        "_read_tixcraft_page_health",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result={
                "blocked": False,
                "readyState": "complete",
                "hasBody": True,
                "bodyText": "area content",
                "title": "",
                "elementCount": 30,
                "hasKnownContent": True,
                "knownAreaContent": True,
                "knownActivityContent": False,
                "knownTicketContent": False,
                "knownOrderContent": False,
                "whiteOverlay": False,
            },
        ),
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )

    assert await tixcraft._handle_tixcraft_soft_block(
        tab,
        config,
        AREA_URL,
        {"kind": "stable_blank", "original_url": "", "client_ip": "redacted"},
    )
    assert tixcraft._state["soft_block_recovery_scan_pending"] is True
    assert scheduler.next_cycle_at > 0


@pytest.mark.asyncio
async def test_soft_block_timeout_without_new_document_is_not_marked_landed(
    monkeypatch,
) -> None:
    config = _runtime_config("leak_watch", delay=7)
    tab = _RecoveryTab(AREA_URL)
    scheduler = LeakWatchScheduler()
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "last_valid_area_url": AREA_URL,
            "recent_area_route_url": AREA_URL,
            "soft_block_recovery_in_progress": False,
            "leak_scheduler": scheduler,
        }
    )

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "sleep_with_heartbeat",
        lambda *_args, **_kwargs: asyncio.sleep(0, result="done"),
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "guarded_get",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=False),
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(
        tixcraft,
        "_read_tixcraft_page_health",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result={
                "blocked": True,
                "kind": "eps_js",
                "readyState": "complete",
                "hasBody": True,
                "hasKnownContent": False,
            },
        ),
    )

    assert await tixcraft._handle_tixcraft_soft_block(
        tab,
        config,
        AREA_URL,
        {"kind": "stable_blank", "original_url": "", "client_ip": "redacted"},
    )
    assert not tixcraft._state.get("soft_block_recovery_scan_pending", False)
    assert tixcraft._state["soft_block_phase"] == "recovering"
    assert tixcraft._state["soft_block_recovery_retry_at"] > 0
    assert scheduler.next_cycle_at == 0


@pytest.mark.asyncio
async def test_successful_navigation_is_not_a_healthy_recovery_without_dom_proof(
    monkeypatch,
) -> None:
    config = _runtime_config("leak_watch", delay=7)
    tab = _RecoveryTab(AREA_URL)
    scheduler = LeakWatchScheduler()
    health_calls = 0
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "last_valid_area_url": AREA_URL,
            "recent_area_route_url": AREA_URL,
            "soft_block_recovery_in_progress": False,
            "leak_scheduler": scheduler,
        }
    )

    async def navigation_reported_success(_tab, url, *_args, **_kwargs):
        _tab.target.url = url
        return True

    async def still_blocked(*_args, **_kwargs):
        nonlocal health_calls
        health_calls += 1
        return {
            "blocked": True,
            "kind": "eps_js",
            "readyState": "complete",
            "hasBody": True,
            "hasKnownContent": False,
            "whiteOverlay": True,
            "knownOrderProcessing": False,
        }

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "sleep_with_heartbeat",
        lambda *_args, **_kwargs: asyncio.sleep(0, result="done"),
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "guarded_get",
        navigation_reported_success,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(tixcraft, "_read_tixcraft_page_health", still_blocked)

    assert await tixcraft._handle_tixcraft_soft_block(
        tab,
        config,
        AREA_URL,
        {"kind": "stable_blank", "original_url": "", "client_ip": "redacted"},
    )
    assert health_calls == 1
    assert not tixcraft._state.get("soft_block_recovery_scan_pending", False)
    assert tixcraft._state["soft_block_phase"] == "recovering"
    assert tixcraft._state["soft_block_backoff_until"] > 0
    assert tixcraft._state["soft_block_recovery_retry_at"] > 0
    assert scheduler.next_cycle_at == 0


@pytest.mark.asyncio
async def test_soft_block_empty_landing_url_is_never_marked_recovered(
    monkeypatch,
) -> None:
    config = _runtime_config("leak_watch", delay=7)
    tab = _RecoveryTab(AREA_URL)
    scheduler = LeakWatchScheduler()
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "last_valid_area_url": AREA_URL,
            "recent_area_route_url": AREA_URL,
            "soft_block_recovery_in_progress": False,
            "leak_scheduler": scheduler,
        }
    )

    async def navigation_returned_without_a_target(_tab, _url, *_args, **_kwargs):
        _tab.target.url = ""
        return True

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "sleep_with_heartbeat",
        lambda *_args, **_kwargs: asyncio.sleep(0, result="done"),
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "guarded_get",
        navigation_returned_without_a_target,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )

    assert await tixcraft._handle_tixcraft_soft_block(
        tab,
        config,
        AREA_URL,
        {"kind": "stable_blank", "original_url": "", "client_ip": "redacted"},
    )
    assert not tixcraft._state.get("soft_block_recovery_scan_pending", False)
    assert scheduler.next_cycle_at == 0


@pytest.mark.asyncio
async def test_controlled_recovery_timeout_still_confirms_landed_area(
    monkeypatch,
) -> None:
    config = _runtime_config("leak_watch")
    tab = _RecoveryTab("https://tixcraft.com/activity/game/26_plave")
    scheduler = LeakWatchScheduler()
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "last_valid_area_url": AREA_URL,
            "recent_area_route_url": AREA_URL,
            "leak_scheduler": scheduler,
        }
    )

    async def navigation_timed_out_after_landing(_tab, url, *_args, **_kwargs):
        _tab.target.url = url
        return False

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "guarded_get",
        navigation_timed_out_after_landing,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        tixcraft,
        "_read_tixcraft_page_health",
        _healthy_area_page_health,
    )

    assert await tixcraft._recover_to_last_valid_area(
        tab,
        config,
        "offline_fixture",
    )
    assert tixcraft._state["soft_block_recovery_scan_pending"] is True
    assert scheduler.next_cycle_at > 0


@pytest.mark.asyncio
async def test_soft_block_single_flight_allows_only_one_recovery_navigation(
    monkeypatch,
) -> None:
    config = _runtime_config("onsale", delay=7)
    tab = _RecoveryTab()
    entered = asyncio.Event()
    release = asyncio.Event()
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "last_valid_area_url": AREA_URL,
            "soft_block_recovery_in_progress": False,
        }
    )

    async def no_control_request(_config=None):
        return False

    async def held_sleep(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return "done"

    async def fake_get(_tab, url, *_args, **_kwargs):
        await _tab.get(url)
        return True

    monkeypatch.setattr(tixcraft, "check_and_handle_pause", no_control_request)
    monkeypatch.setattr(tixcraft, "check_and_handle_quit", no_control_request)
    monkeypatch.setattr(tixcraft.runtime_health, "sleep_with_heartbeat", held_sleep)
    monkeypatch.setattr(tixcraft.runtime_health, "guarded_get", fake_get)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(
        tixcraft,
        "_read_tixcraft_page_health",
        _healthy_area_page_health,
    )
    monkeypatch.setattr(tixcraft.runtime_health, "runtime_log", lambda *_args, **_kwargs: None)

    first = asyncio.create_task(
        tixcraft._handle_tixcraft_soft_block(tab, config, AREA_URL)
    )
    await entered.wait()
    assert await tixcraft._handle_tixcraft_soft_block(tab, config, AREA_URL)
    release.set()
    assert await first
    assert tab.get_calls == [AREA_URL]


@pytest.mark.asyncio
async def test_no_observed_area_uses_controlled_activity_route_once(
    monkeypatch,
) -> None:
    config = _runtime_config("onsale", delay=7)
    tab = _RecoveryTab(config["homepage"])
    tixcraft._state.clear()
    tixcraft._state["soft_block_recovery_in_progress"] = False

    async def no_control_request(_config=None):
        return False

    async def fake_get(_tab, url, *_args, **_kwargs):
        await _tab.get(url)
        return True

    monkeypatch.setattr(tixcraft, "check_and_handle_pause", no_control_request)
    monkeypatch.setattr(tixcraft, "check_and_handle_quit", no_control_request)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "sleep_with_heartbeat",
        lambda *_args, **_kwargs: asyncio.sleep(0, result="done"),
    )
    monkeypatch.setattr(tixcraft.runtime_health, "guarded_get", fake_get)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(
        tixcraft,
        "_read_tixcraft_page_health",
        _healthy_activity_page_health,
    )

    assert await tixcraft._handle_tixcraft_soft_block(
        tab,
        config,
        config["homepage"],
        {"kind": "text_marker", "original_url": ""},
    )
    assert tab.get_calls == [config["homepage"]]
    assert not tixcraft._state.get("soft_block_recovery_scan_pending", False)
    assert tixcraft._state["soft_block_phase"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("guarded_result", "blocked"),
    [(True, True), (False, True), (False, False)],
)
async def test_controlled_activity_recovery_requires_healthy_landing(
    monkeypatch,
    guarded_result: bool,
    blocked: bool,
) -> None:
    config = _runtime_config("onsale", delay=7)
    tab = _RecoveryTab(config["homepage"])
    tixcraft._state.clear()
    tixcraft._state["soft_block_recovery_in_progress"] = False

    async def fake_get(_tab, url, *_args, **_kwargs):
        _tab.target.url = url
        return guarded_result

    async def health(*_args, **_kwargs):
        if blocked:
            return {
                "blocked": True,
                "readyState": "complete",
                "hasBody": True,
                "hasKnownContent": False,
                "whiteOverlay": True,
                "knownOrderProcessing": False,
            }
        return await _healthy_activity_page_health()

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "sleep_with_heartbeat",
        lambda *_args, **_kwargs: asyncio.sleep(0, result="done"),
    )
    monkeypatch.setattr(tixcraft.runtime_health, "guarded_get", fake_get)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(tixcraft, "_read_tixcraft_page_health", health)

    assert await tixcraft._handle_tixcraft_soft_block(
        tab,
        config,
        config["homepage"],
        {"kind": "text_marker", "original_url": ""},
    )
    if blocked:
        assert tixcraft._state["soft_block_phase"] == "recovering"
        assert tixcraft._state["soft_block_recovery_retry_at"] > 0
    else:
        assert tixcraft._state["soft_block_phase"] == ""


@pytest.mark.asyncio
async def test_controlled_activity_recovery_rejects_redirected_landing(
    monkeypatch,
) -> None:
    config = _runtime_config("onsale", delay=7)
    tab = _RecoveryTab(config["homepage"])
    tixcraft._state.clear()
    tixcraft._state["soft_block_recovery_in_progress"] = False

    async def redirected(_tab, _url, *_args, **_kwargs):
        _tab.target.url = "https://tixcraft.com/activity/detail/another-event"
        return True

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "sleep_with_heartbeat",
        lambda *_args, **_kwargs: asyncio.sleep(0, result="done"),
    )
    monkeypatch.setattr(tixcraft.runtime_health, "guarded_get", redirected)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(
        tixcraft,
        "_read_tixcraft_page_health",
        _healthy_area_page_health,
    )

    assert await tixcraft._handle_tixcraft_soft_block(
        tab,
        config,
        config["homepage"],
        {"kind": "text_marker", "original_url": ""},
    )
    assert tixcraft._state["soft_block_phase"] == "recovering"
    assert tixcraft._state["soft_block_recovery_retry_at"] > 0


class _AreaLink:
    def __init__(self) -> None:
        self.clicks = 0

    async def click(self) -> None:
        self.clicks += 1


class _AreaZone:
    def __init__(self, link: _AreaLink) -> None:
        self.link = link

    async def query_selector_all(self, _selector: str):
        return [self.link]


class _AreaTab(_RecoveryTab):
    def __init__(self) -> None:
        super().__init__(AREA_URL)
        self.link = _AreaLink()
        self.zone = _AreaZone(self.link)

    async def query_selector(self, _selector: str):
        return self.zone

    async def evaluate(self, script: str):
        if "Array.from(document.querySelectorAll('.zone a'))" in script:
            return json.dumps(
                [{"text": "2F Y1B-1區5300", "fontText": ""}],
                ensure_ascii=False,
            )
        return ""


@pytest.mark.asyncio
@pytest.mark.parametrize("run_mode", ["onsale", "leak_watch"])
async def test_recovery_landing_scans_and_clicks_before_any_reload(
    monkeypatch,
    run_mode: str,
) -> None:
    config = _runtime_config(run_mode)
    tab = _AreaTab()
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "current_event_id": "26_plave",
            "current_game_id": "5300",
            "notification_flow_url": AREA_URL,
            "notification_flow_generation": 1,
            "last_valid_area_url": AREA_URL,
            "recent_area_route_url": AREA_URL,
            "manual_intervention_required": False,
            "leak_scheduler": LeakWatchScheduler(),
        }
    )
    tixcraft._mark_tixcraft_recovery_landed(config, AREA_URL)

    async def no_pause(_config=None):
        return False

    async def ready(*_args, **_kwargs):
        return True

    async def query_one(obj, selector, *_args, **_kwargs):
        return await obj.query_selector(selector)

    async def query_all(obj, selector, *_args, **_kwargs):
        return await obj.query_selector_all(selector)

    async def evaluate(obj, script, *_args, **_kwargs):
        return await obj.evaluate(script)

    async def forbidden_reload(*_args, **_kwargs):
        raise AssertionError("recovery landing must scan/click before reload")

    monkeypatch.setattr(tixcraft, "check_and_handle_pause", no_pause)
    monkeypatch.setattr(tixcraft.runtime_health, "wait_for_interactive_ready", ready)
    monkeypatch.setattr(tixcraft.runtime_health, "query_selector_with_timeout", query_one)
    monkeypatch.setattr(tixcraft.runtime_health, "query_selector_all_with_timeout", query_all)
    monkeypatch.setattr(tixcraft.runtime_health, "evaluate_with_timeout", evaluate)
    monkeypatch.setattr(tixcraft, "guarded_reload", forbidden_reload)
    monkeypatch.setattr(tixcraft.runtime_health, "runtime_log", lambda *_args, **_kwargs: None)

    await tixcraft.nodriver_tixcraft_area_auto_select(tab, AREA_URL, config)
    assert tab.link.clicks == 1
    assert tab.reload_calls == 0
    assert tixcraft._state["soft_block_recovery_scan_pending"] is False
    assert tixcraft._state["soft_block_recovery_landing_url"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("run_mode", ["onsale", "leak_watch"])
async def test_recovery_no_match_does_not_reload_again_before_interval(
    monkeypatch,
    run_mode: str,
) -> None:
    config = _runtime_config(run_mode)
    tab = _AreaTab()
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "current_event_id": "26_plave",
            "current_game_id": "5300",
            "notification_flow_url": AREA_URL,
            "notification_flow_generation": 1,
            "last_valid_area_url": AREA_URL,
            "recent_area_route_url": AREA_URL,
            "manual_intervention_required": False,
            "leak_scheduler": LeakWatchScheduler(),
        }
    )
    tixcraft._mark_tixcraft_recovery_landed(config, AREA_URL)
    reloads: list[str] = []

    async def no_pause(_config=None):
        return False

    async def ready(*_args, **_kwargs):
        return True

    async def query_one(obj, selector, *_args, **_kwargs):
        return await obj.query_selector(selector)

    async def query_all(obj, selector, *_args, **_kwargs):
        return await obj.query_selector_all(selector)

    async def evaluate(obj, script, *_args, **_kwargs):
        return await obj.evaluate(script)

    async def no_match(*_args, **_kwargs):
        return True, None

    async def record_reload(*_args, **_kwargs):
        reloads.append("reload")
        return True

    monkeypatch.setattr(tixcraft, "check_and_handle_pause", no_pause)
    monkeypatch.setattr(tixcraft.runtime_health, "wait_for_interactive_ready", ready)
    monkeypatch.setattr(tixcraft.runtime_health, "query_selector_with_timeout", query_one)
    monkeypatch.setattr(tixcraft.runtime_health, "query_selector_all_with_timeout", query_all)
    monkeypatch.setattr(tixcraft.runtime_health, "evaluate_with_timeout", evaluate)
    monkeypatch.setattr(tixcraft, "nodriver_get_tixcraft_target_area", no_match)
    monkeypatch.setattr(tixcraft, "guarded_reload", record_reload)
    monkeypatch.setattr(tixcraft.runtime_health, "runtime_log", lambda *_args, **_kwargs: None)

    await tixcraft.nodriver_tixcraft_area_auto_select(tab, AREA_URL, config)
    await tixcraft.nodriver_tixcraft_area_auto_select(tab, AREA_URL, config)
    assert reloads == []
