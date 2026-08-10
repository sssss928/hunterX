from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from platform_engine import PlatformEngine
from platform_engine import platform_engine
from refresh_coordinator import NS_PER_SECOND, RefreshCoordinator
from reload_guard import guarded_reload
from run_modes import get_mode_policy
from tab_ownership import owned_tab_count, register_owned_tab
from platforms import tixcraft


class FakeClock:
    def __init__(self, seconds: float = 0.0) -> None:
        self.now_ns = round(seconds * NS_PER_SECOND)

    def __call__(self) -> int:
        return self.now_ns

    def advance(self, seconds: float) -> None:
        self.now_ns += round(seconds * NS_PER_SECOND)


def test_minimum_interval_is_deterministic_for_fifty_thousand_ticks() -> None:
    clock = FakeClock(10.0)
    coordinator = RefreshCoordinator(clock_ns=clock)
    dispatches: list[int] = []

    for _ in range(50_000):
        decision = coordinator.begin_dispatch("inventory_empty", 5.0)
        if decision.allowed:
            dispatches.append(decision.requested_ns)
            coordinator.complete_dispatch(decision.token, True)
        clock.advance(0.1)

    assert len(dispatches) == 1_000
    assert all(
        later - earlier >= 5 * NS_PER_SECOND
        for earlier, later in zip(dispatches, dispatches[1:])
    )
    assert len(coordinator.trace) == 256


def test_modes_are_replaceable_policies_not_duplicate_purchase_pipelines() -> None:
    onsale = get_mode_policy({"advanced": {"run_mode": "onsale"}})
    leak = get_mode_policy({"advanced": {"run_mode": "leak_watch"}})

    assert onsale.refresh_interval_key == "auto_reload_page_interval"
    assert onsale.max_no_ticket_scans_per_generation is None
    assert leak.refresh_interval_key == "leak_refresh_interval_seconds"
    assert leak.max_no_ticket_scans_per_generation == 1
    assert onsale.scheduled_one_shot is leak.scheduled_one_shot is True


def test_event_loop_stall_never_creates_a_catch_up_burst() -> None:
    clock = FakeClock(1.0)
    coordinator = RefreshCoordinator(clock_ns=clock)
    first = coordinator.begin_dispatch("periodic", 3.0)
    coordinator.complete_dispatch(first.token, True)

    clock.advance(90.0)
    after_stall = coordinator.begin_dispatch("periodic", 3.0)
    coordinator.complete_dispatch(after_stall.token, True)
    immediate = coordinator.begin_dispatch("periodic", 3.0)

    assert first.allowed is True
    assert after_stall.allowed is True
    assert immediate.allowed is False
    assert immediate.reason == "minimum_interval"


def test_scheduled_one_shot_coalesces_nearby_periodic_intent() -> None:
    clock = FakeClock(100.0)
    coordinator = RefreshCoordinator(clock_ns=clock)
    deadline = clock() + 2 * NS_PER_SECOND
    assert coordinator.arm_scheduled(
        "sale-2026-08-10T11:00:00+08:00",
        deadline,
        target_wall="2026/08/10 11:00:00.000 Asia/Taipei",
    )

    periodic = coordinator.begin_dispatch("periodic", 5.0)
    assert periodic.allowed is False
    assert periodic.reason == "coalesced_into_scheduled"

    clock.advance(2.0)
    scheduled = coordinator.begin_dispatch(
        "refresh_datetime_trigger",
        5.0,
        priority="scheduled",
    )
    assert scheduled.allowed is True
    assert scheduled.lateness_ms == pytest.approx(0.0)
    coordinator.complete_dispatch(scheduled.token, True)

    duplicate = coordinator.begin_dispatch(
        "refresh_datetime_trigger",
        5.0,
        priority="scheduled",
    )
    assert duplicate.allowed is False
    assert duplicate.reason == "scheduled_not_armed"


def test_scheduled_deadline_can_reanchor_before_dispatch_without_duplication() -> None:
    clock = FakeClock(100.0)
    coordinator = RefreshCoordinator(clock_ns=clock)
    token = "same-config-clock-reanchor"
    coordinator.arm_scheduled(token, clock() + 20 * NS_PER_SECOND)
    coordinator.arm_scheduled(token, clock() + 10 * NS_PER_SECOND)

    assert coordinator.scheduled_deadline_ns == clock() + 10 * NS_PER_SECOND
    clock.advance(10.0)
    decision = coordinator.begin_dispatch(
        "refresh_datetime_trigger",
        5.0,
        priority="scheduled",
    )
    coordinator.complete_dispatch(decision.token, True)

    assert decision.allowed is True
    assert decision.lateness_ms == pytest.approx(0.0)
    assert coordinator.arm_scheduled(token, clock()) is False


def test_controlled_scheduled_dispatch_lateness_meets_acceptance_threshold() -> None:
    lateness_values = []
    for index in range(1_000):
        clock = FakeClock(100.0)
        coordinator = RefreshCoordinator(clock_ns=clock)
        deadline = clock() + NS_PER_SECOND
        coordinator.arm_scheduled(f"sale-{index}", deadline)

        # Deterministic fixture models cooperative-loop wake-up distribution,
        # including a 20 ms worst case without sleeping in the test.
        clock.now_ns = deadline + (index % 21) * 1_000_000
        decision = coordinator.begin_dispatch(
            "refresh_datetime_trigger",
            5.0,
            priority="scheduled",
        )
        assert decision.allowed
        lateness_values.append(decision.lateness_ms)

    ordered = sorted(value for value in lateness_values if value is not None)
    p95 = ordered[round(0.95 * (len(ordered) - 1))]
    assert p95 <= 50.0
    assert max(ordered) <= 100.0


def test_platform_engine_owns_independent_per_tab_coordinators() -> None:
    class Tab:
        pass

    engine = PlatformEngine()
    first_tab = Tab()
    second_tab = Tab()
    first = engine.refresh_coordinator_for(first_tab)
    second = engine.refresh_coordinator_for(second_tab)

    assert first is engine.refresh_coordinator_for(first_tab)
    assert first is not second
    assert first.tab_identity == id(first_tab)
    assert second.tab_identity == id(second_tab)

    engine.clear_tab(first_tab)
    assert engine.refresh_coordinator_for(first_tab) is not first


def test_fallback_state_and_ownership_registries_remain_bounded() -> None:
    engine = PlatformEngine()
    fallback_tabs = []

    for index in range(1_000):
        tab = {"tab": index}
        fallback_tabs.append(tab)
        engine.refresh_coordinator_for(tab)
        register_owned_tab(tab, "bounded_soak")

    assert engine.refresh_coordinator_count <= engine._fallback_capacity
    assert owned_tab_count() <= 128


@pytest.mark.asyncio
async def test_legacy_reload_call_uses_platform_atomic_config_snapshot() -> None:
    class Tab:
        def __init__(self) -> None:
            self.target = SimpleNamespace(
                url="https://tixcraft.com/activity/detail/config-snapshot",
            )
            self.reload_count = 0

        async def reload(self) -> None:
            self.reload_count += 1

    tab = Tab()
    config = {
        "advanced": {
            "run_mode": "onsale",
            "auto_reload_page_interval": 30.0,
        }
    }
    platform_engine.clear_tab(tab)
    platform_engine.before_dispatch(tab, tab.target.url, config)

    assert await guarded_reload(tab, reason="legacy_platform_reload") is True
    assert await guarded_reload(tab, reason="legacy_platform_reload") is False
    assert tab.reload_count == 1
    assert (
        platform_engine.refresh_coordinator_for(tab).trace[-1]["outcome"]
        == "minimum_interval"
    )
    platform_engine.clear_tab(tab)


@pytest.mark.asyncio
async def test_soft_block_retry_never_degrades_to_one_second_after_hours(
    monkeypatch,
) -> None:
    clock = {"now": 100.0}
    probe_times: list[float] = []
    navigation_calls: list[str] = []
    tab = SimpleNamespace()
    config = {
        "homepage": "https://tixcraft.com/activity/detail/demo",
        "advanced": {
            "run_mode": "onsale",
            "auto_reload_page_interval": 2.0,
            "tixcraft_soft_block_delay": "7",
        },
    }
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "soft_block_phase": "recovering",
            "soft_block_state": "RECOVERING",
            "soft_block_retry_wait_seconds": 7.0,
            "soft_block_recovery_retry_at": 100.0,
            "ip_block_until": 100.0,
            "soft_block_recovery_in_progress": False,
        }
    )

    async def inconclusive(*_args, **_kwargs):
        probe_times.append(clock["now"])
        return {
            "blocked": False,
            "health_confirmed": False,
            "inconclusive": True,
        }

    async def forbidden_navigation(*_args, **_kwargs):
        navigation_calls.append("navigation")
        return False

    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(tixcraft, "_detect_tixcraft_soft_block", inconclusive)
    monkeypatch.setattr(tixcraft.runtime_health, "guarded_get", forbidden_navigation)

    # Ten virtual hours at 100 ms loop cadence; hot reload attempts to shorten
    # the setting after one hour, but the active incident keeps its immutable 7s.
    for tick in range(360_000):
        if tick == 36_000:
            config["advanced"]["tixcraft_soft_block_delay"] = "1"
        await tixcraft._nodriver_ticketmaster_check_ip_block_impl(
            tab,
            config,
            current_url="https://tixcraft.com/ticket/area/demo/1",
        )
        clock["now"] += 0.1

    assert navigation_calls == []
    assert len(probe_times) > 1_000
    assert all(
        later - earlier >= 6.999
        for earlier, later in zip(probe_times, probe_times[1:])
    )
    assert tixcraft._state["soft_block_retry_wait_seconds"] == 7.0
    assert tixcraft._state["soft_block_recovery_retry_at"] > clock["now"] - 7.0


def test_soft_block_coordinator_suppresses_ordinary_reload_until_deadline() -> None:
    clock = FakeClock(50.0)
    coordinator = RefreshCoordinator(clock_ns=clock)
    coordinator.begin_soft_block(clock() + 7 * NS_PER_SECOND)

    for _ in range(70):
        assert coordinator.begin_dispatch("periodic", 1.0).allowed is False
        clock.advance(0.1)

    coordinator.mark_soft_block_recovering()
    recovery = coordinator.begin_dispatch("soft_block_recovery", 1.0, priority="recovery")
    assert recovery.allowed is True


def test_custom_soft_block_delay_does_not_leak_into_ticketmaster() -> None:
    config = {"advanced": {"tixcraft_soft_block_delay": "7"}}

    tixcraft_wait, tixcraft_custom = tixcraft._resolve_soft_block_wait_seconds(
        config,
        "https://tixcraft.com/ticket/area/demo/1",
        default_wait_seconds=300,
    )
    ticketmaster_wait, ticketmaster_custom = (
        tixcraft._resolve_soft_block_wait_seconds(
            config,
            "https://ticketmaster.sg/ticket/area/demo/1",
            default_wait_seconds=300,
        )
    )

    assert (tixcraft_wait, tixcraft_custom) == (7, True)
    assert (ticketmaster_wait, ticketmaster_custom) == (300, False)


class _TicketmasterSelect:
    attrs = {"id": "ticketPrice_1"}

    async def update(self) -> None:
        return None


class _TicketmasterTable:
    async def query_selector(self, selector: str):
        assert selector == "select"
        return _TicketmasterSelect()


class _TicketmasterButton:
    def __init__(self) -> None:
        self.clicks = 0

    async def click(self) -> None:
        self.clicks += 1


class _TicketmasterTab:
    def __init__(self, options: list[int], current: int = 0) -> None:
        self.options = options
        self.current = current
        self.button = _TicketmasterButton()
        self.selection_scripts: list[str] = []

    async def evaluate(self, script: str):
        if "selectedIndex].text" in script:
            return str(self.current) if self.current else "0"
        if "Array.from(selectEl.options).map" in script:
            return [f"{value}|{value}" for value in self.options]
        if "targetCount, allowLess" in script:
            self.selection_scripts.append(script)
            target = 4
            allow_less = script.rstrip().endswith("true);")
            if target in self.options:
                self.current = target
                return {"success": True, "selected": str(target), "value": str(target)}
            available = [value for value in self.options if 0 < value < target]
            if allow_less and available:
                self.current = max(available)
                return {
                    "success": True,
                    "selected": str(self.current),
                    "value": str(self.current),
                    "fallback": True,
                }
            return {"success": False, "error": "Exact ticket count unavailable"}
        raise AssertionError(f"unexpected Ticketmaster script: {script[:80]}")

    async def query_selector(self, selector: str):
        assert selector == "#autoMode"
        return self.button

    async def sleep(self, _seconds: float) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("allow_less", "options", "expected_result", "expected_count"),
    [
        (False, [0, 1, 2, 5], False, 0),
        (True, [0, 1, 2, 5], True, 2),
        (False, [0, 1, 4, 5], True, 4),
        (True, [0, 1, 4, 5], True, 4),
    ],
)
async def test_ticketmaster_quantity_is_exact_or_explicitly_less_never_more(
    monkeypatch,
    allow_less: bool,
    options: list[int],
    expected_result: bool,
    expected_count: int,
) -> None:
    tab = _TicketmasterTab(options)
    monkeypatch.setattr(
        tixcraft,
        "nodriver_ticketmaster_get_ticketPriceList",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=_TicketmasterTable()),
    )

    result = await tixcraft.nodriver_ticketmaster_assign_ticket_number(
        tab,
        {
            "ticket_number": 4,
            "tixcraft": {"allow_less_tickets": allow_less},
            "advanced": {"verbose": False},
        },
    )

    assert result is expected_result
    assert tab.current == expected_count
    assert tab.current <= 4
    assert tab.button.clicks == int(expected_result)


@pytest.mark.asyncio
async def test_ticketmaster_reselects_an_unacceptable_existing_quantity(
    monkeypatch,
) -> None:
    tab = _TicketmasterTab([0, 1, 4, 5], current=5)
    monkeypatch.setattr(
        tixcraft,
        "nodriver_ticketmaster_get_ticketPriceList",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=_TicketmasterTable()),
    )

    assert await tixcraft.nodriver_ticketmaster_assign_ticket_number(
        tab,
        {
            "ticket_number": 4,
            "tixcraft": {"allow_less_tickets": True},
            "advanced": {"verbose": False},
        },
    )
    assert tab.current == 4
    assert len(tab.selection_scripts) == 1


def test_ticketmaster_detail_route_is_owned_by_ticketmaster_date_handler() -> None:
    ticketmaster_detail = (
        "https://ticketmaster.sg/activity/detail/26_bigbang"
    )
    tixcraft_detail = "https://tixcraft.com/activity/detail/26_bigbang"

    assert tixcraft._is_ticketmaster_date_page(ticketmaster_detail) is True
    assert tixcraft._should_redirect_tixcraft_detail(ticketmaster_detail) is False
    assert tixcraft._is_ticketmaster_date_page(tixcraft_detail) is False
    assert tixcraft._should_redirect_tixcraft_detail(tixcraft_detail) is True
