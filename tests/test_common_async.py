from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from platforms.common_async import (
    bounded_poll,
    get_auto_reload_interval,
    is_interval_due,
    js_string_literal,
    run_cpu_bound,
)


@pytest.mark.asyncio
async def test_bounded_poll_returns_first_truthy_value() -> None:
    attempts = 0

    async def callback() -> str | None:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            return "ready"
        return None

    assert await bounded_poll(callback, timeout=1.0, interval=0.01) == "ready"
    assert attempts == 2


@pytest.mark.asyncio
async def test_bounded_poll_times_out_without_raising() -> None:
    assert await bounded_poll(lambda: None, timeout=0.02, interval=0.01) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout": float("nan")},
        {"timeout": float("inf")},
        {"interval": float("nan")},
        {"interval": float("inf")},
        {"timeout": "bad"},
        {"interval": "bad"},
        {"timeout": None},
        {"interval": None},
    ],
)
async def test_bounded_poll_rejects_invalid_timing_values(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        await asyncio.wait_for(bounded_poll(lambda: None, **kwargs), timeout=0.05)


@pytest.mark.asyncio
async def test_bounded_poll_logs_transient_exceptions(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    attempts = 0

    def callback() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("not ready")
        return True

    assert await bounded_poll(callback, timeout=1.0, interval=0.01, description="test callback") is True
    assert "test callback" in caplog.text


@pytest.mark.asyncio
async def test_run_cpu_bound_uses_thread_executor() -> None:
    def blocking_add(left: int, right: int) -> int:
        return left + right

    assert await run_cpu_bound(blocking_add, 2, 3) == 5


def test_js_string_literal_escapes_for_javascript() -> None:
    assert js_string_literal('a"b\\c\n') == '"a\\"b\\\\c\\n"'
    assert js_string_literal("</script>") == '"</script>"'


def test_is_interval_due_handles_initial_elapsed_and_invalid_intervals() -> None:
    assert is_interval_due(10.0, 0.0, 0.5) is True
    assert is_interval_due(10.0, 9.0, 0.5) is True
    assert is_interval_due(10.0, 9.8, 0.5) is False
    assert is_interval_due(10.0, 9.8, "bad") is True
    assert is_interval_due(10.0, 9.8, "nan") is True
    assert is_interval_due(10.0, 9.8, "inf") is True


def test_get_auto_reload_interval_normalizes_user_values() -> None:
    assert get_auto_reload_interval({"advanced": {"auto_reload_page_interval": 0}}) == 0.0
    assert get_auto_reload_interval({"advanced": {"auto_reload_page_interval": "0"}}) == 0.0
    assert get_auto_reload_interval({"advanced": {"auto_reload_page_interval": "3.5"}}) == 3.5
    assert get_auto_reload_interval({"advanced": {"auto_reload_page_interval": "1.400"}}) == 1.4
    assert get_auto_reload_interval({"advanced": {"auto_reload_page_interval": "1.4"}}) == 1.4
    assert get_auto_reload_interval({"advanced": {"auto_reload_page_interval": ""}}, default=2.0) == 2.0
    assert get_auto_reload_interval({"advanced": {"auto_reload_page_interval": -1}}, default=2.0) == 0.0
    assert get_auto_reload_interval({"advanced": {"auto_reload_page_interval": "bad"}}, default=2.0) == 2.0
    assert get_auto_reload_interval({"advanced": {"auto_reload_page_interval": "nan"}}, default=2.0) == 2.0
    assert get_auto_reload_interval({"advanced": {"auto_reload_page_interval": "inf"}}, default=2.0) == 2.0
