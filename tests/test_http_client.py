from __future__ import annotations

from typing import Any

import pytest

import util


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> object:
        self.calls.append((method, url, kwargs))
        return self


def test_http_request_requires_positive_timeout() -> None:
    with pytest.raises(ValueError):
        util.http_get("https://example.invalid", timeout=0)


def test_http_get_uses_provided_session_and_timeout() -> None:
    session = FakeSession()

    response = util.http_get("https://example.invalid", session=session, timeout=1.5, allow_redirects=False)

    assert response is session
    assert session.calls == [
        ("GET", "https://example.invalid", {"timeout": 1.5, "allow_redirects": False}),
    ]


def test_shared_http_session_is_reused() -> None:
    util.close_http_session()
    try:
        first = util.get_http_session()
        second = util.get_http_session()
        assert first is second
    finally:
        util.close_http_session()
