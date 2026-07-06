from __future__ import annotations

from typing import Any

import pytest

from platforms.facebook import nodriver_facebook_login, nodriver_facebook_main


class FakeElement:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_keys(self, value: str) -> None:
        self.sent.append(value)


class FakeTab:
    def __init__(self, elements: dict[str, FakeElement | None]) -> None:
        self.elements = elements
        self.sent_events: list[Any] = []

    async def query_selector(self, selector: str) -> FakeElement | None:
        return self.elements.get(selector)

    async def send(self, event: Any) -> None:
        self.sent_events.append(event)


@pytest.mark.asyncio
async def test_facebook_login_success_with_fake_tab() -> None:
    account = FakeElement()
    password = FakeElement()
    tab = FakeTab({"#email": account, "#pass": password})

    result = await nodriver_facebook_login(tab, "user@example.com", "secret")

    assert result is True
    assert account.sent == ["user@example.com"]
    assert password.sent == ["secret"]
    assert len(tab.sent_events) == 2


@pytest.mark.asyncio
async def test_facebook_login_missing_password_returns_false() -> None:
    tab = FakeTab({"#email": FakeElement(), "#pass": None})

    assert await nodriver_facebook_login(tab, "user@example.com", "secret") is False


@pytest.mark.asyncio
async def test_facebook_main_skips_short_account() -> None:
    tab = FakeTab({"#email": FakeElement(), "#pass": FakeElement()})
    config = {"accounts": {"facebook_account": "abc", "facebook_password": "secret"}}

    assert await nodriver_facebook_main(tab, config) is False
    assert tab.sent_events == []
