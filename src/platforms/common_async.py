"""Async utility helpers shared by platform modules.

The helpers in this module are intentionally generic. They do not encode any
platform-specific automation behavior; they only provide bounded waiting,
safe CPU offloading, and JavaScript string serialization primitives.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, TypeVar, overload

from run_modes import get_effective_reload_interval


LOGGER = logging.getLogger(__name__)

T = TypeVar("T")
PollCallback = Callable[[], T | None | Awaitable[T | None]]


async def _maybe_await(value: T | None | Awaitable[T | None]) -> T | None:
    if inspect.isawaitable(value):
        return await value
    return value


def _finite_float(value: Any, error_message: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(error_message) from exc
    if not math.isfinite(normalized):
        raise ValueError(error_message)
    return normalized


@overload
async def bounded_poll(
    callback: Callable[[], Awaitable[T | None]],
    *,
    timeout: float = 5.0,
    interval: float = 0.25,
    description: str = "condition",
    log_exceptions: bool = True,
) -> T | None: ...


@overload
async def bounded_poll(
    callback: Callable[[], T | None],
    *,
    timeout: float = 5.0,
    interval: float = 0.25,
    description: str = "condition",
    log_exceptions: bool = True,
) -> T | None: ...


async def bounded_poll(
    callback: PollCallback[T],
    *,
    timeout: float = 5.0,
    interval: float = 0.25,
    description: str = "condition",
    log_exceptions: bool = True,
) -> T | None:
    """Poll ``callback`` until it returns a truthy value or the timeout expires.

    ``callback`` may be sync or async. Exceptions are logged at DEBUG level and
    polling continues until the timeout; this keeps transient DOM lookup errors
    from crashing normal browser flows while still making failures visible.
    """
    timeout_value = _finite_float(timeout, "timeout must be non-negative")
    interval_value = _finite_float(interval, "interval must be positive")

    if timeout_value < 0:
        raise ValueError("timeout must be non-negative")
    if interval_value <= 0:
        raise ValueError("interval must be positive")

    deadline = time.monotonic() + timeout_value
    last_error: Exception | None = None

    while True:
        try:
            result = await _maybe_await(callback())
            if result:
                return result
        except Exception as exc:
            last_error = exc
            if log_exceptions:
                LOGGER.debug("Polling %s failed transiently: %s", description, exc)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if last_error and log_exceptions:
                LOGGER.debug(
                    "Polling %s timed out after transient errors",
                    description,
                    exc_info=(type(last_error), last_error, last_error.__traceback__),
                )
            return None

        await asyncio.sleep(min(interval_value, remaining))


async def run_cpu_bound(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a CPU-bound or blocking callable without blocking the event loop."""
    return await asyncio.to_thread(func, *args, **kwargs)


def js_string_literal(value: str) -> str:
    """Serialize a Python string as a safe JavaScript string literal."""
    return json.dumps(value, ensure_ascii=False)


def is_interval_due(now: float, last_check: float, interval: float) -> bool:
    """Return True when a periodic background check should run."""
    try:
        interval_value = float(interval)
    except (TypeError, ValueError):
        interval_value = 0.0

    if last_check <= 0 or interval_value <= 0 or not math.isfinite(interval_value):
        return True
    return (now - last_check) >= interval_value


def get_auto_reload_interval(config_dict: dict[str, Any] | None, default: float = 0.0) -> float:
    """Read the active safe-page reload interval.

    Onsale mode preserves the v0.4.0 hotfix behavior and reads
    ``auto_reload_page_interval``. Leak-watch mode reads the dedicated
    ``leak_refresh_interval_seconds`` value. Platform modules call this helper
    from safe selection pages; protected ticket/order/checkout/payment reloads
    remain blocked by ReloadGuard.
    """
    return float(get_effective_reload_interval(config_dict, default))


async def reload_safe_page_when_due(
    tab: Any,
    config_dict: dict[str, Any] | None,
    state: MutableMapping[str, Any],
    state_key: str,
    reason: str,
    *,
    default: float = 0.0,
    now: float | None = None,
) -> bool:
    """Reload a repeatedly unsuccessful *safe* selection page when due.

    The first failed scan arms a deadline instead of blocking the platform loop.
    Later scans reload only after the configured interval.  ``guarded_reload``
    remains the final authority, so queue, ticket, order, checkout, payment and
    unknown routes on known ticketing hosts still fail closed.

    ``state_key`` gives each platform flow an independent deadline.  A URL or
    interval change automatically rearms it, which avoids carrying a stale
    deadline across navigation or a live settings update.
    """
    interval = get_auto_reload_interval(config_dict, default=default)
    prefix = f"_safe_page_reload:{state_key}"
    identity_key = f"{prefix}:identity"
    deadline_key = f"{prefix}:deadline"

    if interval <= 0:
        state.pop(identity_key, None)
        state.pop(deadline_key, None)
        return False

    target = getattr(tab, "target", None)
    url = str(getattr(target, "url", "") or "")
    identity = (url, interval)
    current = time.monotonic() if now is None else _finite_float(now, "now must be finite")

    if state.get(identity_key) != identity:
        state[identity_key] = identity
        state[deadline_key] = current + interval
        return False

    try:
        deadline = float(state.get(deadline_key, current + interval))
    except (TypeError, ValueError):
        deadline = current + interval
    if not math.isfinite(deadline) or current < deadline:
        if not math.isfinite(deadline):
            state[deadline_key] = current + interval
        return False

    # Advance before awaiting so concurrent dispatch iterations cannot start a
    # second reload.  The shared refresh coordinator supplies another
    # single-flight/minimum-interval boundary at the browser-action layer.
    state[deadline_key] = current + interval
    from reload_guard import guarded_reload

    return bool(await guarded_reload(tab, reason=reason, config_dict=config_dict))
