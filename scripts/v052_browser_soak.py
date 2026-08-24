#!/usr/bin/env python3
"""Actual-browser local synthetic soak; never contacts a ticketing service."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import zendriver as uc

import settings
from browser_session import BrowserSessionManager
from dom_drift import SelectorCandidate, resolve_selector
from nodriver_common import (
    get_extension_config,
    nodriver_overwrite_prefs,
)
from nodriver_tixcraft import RuntimeIterationContext, run_runtime_iteration
from page_classifier import PageClass
from runtime_diagnostics import collect_runtime_diagnostics
from runtime_health import RuntimeHealthSupervisor, classify_browser_exception


@dataclass
class SoakResult:
    instance: str
    duration_seconds: float
    cycles: int = 0
    target_replacements: int = 0
    reload_injections: int = 0
    success_continue_cycles: int = 0
    login_restore_cycles: int = 0
    fallback_resolutions: int = 0
    duplicate_submit_claims: int = 0
    errors: int = 0
    cdp_errors: int = 0
    recovery_count: int = 0
    state_transition_count: int = 0
    max_tab_count: int = 0
    task_count_start: int = 0
    task_count_end: int = 0
    task_count_max: int = 0
    asyncio_task_count_start: int = 0
    asyncio_task_count_end: int = 0
    asyncio_task_count_max: int = 0
    browser_action_count_max: int = 0
    cdp_mapper_count_start: int = 0
    cdp_mapper_count_end: int = 0
    cdp_mapper_count_max: int = 0
    hunterx_rss_start: int | None = None
    hunterx_rss_end: int | None = None
    hunterx_rss_max: int | None = None
    browser_rss_start: int | None = None
    browser_rss_end: int | None = None
    browser_rss_max: int | None = None
    hunterx_rss_samples: list[int] = field(default_factory=list)
    browser_rss_samples: list[int] = field(default_factory=list)
    stalled_seconds_max: float = 0.0


@dataclass
class SyntheticRouteState:
    platform: str
    stage: str
    cycle: int


def _max_optional(current: int | None, value: int | None) -> int | None:
    if value is None:
        return current
    return value if current is None else max(current, value)


def _zendriver_mapper_count(driver: Any) -> int:
    connections: list[Any] = [driver]
    tabs = getattr(driver, "tabs", ())
    if isinstance(tabs, (tuple, list)):
        connections.extend(tabs)
    total = 0
    for connection in connections:
        mapper = getattr(connection, "mapper", None)
        if isinstance(mapper, dict):
            total += len(mapper)
    return total


def _sample_resources(
    result: SoakResult,
    manager: BrowserSessionManager,
    health: RuntimeHealthSupervisor,
    *,
    initial: bool = False,
) -> None:
    snapshot = collect_runtime_diagnostics(manager, health)
    asyncio_task_count = len(asyncio.all_tasks())
    mapper_count = _zendriver_mapper_count(manager.driver)
    if initial:
        result.hunterx_rss_start = snapshot.hunterx_rss_bytes
        result.browser_rss_start = snapshot.browser_rss_bytes
        result.task_count_start = snapshot.hunterx_task_count
        result.asyncio_task_count_start = asyncio_task_count
        result.cdp_mapper_count_start = mapper_count
    result.hunterx_rss_end = snapshot.hunterx_rss_bytes
    result.browser_rss_end = snapshot.browser_rss_bytes
    result.task_count_end = snapshot.hunterx_task_count
    result.asyncio_task_count_end = asyncio_task_count
    result.cdp_mapper_count_end = mapper_count
    result.cdp_mapper_count_max = max(result.cdp_mapper_count_max, mapper_count)
    result.hunterx_rss_max = _max_optional(
        result.hunterx_rss_max,
        snapshot.hunterx_rss_bytes,
    )
    result.browser_rss_max = _max_optional(
        result.browser_rss_max,
        snapshot.browser_rss_bytes,
    )
    if snapshot.hunterx_rss_bytes is not None:
        result.hunterx_rss_samples.append(snapshot.hunterx_rss_bytes)
    if snapshot.browser_rss_bytes is not None:
        result.browser_rss_samples.append(snapshot.browser_rss_bytes)
    result.task_count_max = max(result.task_count_max, snapshot.hunterx_task_count)
    result.asyncio_task_count_max = max(
        result.asyncio_task_count_max,
        asyncio_task_count,
    )
    result.browser_action_count_max = max(
        result.browser_action_count_max,
        snapshot.browser_action_count,
    )
    result.recovery_count = snapshot.recovery_count


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


def _start_server() -> tuple[ThreadingHTTPServer, str]:
    fixture_dir = REPO_ROOT / "tests" / "fixtures"

    def handler(*args: Any, **kwargs: Any) -> _QuietHandler:
        return _QuietHandler(*args, directory=str(fixture_dir), **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}/synthetic_ticket_spa.html"


def _synthetic_platform_url(platform_key: str, stage: str, cycle: int) -> str:
    if platform_key == "tixcraft":
        if stage == "protected":
            return f"https://tixcraft.com/ticket/ticket/synthetic/{cycle}"
        if stage == "area":
            return "https://tixcraft.com/ticket/area/synthetic/1"
        return "https://tixcraft.com/activity/detail/synthetic"
    if platform_key == "kktix":
        if stage == "protected":
            return f"https://kktix.com/events/synthetic/registrations/{cycle}"
        if stage == "area":
            return "https://kktix.com/events/synthetic/registrations/new"
        return "https://kktix.com/events/synthetic"
    if stage == "protected":
        return f"https://ticketplus.com.tw/confirm/synthetic/session-{cycle}"
    if stage == "area":
        return "https://ticketplus.com.tw/order/synthetic/session"
    return "https://ticketplus.com.tw/activity/synthetic/session"


async def _run_instance(instance: str, base_url: str, duration: float) -> SoakResult:
    started = time.monotonic()
    result = SoakResult(instance=instance, duration_seconds=duration)
    config = settings.get_default_config()
    config["homepage"] = base_url
    config["advanced"]["browser_type"] = "edge"
    config["advanced"]["browser_private_mode"] = False
    config["advanced"]["headless"] = True
    config["advanced"]["run_mode"] = (
        "leak_watch" if instance.endswith(("2", "3")) else "onsale"
    )
    args = SimpleNamespace(
        instance=f"v052-soak-{instance}",
        browser="edge",
        browser_private_mode=False,
        mcp_connect=None,
        mcp_debug=False,
    )
    manager = BrowserSessionManager(config, args)
    driver = None
    health = RuntimeHealthSupervisor()
    last_progress_at = time.monotonic()
    try:
        conf = get_extension_config(config, args, session_manager=manager)
        nodriver_overwrite_prefs(conf)
        driver = await uc.start(conf)
        tab = await driver.get(base_url)
        manager.attach(driver, tab)
        _sample_resources(result, manager, health, initial=True)
        route_state = SyntheticRouteState("ticketplus", "activity", 0)
        runtime_context = RuntimeIterationContext(
            config_dict=config,
            driver=driver,
            tab=tab,
            session_manager=manager,
            health_supervisor=health,
            refresh_datetime_state={"target_str": "", "reached": False},
            test_local_route_mapper=lambda _actual_url: _synthetic_platform_url(
                route_state.platform,
                route_state.stage,
                route_state.cycle,
            ),
        )
        while time.monotonic() - started < duration:
            result.cycles += 1
            cycle = result.cycles
            stage = "activity"
            platform_key = ("ticketplus", "tixcraft", "kktix")[(cycle // 30) % 3]
            if cycle % 25 == 0:
                await tab.evaluate(
                    "window.syntheticTicket.rerender('fallback');"
                )
                stage = "area"
                candidate = SelectorCandidate(
                    "#primary-area",
                    1.0,
                    "synthetic primary",
                    frozenset({PageClass.AREA}),
                )
                fallback = SelectorCandidate(
                    "[data-testid=known-area]",
                    0.95,
                    "synthetic known fallback",
                    frozenset({PageClass.AREA}),
                )
                resolution = await resolve_selector(
                    tab,
                    candidate,
                    (fallback,),
                    page_class=PageClass.AREA,
                    config_dict=config,
                )
                result.fallback_resolutions += int(resolution.used_fallback)
            elif cycle % 20 == 0:
                stage = "protected"
                await tab.evaluate(
                    "history.pushState({}, '', '/synthetic_ticket_spa.html?continue=1')"
                )
                result.success_continue_cycles += 1
            elif cycle % 17 == 0:
                await tab.evaluate(
                    "history.pushState({}, '', '/synthetic_ticket_spa.html?login=1');"
                    "history.replaceState({}, '', '/synthetic_ticket_spa.html?restored=1')"
                )
                result.login_restore_cycles += 1
                result.state_transition_count += 2
            elif cycle % 7 == 0:
                await tab.evaluate(
                    f"window.syntheticTicket.replace('/synthetic_ticket_spa.html?replace={cycle}')"
                )
            else:
                await tab.evaluate(
                    f"window.syntheticTicket.push('/synthetic_ticket_spa.html?push={cycle}')"
                )

            if cycle % 300 == 0:
                old_tab = tab
                tab = await driver.get(base_url, new_tab=True)
                manager.attach(driver, tab)
                runtime_context.tab = tab
                close = getattr(old_tab, "close", None)
                if callable(close):
                    closed = close()
                    if asyncio.iscoroutine(closed):
                        await closed
                result.target_replacements += 1
            elif cycle % 150 == 0:
                await tab.reload()
                result.reload_injections += 1

            route_state.platform = platform_key
            route_state.stage = stage
            route_state.cycle = cycle
            iteration = await run_runtime_iteration(runtime_context)
            driver = runtime_context.driver
            tab = runtime_context.tab
            if iteration.should_break:
                raise RuntimeError(
                    f"production iteration stopped: {iteration.action}:{iteration.reason}"
                )
            result.state_transition_count += int(iteration.new_attempt_started)
            tabs = getattr(driver, "tabs", ())
            result.max_tab_count = max(result.max_tab_count, len(tabs or ()))
            now = time.monotonic()
            result.stalled_seconds_max = max(
                result.stalled_seconds_max,
                now - last_progress_at,
            )
            last_progress_at = now
            if cycle % 20 == 0:
                _sample_resources(result, manager, health)
            await asyncio.sleep(0.05)
    except BaseException as exc:
        result.errors += 1
        result.cdp_errors += int(classify_browser_exception(exc).value != "unknown")
        raise
    finally:
        if driver is not None:
            _sample_resources(result, manager, health)
        if driver is not None:
            await manager.stop_browser()
        result.duration_seconds = time.monotonic() - started
    return result


async def _async_main(args: argparse.Namespace) -> list[SoakResult]:
    server, base_url = _start_server()
    try:
        return await asyncio.gather(
            *(
                _run_instance(str(index + 1), base_url, args.duration)
                for index in range(args.instances)
            )
        )
    finally:
        server.shutdown()
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--instances", type=int, choices=(1, 3), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = {
        "status": "PASS",
        "requested_duration_seconds": args.duration,
        "instances": args.instances,
        "results": [],
    }
    try:
        results = asyncio.run(_async_main(args))
        payload["results"] = [asdict(result) for result in results]
        if any(result.errors or result.duplicate_submit_claims for result in results):
            payload["status"] = "FAIL"
    except BaseException as exc:
        payload["status"] = "FAIL"
        payload["error"] = f"{type(exc).__name__}: {exc}"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
