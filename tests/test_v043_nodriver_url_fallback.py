from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

import nodriver_common


@dataclass
class _Target:
    url: str


class _Tab:
    def __init__(self, outcomes: list[Any], target_url: str = "") -> None:
        self._outcomes = list(outcomes)
        self.target = _Target(target_url)

    async def js_dumps(self, script: str) -> Any:
        assert script == "window.location.href"
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _Debug:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def log(self, *parts: object) -> None:
        self.messages.append(" ".join(str(part) for part in parts))


def _js_url(url: str) -> dict[str, dict[str, str]]:
    return {"0": {"0": url}}


class _BrokenTarget:
    @property
    def url(self) -> str:
        raise RuntimeError("target-getter-secret")


@pytest.fixture
def debug(monkeypatch: pytest.MonkeyPatch) -> _Debug:
    logger = _Debug()
    monkeypatch.setattr(
        nodriver_common.util,
        "create_debug_logger",
        lambda config_dict=None: logger,
    )
    return logger


@pytest.mark.asyncio
async def test_general_js_exception_uses_target_url_fallback(debug: _Debug) -> None:
    target_url = "https://tixcraft.com/ticket/area/26_event/123"
    tab = _Tab([RuntimeError("execution context was destroyed")], target_url)

    url, is_quit_bot = await nodriver_common.nodriver_current_url(tab)

    assert url == target_url
    assert is_quit_bot is False


@pytest.mark.asyncio
async def test_js_timeout_keeps_existing_target_url_fallback(debug: _Debug) -> None:
    target_url = "https://tixcraft.com/ticket/area/26_event/456"
    tab = _Tab([asyncio.TimeoutError()], target_url)

    url, is_quit_bot = await nodriver_common.nodriver_current_url(tab)

    assert url == target_url
    assert is_quit_bot is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "js_result",
    [
        None,
        "",
        "   ",
        {},
        [],
        42,
        {"unexpected": {"0": "not-a-url"}},
        {"0": None},
        {"0": {"unexpected": "not-a-url"}},
        {"0": {"0": None}},
    ],
)
async def test_empty_or_malformed_js_result_uses_target_url_fallback(
    debug: _Debug,
    js_result: Any,
) -> None:
    target_url = (
        "https://tixcraft.com/ticket/area/26_event/457"
        "?session=target-secret#fragment-secret"
    )
    tab = _Tab([js_result], target_url)

    url, is_quit_bot = await nodriver_common.nodriver_current_url(tab)

    assert url == target_url
    assert is_quit_bot is False
    diagnostics = "\n".join(debug.messages)
    assert "target-secret" not in diagnostics
    assert "fragment-secret" not in diagnostics


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "js_result",
    [
        _js_url("https://tixcraft.com/ticket/area/26_current/458"),
        "https://tixcraft.com/ticket/area/26_current/458",
    ],
)
async def test_valid_js_url_wins_over_stale_target_url(
    debug: _Debug,
    js_result: Any,
) -> None:
    current_url = "https://tixcraft.com/ticket/area/26_current/458"
    stale_target_url = "https://tixcraft.com/ticket/area/26_stale/111"
    tab = _Tab([js_result], stale_target_url)

    url, is_quit_bot = await nodriver_common.nodriver_current_url(tab)

    assert url == current_url
    assert is_quit_bot is False


@pytest.mark.asyncio
async def test_serialized_url_preserves_payload_insertion_order(
    debug: _Debug,
) -> None:
    current_url = "https://tixcraft.com/ticket/area/26_current/458"
    serialized = {
        "2": {"0": "https://"},
        "0": {"0": "tixcraft.com"},
        "1": {"0": "/ticket/area/26_current/458"},
    }
    tab = _Tab([serialized], "https://tixcraft.com/activity/detail/stale")

    url, is_quit_bot = await nodriver_common.nodriver_current_url(tab)

    assert url == current_url
    assert is_quit_bot is False


@pytest.mark.asyncio
async def test_target_url_getter_exception_is_safe(
    debug: _Debug,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tab = _Tab([None])
    tab.target = _BrokenTarget()

    url, is_quit_bot = await nodriver_common.nodriver_current_url(tab)

    assert url == ""
    assert is_quit_bot is False
    assert "target-getter-secret" not in "\n".join(debug.messages)
    assert "target-getter-secret" not in capsys.readouterr().out


def test_empty_url_diagnostic_keeps_route_without_query_or_fragment() -> None:
    target_secret = "target-secret-value"
    fragment_secret = "fragment-secret-value"
    tab = _Tab(
        [],
        (
            "https://tixcraft.com/ticket/area/26_event/457"
            f"?session={target_secret}#{fragment_secret}"
        ),
    )

    diagnostic = nodriver_common.format_cached_target_url_diagnostic(tab)

    assert diagnostic == (
        "fallback_available=True; "
        "target_route='https://tixcraft.com/ticket/area/26_event/457'"
    )
    assert target_secret not in diagnostic
    assert fragment_secret not in diagnostic


def test_empty_url_diagnostic_handles_target_getter_exception() -> None:
    tab = _Tab([])
    tab.target = _BrokenTarget()

    diagnostic = nodriver_common.format_cached_target_url_diagnostic(tab)

    assert diagnostic == "fallback_available=False; target_route=''"
    assert "target-getter-secret" not in diagnostic


def test_empty_url_diagnostic_removes_embedded_credentials() -> None:
    tab = _Tab(
        [],
        (
            "https://diagnostic-user:diagnostic-password@tixcraft.com:443/"
            "ticket/area/26_event/457?session=query-secret#fragment-secret"
        ),
    )

    diagnostic = nodriver_common.format_cached_target_url_diagnostic(tab)

    assert diagnostic == (
        "fallback_available=True; "
        "target_route='https://tixcraft.com:443/ticket/area/26_event/457'"
    )
    assert "diagnostic-user" not in diagnostic
    assert "diagnostic-password" not in diagnostic
    assert "query-secret" not in diagnostic
    assert "fragment-secret" not in diagnostic


@pytest.mark.asyncio
async def test_empty_sources_do_not_reuse_stale_url_and_next_call_recovers(
    debug: _Debug,
) -> None:
    stale_url = "https://tixcraft.com/ticket/area/26_old/111"
    recovered_url = "https://tixcraft.com/ticket/area/26_new/222"
    tab = _Tab(
        [
            _js_url(stale_url),
            RuntimeError("no close frame received or sent"),
            _js_url(recovered_url),
        ]
    )

    first_url, _ = await nodriver_common.nodriver_current_url(tab)
    tab.target.url = ""
    empty_url, empty_quit = await nodriver_common.nodriver_current_url(tab)
    recovered, recovered_quit = await nodriver_common.nodriver_current_url(tab)

    assert first_url == stale_url
    assert empty_url == ""
    assert empty_quit is True
    assert recovered == recovered_url
    assert recovered_quit is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "no close frame received or sent",
        "Target closed",
        "Tab closed",
        "WebSocket connection is closed",
    ],
)
async def test_closing_active_tab_stops_automation(
    debug: _Debug,
    message: str,
) -> None:
    tab = _Tab([RuntimeError(message)], target_url="")

    url, is_quit_bot = await nodriver_common.nodriver_current_url(tab)

    assert url == ""
    assert is_quit_bot is True
    assert any("active tab closed" in message for message in debug.messages)


@pytest.mark.asyncio
async def test_close_frame_during_navigation_keeps_live_target(
    debug: _Debug,
) -> None:
    target_url = "https://www.indievox.com/activity/game/26_iv0404354"
    tab = _Tab([RuntimeError("no close frame received or sent")], target_url)

    url, is_quit_bot = await nodriver_common.nodriver_current_url(tab)

    assert url == target_url
    assert is_quit_bot is False


@pytest.mark.asyncio
async def test_fallback_preserves_quit_signal(debug: _Debug) -> None:
    target_url = "https://tixcraft.com/ticket/area/26_event/789"
    tab = _Tab([RuntimeError("[WinError 1225] browser closed")], target_url)

    url, is_quit_bot = await nodriver_common.nodriver_current_url(tab)

    assert url == target_url
    assert is_quit_bot is True


@pytest.mark.asyncio
async def test_url_diagnostics_do_not_write_target_query_or_exception_secret(
    debug: _Debug,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target_secret = "target-secret-value"
    fragment_secret = "fragment-secret-value"
    exception_secret = "exception-secret-value"
    tab = _Tab(
        [
            RuntimeError(
                f"execution context destroyed; token={exception_secret}"
            )
        ],
        (
            "https://tixcraft.com/ticket/area/26_event/999"
            f"?session={target_secret}#{fragment_secret}"
        ),
    )

    url, is_quit_bot = await nodriver_common.nodriver_current_url(tab)

    assert target_secret in url
    assert is_quit_bot is False
    diagnostics = "\n".join(debug.messages)
    assert target_secret not in diagnostics
    assert fragment_secret not in diagnostics
    assert exception_secret not in diagnostics
    stdout = capsys.readouterr().out
    assert target_secret not in stdout
    assert fragment_secret not in stdout
    assert exception_secret not in stdout
