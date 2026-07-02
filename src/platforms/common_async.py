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
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, overload


LOGGER = logging.getLogger(__name__)

T = TypeVar("T")
PollCallback = Callable[[], T | None | Awaitable[T | None]]


async def _maybe_await(value: T | None | Awaitable[T | None]) -> T | None:
    if inspect.isawaitable(value):
        return await value
    return value


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
    if timeout < 0:
        raise ValueError("timeout must be non-negative")
    if interval <= 0:
        raise ValueError("interval must be positive")

    deadline = time.monotonic() + timeout
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

        await asyncio.sleep(min(interval, remaining))


async def run_cpu_bound(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a CPU-bound or blocking callable without blocking the event loop."""
    return await asyncio.to_thread(func, *args, **kwargs)


def js_string_literal(value: str) -> str:
    """Serialize a Python string as a safe JavaScript string literal."""
    return json.dumps(value, ensure_ascii=False)
