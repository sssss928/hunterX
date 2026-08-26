from __future__ import annotations

from types import SimpleNamespace

import pytest

from platforms import cityline, famiticket, fansigo, funone, hkticketing


class _Debug:
    enabled = False

    def log(self, _message: str) -> None:
        return None


class _Tab:
    def __init__(self, url: str, evaluate_result=None) -> None:
        self.target = SimpleNamespace(url=url)
        self.evaluate_result = evaluate_result

    async def evaluate(self, _script: str):
        return self.evaluate_result

    async def sleep(self, _seconds: float) -> None:
        return None


async def _false(*_args, **_kwargs):
    return False


@pytest.fixture(autouse=True)
def _reset_platform_fallback_state():
    modules = (cityline, famiticket, fansigo, funone, hkticketing)
    for module in modules:
        module._state.clear()
    yield
    for module in modules:
        module._state.clear()


@pytest.mark.asyncio
async def test_famiticket_failed_area_scan_arms_shared_safe_refresh(monkeypatch) -> None:
    calls: list[str] = []

    async def record_reload(*_args, **kwargs):
        calls.append(kwargs.get("reason", _args[4]))
        return False

    monkeypatch.setattr(famiticket.util, "create_debug_logger", lambda _config: _Debug())
    monkeypatch.setattr(famiticket, "nodriver_fami_date_to_area", _false)
    monkeypatch.setattr(famiticket, "reload_safe_page_when_due", record_reload)
    tab = _Tab("https://www.famiticket.com.tw/Home/Activity/1", "area")
    config = {"area_auto_select": {"enable": True}}

    assert not await famiticket.nodriver_fami_home_auto_select(tab, config, "")
    assert calls == ["famiticket_area_inventory_retry"]


@pytest.mark.asyncio
async def test_cityline_failed_performance_stage_arms_shared_safe_refresh(monkeypatch) -> None:
    calls: list[str] = []

    async def record_reload(*_args, **kwargs):
        calls.append(kwargs.get("reason", _args[4]))
        return False

    monkeypatch.setattr(cityline, "check_and_handle_pause", _false)
    monkeypatch.setattr(cityline, "nodriver_cityline_date_auto_select", _false)
    monkeypatch.setattr(cityline, "reload_safe_page_when_due", record_reload)
    tab = _Tab("https://venue.cityline.com/utsvInternet/demo/performance?event=1")

    assert not await cityline.nodriver_cityline_performance(tab, {})
    assert calls == ["cityline_performance_inventory_retry"]


@pytest.mark.asyncio
async def test_fansigo_returned_show_resets_stale_quantity_and_refreshes(monkeypatch) -> None:
    calls: list[str] = []

    async def record_reload(*_args, **kwargs):
        calls.append(kwargs.get("reason", _args[4]))
        return False

    monkeypatch.setattr(fansigo.util, "create_debug_logger", lambda _config: _Debug())
    monkeypatch.setattr(fansigo, "check_and_handle_pause", _false)
    monkeypatch.setattr(fansigo, "nodriver_fansigo_area_auto_select", lambda *_args: _false())
    monkeypatch.setattr(fansigo, "reload_safe_page_when_due", record_reload)
    fansigo._ensure_fansigo_state_defaults()
    fansigo._state["is_cookie_injected"] = True
    fansigo._state["last_page_type"] = "checkout"
    fansigo._state["qty_set_url"] = "https://go.fansi.me/tickets/show/1"
    tab = _Tab("https://go.fansi.me/tickets/show/1")

    await fansigo.nodriver_fansigo_main(tab, tab.target.url, {})

    assert fansigo._state["qty_set_url"] is None
    assert calls == ["fansigo_show_inventory_retry"]


@pytest.mark.asyncio
async def test_hkticketing_type02_failed_stage_arms_shared_safe_refresh(monkeypatch) -> None:
    calls: list[str] = []

    async def record_reload(*_args, **kwargs):
        calls.append(kwargs.get("reason", _args[4]))
        return False

    monkeypatch.setattr(hkticketing.util, "create_debug_logger", lambda _config: _Debug())
    monkeypatch.setattr(hkticketing, "nodriver_hkticketing_type02_date_assign", _false)
    monkeypatch.setattr(hkticketing, "reload_safe_page_when_due", record_reload)
    tab = _Tab(
        "https://hkt.hkticketing.com/hant/#/allEvents/detail/selectTicket?activityId=1"
    )
    config = {
        "date_auto_select": {"enable": True},
        "area_auto_select": {"enable": True, "area_keyword": ""},
    }

    assert not await hkticketing.nodriver_hkticketing_type02_performance(tab, config)
    assert calls == ["hkticketing_type02_inventory_retry"]


@pytest.mark.asyncio
async def test_hkticketing_type01_failed_area_scan_arms_shared_safe_refresh(monkeypatch) -> None:
    calls: list[str] = []

    async def area_failed(*_args, **_kwargs):
        return True, False

    async def record_reload(*_args, **kwargs):
        calls.append(kwargs.get("reason", _args[4]))
        return False

    monkeypatch.setattr(hkticketing.util, "create_debug_logger", lambda _config: _Debug())
    monkeypatch.setattr(hkticketing, "nodriver_hkticketing_hide_tickets_blocks", _false)
    monkeypatch.setattr(hkticketing, "nodriver_hkticketing_area_auto_select", area_failed)
    monkeypatch.setattr(hkticketing, "reload_safe_page_when_due", record_reload)
    tab = _Tab("https://premier.hkticketing.com/events/1/performances/2/tickets")
    config = {"area_auto_select": {"area_keyword": ""}}

    assert not await hkticketing.nodriver_hkticketing_performance(
        tab, config, "premier.hkticketing.com"
    )
    assert calls == ["hkticketing_type01_inventory_retry"]


@pytest.mark.asyncio
async def test_hkticketing_type01_quantity_failure_does_not_continue_submit(monkeypatch) -> None:
    calls: list[str] = []
    next_calls = 0

    async def area_selected(*_args, **_kwargs):
        return False, True

    async def next_button(*_args, **_kwargs):
        nonlocal next_calls
        next_calls += 1
        return True

    async def record_reload(*_args, **kwargs):
        calls.append(kwargs.get("reason", _args[4]))
        return False

    monkeypatch.setattr(hkticketing.util, "create_debug_logger", lambda _config: _Debug())
    monkeypatch.setattr(hkticketing, "nodriver_hkticketing_hide_tickets_blocks", _false)
    monkeypatch.setattr(hkticketing, "nodriver_hkticketing_area_auto_select", area_selected)
    monkeypatch.setattr(hkticketing, "nodriver_hkticketing_ticket_number_auto_select", _false)
    monkeypatch.setattr(hkticketing, "nodriver_hkticketing_next_button_press", next_button)
    monkeypatch.setattr(hkticketing, "reload_safe_page_when_due", record_reload)
    tab = _Tab("https://premier.hkticketing.com/events/1/performances/2/tickets")
    config = {"area_auto_select": {"area_keyword": ""}}

    assert await hkticketing.nodriver_hkticketing_performance(
        tab, config, "premier.hkticketing.com"
    )
    assert next_calls == 0
    assert calls == ["hkticketing_type01_inventory_retry"]


@pytest.mark.asyncio
async def test_funone_real_area_failure_does_not_fall_through_to_quantity(monkeypatch) -> None:
    calls: list[str] = []
    quantity_calls = 0

    async def area_failed(*_args, **_kwargs):
        funone._state["area_refresh_required"] = True
        return False

    async def quantity(*_args, **_kwargs):
        nonlocal quantity_calls
        quantity_calls += 1
        return True

    async def record_reload(*_args, **kwargs):
        calls.append(kwargs.get("reason", _args[4]))
        return False

    async def true_value(*_args, **_kwargs):
        return True

    async def step_one(*_args, **_kwargs):
        return 1

    monkeypatch.setattr(funone.util, "create_debug_logger", lambda _config: _Debug())
    monkeypatch.setattr(funone, "check_and_handle_pause", _false)
    monkeypatch.setattr(funone, "nodriver_funone_close_popup", _false)
    monkeypatch.setattr(funone, "nodriver_funone_verify_login", true_value)
    monkeypatch.setattr(funone, "nodriver_funone_detect_step", step_one)
    monkeypatch.setattr(funone, "nodriver_funone_area_auto_select", area_failed)
    monkeypatch.setattr(funone, "nodriver_funone_assign_ticket_number", quantity)
    monkeypatch.setattr(funone, "reload_safe_page_when_due", record_reload)
    tab = _Tab("https://tickets.funone.io/purchase_choose_ticket/1")

    await funone.nodriver_funone_main(tab, tab.target.url, {})

    assert quantity_calls == 0
    assert calls == ["funone_area_inventory_retry"]


@pytest.mark.asyncio
async def test_funone_area_click_keeps_original_dom_index_after_filtering(monkeypatch) -> None:
    scripts: list[str] = []
    results = iter(
        (
            [
                {
                    "text": "Sold out A",
                    "fullText": "Sold out A",
                    "index": 0,
                    "disabled": True,
                    "type": "zone_box",
                },
                {
                    "text": "Target B",
                    "fullText": "Target B",
                    "index": 1,
                    "disabled": False,
                    "type": "zone_box",
                },
            ],
            True,
        )
    )

    class _SequenceTab(_Tab):
        async def evaluate(self, script: str):
            scripts.append(script)
            return next(results)

    monkeypatch.setattr(funone.util, "create_debug_logger", lambda _config: _Debug())
    tab = _SequenceTab("https://tickets.funone.io/purchase_choose_ticket/1")
    config = {
        "area_auto_select": {"area_keyword": "Target B", "mode": "from top to bottom"},
        "keyword_exclude": "",
        "area_auto_fallback": False,
    }

    assert await funone.nodriver_funone_area_auto_select(tab, tab.target.url, config)
    assert "zoneBoxes[1]" in scripts[1]
    assert funone._state["area_refresh_required"] is False


@pytest.mark.asyncio
async def test_funone_no_zone_boxes_remains_quantity_page_signal(monkeypatch) -> None:
    monkeypatch.setattr(funone.util, "create_debug_logger", lambda _config: _Debug())
    tab = _Tab("https://tickets.funone.io/purchase_choose_ticket/1", [])

    assert not await funone.nodriver_funone_area_auto_select(tab, tab.target.url, {})
    assert funone._state["area_refresh_required"] is False
