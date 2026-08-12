from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from page_classifier import PageClass, classify_page
from platforms import tixcraft


DETAIL_URL = "https://tixcraft.com/activity/detail/26_bigbang"
AREA_URL = "https://tixcraft.com/ticket/area/26_bigbang/12345"


def _config(run_mode: str) -> dict:
    return {
        "homepage": DETAIL_URL,
        "advanced": {
            "run_mode": run_mode,
            "auto_reload_page_interval": 3.0,
            "leak_refresh_interval_seconds": 3.0,
        },
        "area_auto_select": {
            "enable": True,
            "mode": "from top to bottom",
            "area_keyword": '"VIP"',
        },
        "area_auto_fallback": False,
        "ticket_number": 2,
        "keyword_exclude": "",
        "tixcraft": {"allow_less_tickets": False},
    }


class _DetailTab:
    target = SimpleNamespace(url=DETAIL_URL)

    async def evaluate(self, _script: str) -> bool:
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("run_mode", ["onsale", "leak_watch"])
async def test_detail_page_without_purchase_entry_requests_mode_refresh(
    monkeypatch,
    run_mode: str,
) -> None:
    reloads: list[tuple[str, str]] = []

    async def reload_when_due(_tab, config, state_key, log_prefix):
        reloads.append((state_key, config["advanced"]["run_mode"]))
        assert log_prefix == "[ACTIVITY DETAIL]"
        return True

    monkeypatch.setattr(tixcraft, "_reload_page_when_due", reload_when_due)

    redirected = await tixcraft.nodriver_tixcraft_redirect(
        _DetailTab(),
        DETAIL_URL,
        _config(run_mode),
    )

    assert redirected is False
    assert reloads == [("tixcraft_detail_reload", run_mode)]


@pytest.mark.parametrize("url", [
    "https://tixcraft.com/ticket/area",
    "https://tixcraft.com/ticket/area/",
    AREA_URL,
])
def test_area_route_accepts_root_and_event_variants(url: str) -> None:
    assert classify_page(url) is PageClass.AREA


class _AreaRow:
    def __init__(self) -> None:
        self.clicked = 0

    async def click(self) -> None:
        self.clicked += 1


class _Zone:
    def __init__(self, row: _AreaRow) -> None:
        self.row = row

    async def query_selector_all(self, selector: str):
        assert selector == "a"
        return [self.row]


class _AreaTab:
    def __init__(self, row: _AreaRow) -> None:
        self.target = SimpleNamespace(url=AREA_URL)
        self.zone = _Zone(row)

    async def query_selector(self, selector: str):
        assert selector == ".zone"
        return self.zone

    async def evaluate(self, script: str):
        assert "document.querySelectorAll('.zone a')" in script
        return json.dumps([{"text": "VIP A區", "fontText": "剩餘 9"}])


@pytest.mark.asyncio
@pytest.mark.parametrize("run_mode", ["onsale", "leak_watch"])
async def test_area_keyword_and_order_dispatch_click_in_both_modes(
    monkeypatch,
    run_mode: str,
) -> None:
    row = _AreaRow()
    tab = _AreaTab(row)
    config = _config(run_mode)
    tixcraft._state.clear()

    async def not_paused(_config):
        return False

    async def interactive_ready(*_args, **_kwargs):
        return True

    monkeypatch.setattr(tixcraft, "check_and_handle_pause", not_paused)
    monkeypatch.setattr(tixcraft.runtime_health, "wait_for_interactive_ready", interactive_ready)
    monkeypatch.setattr(tixcraft.runtime_health, "runtime_log", lambda *_args, **_kwargs: None)

    selected = await tixcraft.nodriver_tixcraft_area_auto_select(tab, AREA_URL, config)

    assert selected is True
    assert row.clicked == 1
    assert tixcraft._state["selected_area_candidate"] == "VIP A區"


@pytest.mark.asyncio
@pytest.mark.parametrize("run_mode", ["onsale", "leak_watch"])
async def test_single_digit_inventory_is_compared_to_requested_count(run_mode: str) -> None:
    enough_row = object()
    insufficient_row = object()
    config = _config(run_mode)

    _, enough = await tixcraft.nodriver_get_tixcraft_target_area(
        object(),
        config,
        "VIP",
        area_list_cache=[enough_row],
        area_text_cache=[{"text": "VIP A區", "fontText": "剩餘 9"}],
    )
    needs_refresh, insufficient = await tixcraft.nodriver_get_tixcraft_target_area(
        object(),
        config,
        "VIP",
        area_list_cache=[insufficient_row],
        area_text_cache=[{"text": "VIP B區", "fontText": "剩餘 1"}],
    )

    assert enough == [enough_row]
    assert needs_refresh is True
    assert insufficient is None
