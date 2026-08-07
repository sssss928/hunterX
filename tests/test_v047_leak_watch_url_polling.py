from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import nodriver_common
import nodriver_tixcraft as runtime
from leak_watch import LeakWatchScheduler
from platform_adapters import adapter_for_key
from platform_engine import platform_engine
from platforms import tixcraft


AREA_URL = "https://tixcraft.com/ticket/area/26_event/123"
TICKET_URL = "https://tixcraft.com/ticket/ticket/26_event/123"


def _config(*, run_mode: str = "leak_watch", interval: float = 5.0) -> dict:
    return {
        "advanced": {
            "run_mode": run_mode,
            "leak_refresh_interval_seconds": interval,
            "auto_reload_page_interval": interval,
        }
    }


@dataclass
class _Target:
    url: str


class _Tab:
    def __init__(self, target_url: str = AREA_URL, js_url: str = AREA_URL) -> None:
        self.target = _Target(target_url)
        self.js_url = js_url
        self.js_calls = 0

    async def js_dumps(self, script: str) -> str:
        assert script == "window.location.href"
        self.js_calls += 1
        return self.js_url


def _platform_data(tab: _Tab) -> dict:
    adapter = adapter_for_key("tixcraft")
    assert adapter is not None
    return platform_engine.state_for(tab, adapter).platform_data


def _mark_area_document_consumed(tab: _Tab, config: dict) -> LeakWatchScheduler:
    state = _platform_data(tab)
    scheduler = LeakWatchScheduler()
    state["leak_scheduler"] = scheduler
    scheduler.mark_dom_scan_end(now=100.0)
    assert scheduler.should_wait_for_reload_before_dom_scan(config)
    return scheduler


def test_leak_wait_prefers_cached_url_only_after_area_document_scan() -> None:
    tab = _Tab()
    config = _config()

    assert tixcraft.should_prefer_cached_url_during_leak_wait(tab, config) is False

    _mark_area_document_consumed(tab, config)

    assert tixcraft.should_prefer_cached_url_during_leak_wait(tab, config) is True
    assert runtime._should_prefer_cached_runtime_url(tab, config, {}) is True


@pytest.mark.asyncio
async def test_leak_wait_thousands_of_url_polls_do_not_enter_javascript_context() -> None:
    tab = _Tab()
    config = _config()
    _mark_area_document_consumed(tab, config)

    for _ in range(2000):
        prefer_cached = runtime._should_prefer_cached_runtime_url(tab, config, {})
        url, is_quit_bot = await nodriver_common.nodriver_current_url(
            tab,
            config,
            prefer_cached=prefer_cached,
        )
        assert url == AREA_URL
        assert is_quit_bot is False

    assert tab.js_calls == 0


@pytest.mark.asyncio
async def test_pending_area_navigation_restores_normal_javascript_url_probe() -> None:
    tab = _Tab(js_url=TICKET_URL)
    config = _config()
    _mark_area_document_consumed(tab, config)
    state = _platform_data(tab)
    state["pending_area_navigation"] = tixcraft.TixCraftPendingNavigation(
        kind="area_click",
        source_url=AREA_URL,
        tab_identity=id(tab),
        started_at=100.0,
    )

    prefer_cached = runtime._should_prefer_cached_runtime_url(tab, config, {})
    url, is_quit_bot = await nodriver_common.nodriver_current_url(
        tab,
        config,
        prefer_cached=prefer_cached,
    )

    assert prefer_cached is False
    assert url == TICKET_URL
    assert is_quit_bot is False
    assert tab.js_calls == 1


@pytest.mark.parametrize(
    "mutator",
    [
        lambda scheduler, state: setattr(scheduler, "area_click_pending", True),
        lambda scheduler, state: setattr(scheduler, "reload_pending", True),
        lambda scheduler, state: setattr(scheduler, "submit_pending", True),
        lambda scheduler, state: state.__setitem__("area_navigation_retry_due", True),
        lambda scheduler, state: state.__setitem__("submit_in_flight", object()),
        lambda scheduler, state: state.__setitem__("manual_intervention_required", True),
    ],
)
def test_active_transition_states_disable_cached_leak_wait_fast_path(mutator) -> None:
    tab = _Tab()
    config = _config()
    scheduler = _mark_area_document_consumed(tab, config)
    state = _platform_data(tab)
    mutator(scheduler, state)

    assert tixcraft.should_prefer_cached_url_during_leak_wait(tab, config) is False


def test_cached_leak_wait_fast_path_is_disabled_outside_exact_safe_scope() -> None:
    tab = _Tab()
    config = _config()
    _mark_area_document_consumed(tab, config)

    assert tixcraft.should_prefer_cached_url_during_leak_wait(
        tab, _config(run_mode="onsale")
    ) is False
    assert tixcraft.should_prefer_cached_url_during_leak_wait(
        tab, _config(interval=0.0)
    ) is False

    tab.target.url = TICKET_URL
    assert tixcraft.should_prefer_cached_url_during_leak_wait(tab, config) is False


def test_purchase_attempt_disables_cached_leak_wait_fast_path() -> None:
    tab = _Tab()
    config = _config()
    _mark_area_document_consumed(tab, config)
    state = _platform_data(tab)
    state["purchase_attempt"] = tixcraft.TixCraftPurchaseAttempt(
        session_id="session",
        attempt_id=1,
    )

    assert tixcraft.should_prefer_cached_url_during_leak_wait(tab, config) is False

@pytest.mark.asyncio
async def test_successful_reload_uses_cached_url_before_first_fresh_dom_scan() -> None:
    tab = _Tab()
    config = _config()
    state = _platform_data(tab)
    scheduler = LeakWatchScheduler()
    state["leak_scheduler"] = scheduler

    scheduler.begin_reload_cycle(AREA_URL, now=100.0)
    scheduler.finish_reload_cycle(config, success=True, now=100.1)

    assert scheduler.dom_scan_completed_since_reload is False
    assert scheduler.fresh_document_after_reload is True
    assert tixcraft.should_prefer_cached_url_during_leak_wait(tab, config) is True

    for _ in range(500):
        prefer_cached = runtime._should_prefer_cached_runtime_url(tab, config, {})
        url, _ = await nodriver_common.nodriver_current_url(
            tab,
            config,
            prefer_cached=prefer_cached,
        )
        assert url == AREA_URL

    assert tab.js_calls == 0

    assert scheduler.mark_dom_scan_start(now=100.2) is True
    assert scheduler.fresh_document_after_reload is False
    assert tixcraft.should_prefer_cached_url_during_leak_wait(tab, config) is False

    scheduler.mark_dom_scan_end(now=100.3)
    assert tixcraft.should_prefer_cached_url_during_leak_wait(tab, config) is True


def test_failed_reload_does_not_invent_a_fresh_document_fast_path() -> None:
    tab = _Tab()
    config = _config()
    state = _platform_data(tab)
    scheduler = LeakWatchScheduler()
    state["leak_scheduler"] = scheduler

    scheduler.begin_reload_cycle(AREA_URL, now=100.0)
    scheduler.finish_reload_cycle(config, success=False, now=100.1)

    assert scheduler.fresh_document_after_reload is False
    assert scheduler.dom_scan_completed_since_reload is False
    assert tixcraft.should_prefer_cached_url_during_leak_wait(tab, config) is False
