from __future__ import annotations

import asyncio
import os
import time
from contextlib import suppress
from typing import Any, Awaitable

import util
from notification_context import redact_sensitive_text


DEFAULT_RELOAD_TIMEOUT_SECONDS = 10.0
DEFAULT_NAVIGATION_TIMEOUT_SECONDS = 10.0
DEFAULT_EVALUATE_TIMEOUT_SECONDS = 4.0
DEFAULT_READY_STATE_TIMEOUT_SECONDS = 6.0
HEARTBEAT_FILE = "heartbeat.txt"


def _instance_log_path() -> str:
    app_root = util.get_app_root()
    instance_id = util.get_instance_id()
    if instance_id == util.CONST_DEFAULT_INSTANCE_ID:
        log_dir = os.path.join(app_root, "logs")
        filename = "runtime_default.log"
    else:
        log_dir = os.path.join(app_root, "instances", instance_id)
        filename = "runtime.log"
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, filename)


def runtime_log(message: str, config_dict: dict[str, Any] | None = None, **fields: Any) -> None:
    """Append a redacted runtime diagnostic line without depending on verbose mode."""

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    instance_id = util.get_instance_id()
    run_mode = ""
    try:
        run_mode = str((config_dict or {}).get("advanced", {}).get("run_mode", ""))
    except Exception:
        run_mode = ""

    parts = [f"{timestamp}", f"instance={instance_id}"]
    if run_mode:
        parts.append(f"run_mode={run_mode}")
    parts.append(str(message))
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    line = redact_sensitive_text(" ".join(parts))

    with suppress(Exception):
        with open(_instance_log_path(), "a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")


def touch_heartbeat(filename: str = HEARTBEAT_FILE) -> None:
    with suppress(Exception):
        with open(util.get_instance_state_path(filename), "w", encoding="utf-8") as heartbeat_file:
            heartbeat_file.write(str(int(time.time())))


async def sleep_with_heartbeat(
    seconds: float,
    config_dict: dict[str, Any] | None = None,
    *,
    reason: str = "wait",
    chunk_seconds: float = 5.0,
    stop_checker: Any | None = None,
    quit_checker: Any | None = None,
) -> str:
    """Sleep in small chunks so stop/quit and heartbeat remain responsive."""

    total = max(0.0, float(seconds or 0))
    deadline = time.monotonic() + total
    next_log_at = time.monotonic()
    while True:
        touch_heartbeat()
        if quit_checker is not None and await quit_checker(config_dict):
            runtime_log(f"[{reason}] quit requested", config_dict)
            return "quit"
        if stop_checker is not None and await stop_checker(config_dict):
            runtime_log(f"[{reason}] stop requested", config_dict)
            return "stop"

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        now = time.monotonic()
        if now >= next_log_at:
            runtime_log(f"[{reason}] waiting", config_dict, remaining_s=int(remaining))
            next_log_at = now + 60
        await asyncio.sleep(min(max(0.1, chunk_seconds), remaining))
    touch_heartbeat()
    return "done"


async def wait_for_operation(
    awaitable: Awaitable[Any],
    timeout_seconds: float,
    action: str,
    config_dict: dict[str, Any] | None = None,
    *,
    default: Any = None,
    raise_on_timeout: bool = False,
) -> Any:
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(awaitable, timeout=max(0.1, float(timeout_seconds)))
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        runtime_log(f"[{action}] done", config_dict, elapsed_ms=elapsed_ms)
        return result
    except asyncio.TimeoutError:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        runtime_log(f"[{action}] timeout", config_dict, elapsed_ms=elapsed_ms)
        if raise_on_timeout:
            raise
        return default


async def guarded_get(
    tab: Any,
    url: str,
    config_dict: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_NAVIGATION_TIMEOUT_SECONDS,
    reason: str = "navigation",
) -> bool:
    try:
        await wait_for_operation(
            tab.get(url),
            timeout_seconds,
            reason,
            config_dict,
            raise_on_timeout=True,
        )
        return True
    except TimeoutError:
        return False


async def evaluate_with_timeout(
    tab: Any,
    script: str,
    config_dict: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_EVALUATE_TIMEOUT_SECONDS,
    reason: str = "evaluate",
    default: Any = None,
) -> Any:
    return await wait_for_operation(
        tab.evaluate(script),
        timeout_seconds,
        reason,
        config_dict,
        default=default,
    )


async def query_selector_with_timeout(
    obj: Any,
    selector: str,
    config_dict: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_EVALUATE_TIMEOUT_SECONDS,
    reason: str = "query_selector",
) -> Any:
    return await wait_for_operation(
        obj.query_selector(selector),
        timeout_seconds,
        reason,
        config_dict,
        default=None,
    )


async def query_selector_all_with_timeout(
    obj: Any,
    selector: str,
    config_dict: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_EVALUATE_TIMEOUT_SECONDS,
    reason: str = "query_selector_all",
) -> list[Any] | None:
    return await wait_for_operation(
        obj.query_selector_all(selector),
        timeout_seconds,
        reason,
        config_dict,
        default=None,
    )


async def read_document_ready_state(tab: Any, config_dict: dict[str, Any] | None = None) -> str:
    result = await evaluate_with_timeout(
        tab,
        "document.readyState",
        config_dict,
        timeout_seconds=1.0,
        reason="ready_state",
        default="",
    )
    try:
        return str(util.parse_nodriver_result(result) or "")
    except Exception:
        return str(result or "")


async def wait_for_interactive_ready(
    tab: Any,
    config_dict: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_READY_STATE_TIMEOUT_SECONDS,
) -> bool:
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    last_state = ""
    while time.monotonic() < deadline:
        touch_heartbeat()
        state = await read_document_ready_state(tab, config_dict)
        last_state = state
        if state in {"interactive", "complete"}:
            runtime_log("[LEAK] ready_state", config_dict, state=state)
            return True
        await asyncio.sleep(0.2)
    runtime_log("[LEAK] ready_state_timeout", config_dict, state=last_state)
    return False
