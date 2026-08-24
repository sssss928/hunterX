from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from leak_watch import LeakWatchScheduler
from leak_watch import is_safe_page
from page_classifier import PageClass, classify_page
from platforms import tixcraft


AREA_URL = "https://tixcraft.com/ticket/area/26_plave/5300"


def _config(interval: float = 3.0, run_mode: str = "leak_watch") -> dict:
    return {
        "homepage": "https://tixcraft.com/activity/detail/26_plave",
        "area_auto_select": {
            "enable": True,
            "area_keyword": "",
            "mode": "from top to bottom",
        },
        "area_auto_fallback": False,
        "keyword_exclude": "",
        "advanced": {
            "run_mode": run_mode,
            "auto_reload_page_interval": interval,
            "leak_refresh_interval_seconds": interval,
        },
    }


def _seed_state() -> LeakWatchScheduler:
    scheduler = LeakWatchScheduler()
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "notification_flow_url": AREA_URL,
            "notification_flow_generation": 1,
            "current_event_id": "26_plave",
            "current_game_id": "5300",
            "last_valid_area_url": AREA_URL,
            "recent_area_route_url": AREA_URL,
            "manual_intervention_required": False,
            "alert_handler_registered": True,
            "queue_it_enter_time": None,
            "leak_scheduler": scheduler,
        }
    )
    return scheduler


class _AreaTab:
    def __init__(self) -> None:
        self.target = SimpleNamespace(url=AREA_URL)


async def _no_pause(_config=None) -> bool:
    return False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_mode",
    ["page_not_ready", "zone_missing", "zone_query_error"],
)
async def test_every_area_dom_exit_reaches_reload_finalizer(
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    scheduler = _seed_state()
    tab = _AreaTab()
    reload_finalizer_calls: list[str] = []

    async def ready(*_args, **_kwargs) -> bool:
        return failure_mode != "page_not_ready"

    async def query_zone(*_args, **_kwargs):
        if failure_mode == "zone_query_error":
            raise TimeoutError("offline fixture")
        return None

    async def record_reload(_tab, _config, state_key, _prefix) -> bool:
        reload_finalizer_calls.append(state_key)
        return True

    monkeypatch.setattr(tixcraft, "check_and_handle_pause", _no_pause)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        ready,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "query_selector_with_timeout",
        query_zone,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(tixcraft, "_reload_page_when_due", record_reload)

    await tixcraft.nodriver_tixcraft_area_auto_select(tab, AREA_URL, _config())

    assert reload_finalizer_calls == ["tixcraft_area_reload"]
    assert scheduler.dom_scan_pending is False


def test_recovery_landing_uses_only_monotonic_deadlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _seed_state()

    def wall_clock_must_not_be_used() -> float:
        raise AssertionError("recovery scheduling must not use wall clock time")

    monkeypatch.setattr(tixcraft.time, "time", wall_clock_must_not_be_used)
    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: 100.0)

    tixcraft._mark_tixcraft_recovery_landed(_config(5.0), AREA_URL)

    assert tixcraft._state["soft_block_recovery_landed_at"] == 100.0
    assert tixcraft._state["soft_block_recovery_scan_deadline"] == 105.0
    assert scheduler.next_cycle_at == 105.0


@pytest.mark.asyncio
async def test_recovery_zone_missing_scans_once_then_resumes_interval_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _seed_state()
    tab = _AreaTab()
    clock = {"now": 100.0}
    reloads: list[float] = []

    async def ready(*_args, **_kwargs) -> bool:
        return True

    async def missing_zone(*_args, **_kwargs):
        return None

    async def record_reload(*_args, **_kwargs) -> bool:
        reloads.append(clock["now"])
        return True

    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(tixcraft, "check_and_handle_pause", _no_pause)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        ready,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "query_selector_with_timeout",
        missing_zone,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(tixcraft, "guarded_reload", record_reload)

    config = _config(5.0)
    tixcraft._mark_tixcraft_recovery_landed(config, AREA_URL)

    await tixcraft.nodriver_tixcraft_area_auto_select(tab, AREA_URL, config)
    assert reloads == []
    assert tixcraft._state["soft_block_recovery_scan_pending"] is False

    clock["now"] = 104.999
    await tixcraft.nodriver_tixcraft_area_auto_select(tab, AREA_URL, config)
    assert reloads == []

    clock["now"] = 105.0
    await tixcraft.nodriver_tixcraft_area_auto_select(tab, AREA_URL, config)
    assert reloads == [105.0]
    assert scheduler.reload_pending is False


@pytest.mark.asyncio
async def test_reload_scheduler_uses_monotonic_clock_and_repeats_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    tab = _AreaTab()
    clock = {"now": 10.0}
    reloads: list[float] = []

    def wall_clock_must_not_be_used() -> float:
        raise AssertionError("reload scheduler must not use wall clock time")

    async def record_reload(*_args, **_kwargs) -> bool:
        reloads.append(clock["now"])
        return True

    monkeypatch.setattr(tixcraft.time, "time", wall_clock_must_not_be_used)
    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(tixcraft, "guarded_reload", record_reload)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        lambda *_args, **_kwargs: _async_result(True),
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )

    config = _config(3.0)
    for current in (10.0, 12.999, 13.0, 16.0, 19.0):
        clock["now"] = current
        await tixcraft._reload_page_when_due(
            tab,
            config,
            "tixcraft_area_reload",
            "[AREA SELECT]",
        )

    assert reloads == [10.0, 13.0, 16.0, 19.0]


@pytest.mark.asyncio
async def test_reload_single_flight_survives_overlapping_full_cycle_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _seed_state()
    tab = _AreaTab()
    clock = {"now": 0.0}
    entered = asyncio.Event()
    release = asyncio.Event()
    reload_calls = 0
    active_reloads = 0
    max_active_reloads = 0

    async def blocking_reload(*_args, **_kwargs) -> bool:
        nonlocal reload_calls, active_reloads, max_active_reloads
        reload_calls += 1
        active_reloads += 1
        max_active_reloads = max(max_active_reloads, active_reloads)
        entered.set()
        try:
            await release.wait()
            return True
        finally:
            active_reloads -= 1

    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(tixcraft, "guarded_reload", blocking_reload)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        lambda *_args, **_kwargs: _async_result(True),
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )

    config = _config(3.0)
    first = asyncio.create_task(
        tixcraft._reload_page_when_due(
            tab,
            config,
            "tixcraft_area_reload",
            "[AREA SELECT]",
        )
    )
    await entered.wait()

    # A legitimate reload may still be in its 10s reload + 6s ready-state
    # window. A second caller at 15.5s must observe the existing single flight.
    clock["now"] = 15.5
    second = asyncio.create_task(
        tixcraft._reload_page_when_due(
            tab,
            config,
            "tixcraft_area_reload",
            "[AREA SELECT]",
        )
    )
    await asyncio.sleep(0)
    assert second.done()
    assert second.result() is False
    assert reload_calls == 1
    assert active_reloads == 1
    assert scheduler.reload_pending is True

    release.set()
    assert await asyncio.gather(first, second) == [True, False]
    assert max_active_reloads == 1
    assert active_reloads == 0
    assert scheduler.reload_pending is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_url",
    [
        "",
        "https://tixcraft.com/unknown",
        "https://tixcraft.com/ticket/verify/26_plave/5300",
        "https://tixcraft.com/ticket/order",
        "https://tixcraft.com/ticket/checkout",
        "https://queue-it.net/?c=tixcraft",
        "https://tixcraft.com/activity/game/26_plave?queue=waiting",
    ],
)
async def test_leak_reload_never_falls_through_on_unknown_or_protected_route(
    monkeypatch: pytest.MonkeyPatch,
    unsafe_url: str,
) -> None:
    _seed_state()
    tab = _AreaTab()
    tab.target.url = unsafe_url
    reload_calls: list[str] = []

    async def record_reload(*_args, **_kwargs):
        reload_calls.append(unsafe_url)
        return True

    monkeypatch.setattr(tixcraft, "guarded_reload", record_reload)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )

    assert not await tixcraft._reload_page_when_due(
        tab,
        _config(3.0),
        "tixcraft_area_reload",
        "[AREA SELECT]",
    )
    assert reload_calls == []


def test_queue_classification_is_never_a_safe_leak_watch_page() -> None:
    assert not is_safe_page(
        "https://tixcraft.com/activity/game/26_plave?queue=waiting"
    )


def test_queue_classification_uses_route_semantics_not_arbitrary_substrings() -> None:
    queue_host = "https://tixcraft.queue-it.net/?c=tixcraft"
    returned_area = (
        f"{AREA_URL}?queueittoken=e_tixcraft~q_123~ts_123~ce_true"
    )
    queue_slug = "https://tixcraft.com/activity/detail/queue-festival"
    exact_queue_slug = "https://tixcraft.com/activity/detail/queue"
    checkout = "https://tixcraft.com/ticket/checkout?queueittoken=return"

    assert classify_page(queue_host) == PageClass.QUEUE
    assert classify_page(returned_area) == PageClass.AREA
    assert is_safe_page(returned_area)
    assert classify_page(queue_slug) == PageClass.ACTIVITY
    assert classify_page(exact_queue_slug) == PageClass.ACTIVITY
    assert classify_page(checkout) == PageClass.CHECKOUT
    assert not is_safe_page(checkout)


@pytest.mark.asyncio
async def test_zero_interval_disables_periodic_reload_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    tab = _AreaTab()

    async def forbidden_reload(*_args, **_kwargs):
        raise AssertionError("interval=0 must disable periodic reload")

    monkeypatch.setattr(tixcraft, "guarded_reload", forbidden_reload)

    for _ in range(20):
        assert not await tixcraft._reload_page_when_due(
            tab,
            _config(0.0),
            "tixcraft_area_reload",
            "[AREA SELECT]",
        )


@pytest.mark.asyncio
async def test_paused_area_iteration_has_no_dom_or_reload_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _seed_state()
    tab = _AreaTab()

    async def paused(*_args, **_kwargs) -> bool:
        return True

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("paused area iteration must be side-effect free")

    monkeypatch.setattr(tixcraft, "check_and_handle_pause", paused)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        forbidden,
    )
    monkeypatch.setattr(tixcraft, "_reload_page_when_due", forbidden)

    assert not await tixcraft.nodriver_tixcraft_area_auto_select(
        tab,
        AREA_URL,
        _config(),
    )
    assert scheduler.reload_pending is False
    assert scheduler.dom_scan_pending is False
    assert scheduler.area_click_pending is False


@pytest.mark.asyncio
async def test_queue_it_main_dispatch_has_no_dom_reload_or_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    tab = _AreaTab()
    queue_url = "https://example.queue-it.net/?c=tixcraft"
    tab.target.url = queue_url

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Queue-it must short-circuit purchase dispatch")

    monkeypatch.setattr(tixcraft, "nodriver_tixcraft_home_close_window", forbidden)
    monkeypatch.setattr(tixcraft, "nodriver_ticketmaster_check_ip_block", forbidden)
    monkeypatch.setattr(tixcraft, "_reload_page_when_due", forbidden)
    monkeypatch.setattr(tixcraft, "_emit_tixcraft_attempt_notification", forbidden)

    tab_state = tixcraft._state_for_tab(tab)
    tab_state.clear()
    tab_state.update(tixcraft._default_state)
    with tixcraft._bind_tixcraft_tab_state(tab):
        assert not await tixcraft.nodriver_tixcraft_main(
            tab,
            queue_url,
            _config(),
            None,
            None,
        )
        assert tixcraft._state["queue_it_enter_time"] is not None


@pytest.mark.asyncio
async def test_controlled_recovery_requires_interactive_confirmed_area(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    tab = _AreaTab()
    config = _config(3.0)

    async def accepted_get(*_args, **_kwargs) -> bool:
        return True

    async def not_ready(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(tixcraft.runtime_health, "guarded_get", accepted_get)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        not_ready,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )

    assert not await tixcraft._recover_to_last_valid_area(
        tab,
        config,
        "offline_fixture",
    )
    assert not tixcraft._state.get("soft_block_recovery_scan_pending", False)


async def _async_result(value):
    return value
