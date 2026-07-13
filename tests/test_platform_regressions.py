from __future__ import annotations

import pytest

import platforms.ibon as ibon_platform
import platforms.kham as kham_platform
import platforms.kktix as kktix_platform
import platforms.tixcraft as tixcraft_platform
from platforms.cityline import is_cityline_login_page
from platforms.ibon import _ibon_filter_enabled_purchase_buttons
from platforms.ticketplus import _ticketplus_path_segment_count
from platforms.tixcraft import nodriver_tixcraft_redirect


class _Debug:
    def __init__(self) -> None:
        self.enabled = False
        self.messages: list[str] = []

    def log(self, message: str) -> None:
        self.messages.append(message)


class _FakeReloadTarget:
    url = "https://example.com/event"


class _FakeReloadTab:
    target = _FakeReloadTarget()

    def __init__(self) -> None:
        self.reload_count = 0

    async def reload(self) -> None:
        self.reload_count += 1


class _FakeEvaluateTab:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.scripts: list[str] = []

    async def evaluate(self, script: str) -> object:
        self.scripts.append(script)
        return self.responses.pop(0)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_module", [tixcraft_platform, kktix_platform, kham_platform])
async def test_zero_auto_reload_interval_disables_throttled_reload(platform_module, monkeypatch) -> None:
    platform_module._state.clear()
    monkeypatch.setattr(platform_module.util, "create_debug_logger", lambda config: _Debug())
    tab = _FakeReloadTab()

    did_reload = await platform_module._reload_page_when_due(
        tab,
        {"advanced": {"auto_reload_page_interval": 0}},
        "test_reload",
        "[TEST]",
    )

    assert did_reload is False
    assert tab.reload_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_module", [tixcraft_platform, kktix_platform, kham_platform])
async def test_positive_auto_reload_interval_allows_throttled_reload(platform_module, monkeypatch) -> None:
    platform_module._state.clear()
    monkeypatch.setattr(platform_module.util, "create_debug_logger", lambda config: _Debug())
    tab = _FakeReloadTab()

    did_reload = await platform_module._reload_page_when_due(
        tab,
        {"advanced": {"auto_reload_page_interval": "1"}},
        "test_reload",
        "[TEST]",
    )

    assert did_reload is True
    assert tab.reload_count == 1


@pytest.mark.asyncio
async def test_ibon_ticket_quantity_script_json_escapes_ticket_number(monkeypatch) -> None:
    async def no_pause(config: dict) -> bool:
        return False

    monkeypatch.setattr(ibon_platform, "check_and_handle_pause", no_pause)
    monkeypatch.setattr(ibon_platform.util, "create_debug_logger", lambda config: _Debug())
    tab = _FakeEvaluateTab(
        [
            {"ready": True, "selector_used": "form-control-sm"},
            {
                "ticketTypes": [
                    {
                        "index": 0,
                        "name": "VIP",
                        "hasValidOption": True,
                        "isAlreadySelected": False,
                        "hasTargetOption": False,
                    }
                ],
                "tableType": "rwdtable",
                "totalRows": 1,
            },
            {"success": True, "set_value": "1", "ticket_name": "VIP"},
        ]
    )

    result = await ibon_platform.nodriver_ibon_ticket_number_auto_select(
        tab,
        {
            "ticket_number": '2";alert(1)//',
            "area_auto_select": {"area_keyword": "", "mode": 1},
            "keyword_exclude": "",
        },
    )

    assert result is True
    ticket_scan_script = tab.scripts[1]
    assert 'const targetTicketNumber = "2\\";alert(1)//";' in ticket_scan_script
    assert 'const targetTicketNumber = "2";alert(1)//";' not in ticket_scan_script


@pytest.mark.asyncio
async def test_kham_seat_script_json_escapes_ticket_number(monkeypatch) -> None:
    monkeypatch.setattr(kham_platform.util, "create_debug_logger", lambda enabled=False: _Debug())
    tab = _FakeEvaluateTab(
        [
            {
                "success": False,
                "found": 0,
                "selected": 0,
                "direction": "up",
            }
        ]
    )

    result = await kham_platform.nodriver_kham_seat_auto_select(
        tab,
        {
            "ticket_number": '2";alert(1)//',
            "advanced": {"disable_adjacent_seat": False, "verbose": False},
        },
    )

    assert result is False
    assert 'const ticketNumber = "2\\";alert(1)//";' in tab.scripts[0]
    assert 'const ticketNumber = 2";alert(1)//;' not in tab.scripts[0]
