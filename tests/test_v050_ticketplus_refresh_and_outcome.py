from __future__ import annotations

import asyncio
from types import SimpleNamespace

from platform_engine import platform_engine
from reload_guard import guarded_reload
from platforms import ticketplus


class _Debug:
    enabled = True

    def __init__(self) -> None:
        self.messages: list[str] = []

    def log(self, *parts: object) -> None:
        self.messages.append(" ".join(str(part) for part in parts))


class _ReloadTab:
    def __init__(self, url: str) -> None:
        self.target = SimpleNamespace(url=url)
        self.url = url
        self.reload_count = 0

    async def reload(self) -> None:
        self.reload_count += 1


def _onsale_config(interval: float = 0.0) -> dict:
    return {
        "advanced": {
            "run_mode": "onsale",
            "auto_reload_page_interval": interval,
            "verbose": False,
        }
    }


def _leak_config(interval: float = 3.0) -> dict:
    return {
        "advanced": {
            "run_mode": "leak_watch",
            "leak_refresh_interval_seconds": interval,
            "verbose": False,
        }
    }


def test_ticketplus_order_selection_allows_scheduled_reload() -> None:
    async def scenario() -> tuple[_ReloadTab, bool]:
        tab = _ReloadTab("https://ticketplus.com.tw/order/event/session/tickets")
        coordinator = platform_engine.refresh_coordinator_for(tab)
        coordinator.arm_scheduled(
            "ticketplus-sale-boundary",
            coordinator.clock_ns(),
            target_wall="2030/01/01 12:00:00.000",
        )
        reloaded = await guarded_reload(
            tab,
            reason="refresh_datetime_trigger",
            config_dict=_onsale_config(2.0),
        )
        return tab, reloaded

    tab, reloaded = asyncio.run(scenario())
    assert reloaded is True
    assert tab.reload_count == 1


def test_ticketplus_order_selection_allows_periodic_reload_once_per_window() -> None:
    async def scenario() -> tuple[_ReloadTab, bool, bool]:
        tab = _ReloadTab("https://ticketplus.com.tw/order/event/session/tickets")
        first = await guarded_reload(
            tab,
            reason="ticketplus_periodic_test",
            config_dict=_onsale_config(10.0),
        )
        second = await guarded_reload(
            tab,
            reason="ticketplus_periodic_test",
            config_dict=_onsale_config(10.0),
        )
        return tab, first, second

    tab, first, second = asyncio.run(scenario())
    assert first is True
    assert second is False
    assert tab.reload_count == 1


def test_ticketplus_confirm_routes_and_unknown_routes_remain_protected() -> None:
    async def scenario(url: str) -> tuple[_ReloadTab, bool]:
        tab = _ReloadTab(url)
        result = await guarded_reload(
            tab,
            reason="ticketplus_safety_test",
            config_dict=_onsale_config(),
        )
        return tab, result

    for url in (
        "https://ticketplus.com.tw/confirm/event/session/tickets",
        "https://ticketplus.com.tw/confirmseat/event/session/tickets",
        "https://ticketplus.com.tw/unrecognized-sensitive-route",
    ):
        tab, result = asyncio.run(scenario(url))
        assert result is False
        assert tab.reload_count == 0


def test_failure_popup_click_is_verified_before_success(monkeypatch) -> None:
    serialized_absent = [
        ["foundFailure", {"type": "boolean", "value": False}],
        ["buttonClicked", {"type": "boolean", "value": False}],
        ["dialogText", {"type": "string", "value": ""}],
        ["buttonText", {"type": "string", "value": ""}],
    ]

    class Tab:
        def __init__(self) -> None:
            self.results = [
                {
                    "foundFailure": True,
                    "buttonClicked": True,
                    "dialogText": "購票失敗：票種已售完",
                    "buttonText": "我知道了",
                },
                serialized_absent,
            ]

        async def evaluate(self, _script: str):
            return self.results.pop(0)

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(ticketplus.asyncio, "sleep", no_wait)
    result = asyncio.run(ticketplus._ticketplus_order_failure_action(Tab(), _Debug()))
    assert result == {
        "status": "dismissed",
        "dialog_text": "購票失敗：票種已售完",
    }


def test_failure_popup_without_dismiss_button_blocks_submission() -> None:
    class Tab:
        async def evaluate(self, _script: str):
            return {
                "foundFailure": True,
                "buttonClicked": False,
                "dialogText": "購票失敗",
                "buttonText": "",
            }

    result = asyncio.run(ticketplus._ticketplus_order_failure_action(Tab(), _Debug()))
    assert result["status"] == "blocked"


def test_overlay_alone_is_not_queue_but_positive_queue_evidence_is() -> None:
    assert not ticketplus._ticketplus_queue_evidence_is_active(
        {
            "inQueue": True,
            "hasOverlay": True,
            "hasQueueDialog": False,
            "hasFailureDialog": False,
            "explicitQueueRoute": False,
            "foundKeywords": [],
        }
    )
    assert not ticketplus._ticketplus_queue_evidence_is_active(
        {
            "hasOverlay": True,
            "hasQueueDialog": True,
            "hasFailureDialog": True,
            "explicitQueueRoute": False,
            "foundKeywords": ["請稍候"],
        }
    )
    assert ticketplus._ticketplus_queue_evidence_is_active(
        {
            "hasOverlay": False,
            "hasQueueDialog": True,
            "hasFailureDialog": False,
            "explicitQueueRoute": False,
            "foundKeywords": [],
        }
    )


def test_ticketplus_refresh_deadline_is_nonblocking_and_mode_aware(monkeypatch) -> None:
    clock = {"now": 100.0}
    calls: list[str] = []

    async def refresh(_tab, _config, _debug, reason: str) -> bool:
        calls.append(reason)
        return True

    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(ticketplus, "_ticketplus_refresh_inventory", refresh)
    ticketplus._state.clear()
    ticketplus._ensure_ticketplus_state_defaults()
    tab = _ReloadTab("https://ticketplus.com.tw/order/event/session/tickets")
    debug = _Debug()

    async def request(config: dict) -> bool:
        return await ticketplus._ticketplus_refresh_when_due(
            tab,
            config,
            debug,
            "inventory",
            "inventory_test",
        )

    assert asyncio.run(request(_onsale_config(2.0))) is False
    clock["now"] = 101.999
    assert asyncio.run(request(_onsale_config(2.0))) is False
    clock["now"] = 102.0
    assert asyncio.run(request(_onsale_config(2.0))) is True
    assert calls == ["inventory_test"]

    # Changing run mode changes the effective interval and starts a fresh,
    # non-blocking deadline for the same route.
    clock["now"] = 200.0
    assert asyncio.run(request(_leak_config(0.5))) is False
    clock["now"] = 200.5
    assert asyncio.run(request(_leak_config(0.5))) is True
    assert calls == ["inventory_test", "inventory_test"]


def test_pending_submission_owns_order_route_and_prevents_duplicate_submit(monkeypatch) -> None:
    ticketplus._state.clear()
    ticketplus._ensure_ticketplus_state_defaults()
    ticketplus._ticketplus_arm_submission_watch()
    order_calls: list[int] = []

    async def not_paused(_config) -> bool:
        return False

    async def failure_absent(_tab, _debug):
        return {"status": "absent", "dialog_text": ""}

    async def still_pending(_tab, _config, _debug):
        return {"status": "pending", "dialog_text": ""}

    async def order(*_args, **_kwargs) -> None:
        order_calls.append(1)

    monkeypatch.setattr(ticketplus, "check_and_handle_pause", not_paused)
    monkeypatch.setattr(ticketplus, "_ticketplus_order_failure_action", failure_absent)
    monkeypatch.setattr(ticketplus, "_ticketplus_probe_submission_outcome", still_pending)
    monkeypatch.setattr(ticketplus, "nodriver_ticketplus_order", order)

    tab = _ReloadTab("https://ticketplus.com.tw/order/event/session/tickets")
    config = _onsale_config(2.0)
    for _ in range(2):
        asyncio.run(
            ticketplus.nodriver_ticketplus_main(
                tab,
                tab.url,
                config,
                None,
                None,
            )
        )

    assert order_calls == []
    assert ticketplus._state["submission_pending"] is True


def test_unknown_submission_outcome_expires_into_guarded_inventory_retry(monkeypatch) -> None:
    clock = {"now": 300.0}
    ticketplus._state.clear()
    ticketplus._ensure_ticketplus_state_defaults()
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: clock["now"])
    ticketplus._ticketplus_arm_submission_watch()

    async def unknown(_tab, _config, _debug):
        return {"status": "unknown", "dialog_text": ""}

    monkeypatch.setattr(ticketplus, "_ticketplus_probe_submission_outcome", unknown)
    clock["now"] = 330.01
    handled = asyncio.run(
        ticketplus._ticketplus_handle_submission_watch(
            _ReloadTab("https://ticketplus.com.tw/order/event/session/tickets"),
            _onsale_config(2.0),
            _Debug(),
            force=True,
        )
    )
    assert handled is True
    assert ticketplus._state["submission_pending"] is False
    assert ticketplus._state["failure_retry_pending"] is True
