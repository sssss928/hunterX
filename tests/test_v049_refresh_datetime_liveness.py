from __future__ import annotations

import asyncio
import time
from datetime import datetime

import pytest

import nodriver_tixcraft as runtime
import platforms.tixcraft as tixcraft_platform
from leak_watch import LeakWatchScheduler
from page_classifier import PageClass
from refresh_timing import Clock, NS_PER_MS, RefreshTriggerController, resolve_refresh_timezone
from trigger_arbiter import TriggerReloadDecision


class FakeClock(Clock):
    def __init__(self, wall_ns: int, monotonic_ns: int) -> None:
        self.wall_ns = wall_ns
        self.mono_ns = monotonic_ns

    def wall_time_ns(self) -> int:
        return self.wall_ns

    def monotonic_ns(self) -> int:
        return self.mono_ns

    def advance_ms(self, value: int) -> None:
        delta = value * NS_PER_MS
        self.wall_ns += delta
        self.mono_ns += delta


class Target:
    def __init__(self, url: str) -> None:
        self.url = url


class Tab:
    def __init__(self, url: str) -> None:
        self.target = Target(url)


def _gate_state(clock: Clock) -> dict[str, object]:
    return {
        "state_key": "",
        "target_str": "",
        "reached": False,
        "last_countdown_print": 0.0,
        "reported_plan": False,
        "controller": RefreshTriggerController(clock=clock),
    }


def _config(target: datetime) -> dict[str, object]:
    return {
        "refresh_datetime": target.strftime("%Y/%m/%d %H:%M:%S.%f")[:-3],
        "time_calibration": {"mode": "system"},
        "advanced": {
            "run_mode": "leak_watch",
            "leak_refresh_interval_seconds": 3,
        },
    }


@pytest.mark.asyncio
async def test_unavailable_boundary_health_cannot_deadlock_scheduled_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unavailable health probe may delay once, but cannot own the schedule."""

    target = datetime(
        2026,
        8,
        13,
        10,
        21,
        30,
        500000,
        tzinfo=resolve_refresh_timezone("Asia/Taipei"),
    )
    url = "https://tixcraft.com/activity/detail/26_event"
    clock = FakeClock(int(target.timestamp() * 1_000_000_000), time.monotonic_ns())
    state = _gate_state(clock)
    tab = Tab(url)
    reloads: list[str] = []

    async def unavailable_probe(*_args, **_kwargs):
        return {"probeFailed": True}

    async def scheduled_reload(*_args, **kwargs):
        reloads.append(str(kwargs.get("reason", "")))
        return True

    tixcraft_platform._state.clear()
    tixcraft_platform._state.update({"leak_scheduler": LeakWatchScheduler()})
    monkeypatch.setattr(
        runtime.tixcraft_platform,
        "_read_tixcraft_page_health",
        unavailable_probe,
    )
    monkeypatch.setattr(runtime, "guarded_reload", scheduled_reload)

    first = await runtime.check_refresh_datetime_gate(tab, _config(target), state, url)
    second = await runtime.check_refresh_datetime_gate(tab, _config(target), state, url)

    assert first is True
    assert second is False
    assert reloads == ["refresh_datetime_trigger"]
    assert state["reached"] is True
    assert state["refresh_retry_pending"] is False
    assert state["last_refresh_reload_decision"] == "reloaded"


@pytest.mark.asyncio
async def test_confirmed_soft_block_still_prevents_boundary_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-open applies only to unknown health, never to confirmed block evidence."""

    target = datetime(
        2026,
        8,
        13,
        10,
        21,
        30,
        500000,
        tzinfo=resolve_refresh_timezone("Asia/Taipei"),
    )
    url = "https://tixcraft.com/ticket/area/event/game"
    clock = FakeClock(int(target.timestamp() * 1_000_000_000), time.monotonic_ns())
    state = _gate_state(clock)
    tab = Tab(url)
    reloads: list[str] = []

    async def blocked_probe(*_args, **_kwargs):
        return {
            "blocked": False,
            "readyState": "complete",
            "hasBody": True,
            "hasKnownContent": False,
            "bodyText": "Your browsing activity has been paused",
            "title": "",
            "elementCount": 10,
        }

    async def forbidden_reload(*_args, **kwargs):
        reloads.append(str(kwargs.get("reason", "")))
        return True

    tixcraft_platform._state.clear()
    tixcraft_platform._state.update({"leak_scheduler": LeakWatchScheduler()})
    monkeypatch.setattr(
        runtime.tixcraft_platform,
        "_read_tixcraft_page_health",
        blocked_probe,
    )
    monkeypatch.setattr(runtime, "guarded_reload", forbidden_reload)

    active = await runtime.check_refresh_datetime_gate(tab, _config(target), state, url)

    assert active is True
    assert reloads == []
    assert state["last_refresh_reload_decision"] == "soft_block_detected"
    assert state["refresh_retry_pending"] is True


@pytest.mark.asyncio
async def test_soft_block_retry_budget_is_fixed_and_cannot_extend_forever() -> None:
    target = datetime(
        2026,
        8,
        13,
        10,
        21,
        30,
        500000,
        tzinfo=resolve_refresh_timezone("Asia/Taipei"),
    )
    clock = FakeClock(int(target.timestamp() * 1_000_000_000), 10_000_000_000)
    controller = RefreshTriggerController(clock=clock)
    controller.trigger_deadline_monotonic_ns = clock.monotonic_ns()
    controller.phase = runtime.TriggerPhase.TRIGGERED
    state: dict[str, object] = {
        "controller": controller,
        "refresh_retry_deadline_monotonic_ns": None,
    }
    decision = TriggerReloadDecision(
        attempted=False,
        reloaded=False,
        reason="soft_block_detected",
        page_class=PageClass.AREA,
    )

    await runtime._defer_refresh_trigger_for_page_recovery(
        object(), "https://tixcraft.com/ticket/area/event/game", {}, state, controller, decision
    )
    first_deadline = state["refresh_retry_deadline_monotonic_ns"]

    clock.advance_ms(500)
    await runtime._defer_refresh_trigger_for_page_recovery(
        object(), "https://tixcraft.com/ticket/area/event/game", {}, state, controller, decision
    )

    assert state["refresh_retry_deadline_monotonic_ns"] == first_deadline
    assert first_deadline == 12_000_000_000


@pytest.mark.asyncio
async def test_trigger_retry_reprobes_after_recovery_invalidates_old_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://tixcraft.com/ticket/area/event/game"
    clock = FakeClock(1_000_000_000, 10_000_000_000)
    controller = RefreshTriggerController(clock=clock)
    controller.trigger_deadline_monotonic_ns = 9_000_000_000
    controller.phase = runtime.TriggerPhase.TRIGGERED
    state: dict[str, object] = {
        "controller": controller,
        "state_key": "test",
    }
    runtime._set_refresh_gate_health_evidence(state, url, "blocked")
    runtime._invalidate_refresh_gate_health_evidence(state)
    probes = 0

    async def safe_probe(*_args, **_kwargs):
        nonlocal probes
        probes += 1
        return {
            "blocked": False,
            "readyState": "complete",
            "hasBody": True,
            "hasKnownContent": True,
            "bodyText": "",
            "title": "",
            "elementCount": 1,
        }

    monkeypatch.setattr(
        runtime.tixcraft_platform,
        "_read_tixcraft_page_health",
        safe_probe,
    )

    decision = await runtime._preflight_tixcraft_refresh_boundary(
        Tab(url), url, {}, state, "trigger_retry"
    )

    assert decision is None
    assert probes == 1
    assert state["refresh_gate_health_status"] == "ready"
