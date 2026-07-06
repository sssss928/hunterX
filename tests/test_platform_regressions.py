from __future__ import annotations

import pytest

from platforms.cityline import is_cityline_login_page
from platforms.ibon import _ibon_filter_enabled_purchase_buttons
from platforms.ticketplus import _ticketplus_path_segment_count
from platforms.tixcraft import nodriver_tixcraft_redirect


class _Debug:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def log(self, message: str) -> None:
        self.messages.append(message)


def test_ticketplus_path_segment_count_normalizes_trailing_slash_query_and_fragment() -> None:
    assert _ticketplus_path_segment_count("https://ticketplus.com.tw/activity/abc") == 5
    assert _ticketplus_path_segment_count("https://ticketplus.com.tw/activity/abc/") == 5
    assert _ticketplus_path_segment_count("https://ticketplus.com.tw/order/abc/def/?x=1#top") == 6
    assert _ticketplus_path_segment_count("https://ticketplus.com.tw/confirmseat/abc/def/#seat") == 6


def test_cityline_login_page_detector_covers_hong_kong_realm() -> None:
    assert is_cityline_login_page("https://www.cityline.com/Login.html?targetUrl=x")
    assert is_cityline_login_page("https://www.cityline.com.hk/en_US/Login.html?targetUrl=x")
    assert not is_cityline_login_page("https://venue.cityline.com/utsvInternet/eventDetail?event=abc")


def test_ibon_date_filter_applies_keyword_exclude() -> None:
    config = {"keyword_exclude": '"單日票"'}
    buttons = [
        {"disabled": False, "date_context": "2026/07/06 單日票"},
        {"disabled": False, "date_context": "2026/07/06 VIP套票"},
        {"disabled": True, "date_context": "2026/07/06 已售完"},
    ]
    debug = _Debug()

    result = _ibon_filter_enabled_purchase_buttons(buttons, config, debug)

    assert result == [{"disabled": False, "date_context": "2026/07/06 VIP套票"}]
    assert any("Excluded by keyword_exclude" in msg for msg in debug.messages)


class _FakeTixcraftTab:
    def __init__(self) -> None:
        self.got_url = ""
        self.waited_for = ""

    async def evaluate(self, javascript: str) -> bool:
        assert "26_fireball" in javascript
        assert "#intro" not in javascript
        return True

    async def get(self, url: str) -> None:
        self.got_url = url

    async def wait_for(self, selector: str, timeout: int) -> None:
        self.waited_for = selector


@pytest.mark.asyncio
async def test_tixcraft_redirect_strips_detail_fragment() -> None:
    tab = _FakeTixcraftTab()

    redirected = await nodriver_tixcraft_redirect(
        tab,
        "https://tixcraft.com/activity/detail/26_fireball#intro",
    )

    assert redirected is True
    assert tab.got_url == "https://tixcraft.com/activity/game/26_fireball"
    assert tab.waited_for == "#gameList > table > tbody > tr"
