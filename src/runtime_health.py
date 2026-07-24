from __future__ import annotations

import asyncio
import inspect
import os
import time
import weakref
from contextlib import suppress
from typing import Any, Awaitable

import util
from notification_context import redact_sensitive_text


DEFAULT_RELOAD_TIMEOUT_SECONDS = 10.0
DEFAULT_NAVIGATION_TIMEOUT_SECONDS = 10.0
DEFAULT_EVALUATE_TIMEOUT_SECONDS = 4.0
DEFAULT_READY_STATE_TIMEOUT_SECONDS = 6.0
HEARTBEAT_FILE = "heartbeat.txt"

_PENDING_OPERATIONS: set[asyncio.Task[Any]] = set()
_OWNER_OPERATIONS: dict[int, asyncio.Task[Any]] = {}
_CLOSED_OWNER_IDS: set[int] = set()
_CLOSED_OWNERS: weakref.WeakSet[Any] = weakref.WeakSet()


def is_connection_closed_error(exc: BaseException) -> bool:
    """Return True for transport/browser shutdown errors from supported drivers."""

    class_name = type(exc).__name__.lower()
    module_name = type(exc).__module__.lower()
    message = str(exc).lower()
    if class_name in {
        "connectionclosed",
        "connectionclosederror",
        "connectionclosedok",
        "invalidstateerror",
    }:
        return True
    if "websocket" in module_name and "closed" in class_name:
        return True
    markers = (
        "no close frame received or sent",
        "connection is closed",
        "connection closed",
        "websocket is closed",
        "no websocket connection",
        "browser has been closed",
        "target closed",
        "session closed",
        "executor shutdown has been called",
        "cannot schedule new futures after shutdown",
    )
    return any(marker in message for marker in markers)


def mark_connection_closed(owner: Any | None) -> None:
    if owner is None:
        return
    try:
        _CLOSED_OWNERS.add(owner)
    except TypeError:
        _CLOSED_OWNER_IDS.add(id(owner))


def is_connection_available(owner: Any | None) -> bool:
    if owner is None:
        return True
    try:
        if owner in _CLOSED_OWNERS:
            return False
    except TypeError:
        if id(owner) in _CLOSED_OWNER_IDS:
            return False
    if id(owner) in _CLOSED_OWNER_IDS:
        return False
    websocket = getattr(owner, "websocket", ...)
    if websocket is None:
        return False
    connection = getattr(owner, "connection", None)
    if connection is not None and bool(getattr(connection, "closed", False)):
        return False
    browser = getattr(owner, "browser", None)
    if browser is not None and bool(getattr(browser, "stopped", False)):
        return False
    return True


def _dispose_unstarted_awaitable(awaitable: Awaitable[Any]) -> None:
    """Close a coroutine that cannot be scheduled without emitting a warning."""

    if inspect.iscoroutine(awaitable):
        awaitable.close()


def _consume_operation_result(task: asyncio.Task[Any], owner_id: int | None) -> None:
    _PENDING_OPERATIONS.discard(task)
    if owner_id is not None and _OWNER_OPERATIONS.get(owner_id) is task:
        _OWNER_OPERATIONS.pop(owner_id, None)
    if task.cancelled():
        return
    with suppress(BaseException):
        task.result()


def pending_operation_count() -> int:
    return sum(1 for task in _PENDING_OPERATIONS if not task.done())


async def drain_pending_operations(timeout_seconds: float = 1.0) -> int:
    """Give protocol operations a bounded opportunity to finish during shutdown."""

    pending = {task for task in _PENDING_OPERATIONS if not task.done()}
    if not pending:
        return 0
    _done, still_pending = await asyncio.wait(
        pending,
        timeout=max(0.0, float(timeout_seconds)),
        return_when=asyncio.ALL_COMPLETED,
    )
    return len(still_pending)


async def cancel_pending_operations() -> int:
    """Cancel and retrieve operations only after their transport is stopped."""

    pending = [task for task in _PENDING_OPERATIONS if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return len(pending)


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
    operation_owner: Any | None = None,
) -> Any:
    """Wait without cancelling the driver's protocol Future on timeout.

    Zendriver Transaction objects are Futures. Cancelling one with
    ``asyncio.wait_for`` leaves it in the connection mapper; the listener later
    calls ``set_result`` and raises ``InvalidStateError``. A timed-out operation
    therefore remains tracked until the driver completes it or the transport
    closes. Only one outstanding operation is allowed per owner.
    """

    started = time.perf_counter()
    owner_id = id(operation_owner) if operation_owner is not None else None
    if operation_owner is not None and not is_connection_available(operation_owner):
        _dispose_unstarted_awaitable(awaitable)
        runtime_log(f"[{action}] connection_closed", config_dict, elapsed_ms=0)
        return default
    if owner_id is not None:
        existing = _OWNER_OPERATIONS.get(owner_id)
        if existing is not None and not existing.done():
            _dispose_unstarted_awaitable(awaitable)
            runtime_log(f"[{action}] operation_pending", config_dict)
            return default

    task = asyncio.ensure_future(awaitable)
    _PENDING_OPERATIONS.add(task)
    if owner_id is not None:
        _OWNER_OPERATIONS[owner_id] = task
    task.add_done_callback(lambda done: _consume_operation_result(done, owner_id))

    try:
        done, _pending = await asyncio.wait(
            {task},
            timeout=max(0.1, float(timeout_seconds)),
            return_when=asyncio.FIRST_COMPLETED,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if not done:
            runtime_log(f"[{action}] timeout", config_dict, elapsed_ms=elapsed_ms)
            if raise_on_timeout:
                raise TimeoutError(f"{action} timed out after {timeout_seconds} seconds")
            return default
        result = task.result()
        runtime_log(f"[{action}] done", config_dict, elapsed_ms=elapsed_ms)
        return result
    except asyncio.CancelledError:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        runtime_log(f"[{action}] cancelled", config_dict, elapsed_ms=elapsed_ms)
        raise
    except BaseException as exc:
        if not is_connection_closed_error(exc):
            raise
        mark_connection_closed(operation_owner)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        runtime_log(
            f"[{action}] connection_closed",
            config_dict,
            elapsed_ms=elapsed_ms,
            error_type=type(exc).__name__,
        )
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
            operation_owner=tab,
        )
        return True
    except (TimeoutError, asyncio.TimeoutError):
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
        operation_owner=tab,
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
        operation_owner=obj,
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
        operation_owner=obj,
    )


async def read_document_ready_state(tab: Any, config_dict: dict[str, Any] | None = None) -> str:
    if not is_connection_available(tab):
        return "connection_closed"
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
        if state == "connection_closed":
            runtime_log("[LEAK] ready_state_connection_closed", config_dict)
            return False
        if state in {"interactive", "complete"}:
            runtime_log("[LEAK] ready_state", config_dict, state=state)
            return True
        await asyncio.sleep(0.2)
    runtime_log("[LEAK] ready_state_timeout", config_dict, state=last_state)
    return False
