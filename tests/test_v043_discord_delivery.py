from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

import util


class _MockWebhookState:
    def __init__(self) -> None:
        self.responses: list[tuple[int, dict[str, str], float]] = []
        self.requests: list[dict] = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.block_requests = False


class _MockWebhookHandler(BaseHTTPRequestHandler):
    server: "_MockWebhookServer"

    def do_POST(self) -> None:  # noqa: N802
        state = self.server.state
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        state.requests.append(payload)
        state.started.set()
        if state.block_requests:
            state.release.wait(timeout=2.0)
        status, headers, delay = (
            state.responses.pop(0)
            if state.responses
            else (204, {}, 0.0)
        )
        if delay:
            time.sleep(delay)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()

    def log_message(self, _format: str, *_args) -> None:
        return


class _MockWebhookServer(ThreadingHTTPServer):
    def __init__(self, state: _MockWebhookState) -> None:
        super().__init__(("127.0.0.1", 0), _MockWebhookHandler)
        self.state = state


@pytest.fixture
def mock_webhook() -> Iterator[tuple[str, _MockWebhookState]]:
    state = _MockWebhookState()
    server = _MockWebhookServer(state)
    thread = threading.Thread(
        target=server.serve_forever,
        name="hunterx-test-webhook",
        daemon=True,
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/hook", state
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def _dispatcher(**overrides) -> util.DiscordNotificationDispatcher:
    options = {
        "queue_capacity": 8,
        "worker_count": 1,
        "max_attempts": 3,
        "max_elapsed_seconds": 2.0,
        "base_backoff_seconds": 0.001,
        "max_backoff_seconds": 0.01,
    }
    options.update(overrides)
    return util.DiscordNotificationDispatcher(**options)


@pytest.mark.parametrize("success_status", [200, 204])
def test_http_success_is_the_delivered_terminal_state(
    mock_webhook,
    success_status: int,
) -> None:
    url, state = mock_webhook
    state.responses = [(success_status, {}, 0.0)]
    dispatcher = _dispatcher()

    assert dispatcher.enqueue(
        url,
        "checkout_reached",
        "TixCraft",
        notification_id="attempt-1-checkout",
        custom_message="offline fixture",
    ) == "attempt-1-checkout"
    assert dispatcher.drain(timeout=2.0)

    status = dispatcher.get_status("attempt-1-checkout")
    assert status is not None
    assert status.state == util.DiscordDeliveryState.DELIVERED.value
    assert status.http_status == success_status
    assert status.attempt_count == 1
    assert status.history == ("detected", "enqueued", "sending", "delivered")
    assert state.requests == [{"content": "offline fixture", "username": "HunterX"}]
    assert dispatcher.shutdown(timeout=1.0)


def test_http_429_respects_retry_after_then_delivers(mock_webhook) -> None:
    url, state = mock_webhook
    state.responses = [
        (429, {"Retry-After": "0.015"}, 0.0),
        (204, {}, 0.0),
    ]
    sleeps: list[float] = []
    dispatcher = _dispatcher(sleep_func=sleeps.append)

    assert dispatcher.enqueue(
        url,
        "order_pending",
        "TixCraft",
        notification_id="attempt-1-order",
    )
    assert dispatcher.drain(timeout=2.0)

    status = dispatcher.get_status("attempt-1-order")
    assert status is not None
    assert status.state == "delivered"
    assert status.attempt_count == 2
    assert sleeps == [0.015]
    assert len(state.requests) == 2
    assert dispatcher.shutdown(timeout=1.0)


@pytest.mark.parametrize("first_status", [500, 502, 503])
def test_http_5xx_has_bounded_retry_and_can_recover(
    mock_webhook,
    first_status: int,
) -> None:
    url, state = mock_webhook
    state.responses = [(first_status, {}, 0.0), (204, {}, 0.0)]
    dispatcher = _dispatcher(sleep_func=lambda _seconds: None)

    assert dispatcher.enqueue(
        url,
        "order_pending",
        "TixCraft",
        notification_id=f"retry-{first_status}",
    )
    assert dispatcher.drain(timeout=2.0)
    status = dispatcher.get_status(f"retry-{first_status}")
    assert status is not None
    assert status.state == "delivered"
    assert status.attempt_count == 2
    assert len(state.requests) == 2
    assert dispatcher.shutdown(timeout=1.0)


def test_permanent_http_5xx_stops_at_configured_attempt_limit(
    mock_webhook,
) -> None:
    url, state = mock_webhook
    state.responses = [(503, {}, 0.0)] * 3
    dispatcher = _dispatcher(sleep_func=lambda _seconds: None)

    assert dispatcher.enqueue(
        url,
        "order_pending",
        "TixCraft",
        notification_id="permanent-503",
    )
    assert dispatcher.drain(timeout=2.0)
    status = dispatcher.get_status("permanent-503")
    assert status is not None
    assert status.state == "failed"
    assert status.http_status == 503
    assert status.attempt_count == 3
    assert len(state.requests) == 3
    assert dispatcher.shutdown(timeout=1.0)


def test_non_429_http_4xx_is_not_retried(mock_webhook) -> None:
    url, state = mock_webhook
    state.responses = [(400, {}, 0.0), (204, {}, 0.0)]
    dispatcher = _dispatcher()

    assert dispatcher.enqueue(
        url,
        "order_pending",
        "TixCraft",
        notification_id="bad-request",
    )
    assert dispatcher.drain(timeout=2.0)
    status = dispatcher.get_status("bad-request")
    assert status is not None
    assert status.state == "failed"
    assert status.http_status == 400
    assert status.attempt_count == 1
    assert len(state.requests) == 1
    assert dispatcher.shutdown(timeout=1.0)


def test_timeout_is_retried_only_to_configured_limit(caplog) -> None:
    calls = 0
    sensitive_url = "test-webhook-secret-value"

    def timeout_post(url: str, **_kwargs):
        nonlocal calls
        calls += 1
        raise requests.Timeout(f"timeout posting to {url}; token=private-value")

    dispatcher = _dispatcher(
        post_func=timeout_post,
        sleep_func=lambda _seconds: None,
        max_attempts=3,
    )
    assert dispatcher.enqueue(
        sensitive_url,
        "order_pending",
        "TixCraft",
        notification_id="timeout-event",
    )
    assert dispatcher.drain(timeout=2.0)

    status = dispatcher.get_status("timeout-event")
    assert status is not None
    assert status.state == "failed"
    assert status.attempt_count == 3
    assert calls == 3
    assert sensitive_url not in status.error
    assert "private-value" not in status.error
    assert "token=***" in status.error
    assert sensitive_url not in caplog.text
    assert "private-value" not in caplog.text
    assert dispatcher.shutdown(timeout=1.0)


def test_network_error_after_http_error_clears_stale_status() -> None:
    calls = 0

    def mixed_post(_url: str, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            response = requests.Response()
            response.status_code = 503
            return response
        raise requests.Timeout("second attempt timed out")

    dispatcher = _dispatcher(
        post_func=mixed_post,
        sleep_func=lambda _seconds: None,
        max_attempts=2,
    )
    assert dispatcher.enqueue(
        "offline-webhook",
        "order_pending",
        "TixCraft",
        notification_id="mixed-failure",
    )
    assert dispatcher.drain(timeout=2.0)
    status = dispatcher.get_status("mixed-failure")
    assert status is not None
    assert status.state == "failed"
    assert status.http_status is None
    assert "Timeout" in status.error
    assert dispatcher.shutdown(timeout=1.0)


def test_enqueue_and_duplicate_deduplication_never_wait_for_http(
    mock_webhook,
) -> None:
    url, state = mock_webhook
    state.block_requests = True
    dispatcher = _dispatcher()

    started_at = time.perf_counter()
    first = dispatcher.enqueue(
        url,
        "checkout_reached",
        "TixCraft",
        notification_id="nonblocking",
    )
    elapsed = time.perf_counter() - started_at
    assert first == "nonblocking"
    assert elapsed < 0.05
    assert state.started.wait(timeout=1.0)

    assert dispatcher.enqueue(
        url,
        "checkout_reached",
        "TixCraft",
        notification_id="nonblocking",
    ) == "nonblocking"
    state.release.set()
    assert dispatcher.drain(timeout=2.0)
    assert len(state.requests) == 1
    assert dispatcher.shutdown(timeout=1.0)


def test_queue_full_is_observable_and_same_id_can_retry_after_capacity_returns(
    mock_webhook,
) -> None:
    url, state = mock_webhook
    state.block_requests = True
    dispatcher = _dispatcher(queue_capacity=1)

    assert dispatcher.enqueue(url, "order", "TixCraft", notification_id="active")
    assert state.started.wait(timeout=1.0)
    assert dispatcher.enqueue(url, "order", "TixCraft", notification_id="queued")
    assert (
        dispatcher.enqueue(url, "order", "TixCraft", notification_id="queue-full")
        is None
    )
    full_status = dispatcher.get_status("queue-full")
    assert full_status is not None
    assert full_status.state == "failed"
    assert full_status.error == "queue_full"
    for _ in range(40):
        assert (
            dispatcher.enqueue(
                url,
                "order",
                "TixCraft",
                notification_id="queue-full",
            )
            is None
        )
    bounded_status = dispatcher.get_status("queue-full")
    assert bounded_status is not None
    assert len(bounded_status.history) <= 32

    state.release.set()
    assert dispatcher.drain(timeout=2.0)
    assert dispatcher.enqueue(
        url,
        "order",
        "TixCraft",
        notification_id="queue-full",
    ) == "queue-full"
    assert dispatcher.drain(timeout=2.0)
    retried = dispatcher.get_status("queue-full")
    assert retried is not None
    assert retried.state == "delivered"
    assert retried.history[-1] == "delivered"
    assert len(retried.history) <= 32
    assert dispatcher.shutdown(timeout=1.0)


def test_shutdown_is_bounded_while_worker_is_blocked(mock_webhook) -> None:
    url, state = mock_webhook
    state.block_requests = True
    dispatcher = _dispatcher()
    assert dispatcher.enqueue(
        url,
        "checkout_reached",
        "TixCraft",
        notification_id="shutdown-bound",
    )
    assert state.started.wait(timeout=1.0)

    started_at = time.perf_counter()
    assert dispatcher.shutdown(timeout=0.05) is False
    assert time.perf_counter() - started_at < 0.25

    state.release.set()
    assert dispatcher.drain(timeout=2.0)
    assert dispatcher.shutdown(timeout=1.0)
