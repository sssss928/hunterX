from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from leak_watch import LeakWatchScheduler
from page_classifier import PageClass, classify_page
from platforms import tixcraft


AREA_URL = "https://tixcraft.com/ticket/area/26_plave/5300"
ORDER_URL = "https://tixcraft.com/ticket/order"
DATE_URL = "https://tixcraft.com/activity/game/26_plave"
AREA_NAME = "2F Y1B-1區5300"


def _config(interval: float = 3.0) -> dict:
    return {
        "homepage": "https://tixcraft.com/activity/detail/26_plave",
        "ticket_number": 1,
        "area_auto_select": {
            "enable": True,
            "area_keyword": "",
            "mode": "from top to bottom",
        },
        "date_auto_select": {
            "enable": True,
            "date_keyword": "",
            "mode": "from top to bottom",
        },
        "area_auto_fallback": False,
        "date_auto_fallback": False,
        "keyword_exclude": "",
        "tixcraft": {
            "allow_less_tickets": False,
            "pass_date_is_sold_out": True,
            "auto_reload_coming_soon_page": True,
        },
        "advanced": {
            "run_mode": "leak_watch",
            "auto_reload_page_interval": interval,
            "leak_refresh_interval_seconds": interval,
            "headless": False,
            "play_sound": {"ticket": False, "order": False},
        },
    }


def _seed_state() -> LeakWatchScheduler:
    scheduler = LeakWatchScheduler()
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "notification_session_id": "navigation-test",
            "notification_flow_url": AREA_URL,
            "notification_flow_generation": 1,
            "current_event_id": "26_plave",
            "current_game_id": "5300",
            "event_name": "Offline Event",
            "event_name_quality": 100,
            "event_metadata_cache": {},
            "attempt_sequence": 0,
            "purchase_attempt": None,
            "attempt_last_page_class": "area",
            "last_selected_area": "",
            "selected_area_candidate": "",
            "selected_area_metadata": {},
            "last_valid_area_url": AREA_URL,
            "recent_area_route_url": AREA_URL,
            "last_ticket_count": "1",
            "manual_intervention_required": False,
            "notified_order_pending": False,
            "notified_checkout_reached": False,
            "is_popup_checkout": False,
            "played_sound_ticket": False,
            "played_sound_order": False,
            "printed_completed": False,
            "notification_retry_at": {},
            "notification_submit_started_at": 0.0,
            "notification_order_probe_next_at": 0.0,
            "soft_block_recovery_scan_pending": False,
            "soft_block_recovery_landing_url": "",
            "soft_block_recovery_in_progress": False,
            "alert_handler_registered": True,
            "queue_it_enter_time": None,
            "fail_list": [],
            "fail_promo_list": [],
            "area_retry_count": 0,
            "ticketmaster_phase": "area_select",
            "ticketmaster_captcha_processed_url": "",
            "start_time": None,
            "done_time": None,
            "elapsed_time": None,
            "leak_scheduler": scheduler,
        }
    )
    return scheduler


class _AreaLink:
    def __init__(self) -> None:
        self.clicks = 0

    async def click(self) -> None:
        self.clicks += 1


class _Zone:
    pass


class _AreaTab:
    def __init__(self) -> None:
        self.target = SimpleNamespace(url=AREA_URL)
        self.link = _AreaLink()
        self.zone = _Zone()


async def _no_pause(_config=None) -> bool:
    return False


async def _ready(*_args, **_kwargs) -> bool:
    return True


async def _prepare_area_click(
    monkeypatch: pytest.MonkeyPatch,
    tab: _AreaTab,
    clock: dict[str, float],
) -> None:
    async def query_one(*_args, **_kwargs):
        return tab.zone

    async def query_all(*_args, **_kwargs):
        return [tab.link]

    async def area_cache(*_args, **_kwargs):
        return json.dumps([{"text": AREA_NAME, "fontText": ""}], ensure_ascii=False)

    async def matched(*_args, **_kwargs):
        return False, [tab.link]

    async def selected_name(*_args, **_kwargs):
        return AREA_NAME

    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(tixcraft, "check_and_handle_pause", _no_pause)
    monkeypatch.setattr(tixcraft.runtime_health, "wait_for_interactive_ready", _ready)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "query_selector_with_timeout",
        query_one,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "query_selector_all_with_timeout",
        query_all,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "evaluate_with_timeout",
        area_cache,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(tixcraft, "nodriver_get_tixcraft_target_area", matched)
    monkeypatch.setattr(tixcraft, "_read_selected_area_name", selected_name)

    await tixcraft.nodriver_tixcraft_area_auto_select(tab, AREA_URL, _config())


@pytest.mark.asyncio
async def test_area_click_is_provisional_until_route_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _seed_state()
    tab = _AreaTab()
    clock = {"now": 100.0}

    await _prepare_area_click(monkeypatch, tab, clock)

    assert tab.link.clicks == 1
    assert scheduler.area_click_pending is True
    assert tixcraft._state["purchase_attempt"] is None
    assert tixcraft._state["last_selected_area"] == ""
    assert tixcraft._state["selected_area_metadata"].get("confirmed") is not True
    pending = tixcraft._state["pending_area_navigation"]
    assert pending.seat_area == AREA_NAME
    assert pending.source_url == AREA_URL
    assert pending.started_at == 100.0


@pytest.mark.asyncio
async def test_main_dispatch_confirms_area_only_on_protected_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _seed_state()
    tab = _AreaTab()
    clock = {"now": 100.0}
    await _prepare_area_click(monkeypatch, tab, clock)
    assert tixcraft._state["purchase_attempt"] is None

    tab.target.url = ORDER_URL

    async def no_op(*_args, **_kwargs):
        return None

    async def not_blocked(*_args, **_kwargs) -> bool:
        return False

    async def classify_passthrough(_tab, _url, initial):
        return initial

    async def notification_enqueued(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(tixcraft, "nodriver_tixcraft_home_close_window", no_op)
    monkeypatch.setattr(
        tixcraft,
        "nodriver_ticketmaster_check_ip_block",
        not_blocked,
    )
    monkeypatch.setattr(tixcraft, "_classify_recovery_page", classify_passthrough)
    monkeypatch.setattr(
        tixcraft,
        "_emit_tixcraft_attempt_notification",
        notification_enqueued,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )

    tab_state = tixcraft._state_for_tab(tab)
    tab_state.clear()
    tab_state.update(tixcraft._default_state)
    with tixcraft._bind_tixcraft_tab_state(tab):
        await tixcraft.nodriver_tixcraft_main(
            tab,
            ORDER_URL,
            _config(),
            None,
            None,
        )

        attempt = tixcraft._state["purchase_attempt"]
        assert attempt is not None
        assert attempt.seat_area == AREA_NAME
        assert tixcraft._state["last_selected_area"] == AREA_NAME
        assert tixcraft._state["selected_area_metadata"]["confirmed"] is True
        assert scheduler.area_click_pending is False
        assert "pending_area_navigation" not in tixcraft._state


@pytest.mark.asyncio
async def test_verify_route_is_protected_and_confirms_area_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _seed_state()
    tab = _AreaTab()
    clock = {"now": 100.0}
    await _prepare_area_click(monkeypatch, tab, clock)
    verify_url = "https://tixcraft.com/ticket/verify/26_plave/5300"

    assert classify_page(verify_url) == PageClass.TICKET
    assert tixcraft._reconcile_tixcraft_pending_navigation(
        tab,
        verify_url,
        classify_page(verify_url),
        _config(),
    )
    assert tixcraft._state["purchase_attempt"].seat_area == AREA_NAME
    assert scheduler.area_click_pending is False


@pytest.mark.asyncio
async def test_unconfirmed_area_click_is_not_repeated_at_50ms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    tab = _AreaTab()
    clock = {"now": 100.0}
    await _prepare_area_click(monkeypatch, tab, clock)

    first_started_at = tixcraft._state["pending_area_navigation"].started_at
    clock["now"] = 100.05
    await tixcraft.nodriver_tixcraft_area_auto_select(tab, AREA_URL, _config())

    assert tab.link.clicks == 1
    assert tixcraft._state["pending_area_navigation"].started_at == first_started_at


@pytest.mark.asyncio
async def test_unconfirmed_area_click_timeout_recovers_once_then_resumes_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _seed_state()
    tab = _AreaTab()
    clock = {"now": 100.0}
    reloads: list[float] = []

    async def record_reload(*_args, **_kwargs) -> bool:
        reloads.append(clock["now"])
        return True

    monkeypatch.setattr(tixcraft, "guarded_reload", record_reload)
    await _prepare_area_click(monkeypatch, tab, clock)

    first_pending = tixcraft._state["pending_area_navigation"]
    assert tab.link.clicks == 1
    assert first_pending.started_at == 100.0
    assert tixcraft._state["purchase_attempt"] is None

    clock["now"] = first_pending.deadline
    assert not tixcraft._reconcile_tixcraft_pending_navigation(
        tab,
        AREA_URL,
        PageClass.AREA,
        _config(),
    )
    assert "pending_area_navigation" not in tixcraft._state
    assert scheduler.area_click_pending is False

    assert not await tixcraft.nodriver_tixcraft_area_auto_select(
        tab,
        AREA_URL,
        _config(),
    )
    assert reloads == [first_pending.deadline]
    assert tab.link.clicks == 1
    assert tixcraft._state["purchase_attempt"] is None
    assert tixcraft._state["last_selected_area"] == ""
    assert (
        tixcraft._state["selected_area_metadata"].get("confirmed") is False
    )

    clock["now"] = first_pending.deadline + 0.05
    assert await tixcraft.nodriver_tixcraft_area_auto_select(
        tab,
        AREA_URL,
        _config(),
    )
    second_pending = tixcraft._state["pending_area_navigation"]
    assert tab.link.clicks == 2
    assert reloads == [first_pending.deadline]
    assert second_pending.token > first_pending.token
    assert second_pending.started_at == first_pending.deadline + 0.05


class _DateAction:
    def __init__(self, *, data_href: str = "", as_link: bool = False) -> None:
        self.attrs = {"data-href": data_href} if data_href else {}
        self.data_href = data_href
        self.as_link = as_link
        self.clicks = 0

    async def update(self) -> None:
        return None

    async def click(self) -> None:
        self.clicks += 1


class _NeverDateAction(_DateAction):
    async def click(self) -> None:
        self.clicks += 1
        await asyncio.Event().wait()


class _DelayedDateAction(_DateAction):
    async def click(self) -> None:
        self.clicks += 1
        await asyncio.sleep(0.15)


class _TransitioningDateAction(_DateAction):
    def __init__(self, tab) -> None:
        super().__init__(as_link=True)
        self.tab = tab

    async def click(self) -> None:
        self.clicks += 1
        self.tab.target.url = AREA_URL
        raise RuntimeError("Execution context was destroyed")


class _DateRow:
    def __init__(self, action: _DateAction) -> None:
        self.action = action
        self.html = "<td>Find tickets</td>"

    async def get_html(self) -> str:
        return self.html

    async def query_selector(self, selector: str):
        if selector == "button[data-href]" and self.action.data_href:
            return self.action
        if selector == "a[href]" and self.action.as_link:
            return self.action
        return None


class _DateTab:
    def __init__(self, row: _DateRow) -> None:
        self.row = row
        self.target = SimpleNamespace(url=DATE_URL)

    async def wait_for(self, _selector: str, timeout: int) -> None:
        return None

    async def query_selector_all(self, _selector: str):
        return [self.row]

    async def evaluate(self, script: str):
        if "document.documentElement.lang" in script:
            return "en-US"
        if "querySelectorAll('#gameList > table > tbody > tr')" in script:
            return json.dumps([self.row.html])
        return ""


@pytest.mark.asyncio
async def test_date_guarded_get_false_is_not_navigation_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    action = _DateAction(
        data_href="https://tixcraft.com/ticket/area/26_plave/5300"
    )
    tab = _DateTab(_DateRow(action))
    reloads: list[str] = []

    async def rejected_get(*_args, **_kwargs) -> bool:
        return False

    async def record_reload(_tab, _config, state_key, _prefix) -> bool:
        reloads.append(state_key)
        return True

    monkeypatch.setattr(tixcraft.runtime_health, "guarded_get", rejected_get)
    monkeypatch.setattr(tixcraft, "_reload_page_when_due", record_reload)

    selected = await tixcraft.nodriver_tixcraft_date_auto_select(
        tab,
        DATE_URL,
        _config(),
        "tixcraft.com",
    )

    assert selected is False
    assert action.clicks == 0
    assert reloads == ["tixcraft_date_reload"]


@pytest.mark.asyncio
async def test_date_guarded_get_true_requires_and_accepts_area_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    target_url = "https://tixcraft.com/ticket/area/26_plave/5300?token=secret"
    action = _DateAction(data_href=target_url)
    tab = _DateTab(_DateRow(action))
    reloads: list[str] = []

    async def accepted_get(*_args, **_kwargs) -> bool:
        tab.target.url = target_url
        return True

    async def record_reload(_tab, _config, state_key, _prefix) -> bool:
        reloads.append(state_key)
        return True

    monkeypatch.setattr(tixcraft.runtime_health, "guarded_get", accepted_get)
    monkeypatch.setattr(tixcraft, "_reload_page_when_due", record_reload)

    selected = await tixcraft.nodriver_tixcraft_date_auto_select(
        tab,
        DATE_URL,
        _config(),
        "tixcraft.com",
    )

    assert selected is True
    assert "pending_date_navigation" not in tixcraft._state
    assert reloads == []


@pytest.mark.asyncio
async def test_date_navigation_race_never_reloads_fresh_area_landing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    target_url = "https://tixcraft.com/ticket/area/26_plave/5300"
    action = _DateAction(data_href=target_url)
    tab = _DateTab(_DateRow(action))
    reloads: list[str] = []

    async def timed_out_after_navigation(*_args, **_kwargs) -> bool:
        tab.target.url = target_url
        return False

    async def record_reload(_tab, _config, state_key, _prefix) -> bool:
        reloads.append(state_key)
        return True

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "guarded_get",
        timed_out_after_navigation,
    )
    monkeypatch.setattr(tixcraft, "_reload_page_when_due", record_reload)

    selected = await tixcraft.nodriver_tixcraft_date_auto_select(
        tab,
        DATE_URL,
        _config(),
        "tixcraft.com",
    )

    assert selected is False
    assert tixcraft._state["pending_date_navigation"].target_url == target_url
    assert reloads == []


@pytest.mark.asyncio
async def test_date_without_candidates_reloads_without_unbound_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    tab = _DateTab(_DateRow(_DateAction()))
    reloads: list[str] = []

    async def no_rows(_selector: str):
        return []

    async def record_reload(_tab, _config, state_key, _prefix) -> bool:
        reloads.append(state_key)
        return True

    tab.query_selector_all = no_rows
    monkeypatch.setattr(tixcraft, "_reload_page_when_due", record_reload)

    assert not await tixcraft.nodriver_tixcraft_date_auto_select(
        tab,
        DATE_URL,
        _config(),
        "tixcraft.com",
    )
    assert reloads == ["tixcraft_date_reload"]


@pytest.mark.asyncio
async def test_date_link_click_timeout_is_bounded_then_reloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    action = _NeverDateAction(as_link=True)
    tab = _DateTab(_DateRow(action))
    reloads: list[str] = []

    async def record_reload(_tab, _config, state_key, _prefix) -> bool:
        reloads.append(state_key)
        return True

    monkeypatch.setattr(tixcraft, "_reload_page_when_due", record_reload)
    monkeypatch.setattr(
        tixcraft,
        "_get_tixcraft_navigation_confirmation_seconds",
        lambda _config: 10.0,
    )
    monkeypatch.setattr(
        tixcraft,
        "_TIXCRAFT_CLICK_DISPATCH_TIMEOUT_SECONDS",
        0.05,
    )

    selected = await asyncio.wait_for(
        tixcraft.nodriver_tixcraft_date_auto_select(
            tab,
            DATE_URL,
            _config(),
            "tixcraft.com",
        ),
        timeout=0.75,
    )

    assert selected is False
    assert action.clicks == 1
    assert "pending_date_navigation" not in tixcraft._state
    assert reloads == ["tixcraft_date_reload"]


@pytest.mark.asyncio
async def test_onsale_date_click_keeps_direct_await_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    action = _DelayedDateAction(as_link=True)
    tab = _DateTab(_DateRow(action))
    reloads: list[str] = []
    config = _config()
    config["advanced"]["run_mode"] = "onsale"

    async def record_reload(_tab, _config, state_key, _prefix) -> bool:
        reloads.append(state_key)
        return True

    monkeypatch.setattr(tixcraft, "_reload_page_when_due", record_reload)
    monkeypatch.setattr(
        tixcraft,
        "_TIXCRAFT_CLICK_DISPATCH_TIMEOUT_SECONDS",
        0.01,
    )

    selected = await asyncio.wait_for(
        tixcraft.nodriver_tixcraft_date_auto_select(
            tab,
            DATE_URL,
            config,
            "tixcraft.com",
        ),
        timeout=0.75,
    )

    assert selected is True
    assert action.clicks == 1
    assert reloads == []
    assert isinstance(
        tixcraft._state.get("pending_date_navigation"),
        tixcraft.TixCraftPendingNavigation,
    )


@pytest.mark.asyncio
async def test_date_link_context_destroyed_after_area_transition_never_reloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    placeholder = _DateAction(as_link=True)
    tab = _DateTab(_DateRow(placeholder))
    action = _TransitioningDateAction(tab)
    tab.row.action = action
    reloads: list[str] = []

    async def record_reload(_tab, _config, state_key, _prefix) -> bool:
        reloads.append(state_key)
        return True

    monkeypatch.setattr(tixcraft, "_reload_page_when_due", record_reload)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )

    assert not await tixcraft.nodriver_tixcraft_date_auto_select(
        tab,
        DATE_URL,
        _config(),
        "tixcraft.com",
    )
    assert action.clicks == 1
    assert tixcraft._state["pending_date_navigation"].target_url == AREA_URL
    assert reloads == []


@pytest.mark.asyncio
async def test_date_link_click_waits_for_navigation_without_reclicking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    clock = {"now": 200.0}
    action = _DateAction(as_link=True)
    tab = _DateTab(_DateRow(action))
    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])

    first = await tixcraft.nodriver_tixcraft_date_auto_select(
        tab,
        DATE_URL,
        _config(),
        "tixcraft.com",
    )
    clock["now"] = 200.05
    second = await tixcraft.nodriver_tixcraft_date_auto_select(
        tab,
        DATE_URL,
        _config(),
        "tixcraft.com",
    )

    assert first is True
    assert second is True
    assert action.clicks == 1
    assert tixcraft._state["pending_date_navigation"].started_at == 200.0


@pytest.mark.asyncio
async def test_expired_date_click_reloads_before_another_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    clock = {"now": 300.0}
    action = _DateAction(as_link=True)
    tab = _DateTab(_DateRow(action))
    reloads: list[str] = []
    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])

    assert await tixcraft.nodriver_tixcraft_date_auto_select(
        tab,
        DATE_URL,
        _config(),
        "tixcraft.com",
    )
    clock["now"] = 303.0
    tixcraft._reconcile_tixcraft_pending_navigation(
        tab,
        DATE_URL,
        PageClass.DATE,
        _config(),
    )

    async def record_reload(_tab, _config, state_key, _prefix) -> bool:
        reloads.append(state_key)
        return True

    monkeypatch.setattr(tixcraft, "_reload_page_when_due", record_reload)
    assert not await tixcraft.nodriver_tixcraft_date_auto_select(
        tab,
        DATE_URL,
        _config(),
        "tixcraft.com",
    )
    assert action.clicks == 1
    assert reloads == ["tixcraft_date_reload"]
