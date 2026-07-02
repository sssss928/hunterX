from __future__ import annotations

import logging

import pytest

from platforms.common_async import bounded_poll, js_string_literal, run_cpu_bound


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
