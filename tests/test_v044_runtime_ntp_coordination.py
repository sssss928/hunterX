from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import refresh_timing
from refresh_timing import (
    NS_PER_MS,
    Clock,
    RuntimeNtpCalibrationCoordinator,
)


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


def _time_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "mode": "auto",
        "ntp_servers": ["time-a.invalid", "time-b.invalid"],
        "ntp_timeout_ms": 50,
        "ntp_samples_per_server": 3,
        "ntp_min_valid_samples": 2,
        "background_refresh_seconds": 60,
    }
    config.update(overrides)
    return config


async def _wait_until(predicate, attempts: int = 100) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.002)
    raise AssertionError("condition was not reached")


@pytest.mark.asyncio
async def test_runtime_ntp_is_single_flight_background_work_and_applies_only_scalars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def blocking_calibration(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(2)
        return {
            "success": True,
            "source": "ntp",
            "sample_count": 5,
            "clock_offset_ms": 37,
            "clock_uncertainty_ms": 4,
            "network_delay_ms": 999,
            "confidence": "high",
            "samples": [{"server": "must-not-enter-runtime-state"}],
        }

    monkeypatch.setattr(
        refresh_timing,
        "calibrate_ntp_servers",
        blocking_calibration,
    )
    clock = FakeClock(1_000_000_000, 2_000_000_000)
    coordinator = RuntimeNtpCalibrationCoordinator(clock=clock)

    first = coordinator.tick(
        _time_config(),
        target_identity="2026/07/26 12:00:00.000",
        target_remaining_seconds=120,
    )
    second = coordinator.tick(
        _time_config(),
        target_identity="2026/07/26 12:00:00.000",
        target_remaining_seconds=120,
    )

    assert first is None
    assert second is None
    assert coordinator.task is not None
    await _wait_until(started.is_set)
    # The event loop remains runnable while the blocking SNTP work is in a
    # worker thread; a second tick must not launch another worker.
    await asyncio.sleep(0.01)
    assert calls == 1

    release.set()
    await _wait_until(lambda: coordinator.task is not None and coordinator.task.done())
    applied = coordinator.tick(
        _time_config(),
        target_identity="2026/07/26 12:00:00.000",
        target_remaining_seconds=100,
    )

    assert applied is not None
    assert applied["source"] == "ntp"
    assert applied["clock_offset_ms"] == 37
    assert applied["clock_uncertainty_ms"] == 4
    assert applied["sample_count"] == 5
    assert applied["confidence"] == "high"
    assert "samples" not in applied
    assert "network_delay_ms" not in applied
    assert coordinator.task is None
    assert coordinator.status == "applied"


@pytest.mark.asyncio
async def test_runtime_ntp_does_not_start_or_apply_inside_critical_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def blocking_calibration(*_args, **_kwargs):
        started.set()
        assert release.wait(2)
        return {
            "success": True,
            "source": "ntp",
            "sample_count": 5,
            "clock_offset_ms": 25,
            "clock_uncertainty_ms": 2,
            "confidence": "high",
        }

    monkeypatch.setattr(
        refresh_timing,
        "calibrate_ntp_servers",
        blocking_calibration,
    )
    coordinator = RuntimeNtpCalibrationCoordinator(
        clock=FakeClock(1_000_000_000, 2_000_000_000)
    )

    assert (
        coordinator.tick(
            _time_config(),
            target_identity="critical-without-task",
            target_remaining_seconds=9,
        )
        is None
    )
    assert coordinator.task is None
    assert not started.is_set()

    coordinator.tick(
        _time_config(),
        target_identity="late-result",
        target_remaining_seconds=120,
    )
    await _wait_until(started.is_set)
    release.set()
    await _wait_until(lambda: coordinator.task is not None and coordinator.task.done())
    assert (
        coordinator.tick(
            _time_config(),
            target_identity="late-result",
            target_remaining_seconds=9,
        )
        is None
    )
    assert coordinator.status == "discarded_critical_window"


@pytest.mark.asyncio
async def test_runtime_ntp_discards_old_generation_and_falls_back_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def old_generation(*_args, **_kwargs):
        started.set()
        assert release.wait(2)
        return {
            "success": True,
            "source": "ntp",
            "sample_count": 5,
            "clock_offset_ms": 88,
            "clock_uncertainty_ms": 1,
            "confidence": "high",
        }

    monkeypatch.setattr(refresh_timing, "calibrate_ntp_servers", old_generation)
    coordinator = RuntimeNtpCalibrationCoordinator(
        clock=FakeClock(1_000_000_000, 2_000_000_000)
    )
    coordinator.tick(
        _time_config(),
        target_identity="old-target",
        target_remaining_seconds=120,
    )
    await _wait_until(started.is_set)

    # A changed target/config invalidates the in-flight generation without
    # starting an overlapping worker.
    coordinator.tick(
        _time_config(ntp_servers=["new.invalid"]),
        target_identity="new-target",
        target_remaining_seconds=9,
    )
    release.set()
    await _wait_until(lambda: coordinator.task is not None and coordinator.task.done())
    assert (
        coordinator.tick(
            _time_config(ntp_servers=["new.invalid"]),
            target_identity="new-target",
            target_remaining_seconds=9,
        )
        is None
    )
    assert coordinator.status == "discarded_generation"

    def failed_calibration(*_args, **_kwargs):
        raise ValueError("no usable NTP response")

    monkeypatch.setattr(
        refresh_timing,
        "calibrate_ntp_servers",
        failed_calibration,
    )
    coordinator.tick(
        _time_config(ntp_servers=["failure.invalid"]),
        target_identity="error-target",
        target_remaining_seconds=120,
    )
    await _wait_until(lambda: coordinator.task is not None and coordinator.task.done())
    assert (
        coordinator.tick(
            _time_config(ntp_servers=["failure.invalid"]),
            target_identity="error-target",
            target_remaining_seconds=110,
        )
        is None
    )
    assert coordinator.status == "error:ValueError"


@pytest.mark.asyncio
async def test_runtime_ntp_timeout_and_untrusted_results_use_local_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_wait_for = refresh_timing.asyncio.wait_for

    async def immediate_timeout(awaitable, timeout=None):
        del timeout
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(
        refresh_timing.asyncio,
        "wait_for",
        immediate_timeout,
    )
    coordinator = RuntimeNtpCalibrationCoordinator(
        clock=FakeClock(1_000_000_000, 2_000_000_000)
    )
    coordinator.tick(
        _time_config(),
        target_identity="timeout-target",
        target_remaining_seconds=120,
    )
    await _wait_until(lambda: coordinator.task is not None and coordinator.task.done())
    assert (
        coordinator.tick(
            _time_config(),
            target_identity="timeout-target",
            target_remaining_seconds=110,
        )
        is None
    )
    assert coordinator.status == "error:TimeoutError"

    monkeypatch.setattr(refresh_timing.asyncio, "wait_for", real_wait_for)

    def untrusted_http(*_args, **_kwargs):
        return {
            "success": True,
            "source": "http",
            "sample_count": 5,
            "clock_offset_ms": 300,
            "clock_uncertainty_ms": 1,
            "confidence": "high",
        }

    monkeypatch.setattr(
        refresh_timing,
        "calibrate_ntp_servers",
        untrusted_http,
    )
    coordinator.tick(
        _time_config(ntp_servers=["untrusted.invalid"]),
        target_identity="untrusted-target",
        target_remaining_seconds=120,
    )
    await _wait_until(lambda: coordinator.task is not None and coordinator.task.done())
    assert (
        coordinator.tick(
            _time_config(ntp_servers=["untrusted.invalid"]),
            target_identity="untrusted-target",
            target_remaining_seconds=110,
        )
        is None
    )
    assert coordinator.status == "discarded_untrusted"


@pytest.mark.asyncio
async def test_runtime_ntp_max_age_expires_to_local_clock_and_close_cleans_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()

    def calibration(*_args, **_kwargs):
        return {
            "success": True,
            "source": "sntp",
            "sample_count": 4,
            "clock_offset_ms": -12,
            "clock_uncertainty_ms": 3,
            "confidence": "medium",
        }

    monkeypatch.setattr(refresh_timing, "calibrate_ntp_servers", calibration)
    clock = FakeClock(1_000_000_000, 2_000_000_000)
    coordinator = RuntimeNtpCalibrationCoordinator(clock=clock)
    coordinator.tick(
        _time_config(),
        target_identity="age-target",
        target_remaining_seconds=400,
    )
    await _wait_until(lambda: coordinator.task is not None and coordinator.task.done())
    assert (
        coordinator.tick(
            _time_config(),
            target_identity="age-target",
            target_remaining_seconds=390,
        )
        is not None
    )

    # Max age is twice the 60-second background interval. Outside the frozen
    # window, expiry must remove the correction and safely use local time.
    clock.advance_ms(121_000)

    def blocking_refresh(*_args, **_kwargs):
        assert release.wait(2)
        return calibration()

    monkeypatch.setattr(
        refresh_timing,
        "calibrate_ntp_servers",
        blocking_refresh,
    )
    assert (
        coordinator.tick(
            _time_config(),
            target_identity="age-target",
            target_remaining_seconds=250,
        )
        is None
    )
    assert coordinator.task is not None
    coordinator.close()
    assert coordinator.task is None
    assert coordinator.status == "cancelled"
    release.set()
    await asyncio.sleep(0)


def test_gate_applies_runtime_ntp_once_then_freezes_before_target() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")
    env["HUNTERX_SRC_PATH"] = str(repo_root / "src")
    script = textwrap.dedent(
        r"""
        import asyncio
        import json
        import os
        import sys
        from datetime import datetime, timedelta

        sys.path.insert(0, os.environ["HUNTERX_SRC_PATH"])
        import nodriver_tixcraft as runtime
        import refresh_timing
        from refresh_timing import (
            Clock,
            NS_PER_MS,
            RefreshTriggerController,
            resolve_refresh_timezone,
        )


        class FakeClock(Clock):
            def __init__(self, wall_ns, mono_ns):
                self.wall_ns = wall_ns
                self.mono_ns = mono_ns

            def wall_time_ns(self):
                return self.wall_ns

            def monotonic_ns(self):
                return self.mono_ns

            def advance_ms(self, value):
                self.wall_ns += value * NS_PER_MS
                self.mono_ns += value * NS_PER_MS


        class Target:
            def __init__(self, url):
                self.url = url


        class Tab:
            def __init__(self, url):
                self.target = Target(url)


        async def run():
            now = datetime(
                2026,
                7,
                26,
                11,
                58,
                0,
                tzinfo=resolve_refresh_timezone("Asia/Taipei"),
            )
            target = now + timedelta(seconds=120)
            clock = FakeClock(
                int(now.timestamp() * 1_000_000_000),
                50_000_000_000,
            )
            state = {
                "state_key": "",
                "target_str": "",
                "reached": False,
                "last_countdown_print": 0.0,
                "reported_plan": False,
                "controller": RefreshTriggerController(clock=clock),
            }
            config = {
                "refresh_datetime": target.strftime(
                    "%Y/%m/%d %H:%M:%S.%f"
                )[:-3],
                "time_calibration": {
                    "mode": "auto",
                    "ntp_servers": ["deterministic.invalid"],
                    "ntp_timeout_ms": 50,
                    "ntp_samples_per_server": 3,
                    "ntp_min_valid_samples": 2,
                    "background_refresh_seconds": 60,
                },
            }
            calls = []

            def calibration(*_args, **_kwargs):
                calls.append("ntp")
                return {
                    "success": True,
                    "source": "ntp",
                    "sample_count": 5,
                    "clock_offset_ms": 25,
                    "clock_uncertainty_ms": 2,
                    "confidence": "high",
                }

            refresh_timing.calibrate_ntp_servers = calibration
            tab = Tab("https://tixcraft.com/activity/game/event")

            first_active = await runtime.check_refresh_datetime_gate(
                tab, config, state, tab.target.url
            )
            generation_after_first = state["controller"].generation
            first_deadline = state["controller"].trigger_deadline_monotonic_ns
            for _ in range(100):
                task = state["runtime_ntp_coordinator"].task
                if task is not None and task.done():
                    break
                await asyncio.sleep(0.002)
            second_active = await runtime.check_refresh_datetime_gate(
                tab, config, state, tab.target.url
            )
            generation_after_apply = state["controller"].generation
            corrected_deadline = state["controller"].trigger_deadline_monotonic_ns
            third_active = await runtime.check_refresh_datetime_gate(
                tab, config, state, tab.target.url
            )
            generation_after_repeat = state["controller"].generation

            clock.advance_ms(111_000)
            frozen_active = await runtime.check_refresh_datetime_gate(
                tab, config, state, tab.target.url
            )
            print(
                "RESULT_JSON="
                + json.dumps(
                    {
                        "active": [
                            first_active,
                            second_active,
                            third_active,
                            frozen_active,
                        ],
                        "calls": calls,
                        "generations": [
                            generation_after_first,
                            generation_after_apply,
                            generation_after_repeat,
                            state["controller"].generation,
                        ],
                        "deadline_shift_ms": (
                            corrected_deadline - first_deadline
                        )
                        // NS_PER_MS,
                        "phase": state["controller"].phase.value,
                        "source": state[
                            "runtime_clock_calibration"
                        ]["source"],
                    },
                    sort_keys=True,
                )
            )


        asyncio.run(run())
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload_line = next(
        line for line in result.stdout.splitlines() if line.startswith("RESULT_JSON=")
    )
    payload = json.loads(payload_line.removeprefix("RESULT_JSON="))

    assert payload == {
        "active": [True, True, True, True],
        "calls": ["ntp"],
        "generations": [1, 2, 2, 2],
        "deadline_shift_ms": -25,
        "phase": "FROZEN",
        "source": "ntp",
    }


def test_gate_prunes_expired_navigation_and_scheduler_guards() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")
    env["HUNTERX_SRC_PATH"] = str(repo_root / "src")
    script = textwrap.dedent(
        r"""
        import json
        import os
        import sys

        sys.path.insert(0, os.environ["HUNTERX_SRC_PATH"])
        import nodriver_tixcraft as runtime
        import platforms.tixcraft as platform
        from leak_watch import LeakWatchScheduler


        class Target:
            def __init__(self, url):
                self.url = url


        class Tab:
            def __init__(self, url):
                self.target = Target(url)


        config = {
            "advanced": {
                "run_mode": "leak_watch",
                "leak_refresh_interval_seconds": 3,
            }
        }
        area_one = "https://tixcraft.com/ticket/area/event-one/game-one"
        area_two = "https://tixcraft.com/ticket/area/event-two/game-two"
        tab = Tab(area_one)
        state = platform._state_for_tab(tab)
        scheduler = LeakWatchScheduler(
            dom_scan_pending=True,
            dom_scan_started_at=1.0,
            area_click_pending=True,
            last_area_click_at=1.0,
        )
        state.clear()
        state.update(
            {
                "current_event_id": "event-one",
                "current_game_id": "game-one",
                "notification_flow_generation": 1,
                "pending_area_navigation": platform.TixCraftPendingNavigation(
                    kind="area",
                    source_url=area_one,
                    event_id="event-one",
                    game_id="game-one",
                    flow_generation=1,
                    token=7,
                    tab_identity=id(tab),
                    started_at=1.0,
                    deadline=2.0,
                ),
                "pending_date_navigation": None,
                "soft_block_recovery_scan_pending": True,
                "soft_block_recovery_landing_url": area_one,
                "soft_block_recovery_scan_deadline": 2.0,
                "leak_scheduler": scheduler,
            }
        )
        expired = runtime._maintain_tixcraft_refresh_runtime(
            tab,
            area_one,
            config,
            now_monotonic=30.0,
        )
        expired_result = {
            "events": sorted(expired),
            "area": state.get("pending_area_navigation"),
            "recovery": state["soft_block_recovery_scan_pending"],
            "dom": scheduler.dom_scan_pending,
            "click": scheduler.area_click_pending,
        }

        scheduler.mark_area_click_pending(area_one, now=30.0)
        state["pending_area_navigation"] = (
            platform.TixCraftPendingNavigation(
                kind="area",
                source_url=area_one,
                event_id="event-one",
                game_id="game-one",
                flow_generation=1,
                token=9,
                tab_identity=id(tab),
                started_at=30.0,
                deadline=40.0,
            )
        )
        active_events = runtime._maintain_tixcraft_refresh_runtime(
            tab,
            area_one,
            config,
            now_monotonic=32.0,
        )
        active_result = {
            "events": sorted(active_events),
            "token": state["pending_area_navigation"].token,
            "click": scheduler.area_click_pending,
        }
        deadline_events = runtime._maintain_tixcraft_refresh_runtime(
            tab,
            area_one,
            config,
            now_monotonic=40.0,
        )
        deadline_result = {
            "events": sorted(deadline_events),
            "area": state.get("pending_area_navigation"),
            "click": scheduler.area_click_pending,
        }

        state["pending_date_navigation"] = (
            platform.TixCraftPendingNavigation(
                kind="date",
                source_url=area_one,
                event_id="event-one",
                game_id="game-one",
                flow_generation=1,
                token=8,
                tab_identity=id(tab),
                started_at=30.0,
                deadline=60.0,
            )
        )
        state["current_event_id"] = "event-two"
        switched = runtime._maintain_tixcraft_refresh_runtime(
            tab,
            area_two,
            config,
            now_monotonic=31.0,
        )
        switched_result = {
            "events": sorted(switched),
            "date": state.get("pending_date_navigation"),
        }

        state["current_event_id"] = "event-one"
        state["current_game_id"] = "game-one"
        state["pending_date_navigation"] = (
            platform.TixCraftPendingNavigation(
                kind="date",
                source_url=area_one,
                event_id="event-one",
                game_id="game-one",
                flow_generation=1,
                token=10,
                tab_identity=id(Tab(area_one)),
                started_at=31.0,
                deadline=60.0,
            )
        )
        tab_switched = runtime._maintain_tixcraft_refresh_runtime(
            tab,
            area_one,
            config,
            now_monotonic=32.0,
        )
        tab_result = {
            "events": sorted(tab_switched),
            "date": state.get("pending_date_navigation"),
        }

        print(
            "RESULT_JSON="
            + json.dumps(
                {
                    "expired": expired_result,
                    "active": active_result,
                    "deadline": deadline_result,
                    "switched": switched_result,
                    "tab": tab_result,
                },
                sort_keys=True,
            )
        )
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload_line = next(
        line for line in result.stdout.splitlines() if line.startswith("RESULT_JSON=")
    )
    payload = json.loads(payload_line.removeprefix("RESULT_JSON="))

    assert payload["expired"] == {
        "events": [
            "area_click_pending_expired",
            "dom_scan_pending_expired",
            "pending_area_expired",
            "recovery_scan_expired",
        ],
        "area": None,
        "recovery": False,
        "dom": False,
        "click": False,
    }
    assert payload["active"] == {
        "events": [],
        "token": 9,
        "click": True,
    }
    assert payload["deadline"] == {
        "events": [
            "area_click_pending_expired",
            "pending_area_expired",
        ],
        "area": None,
        "click": False,
    }
    assert payload["switched"] == {
        "events": ["pending_date_context_changed"],
        "date": None,
    }
    assert payload["tab"] == {
        "events": ["pending_date_context_changed"],
        "date": None,
    }


def test_gate_soft_block_preflight_and_leak_scheduler_coordination() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")
    env["HUNTERX_SRC_PATH"] = str(repo_root / "src")
    script = textwrap.dedent(
        r"""
        import asyncio
        import json
        import os
        import sys
        import time
        from datetime import datetime

        sys.path.insert(0, os.environ["HUNTERX_SRC_PATH"])
        import nodriver_tixcraft as runtime
        import platforms.tixcraft as platform
        from leak_watch import LeakWatchScheduler
        from refresh_timing import (
            Clock,
            NS_PER_MS,
            RefreshTriggerController,
            resolve_refresh_timezone,
        )


        class FakeClock(Clock):
            def __init__(self, wall_ns, mono_ns):
                self.wall_ns = wall_ns
                self.mono_ns = mono_ns

            def wall_time_ns(self):
                return self.wall_ns

            def monotonic_ns(self):
                return self.mono_ns

            def advance_ms(self, value):
                self.wall_ns += value * NS_PER_MS
                self.mono_ns += value * NS_PER_MS


        class Target:
            def __init__(self, url):
                self.url = url


        class Tab:
            def __init__(self, url):
                self.target = Target(url)


        target = datetime(
            2026,
            7,
            26,
            12,
            0,
            0,
            tzinfo=resolve_refresh_timezone("Asia/Taipei"),
        )
        area_url = "https://tixcraft.com/ticket/area/event/game"


        def config():
            return {
                "refresh_datetime": target.strftime(
                    "%Y/%m/%d %H:%M:%S.%f"
                )[:-3],
                "advanced": {
                    "run_mode": "leak_watch",
                    "leak_refresh_interval_seconds": 3,
                },
                "time_calibration": {"mode": "system"},
            }


        def gate_state(clock):
            return {
                "state_key": "",
                "target_str": "",
                "reached": False,
                "last_countdown_print": 0.0,
                "reported_plan": False,
                "controller": RefreshTriggerController(clock=clock),
            }


        async def run():
            results = {}
            tab = Tab(area_url)
            state_data = platform._state_for_tab(tab)
            base_mono_ns = time.monotonic_ns()

            # A safe-looking /area URL with a real EPS DOM must not be reloaded.
            state_data.clear()
            state_data.update({"leak_scheduler": LeakWatchScheduler()})
            eps_probes = []
            reloads = []

            health_snapshot = {
                "blocked": True,
                "kind": "eps_js",
                "readyState": "complete",
                "hasBody": True,
            }

            async def eps_probe(*_args, **_kwargs):
                eps_probes.append("probe")
                return dict(health_snapshot)

            async def forbidden_reload(*_args, **kwargs):
                reloads.append(kwargs.get("reason", ""))
                return True

            runtime.tixcraft_platform._read_tixcraft_page_health = eps_probe
            runtime.guarded_reload = forbidden_reload
            clock = FakeClock(
                int((target.timestamp() - 3) * 1_000_000_000),
                base_mono_ns,
            )
            state = gate_state(clock)
            countdown = await runtime.check_refresh_datetime_gate(
                tab, config(), state, area_url
            )
            probes_during_countdown = len(eps_probes)
            clock.advance_ms(3_000)
            target_gate = await runtime.check_refresh_datetime_gate(
                tab, config(), state, area_url
            )
            followup = await runtime.check_refresh_datetime_gate(
                tab, config(), state, area_url
            )
            results["eps"] = {
                "countdown": countdown,
                "target": target_gate,
                "followup": followup,
                "probes_during_countdown": probes_during_countdown,
                "probes": len(eps_probes),
                "reloads": reloads,
                "decision": state["last_refresh_reload_decision"],
            }

            unsafe_snapshots = {
                "text": {
                    "blocked": False,
                    "readyState": "complete",
                    "hasBody": True,
                    "hasKnownContent": False,
                    "bodyText": "Your browsing activity has been paused",
                    "title": "",
                    "elementCount": 10,
                },
                "white": {
                    "blocked": False,
                    "readyState": "complete",
                    "hasBody": True,
                    "hasKnownContent": False,
                    "bodyText": "",
                    "title": "",
                    "elementCount": 10,
                    "whiteOverlay": True,
                },
                "blank": {
                    "blocked": False,
                    "readyState": "complete",
                    "hasBody": True,
                    "hasKnownContent": False,
                    "bodyText": "",
                    "title": "",
                    "elementCount": 2,
                },
                "order": {
                    "blocked": False,
                    "readyState": "complete",
                    "hasBody": True,
                    "hasKnownContent": False,
                    "bodyText": "",
                    "title": "",
                    "elementCount": 2,
                    "knownOrderProcessing": True,
                },
            }
            results["unsafe_kinds"] = {}
            for kind, snapshot in unsafe_snapshots.items():
                health_snapshot.clear()
                health_snapshot.update(snapshot)
                state_data.clear()
                state_data.update(
                    {"leak_scheduler": LeakWatchScheduler()}
                )
                clock = FakeClock(
                    int(target.timestamp() * 1_000_000_000),
                    time.monotonic_ns(),
                )
                state = gate_state(clock)
                before_reload_count = len(reloads)
                gate_active = await runtime.check_refresh_datetime_gate(
                    tab, config(), state, area_url
                )
                results["unsafe_kinds"][kind] = {
                    "gate_active": gate_active,
                    "decision": state["last_refresh_reload_decision"],
                    "reload_count": len(reloads) - before_reload_count,
                }

            # A successful scheduled reload advances the leak scheduler
            # cooldown and holds platform dispatch for that iteration.
            state_data.clear()
            scheduler = LeakWatchScheduler()
            state_data.update({"leak_scheduler": scheduler})
            calls = []

            async def safe_probe(*_args, **_kwargs):
                return {
                    "blocked": False,
                    "readyState": "complete",
                    "hasBody": True,
                    "hasKnownContent": True,
                    "bodyText": "",
                    "title": "",
                    "elementCount": 1,
                }

            async def scheduled_reload(*_args, **kwargs):
                calls.append(kwargs.get("reason", ""))
                return True

            async def area_reload(*_args, **kwargs):
                calls.append(kwargs.get("reason", ""))
                return True

            async def stale_dom(*_args, **_kwargs):
                return False

            runtime.tixcraft_platform._read_tixcraft_page_health = safe_probe
            runtime.guarded_reload = scheduled_reload
            platform.guarded_reload = area_reload
            runtime._read_tixcraft_sale_dom_ready = stale_dom
            clock = FakeClock(
                int(target.timestamp() * 1_000_000_000),
                time.monotonic_ns(),
            )
            state = gate_state(clock)
            first_gate_active = await runtime.check_refresh_datetime_gate(
                tab, config(), state, area_url
            )
            binding = platform._state.bind(state_data)
            try:
                area_reloaded = await platform._reload_page_when_due(
                    tab,
                    config(),
                    "tixcraft_area_reload",
                    "[TEST]",
                )
            finally:
                platform._state.reset_binding(binding)
            calls_after_area = list(calls)
            clock.advance_ms(500)
            stale_gate_active = await runtime.check_refresh_datetime_gate(
                tab, config(), state, area_url
            )
            results["scheduler"] = {
                "first_gate_active": first_gate_active,
                "area_reloaded": area_reloaded,
                "calls_after_area": calls_after_area,
                "calls": calls,
                "stale_gate_active": stale_gate_active,
                "next_cycle_in_future": (
                    scheduler.next_cycle_at > time.monotonic()
                ),
            }
            print("RESULT_JSON=" + json.dumps(results, sort_keys=True))


        asyncio.run(run())
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload_line = next(
        line for line in result.stdout.splitlines() if line.startswith("RESULT_JSON=")
    )
    payload = json.loads(payload_line.removeprefix("RESULT_JSON="))

    assert payload["eps"] == {
        "countdown": True,
        "target": True,
        "followup": True,
        "probes_during_countdown": 1,
        # A blocked boundary is re-confirmed rather than converted into an
        # unavailable-health deadlock.  The scheduled refresh remains blocked.
        "probes": 2,
        "reloads": [],
        "decision": "soft_block_detected",
    }
    assert payload["unsafe_kinds"] == {
        "text": {
            "gate_active": True,
            "decision": "soft_block_detected",
            "reload_count": 0,
        },
        "white": {
            "gate_active": True,
            "decision": "soft_block_detected",
            "reload_count": 0,
        },
        "blank": {
            "gate_active": True,
            "decision": "soft_block_detected",
            "reload_count": 0,
        },
        "order": {
            "gate_active": False,
            "decision": "order_processing_detected",
            "reload_count": 0,
        },
    }
    assert payload["scheduler"] == {
        "first_gate_active": True,
        "area_reloaded": False,
        "calls_after_area": ["refresh_datetime_trigger"],
        "calls": [
            "refresh_datetime_trigger",
            "refresh_datetime_stale_response_retry",
        ],
        "stale_gate_active": True,
        "next_cycle_in_future": True,
    }
