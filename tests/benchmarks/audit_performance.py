from __future__ import annotations

import argparse
import asyncio
import cProfile
import io
import json
import pstats
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import settings
import util
from platforms import kham as kham_platform
from platforms import kktix as kktix_platform
from platforms import tixcraft as tixcraft_platform
from platforms.cityline import is_cityline_login_page
from platforms.common_async import bounded_poll, get_auto_reload_interval, is_interval_due
from platforms.fansigo import get_fansigo_page_type, is_fansigo_url
from platforms.ticketplus import _ticketplus_path_segment_count
from refresh_timing import (
    calculate_refresh_trigger_datetime,
    compute_remaining_ns,
    compute_trigger_plan,
    parse_refresh_datetime_value,
    robust_estimate,
    select_time_source,
)


BenchmarkFunc = Callable[[int], Any]


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, round((len(sorted_values) - 1) * percentile))
    return sorted_values[index]


def _measure(name: str, func: BenchmarkFunc, *, samples: int, iterations: int) -> dict[str, Any]:
    durations_ms: list[float] = []
    cpu_ms: list[float] = []
    peaks: list[int] = []
    last_result: Any = None

    for _ in range(3):
        func(max(1, iterations // 10))

    for _ in range(samples):
        tracemalloc.start()
        start_cpu = time.process_time()
        start_wall = time.perf_counter()
        last_result = func(iterations)
        durations_ms.append((time.perf_counter() - start_wall) * 1000)
        cpu_ms.append((time.process_time() - start_cpu) * 1000)
        _, peak = tracemalloc.get_traced_memory()
        peaks.append(peak)
        tracemalloc.stop()

    return {
        "name": name,
        "samples": samples,
        "iterations_per_sample": iterations,
        "wall_ms_p50": statistics.median(durations_ms),
        "wall_ms_p95": _percentile(durations_ms, 0.95),
        "wall_ms_p99": _percentile(durations_ms, 0.99),
        "wall_ms_mean": statistics.fmean(durations_ms),
        "wall_ms_stdev": statistics.pstdev(durations_ms),
        "cpu_ms_p50": statistics.median(cpu_ms),
        "cpu_ms_p95": _percentile(cpu_ms, 0.95),
        "peak_bytes_max": max(peaks),
        "result_fingerprint": str(last_result)[:160],
    }


def bench_default_config(iterations: int) -> int:
    total = 0
    for _ in range(iterations):
        total += settings.get_default_config()["advanced"]["server_port"]
    return total


def bench_config_migration(iterations: int) -> float:
    raw_config = json.dumps(
        {
            "advanced": {
                "server_port": 16889,
                "auto_reload_page_interval": "0",
            },
            "accounts": {"discount_code": "EARLY"},
        }
    )
    total = 0.0
    for _ in range(iterations):
        migrated = settings.migrate_config(json.loads(raw_config))
        total += float(migrated["advanced"]["auto_reload_page_interval"])
    return total


def bench_interval_parsing(iterations: int) -> float:
    values = [0, "0", "", None, -1, "bad", "3.5", 2]
    total = 0.0
    for index in range(iterations):
        value = values[index % len(values)]
        total += get_auto_reload_interval({"advanced": {"auto_reload_page_interval": value}}, default=2.0)
    return total


def bench_interval_due(iterations: int) -> int:
    total = 0
    now = 1000.0
    values = [0, "0", "bad", 0.5, 2.0]
    for index in range(iterations):
        if is_interval_due(now + index * 0.01, 999.2, values[index % len(values)]):
            total += 1
    return total


async def _bench_bounded_poll_immediate(iterations: int) -> int:
    total = 0
    for _ in range(iterations):
        if await bounded_poll(lambda: True, timeout=0.01, interval=0.001):
            total += 1
    return total


def bench_bounded_poll_immediate(iterations: int) -> int:
    return asyncio.run(_bench_bounded_poll_immediate(iterations))


def bench_keyword_matching(iterations: int) -> int:
    keywords = '"VIP A","VIP B","General 3280","Balcony"'
    texts = [
        "VIP A zone row 1",
        "General 3280 seat",
        "Balcony left",
        "Sold out",
        "Regular area",
    ]
    total = 0
    for index in range(iterations):
        if util.is_text_match_keyword(keywords, texts[index % len(texts)]):
            total += 1
    return total


def bench_refresh_timing(iterations: int) -> str:
    target = "2026/07/09 12:00:00.000"
    config = {
        "enable": True,
        "clock_offset_ms": 2,
        "frontend_delay_ms": 45,
        "network_uplink_ms": 35,
        "safety_margin_ms": 30,
        "freeze_before_seconds": 10,
    }
    last_value = datetime.min
    for _ in range(iterations):
        parsed = parse_refresh_datetime_value(target)
        last_value = calculate_refresh_trigger_datetime(parsed, config)
    return last_value.isoformat()


def bench_trigger_planner(iterations: int) -> str:
    target = datetime(2026, 7, 9, 12, 0, 0)
    config = {
        "enable": True,
        "clock_offset_ms": -2,
        "clock_uncertainty_ms": 5,
        "frontend_delay_ms": 45,
        "network_uplink_ms": 35,
        "scheduler_jitter_ms": 4,
        "safety_margin_ms": 30,
        "freeze_before_seconds": 10,
        "confidence": "medium",
    }
    last_value = ""
    for _ in range(iterations):
        last_value = compute_trigger_plan(target, config).computed_trigger_display
    return last_value


def bench_robust_estimate(iterations: int) -> float:
    values = [98.0, 99.0, 100.0, 101.0, 102.0, 5000.0]
    total = 0.0
    for _ in range(iterations):
        total += robust_estimate(values)["value_ms"]
    return total


def bench_remaining_calculation(iterations: int) -> int:
    total = 0
    deadline = 10_000_000_000
    for index in range(iterations):
        total += compute_remaining_ns(deadline, index * 1_000_000)
    return total


def bench_source_selection(iterations: int) -> int:
    candidates = [
        {"success": True, "source": "ntp", "confidence": "low", "clock_uncertainty_ms": 100, "age_seconds": 1},
        {"success": True, "source": "http", "confidence": "medium", "clock_uncertainty_ms": 40, "age_seconds": 5},
        {"success": False, "source": "system"},
    ]
    total = 0
    for _ in range(iterations):
        total += len(select_time_source(candidates)["source"])
    return total


def bench_platform_interval_helpers(iterations: int) -> float:
    config = {"advanced": {"auto_reload_page_interval": "3.5"}}
    helpers: list[Callable[[dict[str, Any]], float]] = [
        tixcraft_platform._get_auto_reload_interval,
        kktix_platform._get_auto_reload_interval,
        kham_platform._get_auto_reload_interval,
    ]
    total = 0.0
    for index in range(iterations):
        total += helpers[index % len(helpers)](config)
    return total


def bench_url_classification(iterations: int) -> int:
    urls = [
        "https://go.fansi.me/events/123",
        "https://www.cityline.com.hk/en_US/Login.html?targetUrl=x",
        "https://ticketplus.com.tw/activity/abc/",
        "https://go.fansi.me/tickets/payment/checkout/123",
    ]
    total = 0
    for index in range(iterations):
        url = urls[index % len(urls)]
        total += int(is_fansigo_url(url))
        total += len(get_fansigo_page_type(url))
        total += int(is_cityline_login_page(url))
        total += _ticketplus_path_segment_count(url)
    return total


BENCHMARKS: dict[str, BenchmarkFunc] = {
    "default_config": bench_default_config,
    "config_migration": bench_config_migration,
    "interval_parsing": bench_interval_parsing,
    "interval_due": bench_interval_due,
    "bounded_poll_immediate": bench_bounded_poll_immediate,
    "keyword_matching": bench_keyword_matching,
    "refresh_timing": bench_refresh_timing,
    "trigger_planner": bench_trigger_planner,
    "robust_estimate": bench_robust_estimate,
    "remaining_calculation": bench_remaining_calculation,
    "source_selection": bench_source_selection,
    "platform_interval_helpers": bench_platform_interval_helpers,
    "url_classification": bench_url_classification,
}


def _profile_once(iterations: int) -> str:
    profile = cProfile.Profile()
    profile.enable()
    for benchmark in BENCHMARKS.values():
        benchmark(iterations)
    profile.disable()
    stream = io.StringIO()
    stats = pstats.Stats(profile, stream=stream).sort_stats("cumtime")
    stats.print_stats(20)
    return stream.getvalue()


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Performance Benchmark",
        "",
        f"Generated: {report['generated_at']}",
        f"Python: {report['python']}",
        f"Samples: {report['samples']}",
        f"Iterations per sample: {report['iterations_per_sample']}",
        "",
        "| Benchmark | p50 wall ms | p95 wall ms | p99 wall ms | mean wall ms | stdev | p50 CPU ms | max peak bytes |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(
        (
            "| {name} | {wall_ms_p50:.6f} | {wall_ms_p95:.6f} | {wall_ms_p99:.6f} | "
            "{wall_ms_mean:.6f} | {wall_ms_stdev:.6f} | {cpu_ms_p50:.6f} | {peak_bytes_max} |".format(**row)
        )
        for row in report["benchmarks"]
    )
    lines.extend(["", "## cProfile Top 20", "", "```text", report["cprofile_top20"].rstrip(), "```", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--markdown", required=True)
    parser.add_argument("--samples", type=int, default=25)
    parser.add_argument("--iterations", type=int, default=500)
    args = parser.parse_args()

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "python": sys.version,
        "samples": args.samples,
        "iterations_per_sample": args.iterations,
        "benchmarks": [
            _measure(name, func, samples=args.samples, iterations=args.iterations)
            for name, func in BENCHMARKS.items()
        ],
        "cprofile_top20": _profile_once(max(1, args.iterations // 5)),
    }

    output_path = Path(args.output)
    markdown_path = Path(args.markdown)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_markdown(markdown_path, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
