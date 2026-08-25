#!/usr/bin/env python3
"""Actual-browser local synthetic soak; never contacts a ticketing service."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import tempfile
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
import util
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


_SOAK_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}")


def _soak_instance_name(instance: str, run_id: str = "") -> str:
    """Return a bounded profile identity for one independently owned soak."""

    normalized = str(run_id or "").strip()
    if not normalized:
        return f"v052-soak-{instance}"
    if _SOAK_RUN_ID_PATTERN.fullmatch(normalized) is None:
        raise ValueError("soak run id must match [A-Za-z0-9_-]{1,32}")
    return f"v052-soak-{normalized}-{instance}"


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


async def _run_instance(
    instance: str,
    base_url: str,
    duration: float,
    run_id: str = "",
    stop_file: Path | None = None,
) -> SoakResult:
    started = time.monotonic()
    result = SoakResult(instance=instance, duration_seconds=duration)
    instance_name = _soak_instance_name(instance, run_id)
    if not util.set_instance_id(instance_name):
        raise ValueError(f"invalid soak instance identity: {instance_name}")
    config = settings.get_default_config()
    config["homepage"] = base_url
    config["advanced"]["browser_type"] = "edge"
    config["advanced"]["browser_private_mode"] = False
    config["advanced"]["headless"] = True
    config["advanced"]["run_mode"] = (
        "leak_watch" if instance.endswith(("2", "3")) else "onsale"
    )
    args = SimpleNamespace(
        instance=instance_name,
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
            if stop_file is not None and stop_file.exists():
                raise RuntimeError("peer soak worker failed")
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
    worker_instances = (
        (args.worker_instance,)
        if args.worker_instance
        else tuple(str(index + 1) for index in range(args.instances))
    )
    try:
        return await asyncio.gather(
            *(
                _run_instance(
                    instance,
                    base_url,
                    args.duration,
                    args.run_id,
                    args.stop_file,
                )
                for instance in worker_instances
            )
        )
    finally:
        server.shutdown()
        server.server_close()


def _isolated_worker_command(
    args: argparse.Namespace,
    *,
    worker_instance: str,
    output: Path,
    stop_file: Path,
) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--duration",
        str(args.duration),
        "--instances",
        "1",
        "--worker-instance",
        worker_instance,
        "--output",
        str(output),
        "--stop-file",
        str(stop_file),
    ]
    if args.run_id:
        command.extend(("--run-id", args.run_id))
    return command


def _run_isolated_three_instances(args: argparse.Namespace) -> int:
    """Run named-instance qualification in three independent OS processes."""

    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "status": "PASS",
        "requested_duration_seconds": args.duration,
        "instances": 3,
        "run_id": args.run_id,
        "process_isolation": "three_os_processes",
        "results": [],
        "workers": [],
    }
    with tempfile.TemporaryDirectory(
        prefix=".v052-soak-three-",
        dir=args.output.parent,
    ) as temporary_directory:
        temporary_root = Path(temporary_directory)
        stop_file = temporary_root / "STOP"
        processes: list[tuple[str, subprocess.Popen[bytes], Path]] = []
        handles: list[Any] = []
        try:
            for worker_instance in ("1", "2", "3"):
                worker_output = temporary_root / f"worker-{worker_instance}.json"
                stdout_handle = (temporary_root / f"worker-{worker_instance}.stdout.log").open(
                    "wb"
                )
                stderr_handle = (temporary_root / f"worker-{worker_instance}.stderr.log").open(
                    "wb"
                )
                handles.extend((stdout_handle, stderr_handle))
                process = subprocess.Popen(
                    _isolated_worker_command(
                        args,
                        worker_instance=worker_instance,
                        output=worker_output,
                        stop_file=stop_file,
                    ),
                    cwd=REPO_ROOT,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                )
                processes.append((worker_instance, process, worker_output))

            stop_requested_at: float | None = None
            while any(process.poll() is None for _, process, _ in processes):
                failed = any(
                    process.poll() not in (None, 0)
                    for _, process, _ in processes
                )
                if failed and not stop_file.exists():
                    stop_file.touch()
                    stop_requested_at = time.monotonic()
                if (
                    stop_requested_at is not None
                    and time.monotonic() - stop_requested_at > 30.0
                ):
                    for _, process, _ in processes:
                        if process.poll() is None:
                            process.terminate()
                    break
                time.sleep(0.1)
            for _, process, _ in processes:
                try:
                    process.wait(timeout=10.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10.0)
        finally:
            for handle in handles:
                handle.close()

        for worker_instance, process, worker_output in processes:
            worker_payload: dict[str, Any] = {}
            if worker_output.is_file():
                worker_payload = json.loads(worker_output.read_text(encoding="utf-8"))
            payload["workers"].append(
                {
                    "instance": worker_instance,
                    "exit_code": process.returncode,
                    "status": worker_payload.get("status", "MISSING"),
                    "error": worker_payload.get("error", ""),
                }
            )
            payload["results"].extend(worker_payload.get("results", ()))
            if process.returncode != 0 or worker_payload.get("status") != "PASS":
                payload["status"] = "FAIL"

    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--instances", type=int, choices=(1, 3), required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--worker-instance", default="", help=argparse.SUPPRESS)
    parser.add_argument("--stop-file", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.worker_instance and args.instances != 1:
        parser.error("an isolated worker must use --instances 1")
    _soak_instance_name(args.worker_instance or "1", args.run_id)
    if args.instances == 3 and not args.worker_instance:
        return _run_isolated_three_instances(args)
    payload = {
        "status": "PASS",
        "requested_duration_seconds": args.duration,
        "instances": args.instances,
        "run_id": args.run_id,
        "worker_instance": args.worker_instance,
        "process_isolation": "single_os_process",
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
