from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import statistics
import sys
import time
from pathlib import Path


REPO_ROOT = Path(
    os.environ.get("HUNTERX_BENCH_REPO_ROOT", Path(__file__).resolve().parents[2])
).resolve()
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

import runtime_health
import settings
import util
import nodriver_tixcraft as production_runtime
from browser_session import BrowserSessionManager
from page_classifier import PageClass
from platform_adapters import adapter_for_key, adapter_for_url
from platform_engine import PlatformEngine
from platforms import ticketplus
from platforms.common_async import is_interval_due
from refresh_coordinator import NS_PER_SECOND, RefreshCoordinator


class _Tab:
    def __init__(
        self,
        target_id: str = "benchmark",
        url: str = "https://ticketplus.com.tw/activity/demo/session",
    ) -> None:
        self.target = type(
            "Target",
            (),
            {"target_id": target_id, "url": url},
        )()

    async def evaluate(self, _script: str):
        return self.target.url

    async def js_dumps(self, _script: str):
        return self.target.url


class _Driver:
    def __init__(self, *tabs: _Tab) -> None:
        self.tabs = list(tabs)
        self.main_tab = tabs[0] if tabs else None

    async def update_targets(self) -> None:
        return None


def _p95(values: list[float]) -> float:
    return sorted(values)[max(0, min(len(values) - 1, round((len(values) - 1) * 0.95)))]


def _measure(function, *, samples: int, iterations: int) -> dict:
    for _ in range(3):
        function(max(10, iterations // 10))
    values = []
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(samples):
            start = time.perf_counter_ns()
            fingerprint = function(iterations)
            values.append((time.perf_counter_ns() - start) / iterations)
    finally:
        if gc_enabled:
            gc.enable()
    return {
        "median_ns_per_op": statistics.median(values),
        "p95_ns_per_op": _p95(values),
        "min_ns_per_op": min(values),
        "max_ns_per_op": max(values),
        "fingerprint": str(fingerprint)[:100],
        "samples_ns_per_op": values,
    }


def main_loop_noop(iterations: int):
    due = 0
    last = time.monotonic()
    for _ in range(iterations):
        now = time.monotonic()
        due += int(is_interval_due(now, last, 60.0))
    return due


def url_classification(iterations: int):
    urls = (
        "https://tixcraft.com/ticket/area/demo/1",
        "https://ticketplus.com.tw/activity/demo/session",
        "https://demo.kktix.cc/events/demo/registrations/new",
        "https://ticket.ibon.com.tw/ActivityInfo/Details/1",
    )
    total = 0
    for index in range(iterations):
        adapter = adapter_for_url(urls[index % 4])
        total += int(adapter.classify_page(urls[index % 4]) is not PageClass.UNKNOWN)
    return total


def platform_dispatch(iterations: int):
    engine = PlatformEngine()
    tab = _Tab()
    url = "https://ticketplus.com.tw/activity/demo/session"
    total = 0
    for _ in range(iterations):
        total += int(engine.before_dispatch(tab, url, {}).allowed)
    return total


def tixcraft_safe_dispatch(iterations: int):
    engine = PlatformEngine()
    tab = _Tab(url="https://tixcraft.com/ticket/area/demo/1")
    url = tab.target.url
    total = 0
    for _ in range(iterations):
        total += int(engine.before_dispatch(tab, url, {}).allowed)
    return total


def per_tab_state_lookup(iterations: int):
    engine = PlatformEngine()
    adapter = adapter_for_key("ticketplus")
    tabs = [_Tab(str(index)) for index in range(3)]
    total = 0
    for index in range(iterations):
        total += engine.state_for(tabs[index % 3], adapter).cycle_count
    return total


def refresh_idle(iterations: int):
    now = [10 * NS_PER_SECOND]
    coordinator = RefreshCoordinator(clock_ns=lambda: now[0])
    first = coordinator.begin_dispatch("periodic", 5.0)
    coordinator.complete_dispatch(first.token, True)
    total = 0
    for _ in range(iterations):
        total += int(not coordinator.begin_dispatch("periodic", 5.0).allowed)
    return total


def refresh_due(iterations: int):
    total = 0
    for index in range(iterations):
        now = [index * 10 * NS_PER_SECOND]
        coordinator = RefreshCoordinator(clock_ns=lambda: now[0])
        decision = coordinator.begin_dispatch("periodic", 0.0)
        total += int(decision.allowed)
        if decision.allowed:
            coordinator.complete_dispatch(decision.token, True)
    return total


async def _ticket_watch(iterations: int):
    ticketplus._ensure_ticketplus_state_defaults()
    ticketplus._state["submission_pending"] = True
    ticketplus._state["submission_next_probe_at"] = time.monotonic() + 3600
    debug = type("Debug", (), {"log": lambda self, _message: None})()
    total = 0
    for _ in range(iterations):
        total += int(await ticketplus._ticketplus_handle_submission_watch(object(), {}, debug))
    ticketplus._state["submission_pending"] = False
    return total


def ticket_watch_before_probe(iterations: int):
    return asyncio.run(_ticket_watch(iterations))


def multitab_state(iterations: int):
    engine = PlatformEngine()
    tabs = [_Tab(str(index)) for index in range(3)]
    urls = (
        "https://ticketplus.com.tw/activity/a/session",
        "https://tixcraft.com/ticket/area/b/1",
        "https://demo.kktix.cc/events/c/registrations/new",
    )
    total = 0
    for index in range(iterations):
        decision = engine.before_dispatch(tabs[index % 3], urls[index % 3], {})
        total += int(decision.allowed)
    return total


def runtime_health_noop(iterations: int):
    supervisor_type = getattr(runtime_health, "RuntimeHealthSupervisor", None)
    if supervisor_type is None:
        last = 0.0
        for _ in range(iterations):
            last = time.monotonic()
        return last > 0
    supervisor = supervisor_type()
    for _ in range(iterations):
        supervisor.record_loop()
    return supervisor.last_loop_at > 0


def expected_progress_idle_noop(iterations: int):
    supervisor_type = getattr(runtime_health, "RuntimeHealthSupervisor", None)
    if supervisor_type is None:
        return runtime_health_noop(iterations)
    supervisor = supervisor_type()
    observe = getattr(supervisor, "observe_expected_progress", None)
    if not callable(observe):
        return runtime_health_noop(iterations)
    total = 0
    for _ in range(iterations):
        total += len(observe(tab_identity="benchmark"))
    return total


def user_dictionary_parse(iterations: int):
    """Measure the real shared dictionary boundary used by question-capable platforms."""

    getter = getattr(util, "get_answer_list_from_user_guess_string", None)
    if not callable(getter):
        return ("unsupported", iterations)
    config = {
        "advanced": {
            "user_guess_string": '"member A","member \\"B\\"","price 3,280"',
        }
    }
    total = 0
    for _ in range(iterations):
        total += len(
            getter(
                config,
                "HUNTERX_BENCHMARK_MISSING_USER_DICTIONARY.txt",
            )
        )
    return total


def exact_target_reacquire(iterations: int):
    target_url = "https://ticketplus.com.tw/activity/demo/session"
    exact = _Tab("exact", target_url)
    wrong = _Tab("wrong", "https://ticketplus.com.tw/activity/other/session")
    manager = BrowserSessionManager({"advanced": {}})
    if not callable(getattr(manager, "reacquire_tab", None)):
        return ("unsupported", iterations)
    manager.attach(_Driver(exact, wrong), exact)
    total = 0
    for _ in range(iterations):
        total += int(manager.reacquire_tab(target_url, "ticketplus") is exact)
    return total


def wrong_target_rejection(iterations: int):
    target_url = "https://ticketplus.com.tw/activity/demo/session"
    wrong = _Tab("wrong", "https://ticketplus.com.tw/activity/other/session")
    manager = BrowserSessionManager({"advanced": {}})
    if not callable(getattr(manager, "reacquire_tab", None)):
        return ("unsupported", iterations)
    manager.attach(_Driver(wrong))
    total = 0
    for _ in range(iterations):
        manager.active_tab = None
        total += int(manager.reacquire_tab(target_url, "ticketplus") is None)
    return total


async def _healthy_transport_proof(iterations: int):
    target_url = "https://ticketplus.com.tw/activity/demo/session"
    tab = _Tab("healthy", target_url)
    manager = BrowserSessionManager({"advanced": {}})
    if not callable(getattr(manager, "recover", None)):
        return ("unsupported", iterations)
    manager.attach(_Driver(tab), tab)
    recovery_level = getattr(runtime_health.RecoveryLevel, "TRANSPORT_REBIND")
    total = 0
    for _ in range(iterations):
        result = await manager.recover(
            recovery_level,
            target_url=target_url,
            platform_key="ticketplus",
            allow_restart=False,
        )
        total += int(result.success)
    return total


def healthy_transport_proof(iterations: int):
    return asyncio.run(_healthy_transport_proof(iterations))


async def _production_iteration_noop(iterations: int):
    """Measure the real outer-loop iteration on a readable unregistered route."""

    url = "http://127.0.0.1/hunterx-benchmark-noop"
    tab = _Tab("production-noop", url)
    driver = _Driver(tab)
    config = settings.get_default_config()
    config["homepage"] = url
    manager = BrowserSessionManager(config)
    manager.attach(driver, tab)
    context_type = production_runtime.RuntimeIterationContext
    iteration = production_runtime.run_runtime_iteration
    context = context_type(
        config_dict=config,
        driver=driver,
        tab=tab,
        session_manager=manager,
        health_supervisor=runtime_health.RuntimeHealthSupervisor(),
        refresh_datetime_state={"target_str": "", "reached": False},
        last_url=url,
        cloudflare_checked=True,
    )
    total = 0
    for _ in range(iterations):
        result = await iteration(context)
        total += int(result.action == "dispatched")
    return total


def production_iteration_noop(iterations: int):
    if not hasattr(production_runtime, "RuntimeIterationContext") or not callable(
        getattr(production_runtime, "run_runtime_iteration", None)
    ):
        return ("unsupported", iterations)
    return asyncio.run(_production_iteration_noop(iterations))


async def _terminal_lifecycle_healthy_noop(iterations: int):
    """Measure the authoritative owner boundary when no exception is raised."""

    url = "http://127.0.0.1/hunterx-benchmark-owned-noop"
    tab = _Tab("owned-production-noop", url)
    driver = _Driver(tab)
    config = settings.get_default_config()
    config["homepage"] = url
    manager = BrowserSessionManager(config)
    manager.attach(driver, tab)
    context = production_runtime.RuntimeIterationContext(
        config_dict=config,
        driver=driver,
        tab=tab,
        session_manager=manager,
        health_supervisor=runtime_health.RuntimeHealthSupervisor(),
        refresh_datetime_state={"target_str": "", "reached": False},
        last_url=url,
        cloudflare_checked=True,
    )
    total = 0
    for _ in range(iterations):
        try:
            result = await production_runtime.run_runtime_iteration(context)
        except Exception as exc:
            handler = getattr(
                production_runtime,
                "_handle_terminal_iteration_failure",
                None,
            )
            if not callable(handler):
                raise
            result = await handler(context, exc)
        total += int(result.action == "dispatched")
    return total


def terminal_lifecycle_healthy_noop(iterations: int):
    return asyncio.run(_terminal_lifecycle_healthy_noop(iterations))


def logging_disabled(iterations: int):
    logger = util.create_debug_logger(enabled=False)
    for _ in range(iterations):
        logger.log("benchmark disabled")
    return iterations


def instance_profile_one(iterations: int):
    args = type("Args", (), {"instance": "one", "browser": "chrome", "browser_private_mode": False})()
    manager = BrowserSessionManager({"advanced": {}}, args)
    value = ""
    for _ in range(iterations):
        value = str(manager.profile_dir())
    return len(value)


def instance_profile_three(iterations: int):
    managers = []
    for name in ("one", "two", "three"):
        args = type("Args", (), {"instance": name, "browser": "chrome", "browser_private_mode": False})()
        managers.append(BrowserSessionManager({"advanced": {}}, args))
    total = 0
    for index in range(iterations):
        total += len(str(managers[index % 3].profile_dir()))
    return total


BENCHMARKS = {
    "main_loop_noop": main_loop_noop,
    "url_classification": url_classification,
    "platform_dispatch": platform_dispatch,
    "tixcraft_safe_dispatch": tixcraft_safe_dispatch,
    "per_tab_state_lookup": per_tab_state_lookup,
    "refresh_coordinator_idle": refresh_idle,
    "due_refresh_decision": refresh_due,
    "ticketplus_watcher_before_probe": ticket_watch_before_probe,
    "multi_tab_state": multitab_state,
    "runtime_health_noop": runtime_health_noop,
    "expected_progress_idle_noop": expected_progress_idle_noop,
    "user_dictionary_parse": user_dictionary_parse,
    "exact_target_reacquire": exact_target_reacquire,
    "wrong_target_rejection": wrong_target_rejection,
    "healthy_transport_proof": healthy_transport_proof,
    "production_iteration_noop": production_iteration_noop,
    "terminal_lifecycle_healthy_noop": terminal_lifecycle_healthy_noop,
    "logging_disabled": logging_disabled,
    "one_instance_profile": instance_profile_one,
    "three_instance_profile": instance_profile_three,
}

# Browser-session and authoritative production-iteration paths intentionally do
# more work than the nanosecond hot paths above.  Bound only their per-sample
# operation count so balanced A/B runs remain practical while retaining the
# same sample count and requested iteration budget on both candidates.
BENCHMARK_ITERATION_CAPS = {
    "exact_target_reacquire": 2_000,
    "wrong_target_rejection": 2_000,
    "healthy_transport_proof": 500,
    "production_iteration_noop": 500,
    "terminal_lifecycle_healthy_noop": 500,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--samples", type=int, default=21)
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--label", required=True)
    parser.add_argument(
        "--benchmarks",
        default="",
        help="Optional comma-separated benchmark names for focused reruns.",
    )
    args = parser.parse_args()
    selected = {
        item.strip()
        for item in str(args.benchmarks or "").split(",")
        if item.strip()
    }
    unknown = selected.difference(BENCHMARKS)
    if unknown:
        parser.error(f"unknown benchmark(s): {', '.join(sorted(unknown))}")
    results = {}
    for name, function in BENCHMARKS.items():
        if selected and name not in selected:
            continue
        effective_iterations = min(
            args.iterations,
            BENCHMARK_ITERATION_CAPS.get(name, args.iterations),
        )
        row = _measure(
            function,
            samples=args.samples,
            iterations=effective_iterations,
        )
        row["iterations_per_sample"] = effective_iterations
        results[name] = row
    payload = {
        "label": args.label,
        "python": sys.version,
        "samples": args.samples,
        "iterations": args.iterations,
        "results": results,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({name: row["median_ns_per_op"] for name, row in results.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
