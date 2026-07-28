from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from leak_watch import LeakWatchScheduler
from platforms import tixcraft


AREA_URL = "https://tixcraft.com/ticket/area/26_plave/5300"
TICKET_URL = "https://tixcraft.com/ticket/ticket/26_plave/5300"
AREA_NAME = "2F Y1B-1區5300"


def _config(interval: float = 3.0) -> dict:
    return {
        "homepage": "https://tixcraft.com/activity/detail/26_plave",
        "ticket_number": 1,
        "date_auto_select": {
            "enable": True,
            "date_keyword": "",
            "mode": "from top to bottom",
        },
        "date_auto_fallback": False,
        "area_auto_select": {
            "enable": True,
            "area_keyword": "",
            "mode": "from top to bottom",
        },
        "area_auto_fallback": False,
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
        },
    }


def _seed_state() -> LeakWatchScheduler:
    scheduler = LeakWatchScheduler()
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "notification_flow_url": AREA_URL,
            "notification_flow_generation": 1,
            "current_event_id": "26_plave",
            "current_game_id": "5300",
            "last_valid_area_url": AREA_URL,
            "recent_area_route_url": AREA_URL,
            "last_selected_area": "",
            "selected_area_candidate": "",
            "selected_area_metadata": {},
            "manual_intervention_required": False,
            "purchase_attempt": None,
            "attempt_sequence": 0,
            "notification_session_id": "bounded-operation-test",
            "leak_scheduler": scheduler,
        }
    )
    return scheduler


class _AreaTab:
    def __init__(self) -> None:
        self.target = SimpleNamespace(url=AREA_URL)


class _NeverZone:
    async def query_selector_all(self, _selector: str):
        await asyncio.Event().wait()


class _NeverRow:
    async def get_html(self):
        await asyncio.Event().wait()


class _TransitioningLink:
    def __init__(self, tab: _AreaTab) -> None:
        self.tab = tab
        self.native_clicks = 0
        self.javascript_clicks = 0

    async def click(self) -> None:
        self.native_clicks += 1
        self.tab.target.url = TICKET_URL
        raise RuntimeError("Execution context was destroyed")

    async def evaluate(self, _script: str) -> None:
        self.javascript_clicks += 1
        raise AssertionError("JavaScript fallback must not run after navigation")


class _NeverClickLink:
    def __init__(self) -> None:
        self.native_clicks = 0
        self.javascript_clicks = 0

    async def click(self) -> None:
        self.native_clicks += 1
        await asyncio.Event().wait()

    async def evaluate(self, _script: str) -> None:
        self.javascript_clicks += 1
        await asyncio.Event().wait()


class _DelayedClickLink:
    def __init__(self) -> None:
        self.native_clicks = 0
        self.javascript_clicks = 0

    async def click(self) -> None:
        self.native_clicks += 1
        await asyncio.sleep(0.15)

    async def evaluate(self, _script: str) -> None:
        self.javascript_clicks += 1
        raise AssertionError("On-sale delayed native click must not be repeated")


class _NeverMetadataLink:
    def __init__(self) -> None:
        self.native_clicks = 0

    async def text(self):
        await asyncio.Event().wait()

    async def click(self) -> None:
        self.native_clicks += 1


class _FallbackRow:
    async def get_html(self) -> str:
        return "<a>B Area 5300</a>"


class _FallbackZone:
    def __init__(self, row) -> None:
        self.row = row

    async def query_selector_all(self, _selector: str):
        return [self.row]


class _NeverFont:
    async def evaluate(self, _script: str):
        await asyncio.Event().wait()


class _AvailabilityRow(_FallbackRow):
    def __init__(self, hanging_stage: str) -> None:
        self.hanging_stage = hanging_stage

    async def query_selector(self, _selector: str):
        if self.hanging_stage == "query":
            await asyncio.Event().wait()
        return _NeverFont()


class _DateTab:
    def __init__(self) -> None:
        self.target = SimpleNamespace(
            url="https://tixcraft.com/activity/game/26_plave"
        )

    async def wait_for(self, *_args, **_kwargs):
        return None

    async def query_selector_all(self, _selector: str):
        return []

    async def evaluate(self, script: str):
        if script == "document.documentElement.lang":
            await asyncio.Event().wait()
        raise AssertionError(f"Unexpected evaluate call: {script}")


async def _no_pause(_config=None) -> bool:
    return False


async def _ready(*_args, **_kwargs) -> bool:
    return True


def _patch_area_shell(
    monkeypatch: pytest.MonkeyPatch,
    tab: _AreaTab,
    zone,
    reloads: list[str],
) -> None:
    async def query_zone(*_args, **_kwargs):
        return zone

    async def record_reload(_tab, _config, state_key, _prefix) -> bool:
        reloads.append(state_key)
        return True

    monkeypatch.setattr(tixcraft, "check_and_handle_pause", _no_pause)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_interactive_ready",
        _ready,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "query_selector_with_timeout",
        query_zone,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(tixcraft, "_reload_page_when_due", record_reload)
    monkeypatch.setattr(
        tixcraft,
        "_TIXCRAFT_EVALUATE_TIMEOUT_SECONDS",
        0.01,
    )
    tab.target.url = AREA_URL


@pytest.mark.asyncio
async def test_leak_area_fallback_query_is_bounded_and_finalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _seed_state()
    tab = _AreaTab()
    zone = _NeverZone()
    reloads: list[str] = []
    _patch_area_shell(monkeypatch, tab, zone, reloads)

    async def missing_batch_links(*_args, **_kwargs):
        return None

    async def empty_text_cache(*_args, **_kwargs):
        return "[]"

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "query_selector_all_with_timeout",
        missing_batch_links,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "evaluate_with_timeout",
        empty_text_cache,
    )

    selected = await asyncio.wait_for(
        tixcraft.nodriver_tixcraft_area_auto_select(tab, AREA_URL, _config()),
        timeout=0.75,
    )

    assert selected is False
    assert reloads == ["tixcraft_area_reload"]
    assert scheduler.dom_scan_pending is False


@pytest.mark.asyncio
async def test_leak_area_row_html_fallback_is_bounded_and_finalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _seed_state()
    tab = _AreaTab()
    row = _NeverRow()
    zone = SimpleNamespace()
    reloads: list[str] = []
    _patch_area_shell(monkeypatch, tab, zone, reloads)

    async def one_link(*_args, **_kwargs):
        return [row]

    async def invalid_text_cache(*_args, **_kwargs):
        return "null"

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "query_selector_all_with_timeout",
        one_link,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "evaluate_with_timeout",
        invalid_text_cache,
    )

    selected = await asyncio.wait_for(
        tixcraft.nodriver_tixcraft_area_auto_select(tab, AREA_URL, _config()),
        timeout=0.75,
    )

    assert selected is False
    assert reloads == ["tixcraft_area_reload"]
    assert scheduler.dom_scan_pending is False


@pytest.mark.asyncio
async def test_post_zone_exception_reaches_finalizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _seed_state()
    tab = _AreaTab()
    zone = SimpleNamespace()
    reloads: list[str] = []
    _patch_area_shell(monkeypatch, tab, zone, reloads)

    async def empty_links(*_args, **_kwargs):
        return []

    async def empty_cache(*_args, **_kwargs):
        return "[]"

    async def selection_failure(*_args, **_kwargs):
        raise RuntimeError("offline selection fixture")

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "query_selector_all_with_timeout",
        empty_links,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "evaluate_with_timeout",
        empty_cache,
    )
    monkeypatch.setattr(
        tixcraft,
        "nodriver_get_tixcraft_target_area",
        selection_failure,
    )

    selected = await tixcraft.nodriver_tixcraft_area_auto_select(
        tab,
        AREA_URL,
        _config(),
    )

    assert selected is False
    assert reloads == ["tixcraft_area_reload"]
    assert scheduler.dom_scan_pending is False


@pytest.mark.asyncio
async def test_cancelled_dom_scan_is_re_raised_and_guard_is_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _seed_state()
    tab = _AreaTab()
    reloads: list[str] = []
    _patch_area_shell(monkeypatch, tab, SimpleNamespace(), reloads)

    async def cancelled_query(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "query_selector_with_timeout",
        cancelled_query,
    )

    with pytest.raises(asyncio.CancelledError):
        await tixcraft.nodriver_tixcraft_area_auto_select(
            tab,
            AREA_URL,
            _config(),
        )

    assert reloads == []
    assert scheduler.dom_scan_pending is False


@pytest.mark.asyncio
async def test_context_destroyed_after_protected_transition_is_dispatched_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _seed_state()
    tab = _AreaTab()
    link = _TransitioningLink(tab)
    zone = SimpleNamespace()
    reloads: list[str] = []
    _patch_area_shell(monkeypatch, tab, zone, reloads)

    async def one_link(*_args, **_kwargs):
        return [link]

    async def area_cache(*_args, **_kwargs):
        return json.dumps([{"text": AREA_NAME, "fontText": ""}], ensure_ascii=False)

    async def matched(*_args, **_kwargs):
        return False, [link]

    async def selected_name(*_args, **_kwargs):
        return AREA_NAME

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "query_selector_all_with_timeout",
        one_link,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "evaluate_with_timeout",
        area_cache,
    )
    monkeypatch.setattr(tixcraft, "nodriver_get_tixcraft_target_area", matched)
    monkeypatch.setattr(tixcraft, "_read_selected_area_name", selected_name)

    selected = await tixcraft.nodriver_tixcraft_area_auto_select(
        tab,
        AREA_URL,
        _config(),
    )

    assert selected is True
    assert link.native_clicks == 1
    assert link.javascript_clicks == 0
    assert reloads == []
    assert scheduler.area_click_pending is True
    assert tixcraft._state["pending_area_navigation"].seat_area == AREA_NAME
    assert tixcraft._state["purchase_attempt"] is None


@pytest.mark.asyncio
async def test_native_and_javascript_clicks_are_bounded_before_finalizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _seed_state()
    tab = _AreaTab()
    link = _NeverClickLink()
    zone = SimpleNamespace()
    reloads: list[str] = []
    _patch_area_shell(monkeypatch, tab, zone, reloads)

    async def one_link(*_args, **_kwargs):
        return [link]

    async def area_cache(*_args, **_kwargs):
        return json.dumps([{"text": AREA_NAME, "fontText": ""}], ensure_ascii=False)

    async def matched(*_args, **_kwargs):
        return False, [link]

    async def selected_name(*_args, **_kwargs):
        return AREA_NAME

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "query_selector_all_with_timeout",
        one_link,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "evaluate_with_timeout",
        area_cache,
    )
    monkeypatch.setattr(tixcraft, "nodriver_get_tixcraft_target_area", matched)
    monkeypatch.setattr(tixcraft, "_read_selected_area_name", selected_name)
    monkeypatch.setattr(
        tixcraft,
        "_get_tixcraft_navigation_confirmation_seconds",
        lambda _config: 10.0,
    )
    monkeypatch.setattr(
        tixcraft,
        "_TIXCRAFT_CLICK_DISPATCH_TIMEOUT_SECONDS",
        0.01,
    )

    selected = await asyncio.wait_for(
        tixcraft.nodriver_tixcraft_area_auto_select(tab, AREA_URL, _config()),
        timeout=0.75,
    )

    assert selected is False
    assert link.native_clicks == 1
    assert link.javascript_clicks == 1
    assert reloads == ["tixcraft_area_reload"]
    assert scheduler.dom_scan_pending is False
    assert scheduler.area_click_pending is False
    assert "pending_area_navigation" not in tixcraft._state


@pytest.mark.asyncio
async def test_never_resolving_area_metadata_uses_cache_and_still_clicks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _seed_state()
    tab = _AreaTab()
    link = _NeverMetadataLink()
    zone = SimpleNamespace()
    reloads: list[str] = []
    _patch_area_shell(monkeypatch, tab, zone, reloads)

    async def one_link(*_args, **_kwargs):
        return [link]

    async def area_cache(*_args, **_kwargs):
        return json.dumps(
            [{"text": AREA_NAME, "fontText": ""}],
            ensure_ascii=False,
        )

    async def matched(*_args, **_kwargs):
        return False, [link]

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "query_selector_all_with_timeout",
        one_link,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "evaluate_with_timeout",
        area_cache,
    )
    monkeypatch.setattr(tixcraft, "nodriver_get_tixcraft_target_area", matched)

    selected = await asyncio.wait_for(
        tixcraft.nodriver_tixcraft_area_auto_select(
            tab,
            AREA_URL,
            _config(),
        ),
        timeout=0.75,
    )

    assert selected is True
    assert link.native_clicks == 1
    assert reloads == []
    assert scheduler.dom_scan_pending is False
    assert scheduler.area_click_pending is True
    assert tixcraft._state["pending_area_navigation"].seat_area == AREA_NAME


@pytest.mark.asyncio
async def test_onsale_area_click_keeps_direct_await_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    link = _DelayedClickLink()
    config = _config()
    config["advanced"]["run_mode"] = "onsale"
    monkeypatch.setattr(
        tixcraft,
        "_TIXCRAFT_CLICK_DISPATCH_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )

    dispatched = await asyncio.wait_for(
        tixcraft._dispatch_tixcraft_area_click(
            link,
            _AreaTab(),
            AREA_URL,
            config,
        ),
        timeout=0.75,
    )

    assert dispatched is True
    assert link.native_clicks == 1
    assert link.javascript_clicks == 0


@pytest.mark.asyncio
async def test_leak_area_fallback_suppresses_success_log_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    row = _FallbackRow()
    zone = _FallbackZone(row)
    calls: list[tuple[str, bool]] = []

    async def capture_operation(
        awaitable,
        _timeout,
        action,
        *_args,
        **kwargs,
    ):
        calls.append((action, kwargs.get("log_success", True)))
        return await awaitable

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "wait_for_operation",
        capture_operation,
    )

    is_need_refresh, matched = await tixcraft.nodriver_get_tixcraft_target_area(
        zone,
        _config(),
        "",
    )

    assert is_need_refresh is False
    assert matched == [row]
    assert calls == [
        ("AREA_FALLBACK_QUERY_LINKS", False),
        ("AREA_FALLBACK_ROW_HTML", False),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("hanging_stage", ["query", "evaluate"])
async def test_leak_area_font_fallback_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    hanging_stage: str,
) -> None:
    _seed_state()
    row = _AvailabilityRow(hanging_stage)
    config = _config()
    config["ticket_number"] = 2
    monkeypatch.setattr(
        tixcraft,
        "_TIXCRAFT_EVALUATE_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )

    is_need_refresh, matched = await asyncio.wait_for(
        tixcraft.nodriver_get_tixcraft_target_area(
            SimpleNamespace(),
            config,
            "",
            area_list_cache=[row],
        ),
        timeout=0.75,
    )

    assert is_need_refresh is False
    assert matched == [row]


@pytest.mark.asyncio
async def test_leak_date_language_probe_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_state()
    tab = _DateTab()
    reloads: list[str] = []

    async def record_reload(_tab, _config, state_key, _prefix) -> bool:
        reloads.append(state_key)
        return True

    monkeypatch.setattr(
        tixcraft,
        "_TIXCRAFT_EVALUATE_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(tixcraft, "_reload_page_when_due", record_reload)

    selected = await asyncio.wait_for(
        tixcraft.nodriver_tixcraft_date_auto_select(
            tab,
            tab.target.url,
            _config(),
            "tixcraft.com",
        ),
        timeout=0.75,
    )

    assert selected is False
    assert tixcraft._state["html_lang"] == "en-US"
    assert reloads == ["tixcraft_date_reload"]


@pytest.mark.asyncio
async def test_soft_block_hot_probes_suppress_success_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tixcraft._state.clear()
    calls: list[tuple[str, bool]] = []
    clock = {"now": 10.0}

    async def evaluate_probe(
        _tab,
        _script,
        _config=None,
        *,
        reason,
        log_success=True,
        **_kwargs,
    ):
        calls.append((reason, log_success))
        if reason == "SOFT_BLOCK_PAGE_HEALTH":
            return {
                "blocked": False,
                "readyState": "complete",
                "hasBody": True,
                "bodyText": "",
                "title": "",
                "elementCount": 1,
                "hasKnownContent": True,
            }
        raise AssertionError(reason)

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "evaluate_with_timeout",
        evaluate_probe,
    )
    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])

    result = await tixcraft._detect_tixcraft_soft_block(
        _AreaTab(),
        AREA_URL,
        _config(),
    )

    assert result == {
        "blocked": False,
        "health_confirmed": True,
        "inconclusive": False,
    }
    assert calls == [("SOFT_BLOCK_PAGE_HEALTH", False)]

    assert await tixcraft._detect_tixcraft_soft_block(
        _AreaTab(),
        AREA_URL,
        _config(),
    ) == {"blocked": False, "health_confirmed": True}
    assert calls == [("SOFT_BLOCK_PAGE_HEALTH", False)]

    clock["now"] += 0.3
    assert await tixcraft._detect_tixcraft_soft_block(
        _AreaTab(),
        AREA_URL,
        _config(),
    ) == {
        "blocked": False,
        "health_confirmed": True,
        "inconclusive": False,
    }
    assert calls == [
        ("SOFT_BLOCK_PAGE_HEALTH", False),
        ("SOFT_BLOCK_PAGE_HEALTH", False),
    ]
