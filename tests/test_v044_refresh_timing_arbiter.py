from __future__ import annotations

import asyncio
import ast
import json
import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import nodriver_common
from refresh_timing import (
    NS_PER_MS,
    Clock,
    RefreshTriggerController,
    compute_trigger_plan,
)
from trigger_arbiter import TriggerReloadArbiter


class FakeClock(Clock):
    def __init__(self, wall_ns: int, monotonic_ns: int) -> None:
        self.wall_ns = wall_ns
        self.mono_ns = monotonic_ns

    def wall_time_ns(self) -> int:
        return self.wall_ns

    def monotonic_ns(self) -> int:
        return self.mono_ns

    def advance_ms(self, value: int, *, wall: bool = True) -> None:
        delta = value * NS_PER_MS
        if wall:
            self.wall_ns += delta
        self.mono_ns += delta


def test_trusted_ntp_offset_corrects_local_clock_without_rtt_advance() -> None:
    target = datetime(2026, 7, 26, 12, 0, 0)

    plan = compute_trigger_plan(
        target,
        {
            "source": "ntp",
            "confidence": "high",
            "clock_offset_ms": 40,
            "clock_uncertainty_ms": 3,
            "sample_count": 5,
            "network_uplink_ms": 900,
            "frontend_delay_ms": 700,
            "scheduler_jitter_ms": 500,
            "safety_margin_ms": 300,
        },
    )

    assert plan.clock_offset_ms == 40
    assert plan.clock_uncertainty_ms == 3
    assert plan.local_trigger_time == target - timedelta(milliseconds=40)
    assert plan.computed_trigger_reference_time == target
    assert plan.total_advance_ms == 0
    assert plan.ticket_network_budget_ms == 0
    assert plan.frontend_budget_ms == 0
    assert plan.scheduler_budget_ms == 0
    assert any("RTT" in warning for warning in plan.warnings)


def test_trusted_zero_ntp_offset_retains_calibration_confidence() -> None:
    target = datetime(2026, 7, 26, 12, 0, 0)

    plan = compute_trigger_plan(
        target,
        {
            "source": "ntp",
            "confidence": "high",
            "clock_offset_ms": 0,
            "clock_uncertainty_ms": 2,
            "sample_count": 3,
        },
    )

    assert plan.local_trigger_time == target
    assert plan.clock_offset_ms == 0
    assert plan.clock_uncertainty_ms == 2
    assert plan.confidence == "high"
    assert any("trusted NTP" in warning for warning in plan.warnings)


@pytest.mark.parametrize(
    "calibration",
    [
        {
            "source": "http",
            "confidence": "high",
            "clock_offset_ms": 400,
            "clock_uncertainty_ms": 1,
        },
        {
            "source": "ntp",
            "confidence": "low",
            "clock_offset_ms": 40,
            "clock_uncertainty_ms": 1,
            "sample_count": 3,
        },
        {
            "source": "ntp",
            "confidence": "high",
            "clock_offset_ms": 40,
            "clock_uncertainty_ms": 500,
            "sample_count": 3,
        },
        {
            "time_source_mode": "ntp",
            "confidence": "high",
            "clock_offset_ms": 40,
            "clock_uncertainty_ms": 1,
            "sample_count": 3,
        },
        {
            "source": "ntp",
            "confidence": "high",
            "clock_offset_ms": 40,
            "clock_uncertainty_ms": 1,
            "sample_count": 0,
        },
        {
            "source": "ntp",
            "confidence": "high",
            "clock_offset_ms": 40,
            "sample_count": 3,
        },
    ],
)
def test_untrusted_clock_offset_is_not_applied(calibration: dict[str, Any]) -> None:
    target = datetime(2026, 7, 26, 12, 0, 0)

    plan = compute_trigger_plan(target, calibration)

    assert plan.clock_offset_ms == 0
    assert plan.local_trigger_time == target
    assert plan.total_advance_ms == 0


def test_controller_anchors_wall_target_to_monotonic_once() -> None:
    target = datetime(2026, 7, 26, 12, 0, 10)
    clock = FakeClock(
        int(datetime(2026, 7, 26, 12, 0, 0).timestamp() * 1_000_000_000),
        20_000_000_000,
    )
    controller = RefreshTriggerController(clock=clock)

    first_plan = controller.arm(target, {}, "same-target")
    first_deadline = controller.trigger_deadline_monotonic_ns

    # A wall-clock correction after arming must not move the already-anchored
    # sale deadline. Only monotonic elapsed time may change the remaining time.
    clock.wall_ns += 30_000 * NS_PER_MS
    second_plan = controller.arm(target, {}, "same-target")

    assert second_plan is first_plan
    assert controller.trigger_deadline_monotonic_ns == first_deadline
    assert controller.remaining_ns() == 10_000 * NS_PER_MS

    clock.advance_ms(10_000, wall=False)
    assert controller.remaining_ns() == 0
    assert controller.should_trigger_once() is True


@dataclass
class _Target:
    url: str


class _UrlTab:
    def __init__(self, target_url: str, js_url: str) -> None:
        self.target = _Target(target_url)
        self.js_url = js_url
        self.js_calls = 0

    async def js_dumps(self, script: str) -> str:
        assert script == "window.location.href"
        self.js_calls += 1
        return self.js_url


@pytest.mark.asyncio
async def test_cached_target_url_fast_path_avoids_javascript_round_trip() -> None:
    cached = "https://tixcraft.com/activity/game/26_event"
    tab = _UrlTab(cached, "https://tixcraft.com/activity/detail/stale")

    url, is_quit_bot = await nodriver_common.nodriver_current_url(
        tab,
        prefer_cached=True,
    )

    assert url == cached
    assert is_quit_bot is False
    assert tab.js_calls == 0


@pytest.mark.asyncio
async def test_cached_target_url_fast_path_rejects_about_blank() -> None:
    current = "https://tixcraft.com/activity/game/26_event"
    tab = _UrlTab("about:blank", current)

    url, is_quit_bot = await nodriver_common.nodriver_current_url(
        tab,
        prefer_cached=True,
    )

    assert url == current
    assert is_quit_bot is False
    assert tab.js_calls == 1


class _ReloadTab:
    def __init__(self, url: str) -> None:
        self.target = _Target(url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "runtime_state", "expected_reason"),
    [
        ("https://queue-it.net/?c=tixcraft", {}, "protected_page"),
        ("https://tixcraft.com/ticket/checkout/abc", {}, "protected_page"),
        (
            "https://tixcraft.com/activity/game/abc",
            {"soft_block_recovery_in_progress": True},
            "soft_block_recovery",
        ),
        (
            "https://tixcraft.com/activity/game/abc",
            {"soft_block_recovery_scan_pending": True},
            "recovery_scan_pending",
        ),
        (
            "https://tixcraft.com/activity/game/abc",
            {"pending_area_navigation": object()},
            "navigation_pending",
        ),
        (
            "https://tixcraft.com/activity/game/abc",
            {"soft_block_phase": "backoff"},
            "soft_block_backoff",
        ),
        (
            "https://tixcraft.com/activity/game/abc",
            {"soft_block_phase": "recovering"},
            "soft_block_recovery",
        ),
        (
            "https://tixcraft.com/activity/game/abc",
            {"soft_block_backoff_until": 101.0},
            "soft_block_backoff",
        ),
        (
            "https://tixcraft.com/activity/game/abc",
            {"soft_block_recovery_retry_at": 101.0},
            "soft_block_recovery",
        ),
    ],
)
async def test_trigger_arbiter_blocks_protected_runtime_states(
    url: str,
    runtime_state: dict[str, Any],
    expected_reason: str,
) -> None:
    calls = 0

    async def reload_callable(*args: Any, **kwargs: Any) -> bool:
        nonlocal calls
        calls += 1
        return True

    decision = await TriggerReloadArbiter().request_reload(
        _ReloadTab(url),
        current_url=url,
        runtime_state=runtime_state,
        reason="refresh_datetime_trigger",
        reload_callable=reload_callable,
        now_monotonic=100.0,
    )

    assert decision.attempted is False
    assert decision.reloaded is False
    assert decision.reason == expected_reason
    assert calls == 0


@pytest.mark.asyncio
async def test_trigger_arbiter_is_single_flight() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def reload_callable(*args: Any, **kwargs: Any) -> bool:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return True

    url = "https://tixcraft.com/activity/game/abc"
    arbiter = TriggerReloadArbiter()
    first_task = asyncio.create_task(
        arbiter.request_reload(
            _ReloadTab(url),
            current_url=url,
            runtime_state={},
            reason="refresh_datetime_trigger",
            reload_callable=reload_callable,
        )
    )
    await started.wait()
    second = await arbiter.request_reload(
        _ReloadTab(url),
        current_url=url,
        runtime_state={},
        reason="refresh_datetime_trigger_retry",
        reload_callable=reload_callable,
    )
    release.set()
    first = await first_task

    assert first.reloaded is True
    assert second.reloaded is False
    assert second.reason == "reload_in_flight"
    assert calls == 1


@pytest.mark.asyncio
async def test_trigger_arbiter_blocks_active_backoff_and_scheduler_pending() -> None:
    class Scheduler:
        reload_pending = False
        dom_scan_pending = True
        area_click_pending = False
        ticket_form_pending = False
        submit_pending = False

    async def forbidden(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("protected runtime state must not call reload")

    url = "https://tixcraft.com/activity/game/abc"
    arbiter = TriggerReloadArbiter()
    backoff = await arbiter.request_reload(
        _ReloadTab(url),
        current_url=url,
        runtime_state={"ip_block_until": 101.0},
        reason="refresh_datetime_trigger",
        reload_callable=forbidden,
        now_monotonic=100.0,
    )
    pending = await arbiter.request_reload(
        _ReloadTab(url),
        current_url=url,
        runtime_state={"leak_scheduler": Scheduler()},
        reason="refresh_datetime_trigger",
        reload_callable=forbidden,
        now_monotonic=100.0,
    )

    assert backoff.reason == "soft_block_backoff"
    assert pending.reason == "dom_scan_pending"


def test_main_loop_wires_cached_url_fast_path_into_scheduled_gate() -> None:
    source_path = Path(__file__).resolve().parents[1] / "src" / "nodriver_tixcraft.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    current_url_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "nodriver_current_url"
    ]

    assert any(
        any(
            keyword.arg == "prefer_cached"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "prefer_cached_url"
            for keyword in call.keywords
        )
        for call in current_url_calls
    )


def test_refresh_gate_contains_no_direct_reload_call() -> None:
    source_path = Path(__file__).resolve().parents[1] / "src" / "nodriver_tixcraft.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    gate = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "check_refresh_datetime_gate"
    )
    direct_guarded_calls = [
        node
        for node in ast.walk(gate)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "guarded_reload"
    ]

    assert direct_guarded_calls == []


def test_refresh_gate_integrates_failure_and_stale_response_retries() -> None:
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
        from refresh_timing import Clock, NS_PER_MS, RefreshTriggerController


        class FakeClock(Clock):
            def __init__(self, wall_ns, mono_ns):
                self.wall_ns = wall_ns
                self.mono_ns = mono_ns

            def wall_time_ns(self):
                return self.wall_ns

            def monotonic_ns(self):
                return self.mono_ns

            def advance_ms(self, value):
                self.mono_ns += value * NS_PER_MS


        class Target:
            def __init__(self, url):
                self.url = url


        class Tab:
            def __init__(self, url):
                self.target = Target(url)


        class DomTab(Tab):
            def __init__(self, url, dom_result):
                super().__init__(url)
                self.dom_result = dom_result
                self.dom_scripts = []

            async def evaluate(self, script):
                self.dom_scripts.append(script)
                return self.dom_result


        target = datetime(2026, 7, 26, 12, 0, 0)
        safe_url = "https://tixcraft.com/activity/game/26_event"
        area_url = "https://tixcraft.com/ticket/area/26_event/1"
        protected_url = "https://tixcraft.com/ticket/checkout/26_event"


        def config():
            return {
                "refresh_datetime": target.strftime("%Y/%m/%d %H:%M:%S.%f")[:-3],
                "refresh_calibration": {
                    "source": "system",
                    "confidence": "low",
                    "clock_offset_ms": 0,
                },
            }


        def state(clock):
            return {
                "state_key": "",
                "target_str": "",
                "reached": False,
                "last_countdown_print": 0.0,
                "reported_plan": False,
                "controller": RefreshTriggerController(clock=clock),
            }


        async def run():
            runtime._get_trigger_runtime_state = lambda _url: {}
            real_dom_reader = runtime._read_tixcraft_sale_dom_ready

            async def safe_health(*_args, **_kwargs):
                return {
                    "blocked": False,
                    "readyState": "complete",
                    "hasBody": True,
                    "hasKnownContent": True,
                    "bodyText": "",
                    "title": "",
                    "elementCount": 1,
                }

            runtime.tixcraft_platform._read_tixcraft_page_health = safe_health

            async def stale_dom(*_args, **_kwargs):
                return False

            runtime._read_tixcraft_sale_dom_ready = stale_dom
            results = {}

            clock = FakeClock(int(target.timestamp() * 1_000_000_000), 50_000_000_000)
            tab = Tab(safe_url)
            gate_state = state(clock)
            outcomes = iter((False, True))
            reasons = []

            async def failed_then_ok(*_args, **kwargs):
                reasons.append(kwargs.get("reason", ""))
                return next(outcomes)

            runtime.guarded_reload = failed_then_ok
            first_active = await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, tab.target.url
            )
            clock.advance_ms(199)
            before_retry_active = await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, tab.target.url
            )
            clock.advance_ms(1)
            after_retry_active = await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, tab.target.url
            )
            results["failed_then_ok"] = {
                "reasons": reasons,
                "first_active": first_active,
                "before_retry_active": before_retry_active,
                "after_retry_active": after_retry_active,
                "reached": gate_state["reached"],
            }

            clock = FakeClock(int(target.timestamp() * 1_000_000_000), 60_000_000_000)
            tab = Tab(safe_url)
            gate_state = state(clock)
            reasons = []

            async def always_ok(*_args, **kwargs):
                reasons.append(kwargs.get("reason", ""))
                return True

            runtime.guarded_reload = always_ok
            await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, tab.target.url
            )
            armed_after_first = gate_state["post_boundary_retry_pending"]
            clock.advance_ms(499)
            await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, tab.target.url
            )
            count_before_boundary_retry = len(reasons)
            clock.advance_ms(1)
            await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, tab.target.url
            )
            await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, tab.target.url
            )
            results["stale_route"] = {
                "reasons": reasons,
                "armed_after_first": armed_after_first,
                "count_before_boundary_retry": count_before_boundary_retry,
                "pending": gate_state["post_boundary_retry_pending"],
            }

            clock = FakeClock(int(target.timestamp() * 1_000_000_000), 65_000_000_000)
            tab = Tab(safe_url)
            gate_state = state(clock)
            reasons = []
            runtime.guarded_reload = always_ok

            async def advancing_dom(probe_tab, *_args, **_kwargs):
                probe_tab.target.url = area_url
                return False

            runtime._read_tixcraft_sale_dom_ready = advancing_dom
            await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, tab.target.url
            )
            clock.advance_ms(500)
            await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, safe_url
            )
            results["route_advanced_during_probe"] = {
                "reasons": list(reasons),
                "current_url": tab.target.url,
                "pending": gate_state["post_boundary_retry_pending"],
            }

            clock = FakeClock(int(target.timestamp() * 1_000_000_000), 70_000_000_000)
            tab = Tab(safe_url)
            gate_state = state(clock)
            reasons = []
            runtime.guarded_reload = always_ok
            await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, tab.target.url
            )
            tab.target.url = "https://tixcraft.com/ticket/area/26_event/1"
            clock.advance_ms(500)
            await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, tab.target.url
            )
            results["route_advanced"] = {
                "reasons": list(reasons),
                "pending": gate_state["post_boundary_retry_pending"],
            }

            clock = FakeClock(int(target.timestamp() * 1_000_000_000), 75_000_000_000)
            tab = DomTab(area_url, True)
            gate_state = state(clock)
            reasons = []
            runtime.guarded_reload = always_ok
            runtime._read_tixcraft_sale_dom_ready = real_dom_reader
            await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, tab.target.url
            )
            clock.advance_ms(500)
            await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, tab.target.url
            )
            results["ready_area"] = {
                "reasons": list(reasons),
                "pending": gate_state["post_boundary_retry_pending"],
                "zone_probed": any(".zone" in script for script in tab.dom_scripts),
            }

            clock = FakeClock(int(target.timestamp() * 1_000_000_000), 77_000_000_000)
            tab = DomTab(area_url, None)
            gate_state = state(clock)
            reasons = []
            runtime.guarded_reload = always_ok
            await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, tab.target.url
            )
            clock.advance_ms(500)
            await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, tab.target.url
            )
            results["unknown_dom"] = {
                "reasons": list(reasons),
                "pending": gate_state["post_boundary_retry_pending"],
            }

            future_target = target + timedelta(seconds=120)
            future_config = config()
            future_config["refresh_datetime"] = (
                future_target.strftime("%Y/%m/%d %H:%M:%S.%f")[:-3]
            )
            clock = FakeClock(int(target.timestamp() * 1_000_000_000), 78_000_000_000)
            tab = Tab(safe_url)
            gate_state = state(clock)
            calls = []

            async def must_not_resume_reload(*_args, **kwargs):
                calls.append(kwargs.get("reason", ""))
                return True

            runtime.guarded_reload = must_not_resume_reload
            before_suspend_active = await runtime.check_refresh_datetime_gate(
                tab, future_config, gate_state, tab.target.url
            )
            clock.wall_ns += 3_600_000 * NS_PER_MS
            clock.advance_ms(3_600_000)
            after_resume_active = await runtime.check_refresh_datetime_gate(
                tab, future_config, gate_state, tab.target.url
            )
            results["resume_late"] = {
                "calls": calls,
                "before_suspend_active": before_suspend_active,
                "after_resume_active": after_resume_active,
                "reached": gate_state["reached"],
                "phase": gate_state["controller"].phase.value,
            }

            clock = FakeClock(int(target.timestamp() * 1_000_000_000), 80_000_000_000)
            tab = Tab(protected_url)
            gate_state = state(clock)
            calls = []

            async def forbidden(*_args, **kwargs):
                calls.append(kwargs.get("reason", ""))
                return True

            runtime.guarded_reload = forbidden
            await runtime.check_refresh_datetime_gate(
                tab, config(), gate_state, tab.target.url
            )
            results["protected"] = {
                "calls": calls,
                "reached": gate_state["reached"],
                "decision": gate_state["last_refresh_reload_decision"],
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

    assert payload["failed_then_ok"] == {
        "reasons": [
            "refresh_datetime_trigger",
            "refresh_datetime_trigger_retry",
        ],
        "first_active": True,
        "before_retry_active": True,
        "after_retry_active": True,
        "reached": True,
    }
    assert payload["stale_route"] == {
        "reasons": [
            "refresh_datetime_trigger",
            "refresh_datetime_stale_response_retry",
        ],
        "armed_after_first": True,
        "count_before_boundary_retry": 1,
        "pending": False,
    }
    assert payload["route_advanced_during_probe"] == {
        "reasons": ["refresh_datetime_trigger"],
        "current_url": "https://tixcraft.com/ticket/area/26_event/1",
        "pending": False,
    }
    assert payload["route_advanced"] == {
        "reasons": ["refresh_datetime_trigger"],
        "pending": False,
    }
    assert payload["ready_area"] == {
        "reasons": ["refresh_datetime_trigger"],
        "pending": False,
        "zone_probed": True,
    }
    assert payload["unknown_dom"] == {
        "reasons": ["refresh_datetime_trigger"],
        "pending": False,
    }
    assert payload["resume_late"] == {
        "calls": [],
        "before_suspend_active": True,
        "after_resume_active": False,
        "reached": True,
        "phase": "STOPPED",
    }
    assert payload["protected"] == {
        "calls": [],
        "reached": True,
        "decision": "protected_page",
    }


def test_successful_reload_invalidates_health_evidence_before_stale_retry() -> None:
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

        sys.path.insert(0, os.environ["HUNTERX_SRC_PATH"])
        import nodriver_tixcraft as runtime
        from page_classifier import PageClass
        from trigger_arbiter import TriggerReloadDecision


        class Clock:
            def monotonic_ns(self):
                return 1_000_000_000


        class Controller:
            clock = Clock()
            generation = 1


        class Target:
            url = "https://tixcraft.com/activity/game/26_event"


        class Tab:
            target = Target()


        class Arbiter:
            def __init__(self):
                self.requests = 0

            async def request_reload(self, *_args, **_kwargs):
                self.requests += 1
                return TriggerReloadDecision(
                    attempted=True,
                    reloaded=True,
                    reason="reloaded",
                    page_class=PageClass.ACTIVITY,
                )


        async def run():
            tab = Tab()
            arbiter = Arbiter()
            state = {
                "controller": Controller(),
                "refresh_soft_block_preflight_token": ("old",),
                "refresh_soft_block_preflight_reason": "ready",
                "refresh_gate_health_next_probe_at": 999999.0,
                "refresh_gate_health_ready_route": "/activity/game/26_event",
                "refresh_gate_health_ready_at": runtime.time.monotonic(),
            }
            runtime._get_trigger_arbiter = lambda _state: arbiter
            runtime._mark_tixcraft_scheduled_reload_landed = (
                lambda *_args, **_kwargs: None
            )

            first = await runtime._request_refresh_datetime_reload(
                tab,
                {},
                state,
                tab.target.url,
                "refresh_datetime_trigger",
            )

            probes = 0

            async def blocked_probe(*_args, **_kwargs):
                nonlocal probes
                probes += 1
                return {"blocked": True}

            runtime.tixcraft_platform._read_tixcraft_page_health = blocked_probe
            preflight = await runtime._preflight_tixcraft_refresh_boundary(
                tab,
                tab.target.url,
                {},
                state,
                "stale_response_retry",
            )
            if preflight is None:
                await runtime._request_refresh_datetime_reload(
                    tab,
                    {},
                    state,
                    tab.target.url,
                    "refresh_datetime_stale_response_retry",
                )

            print(
                "RESULT_JSON="
                + json.dumps(
                    {
                        "first_reloaded": first.reloaded,
                        "requests": arbiter.requests,
                        "probes": probes,
                        "preflight_reason": getattr(preflight, "reason", ""),
                        "ready_route": state["refresh_gate_health_ready_route"],
                        "ready_at": state["refresh_gate_health_ready_at"],
                        "next_probe_at": state[
                            "refresh_gate_health_next_probe_at"
                        ],
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
        "first_reloaded": True,
        "next_probe_at": 0.0,
        "preflight_reason": "soft_block_detected",
        "probes": 1,
        "ready_at": 0.0,
        "ready_route": "",
        "requests": 1,
    }
