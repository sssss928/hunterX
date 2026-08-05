from __future__ import annotations

import asyncio
import os
import threading
import time
from contextlib import suppress
from typing import Any, Awaitable, cast
from urllib.parse import urlsplit, urlunsplit

import util
from notification_context import redact_sensitive_text


DEFAULT_RELOAD_TIMEOUT_SECONDS = 10.0
DEFAULT_NAVIGATION_TIMEOUT_SECONDS = 10.0
DEFAULT_EVALUATE_TIMEOUT_SECONDS = 4.0
DEFAULT_READY_STATE_TIMEOUT_SECONDS = 6.0
HEARTBEAT_FILE = "heartbeat.txt"
RUNTIME_LOG_MAX_BYTES = 4 * 1024 * 1024
RUNTIME_LOG_BACKUP_COUNT = 2
RUNTIME_LOG_SIZE_RESYNC_WRITES = 256
RUNTIME_FILE_STATE_CAPACITY = 64
RUNTIME_DIAGNOSTIC_URL_MAX_LENGTH = 512
INTERACTIVE_HEARTBEAT_MIN_INTERVAL_SECONDS = 1.0
BROWSER_ACTION_CAPACITY = 256
_RUNTIME_LOG_LOCK = threading.RLock()
_RUNTIME_LOG_SIZE_STATE: dict[str, tuple[int, int]] = {}
_HEARTBEAT_LOCK = threading.Lock()
_HEARTBEAT_LAST_WRITE: dict[str, float] = {}
_BROWSER_ACTION_LOCK = threading.Lock()
_BROWSER_ACTIONS: dict[int, tuple[Any, int, str]] = {}
_BROWSER_ACTION_SEQUENCE = 0
_BROWSER_CONNECTION_CLOSED_MARKERS = (
    "connectionclosed",
    "connection closed",
    "no close frame received",
    "websocket is not connected",
    "executor shutdown has been called",
    "browser is already gone",
)


def try_begin_browser_action(tab: Any, action: str) -> int | None:
    """Acquire the per-tab reload/navigation single-flight slot."""

    global _BROWSER_ACTION_SEQUENCE
    tab_key = id(tab)
    with _BROWSER_ACTION_LOCK:
        if tab_key in _BROWSER_ACTIONS:
            return None
        if len(_BROWSER_ACTIONS) >= BROWSER_ACTION_CAPACITY:
            return None
        _BROWSER_ACTION_SEQUENCE += 1
        token = _BROWSER_ACTION_SEQUENCE
        _BROWSER_ACTIONS[tab_key] = (tab, token, str(action or "browser_action"))
        return token


def finish_browser_action(tab: Any, token: int) -> None:
    """Release a slot only when it is still owned by the supplied token."""

    tab_key = id(tab)
    with _BROWSER_ACTION_LOCK:
        active = _BROWSER_ACTIONS.get(tab_key)
        if active is not None and active[0] is tab and active[1] == token:
            _BROWSER_ACTIONS.pop(tab_key, None)


def get_active_browser_action_count() -> int:
    with _BROWSER_ACTION_LOCK:
        return len(_BROWSER_ACTIONS)


def is_browser_connection_closed_error(exc: BaseException | None) -> bool:
    """Recognize a terminated CDP/WebSocket session without masking other bugs."""

    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        error_name = f"{type(current).__module__}.{type(current).__name__}".casefold()
        message = str(current).casefold()
        if "websockets" in error_name and "connectionclosed" in error_name:
            return True
        if any(marker in error_name or marker in message for marker in _BROWSER_CONNECTION_CLOSED_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


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


def _diagnostic_field_value(key: str, value: Any) -> Any:
    """Keep route diagnostics useful without persisting URL credentials."""

    if "url" not in str(key).casefold() or not isinstance(value, str):
        return value
    safe_value = value.replace("\r", " ").replace("\n", " ").strip()
    try:
        parts = urlsplit(safe_value)
    except ValueError:
        return safe_value[:RUNTIME_DIAGNOSTIC_URL_MAX_LENGTH]
    if not parts.scheme or not parts.netloc:
        return safe_value[:RUNTIME_DIAGNOSTIC_URL_MAX_LENGTH]
    safe_netloc = parts.netloc.rsplit("@", 1)[-1]
    route = urlunsplit((parts.scheme, safe_netloc, parts.path, "", ""))
    return route[:RUNTIME_DIAGNOSTIC_URL_MAX_LENGTH]


def _get_runtime_log_size_state(log_path: str) -> tuple[int, int]:
    state = _RUNTIME_LOG_SIZE_STATE.get(log_path)
    if state is None or state[1] >= RUNTIME_LOG_SIZE_RESYNC_WRITES:
        try:
            file_size = max(0, os.path.getsize(log_path))
        except OSError:
            file_size = 0
        state = (file_size, 0)
        if (
            log_path not in _RUNTIME_LOG_SIZE_STATE
            and len(_RUNTIME_LOG_SIZE_STATE) >= RUNTIME_FILE_STATE_CAPACITY
        ):
            _RUNTIME_LOG_SIZE_STATE.clear()
        _RUNTIME_LOG_SIZE_STATE[log_path] = state
    return state


def _rotate_runtime_log(log_path: str) -> bool:
    """Bound each instance log with a cached size check and thread-safe rotation."""

    with _RUNTIME_LOG_LOCK:
        file_size, _ = _get_runtime_log_size_state(log_path)
        if file_size < RUNTIME_LOG_MAX_BYTES:
            return False

        try:
            oldest = f"{log_path}.{RUNTIME_LOG_BACKUP_COUNT}"
            if os.path.exists(oldest):
                os.remove(oldest)
            for index in range(RUNTIME_LOG_BACKUP_COUNT - 1, 0, -1):
                source = f"{log_path}.{index}"
                if os.path.exists(source):
                    os.replace(source, f"{log_path}.{index + 1}")
            os.replace(log_path, f"{log_path}.1")
        except OSError:
            return False
        _RUNTIME_LOG_SIZE_STATE[log_path] = (0, 0)
        return True


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
        parts.append(f"{key}={_diagnostic_field_value(key, value)}")
    line = redact_sensitive_text(" ".join(parts))

    with suppress(Exception), _RUNTIME_LOG_LOCK:
        log_path = _instance_log_path()
        _rotate_runtime_log(log_path)
        rendered_line = line + "\n"
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(rendered_line)
        cached_size, cached_writes = _get_runtime_log_size_state(log_path)
        rendered_size = len((line + os.linesep).encode("utf-8"))
        _RUNTIME_LOG_SIZE_STATE[log_path] = (
            cached_size + rendered_size,
            cached_writes + 1,
        )


def touch_heartbeat(
    filename: str = HEARTBEAT_FILE,
    *,
    min_interval_seconds: float = 0.0,
) -> None:
    heartbeat_path = util.get_instance_state_path(filename)
    current = time.monotonic()
    try:
        minimum_interval = max(0.0, float(min_interval_seconds))
    except (TypeError, ValueError):
        minimum_interval = 0.0

    with _HEARTBEAT_LOCK:
        previous = _HEARTBEAT_LAST_WRITE.get(heartbeat_path)
        if previous is not None:
            elapsed = current - previous
            if minimum_interval > 0.0 and 0.0 <= elapsed < minimum_interval:
                return
        try:
            with open(heartbeat_path, "w", encoding="utf-8") as heartbeat_file:
                heartbeat_file.write(str(int(time.time())))
        except Exception:
            return
        if (
            heartbeat_path not in _HEARTBEAT_LAST_WRITE
            and len(_HEARTBEAT_LAST_WRITE) >= RUNTIME_FILE_STATE_CAPACITY
        ):
            _HEARTBEAT_LAST_WRITE.clear()
        _HEARTBEAT_LAST_WRITE[heartbeat_path] = current


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
    log_success: bool = True,
) -> Any:
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(awaitable, timeout=max(0.1, float(timeout_seconds)))
        if log_success:
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
    from page_classifier import classify_page

    source_url = getattr(getattr(tab, "target", None), "url", "") or ""
    runtime_log(
        "[NAVIGATION] intent",
        config_dict,
        reason=reason,
        source_url=source_url,
        target_url=url,
        page_class=classify_page(source_url).value,
        attempt_id=None,
        generation=None,
        token=None,
    )
    action_token = try_begin_browser_action(tab, reason)
    if action_token is None:
        runtime_log(
            "[NAVIGATION] skipped",
            config_dict,
            reason="browser_action_in_flight",
            target_url=url,
        )
        return False
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
    finally:
        finish_browser_action(tab, action_token)


async def guarded_driver_get(
    driver: Any,
    url: str,
    config_dict: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_NAVIGATION_TIMEOUT_SECONDS,
    reason: str = "initial_navigation",
) -> Any | None:
    """Single-flight browser startup navigation that returns the created tab."""

    runtime_log(
        "[NAVIGATION] intent",
        config_dict,
        reason=reason,
        source_url="",
        target_url=url,
        page_class="unknown",
        attempt_id=None,
        generation=None,
        token=None,
    )
    action_token = try_begin_browser_action(driver, reason)
    if action_token is None:
        runtime_log(
            "[NAVIGATION] skipped",
            config_dict,
            reason="browser_action_in_flight",
            target_url=url,
        )
        return None
    try:
        return await wait_for_operation(
            driver.get(url),
            timeout_seconds,
            reason,
            config_dict,
            raise_on_timeout=True,
        )
    except TimeoutError:
        return None
    finally:
        finish_browser_action(driver, action_token)


async def evaluate_with_timeout(
    tab: Any,
    script: str,
    config_dict: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_EVALUATE_TIMEOUT_SECONDS,
    reason: str = "evaluate",
    default: Any = None,
    log_success: bool = True,
) -> Any:
    return await wait_for_operation(
        tab.evaluate(script),
        timeout_seconds,
        reason,
        config_dict,
        default=default,
        log_success=log_success,
    )


async def query_selector_with_timeout(
    obj: Any,
    selector: str,
    config_dict: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_EVALUATE_TIMEOUT_SECONDS,
    reason: str = "query_selector",
    log_success: bool = True,
) -> Any:
    return await wait_for_operation(
        obj.query_selector(selector),
        timeout_seconds,
        reason,
        config_dict,
        default=None,
        log_success=log_success,
    )


async def query_selector_all_with_timeout(
    obj: Any,
    selector: str,
    config_dict: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_EVALUATE_TIMEOUT_SECONDS,
    reason: str = "query_selector_all",
    log_success: bool = True,
) -> list[Any] | None:
    return cast(
        list[Any] | None,
        await wait_for_operation(
            obj.query_selector_all(selector),
            timeout_seconds,
            reason,
            config_dict,
            default=None,
            log_success=log_success,
        ),
    )


async def read_document_ready_state(
    tab: Any,
    config_dict: dict[str, Any] | None = None,
    *,
    log_success: bool = True,
) -> str:
    result = await evaluate_with_timeout(
        tab,
        "document.readyState",
        config_dict,
        timeout_seconds=1.0,
        reason="ready_state",
        default="",
        log_success=log_success,
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
    log_success: bool = True,
) -> bool:
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    last_state = ""
    while time.monotonic() < deadline:
        touch_heartbeat(
            min_interval_seconds=INTERACTIVE_HEARTBEAT_MIN_INTERVAL_SECONDS
        )
        state = await read_document_ready_state(
            tab,
            config_dict,
            log_success=log_success,
        )
        last_state = state
        if state in {"interactive", "complete"}:
            if log_success:
                runtime_log("[LEAK] ready_state", config_dict, state=state)
            return True
        await asyncio.sleep(0.2)
    runtime_log("[LEAK] ready_state_timeout", config_dict, state=last_state)
    return False
