from __future__ import annotations

import leak_watch
from leak_watch import (
    AREA_CLICK_PENDING_MAX_SECONDS,
    DOM_SCAN_PENDING_TIMEOUT_SECONDS,
    LEAK_WATCH_HISTORY_CAPACITY,
    RELOAD_PENDING_TIMEOUT_SECONDS,
    LeakWatchScheduler,
)
from reload_guard import RELOAD_GUARD_HISTORY_CAPACITY, ReloadGuard
from run_modes import RunMode


AREA_URL = "https://tixcraft.com/ticket/area/event/game"
CHECKOUT_URL = "https://tixcraft.com/ticket/checkout"


def _config(interval: float = 3.0) -> dict:
    return {
        "advanced": {
            "run_mode": RunMode.LEAK_WATCH.value,
            "leak_refresh_interval_seconds": interval,
        }
    }


def test_explicit_monotonic_now_controls_every_scheduler_timestamp(monkeypatch) -> None:
    def wall_clock_must_not_be_used() -> float:
        raise AssertionError("scheduler deadlines must not use time.time()")

    monkeypatch.setattr(leak_watch.time, "time", wall_clock_must_not_be_used)
    scheduler = LeakWatchScheduler()

    assert scheduler.mark_dom_scan_start(now=10.0) is True
    assert scheduler.dom_scan_started_at == 10.0
    scheduler.mark_dom_scan_end(now=11.0)
    assert scheduler.last_dom_read_at == 11.0

    assert scheduler.mark_area_click_pending(AREA_URL, now=20.0) is True
    assert scheduler.last_area_click_at == 20.0
    scheduler.clear_area_click_pending()

    scheduler.begin_reload_cycle(AREA_URL, now=30.0)
    assert scheduler.cycle_started_at == 30.0
    scheduler.finish_reload_cycle(_config(4.0), success=True, now=31.0)
    assert scheduler.next_cycle_at == 35.0


def test_area_click_pending_is_idempotent_and_never_extends_its_deadline() -> None:
    scheduler = LeakWatchScheduler()

    assert scheduler.mark_area_click_pending(AREA_URL, now=100.0) is True
    assert scheduler.mark_area_click_pending(f"{AREA_URL}?retry=1", now=104.0) is False

    assert scheduler.last_area_click_at == 100.0
    assert scheduler.last_clicked_url == AREA_URL
    assert scheduler.area_click_pending is True

    expired = scheduler.maintenance(_config(3.0), AREA_URL, now=100.0 + AREA_CLICK_PENDING_MAX_SECONDS)

    assert expired == ("area_click_pending_expired",)
    assert scheduler.area_click_pending is False
    assert scheduler.last_area_click_at == 0.0
    assert scheduler.last_clicked_url == ""


def test_maintenance_expires_dom_click_and_reload_without_can_reload() -> None:
    scheduler = LeakWatchScheduler()
    assert scheduler.mark_dom_scan_start(now=10.0)
    assert scheduler.mark_area_click_pending(AREA_URL, now=20.0)
    scheduler.begin_reload_cycle(AREA_URL, now=30.0)

    expired = scheduler.maintenance(
        _config(3.0),
        CHECKOUT_URL,
        now=max(
            10.0 + DOM_SCAN_PENDING_TIMEOUT_SECONDS,
            20.0 + AREA_CLICK_PENDING_MAX_SECONDS,
            30.0 + RELOAD_PENDING_TIMEOUT_SECONDS,
        ),
    )

    assert set(expired) == {
        "dom_scan_pending_expired",
        "area_click_pending_expired",
        "reload_pending_expired",
    }
    assert scheduler.dom_scan_pending is False
    assert scheduler.area_click_pending is False
    assert scheduler.reload_pending is False
    assert scheduler.next_cycle_at > 0.0
    assert scheduler.can_reload(_config(), CHECKOUT_URL, now=10_000.0)[0] is False


def test_pending_watchdogs_do_not_expire_before_their_deadlines() -> None:
    scheduler = LeakWatchScheduler()
    assert scheduler.mark_dom_scan_start(now=10.0)
    assert scheduler.mark_area_click_pending(AREA_URL, now=10.0)
    scheduler.begin_reload_cycle(AREA_URL, now=10.0)

    before_first_deadline = min(
        DOM_SCAN_PENDING_TIMEOUT_SECONDS,
        AREA_CLICK_PENDING_MAX_SECONDS,
        RELOAD_PENDING_TIMEOUT_SECONDS,
    )
    assert scheduler.maintenance(
        _config(AREA_CLICK_PENDING_MAX_SECONDS),
        AREA_URL,
        now=10.0 + before_first_deadline - 0.001,
    ) == ()
    assert scheduler.dom_scan_pending is True
    assert scheduler.area_click_pending is True
    assert scheduler.reload_pending is True


def test_reload_pending_start_is_idempotent_and_does_not_extend_watchdog() -> None:
    scheduler = LeakWatchScheduler()

    assert scheduler.begin_reload_cycle(AREA_URL, now=100.0) is True
    assert scheduler.begin_reload_cycle(f"{AREA_URL}?retry=1", now=110.0) is False

    assert scheduler.cycle_started_at == 100.0
    assert scheduler.last_cycle_url == AREA_URL
    assert scheduler.maintenance(
        _config(),
        AREA_URL,
        now=100.0 + RELOAD_PENDING_TIMEOUT_SECONDS - 0.001,
    ) == ()
    assert scheduler.reload_pending is True
    assert scheduler.maintenance(
        _config(),
        AREA_URL,
        now=100.0 + RELOAD_PENDING_TIMEOUT_SECONDS + 0.001,
    ) == ("reload_pending_expired",)
    assert scheduler.reload_pending is False


def test_pending_watchdogs_fail_open_when_timestamps_use_another_clock() -> None:
    scheduler = LeakWatchScheduler(
        reload_pending=True,
        cycle_started_at=1_700_000_000.0,
        dom_scan_pending=True,
        dom_scan_started_at=1_700_000_000.0,
        area_click_pending=True,
        last_area_click_at=1_700_000_000.0,
        last_clicked_url=AREA_URL,
    )

    expired = scheduler.maintenance(_config(3.0), AREA_URL, now=100.0)

    assert set(expired) == {
        "reload_pending_expired",
        "dom_scan_pending_expired",
        "area_click_pending_expired",
    }
    assert scheduler.reload_pending is False
    assert scheduler.dom_scan_pending is False
    assert scheduler.area_click_pending is False
    assert scheduler.cycle_started_at == 0.0
    assert scheduler.dom_scan_started_at == 0.0
    assert scheduler.last_area_click_at == 0.0
    assert scheduler.last_clicked_url == ""


def test_maintenance_repairs_cross_clock_and_hot_interval_deadlines() -> None:
    scheduler = LeakWatchScheduler(next_cycle_at=1_700_000_000.0)

    assert scheduler.maintenance(_config(3.0), AREA_URL, now=100.0) == ()
    assert scheduler.next_cycle_at == 103.0
    assert scheduler.can_reload(_config(3.0), AREA_URL, now=102.999) == (
        False,
        "interval_wait",
    )
    assert scheduler.can_reload(_config(3.0), AREA_URL, now=103.0) == (
        True,
        "ready",
    )

    scheduler.next_cycle_at = 200.0
    scheduler.maintenance(_config(1.0), AREA_URL, now=150.0)
    assert scheduler.next_cycle_at == 151.0


def test_recovery_landing_and_zero_interval_contract_is_preserved() -> None:
    scheduler = LeakWatchScheduler(
        reload_pending=True,
        cycle_started_at=240.0,
        dom_scan_pending=True,
        dom_scan_started_at=241.0,
        area_click_pending=True,
        last_area_click_at=242.0,
        last_clicked_url=AREA_URL,
        ticket_form_pending=True,
        submit_pending=True,
        next_cycle_at=0.0,
    )

    scheduler.mark_recovery_landed(_config(0.0), now=250.0)

    assert scheduler.reload_pending is False
    assert scheduler.cycle_started_at == 0.0
    assert scheduler.dom_scan_pending is False
    assert scheduler.dom_scan_started_at == 0.0
    assert scheduler.area_click_pending is False
    assert scheduler.last_area_click_at == 0.0
    assert scheduler.last_clicked_url == ""
    assert scheduler.ticket_form_pending is False
    assert scheduler.submit_pending is False
    assert scheduler.next_cycle_at == 251.0
    assert scheduler.can_reload(_config(0.0), AREA_URL, now=250.999) == (
        False,
        "interval_wait",
    )
    assert scheduler.can_reload(_config(0.0), AREA_URL, now=251.0) == (
        True,
        "ready",
    )


def test_recovery_landing_non_finite_now_falls_back_to_monotonic(
    monkeypatch,
) -> None:
    scheduler = LeakWatchScheduler()
    monkeypatch.setattr(leak_watch.time, "monotonic", lambda: 500.0)

    for invalid_now in (float("nan"), float("inf"), "invalid"):
        scheduler.mark_recovery_landed(_config(4.0), now=invalid_now)
        assert scheduler.next_cycle_at == 504.0


def test_scheduler_history_is_bounded_to_recent_events() -> None:
    scheduler = LeakWatchScheduler()

    for index in range(LEAK_WATCH_HISTORY_CAPACITY + 50):
        assert scheduler.mark_dom_scan_start(now=float(index))
        scheduler.mark_dom_scan_end(now=float(index))

    assert len(scheduler.history) == LEAK_WATCH_HISTORY_CAPACITY
    assert scheduler.history[-1] == "dom_scan_end"
    assert "dom_scan_start" in scheduler.history


def test_reload_guard_history_is_bounded_to_recent_decisions() -> None:
    guard = ReloadGuard()

    for index in range(RELOAD_GUARD_HISTORY_CAPACITY + 50):
        guard.can_reload(AREA_URL, reason=f"cycle-{index}")

    assert len(guard.history) == RELOAD_GUARD_HISTORY_CAPACITY
    assert guard.history[-1].reason == (
        f"cycle-{RELOAD_GUARD_HISTORY_CAPACITY + 49}"
    )
    assert guard.history[0].reason == "cycle-50"


def test_no_ticket_scan_runs_once_per_successful_document_generation() -> None:
    scheduler = LeakWatchScheduler()

    assert scheduler.should_scan_current_document() is True
    scheduler.mark_no_ticket_scan_complete()
    assert scheduler.should_scan_current_document() is False

    assert scheduler.begin_reload_cycle(AREA_URL, now=10.0)
    scheduler.finish_reload_cycle(_config(), False, now=11.0)
    assert scheduler.should_scan_current_document() is False

    assert scheduler.begin_reload_cycle(AREA_URL, now=14.0)
    scheduler.finish_reload_cycle(_config(), True, now=15.0)
    assert scheduler.should_scan_current_document() is True
    scheduler.mark_no_ticket_scan_complete()
    assert scheduler.should_scan_current_document() is False


def test_recovery_navigation_advances_document_generation() -> None:
    scheduler = LeakWatchScheduler()
    scheduler.mark_no_ticket_scan_complete()

    scheduler.mark_recovery_landed(_config(), now=20.0)

    assert scheduler.document_generation == 1
    assert scheduler.should_scan_current_document() is True
