from __future__ import annotations

import asyncio
import gc
import tracemalloc
from types import SimpleNamespace

import pytest

from leak_watch import LEAK_WATCH_HISTORY_CAPACITY, LeakWatchScheduler
from platforms import tixcraft


AREA_URL = "https://tixcraft.com/ticket/area/event/game"


def _config(interval: float = 3.0) -> dict:
    return {
        "advanced": {
            "run_mode": "leak_watch",
            "leak_refresh_interval_seconds": interval,
        }
    }


def _area_config(interval: float = 3.0) -> dict:
    config = _config(interval)
    config.update(
        {
            "homepage": "https://tixcraft.com/activity/detail/event",
            "area_auto_select": {
                "enable": True,
                "area_keyword": "",
                "mode": "from top to bottom",
            },
            "area_auto_fallback": False,
            "keyword_exclude": "",
        }
    )
    return config


def test_scheduler_100000_iteration_soak_has_no_stuck_pending_or_growth() -> None:
    scheduler = LeakWatchScheduler()
    config = _config(3.0)
    reload_cycles = 0

    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()
    for iteration in range(100_000):
        now = iteration * 0.05
        if iteration % 250 == 0:
            scheduler.mark_dom_scan_start(now=now)
            scheduler.mark_dom_scan_end(now=now + 0.001)
        if iteration % 400 == 0:
            scheduler.mark_area_click_pending(AREA_URL, now=now)

        scheduler.maintenance(config, AREA_URL, now=now)
        can_reload, _reason = scheduler.can_reload(config, AREA_URL, now=now)
        if can_reload:
            assert scheduler.begin_reload_cycle(AREA_URL, now=now)
            scheduler.finish_reload_cycle(config, success=True, now=now + 0.001)
            reload_cycles += 1

    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    scheduler.maintenance(config, AREA_URL, now=10_000.0)
    assert reload_cycles > 100
    assert scheduler.reload_pending is False
    assert scheduler.dom_scan_pending is False
    assert scheduler.area_click_pending is False
    assert len(scheduler.history) <= LEAK_WATCH_HISTORY_CAPACITY
    assert current_bytes - baseline_current < 5 * 1024 * 1024
    assert peak_bytes - baseline_current < 5 * 1024 * 1024


@pytest.mark.asyncio
async def test_100000_iteration_failure_matrix_preserves_reload_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the actual TixCraft reload gate across long-run route changes."""

    scheduler = LeakWatchScheduler()
    tixcraft._state.clear()
    tixcraft._state["leak_scheduler"] = scheduler
    tab = SimpleNamespace(target=SimpleNamespace(url=AREA_URL))
    clock = {"now": 0.0}
    reload_count = 0
    reload_attempt_count = 0
    active_reloads = 0
    max_active_reloads = 0
    unsafe_reloads = 0
    reload_attempt_times: list[float] = []
    reload_success_times: list[float] = []
    eligible_times: list[float] = []

    async def fake_reload(*_args, **_kwargs) -> bool:
        nonlocal reload_count
        nonlocal reload_attempt_count
        nonlocal active_reloads
        nonlocal max_active_reloads
        nonlocal unsafe_reloads
        reload_attempt_count += 1
        reload_attempt_times.append(clock["now"])
        active_reloads += 1
        max_active_reloads = max(max_active_reloads, active_reloads)
        try:
            if tab.target.url != AREA_URL:
                unsafe_reloads += 1
            if reload_attempt_count % 17 == 0:
                raise TimeoutError("offline reload timeout")
            if reload_attempt_count % 11 == 0:
                return False
            reload_count += 1
            reload_success_times.append(clock["now"])
            return True
        finally:
            active_reloads -= 1

    async def ready(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(tixcraft, "guarded_reload", fake_reload)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        ready,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )

    loop_tasks_before = set(asyncio.all_tasks())
    gc.collect()
    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()

    for iteration in range(100_000):
        clock["now"] = iteration * 0.05
        phase = iteration % 100
        paused = 80 <= phase < 90

        if phase < 60 or phase >= 90:
            tab.target.url = AREA_URL
        elif phase < 70:
            tab.target.url = "https://tixcraft.com/ticket/checkout"
        else:
            tab.target.url = "https://queue-it.net/?c=tixcraft"

        interval = 0.0 if 50 <= phase < 60 else (1.0 if phase >= 90 else 3.0)
        config = _config(interval)

        if iteration and iteration % 1_000 == 0:
            scheduler.mark_recovery_landed(config, now=clock["now"])
        if iteration and iteration % 1_500 == 0:
            scheduler.mark_dom_scan_start(now=clock["now"])
        if iteration and iteration % 2_000 == 0:
            scheduler.mark_area_click_pending(AREA_URL, now=clock["now"])

        scheduler.maintenance(config, tab.target.url, now=clock["now"])
        if not paused:
            attempt_count_before = len(reload_attempt_times)
            eligible_now = (
                interval > 0
                and scheduler.can_reload(
                    config,
                    tab.target.url,
                    now=clock["now"],
                )[0]
            )
            if eligible_now:
                eligible_times.append(clock["now"])
            await tixcraft._reload_page_when_due(
                tab,
                config,
                "tixcraft_area_reload",
                "[AREA SELECT]",
            )
            assert len(reload_attempt_times) == (
                attempt_count_before + int(eligible_now)
            )

    scheduler.maintenance(_config(3.0), AREA_URL, now=10_000.0)
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    loop_tasks_after = set(asyncio.all_tasks())

    assert reload_count > 1_000
    assert reload_attempt_times == eligible_times
    assert reload_success_times
    assert reload_success_times[-1] >= (100_000 - 100) * 0.05
    assert any(timestamp >= 2_500.0 for timestamp in reload_success_times)
    assert max(
        abs(attempted_at - eligible_at)
        for attempted_at, eligible_at in zip(
            reload_attempt_times,
            eligible_times,
            strict=True,
        )
    ) <= 0.05
    assert unsafe_reloads == 0
    assert max_active_reloads == 1
    assert active_reloads == 0
    assert scheduler.reload_pending is False
    assert scheduler.dom_scan_pending is False
    assert scheduler.area_click_pending is False
    assert len(scheduler.history) <= LEAK_WATCH_HISTORY_CAPACITY
    assert loop_tasks_after == loop_tasks_before
    assert current_bytes - baseline_current < 5 * 1024 * 1024
    assert peak_bytes - baseline_current < 5 * 1024 * 1024


@pytest.mark.asyncio
async def test_real_area_zone_missing_reloads_across_fifty_intervals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = LeakWatchScheduler()
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "notification_flow_url": AREA_URL,
            "notification_flow_generation": 1,
            "current_event_id": "event",
            "current_game_id": "game",
            "last_valid_area_url": AREA_URL,
            "recent_area_route_url": AREA_URL,
            "manual_intervention_required": False,
            "leak_scheduler": scheduler,
        }
    )
    tab = SimpleNamespace(target=SimpleNamespace(url=AREA_URL))
    clock = {"now": 10.0}
    reload_times: list[float] = []

    async def no_pause(*_args, **_kwargs) -> bool:
        return False

    async def ready(*_args, **_kwargs) -> bool:
        return True

    async def zone_missing(*_args, **_kwargs):
        return None

    async def record_reload(*_args, **_kwargs) -> bool:
        reload_times.append(clock["now"])
        return True

    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(tixcraft, "check_and_handle_pause", no_pause)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        ready,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "query_selector_with_timeout",
        zone_missing,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(tixcraft, "guarded_reload", record_reload)

    expected_reload_times = [10.0 + cycle * 3.0 for cycle in range(51)]
    for current in expected_reload_times:
        clock["now"] = current
        assert not await tixcraft.nodriver_tixcraft_area_auto_select(
            tab,
            AREA_URL,
            _area_config(3.0),
        )

    assert reload_times == expected_reload_times
    assert reload_times[-1] - reload_times[0] == 150.0
    assert scheduler.reload_pending is False
    assert scheduler.dom_scan_pending is False
    assert scheduler.area_click_pending is False
