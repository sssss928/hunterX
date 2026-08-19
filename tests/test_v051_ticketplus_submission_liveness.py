from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from platforms import ticketplus


class _Debug:
    enabled = True

    def __init__(self) -> None:
        self.messages: list[str] = []

    def log(self, *parts: object) -> None:
        self.messages.append(" ".join(str(part) for part in parts))


class _Tab:
    def __init__(self, url: str = "https://ticketplus.com.tw/order/event/session") -> None:
        self.url = url
        self.target = SimpleNamespace(url=url)


def _onsale_config(interval: float = 2.0) -> dict:
    return {
        "advanced": {
            "run_mode": "onsale",
            "auto_reload_page_interval": interval,
            "verbose": False,
        }
    }


def _leak_config(interval: float = 0.5) -> dict:
    return {
        "advanced": {
            "run_mode": "leak_watch",
            "leak_refresh_interval_seconds": interval,
            "verbose": False,
        }
    }


def _reset_state() -> None:
    ticketplus._state.clear()
    ticketplus._ensure_ticketplus_state_defaults()


def test_queue_deadline_is_non_sliding(monkeypatch) -> None:
    clock = {"now": 100.0}
    _reset_state()
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: clock["now"])
    ticketplus._ticketplus_arm_submission_watch()

    async def queue(_tab, _config, _debug):
        return {"status": "queue", "dialog_text": ""}

    monkeypatch.setattr(ticketplus, "_ticketplus_probe_submission_outcome", queue)
    assert asyncio.run(
        ticketplus._ticketplus_handle_submission_watch(
            _Tab(), _onsale_config(), _Debug(), force=True
        )
    )
    hard_deadline = ticketplus._state["queue_deadline"]
    assert hard_deadline == 700.0

    # Keep presenting positive queue evidence inside each 30-second soft
    # window; only the soft deadline may slide.
    for observed_at in (120.0, 140.0, 160.0, 180.0, 200.0, 220.0, 240.0, 250.0):
        clock["now"] = observed_at
        assert asyncio.run(
            ticketplus._ticketplus_handle_submission_watch(
                _Tab(), _onsale_config(), _Debug(), force=True
            )
        )
    assert ticketplus._state["submission_deadline"] == 280.0
    assert ticketplus._state["queue_deadline"] == hard_deadline


def test_queue_hard_deadline_precedes_an_extra_outcome_probe(monkeypatch) -> None:
    clock = {"now": 700.0}
    probes: list[int] = []
    _reset_state()
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: clock["now"])
    ticketplus._state.update(
        {
            "submission_pending": True,
            "submission_started_at": 100.0,
            "submission_deadline": 730.0,
            "submission_next_probe_at": 0.0,
            "queue_active": True,
            "queue_started_at": 100.0,
            "queue_deadline": 700.0,
        }
    )

    async def probe(_tab, _config, _debug):
        probes.append(1)
        return {"status": "queue", "dialog_text": ""}

    monkeypatch.setattr(ticketplus, "_ticketplus_probe_submission_outcome", probe)
    assert asyncio.run(
        ticketplus._ticketplus_handle_submission_watch(
            _Tab(), _onsale_config(), _Debug(), force=True
        )
    )
    assert probes == []
    assert ticketplus._state["submission_pending"] is False
    assert ticketplus._state["failure_retry_pending"] is True


def test_persistent_queue_soak_reaches_absolute_hard_fuse(monkeypatch) -> None:
    clock = {"now": 100.0}
    probes: list[float] = []
    _reset_state()
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: clock["now"])
    ticketplus._ticketplus_arm_submission_watch()

    async def queue(_tab, _config, _debug):
        probes.append(clock["now"])
        return {"status": "queue", "dialog_text": ""}

    monkeypatch.setattr(ticketplus, "_ticketplus_probe_submission_outcome", queue)
    for offset in range(600):
        clock["now"] = 100.0 + offset
        assert asyncio.run(
            ticketplus._ticketplus_handle_submission_watch(
                _Tab(), _onsale_config(), _Debug(), force=True
            )
        )
        assert ticketplus._state["queue_deadline"] == 700.0

    clock["now"] = 700.0
    assert asyncio.run(
        ticketplus._ticketplus_handle_submission_watch(
            _Tab(), _onsale_config(), _Debug(), force=True
        )
    )
    assert len(probes) == 600
    assert probes[-1] == 699.0
    assert ticketplus._state["submission_pending"] is False
    assert ticketplus._state["queue_active"] is False
    assert ticketplus._state["failure_retry_pending"] is True


def test_queue_soft_deadline_and_probe_are_capped_by_hard_fuse(monkeypatch) -> None:
    clock = {"now": 699.5}
    probes: list[int] = []
    _reset_state()
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: clock["now"])
    ticketplus._state.update(
        {
            "submission_pending": True,
            "submission_started_at": 100.0,
            "submission_deadline": 699.9,
            "submission_next_probe_at": 699.0,
            "queue_active": True,
            "queue_started_at": 100.0,
            "queue_deadline": 700.0,
        }
    )

    async def queue(_tab, _config, _debug):
        probes.append(1)
        return {"status": "queue", "dialog_text": ""}

    monkeypatch.setattr(ticketplus, "_ticketplus_probe_submission_outcome", queue)
    assert asyncio.run(
        ticketplus._ticketplus_handle_submission_watch(
            _Tab(), _onsale_config(), _Debug(), force=True
        )
    )
    assert probes == [1]
    assert ticketplus._state["submission_deadline"] == 700.0
    assert ticketplus._state["submission_next_probe_at"] == 700.0
    assert ticketplus._state["queue_deadline"] == 700.0


def test_blocked_failure_expires_at_submission_soft_deadline(monkeypatch) -> None:
    clock = {"now": 10.0}
    probes: list[int] = []
    _reset_state()
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: clock["now"])
    ticketplus._ticketplus_arm_submission_watch()

    async def blocked(_tab, _config, _debug):
        probes.append(1)
        return {"status": "failure_blocked", "dialog_text": "購票失敗"}

    monkeypatch.setattr(ticketplus, "_ticketplus_probe_submission_outcome", blocked)
    clock["now"] = 39.9
    assert asyncio.run(
        ticketplus._ticketplus_handle_submission_watch(
            _Tab(), _onsale_config(), _Debug(), force=True
        )
    )
    clock["now"] = 40.0
    assert asyncio.run(
        ticketplus._ticketplus_handle_submission_watch(
            _Tab(), _onsale_config(), _Debug(), force=True
        )
    )
    assert probes == [1]
    assert ticketplus._state["submission_pending"] is False
    assert ticketplus._state["failure_retry_pending"] is True


def test_permanent_blocked_failure_soak_stops_at_soft_deadline(monkeypatch) -> None:
    clock = {"now": 100.0}
    probes: list[float] = []
    _reset_state()
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: clock["now"])
    ticketplus._ticketplus_arm_submission_watch()

    async def blocked(_tab, _config, _debug):
        probes.append(clock["now"])
        return {"status": "failure_blocked", "dialog_text": "購票失敗"}

    monkeypatch.setattr(ticketplus, "_ticketplus_probe_submission_outcome", blocked)
    for index in range(200):
        clock["now"] = 100.0 + (index * 0.15)
        assert asyncio.run(
            ticketplus._ticketplus_handle_submission_watch(
                _Tab(), _onsale_config(), _Debug(), force=True
            )
        )

    clock["now"] = 130.0
    assert asyncio.run(
        ticketplus._ticketplus_handle_submission_watch(
            _Tab(), _onsale_config(), _Debug(), force=True
        )
    )
    assert len(probes) == 200
    assert probes[-1] < 130.0
    assert ticketplus._state["submission_pending"] is False
    assert ticketplus._state["failure_retry_pending"] is True


def test_one_thousand_early_iterations_do_not_probe(monkeypatch) -> None:
    probes: list[int] = []
    _reset_state()
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: 100.0)
    ticketplus._state.update(
        {
            "submission_pending": True,
            "submission_started_at": 90.0,
            "submission_deadline": 120.0,
            "submission_next_probe_at": 110.0,
        }
    )

    async def probe(_tab, _config, _debug):
        probes.append(1)
        return {"status": "pending", "dialog_text": ""}

    monkeypatch.setattr(ticketplus, "_ticketplus_probe_submission_outcome", probe)
    for _ in range(1_000):
        assert asyncio.run(
            ticketplus._ticketplus_handle_submission_watch(
                _Tab(), _onsale_config(), _Debug()
            )
        )
    assert probes == []


def test_active_submission_owns_real_order_route_before_outer_sanitizer(monkeypatch) -> None:
    watch_calls: list[int] = []
    sanitizer_calls: list[int] = []
    order_calls: list[int] = []
    _reset_state()
    ticketplus._state["submission_pending"] = True

    async def not_paused(_config):
        return False

    async def watch(_tab, _config, _debug, force=False):
        watch_calls.append(int(force))
        return True

    async def sanitizer(_tab, _debug):
        sanitizer_calls.append(1)
        return {"status": "absent", "dialog_text": ""}

    async def order(*_args, **_kwargs):
        order_calls.append(1)

    monkeypatch.setattr(ticketplus, "check_and_handle_pause", not_paused)
    monkeypatch.setattr(ticketplus, "_ticketplus_handle_submission_watch", watch)
    monkeypatch.setattr(ticketplus, "_ticketplus_order_failure_action", sanitizer)
    monkeypatch.setattr(ticketplus, "nodriver_ticketplus_order", order)
    tab = _Tab()
    asyncio.run(ticketplus.nodriver_ticketplus_main(tab, tab.url, _onsale_config(), None, None))
    assert watch_calls == [0]
    assert sanitizer_calls == []
    assert order_calls == []


def test_stale_blocked_popup_schedules_guarded_retry_without_submit(monkeypatch) -> None:
    order_calls: list[int] = []
    _reset_state()

    async def not_paused(_config):
        return False

    async def blocked(_tab, _debug):
        return {"status": "blocked", "dialog_text": "購票失敗"}

    async def order(*_args, **_kwargs):
        order_calls.append(1)

    monkeypatch.setattr(ticketplus, "check_and_handle_pause", not_paused)
    monkeypatch.setattr(ticketplus, "_ticketplus_order_failure_action", blocked)
    monkeypatch.setattr(ticketplus, "nodriver_ticketplus_order", order)
    tab = _Tab()
    asyncio.run(ticketplus.nodriver_ticketplus_main(tab, tab.url, _onsale_config(), None, None))
    assert order_calls == []
    assert ticketplus._state["failure_retry_pending"] is True


def test_failure_vetoes_overlay_dialog_body_and_explicit_route_queue_evidence() -> None:
    assert not ticketplus._ticketplus_queue_evidence_is_active(
        {
            "inQueue": True,
            "hasOverlay": True,
            "hasQueueDialog": True,
            "hasFailureDialog": True,
            "explicitQueueRoute": True,
            "foundKeywords": ["排隊購票中", "請稍候"],
        }
    )


def test_second_visible_failure_dialog_vetoes_first_benign_queue_dialog() -> None:
    assert not ticketplus._ticketplus_queue_evidence_is_active(
        {
            "hasFailureDialog": False,
            "hasQueueDialog": True,
            "explicitQueueRoute": True,
            "foundKeywords": ["請稍候"],
            "hasOverlay": True,
            "visibleDialogTexts": [
                "一般提醒：請稍候",
                "購票失敗：已無可配座位",
            ],
        }
    )


def test_queue_classifier_scans_all_visible_dialogs_and_ignores_hidden_body_text() -> None:
    class _ClassifierTab:
        def __init__(self) -> None:
            self.script = ""

        async def evaluate(self, script):
            self.script = script
            return {
                "inQueue": False,
                "foundKeywords": ["請稍候"],
                "hasOverlay": True,
                "hasQueueDialog": False,
                "hasFailureDialog": True,
                "explicitQueueRoute": True,
                "dialogText": "",
                "visibleDialogTexts": ["一般提醒", "購票失敗：已售完"],
            }

    tab = _ClassifierTab()
    assert asyncio.run(
        ticketplus.nodriver_ticketplus_check_queue_status(
            tab,
            _onsale_config(),
        )
    ) is False
    assert "document.querySelectorAll" in tab.script
    assert "[role=\"dialog\"], .v-dialog, .v-dialog__content, .v-overlay__content" in tab.script
    assert ".filter(isVisible)" in tab.script
    assert "dialogTexts.some" in tab.script
    assert "visibleDialogTexts: dialogTexts" in tab.script
    assert "document.body.innerText" in tab.script
    assert "document.querySelector('.v-dialog')" not in tab.script


def test_external_queue_route_keeps_read_only_submission_owner(monkeypatch) -> None:
    clock = {"now": 600.0}
    probes: list[int] = []
    _reset_state()
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: clock["now"])
    ticketplus._ticketplus_arm_submission_watch()
    ticketplus._state["submission_next_probe_at"] = 601.0

    async def not_paused(_config):
        return False

    async def probe(_tab, _config, _debug):
        probes.append(1)
        return {"status": "queue", "dialog_text": ""}

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("external queue must remain read-only")

    monkeypatch.setattr(ticketplus, "check_and_handle_pause", not_paused)
    monkeypatch.setattr(ticketplus, "_ticketplus_probe_submission_outcome", probe)
    monkeypatch.setattr(ticketplus, "_ticketplus_order_failure_action", forbidden)
    monkeypatch.setattr(ticketplus, "nodriver_ticketplus_order", forbidden)
    queue_tab = _Tab("https://tenant.queue-it.net/?queue=waiting")

    for _ in range(1_000):
        asyncio.run(
            ticketplus.nodriver_ticketplus_main(
                queue_tab,
                queue_tab.url,
                _onsale_config(),
                None,
                None,
            )
        )

    assert probes == []
    assert ticketplus._state["submission_pending"] is True


def test_external_queue_hard_fuse_clears_submit_without_queue_action(monkeypatch) -> None:
    _reset_state()
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: 700.0)
    ticketplus._state.update(
        {
            "submission_pending": True,
            "submission_started_at": 100.0,
            "submission_deadline": 700.0,
            "submission_next_probe_at": 700.0,
            "queue_active": True,
            "queue_started_at": 100.0,
            "queue_deadline": 700.0,
        }
    )

    async def not_paused(_config):
        return False

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("hard-fuse exit must precede browser work")

    monkeypatch.setattr(ticketplus, "check_and_handle_pause", not_paused)
    monkeypatch.setattr(ticketplus, "_ticketplus_probe_submission_outcome", forbidden)
    queue_tab = _Tab("https://tenant.queue-it.net/?queue=waiting")
    asyncio.run(
        ticketplus.nodriver_ticketplus_main(
            queue_tab,
            queue_tab.url,
            _onsale_config(),
            None,
            None,
        )
    )
    assert ticketplus._state["submission_pending"] is False
    assert ticketplus._state["queue_active"] is False
    assert ticketplus._state["failure_retry_pending"] is True


def test_confirmation_clears_submission_and_queue_state(monkeypatch) -> None:
    _reset_state()
    ticketplus._state.update(
        {
            "submission_pending": True,
            "submission_started_at": 100.0,
            "submission_deadline": 130.0,
            "submission_next_probe_at": 100.0,
            "queue_active": True,
            "queue_started_at": 100.0,
            "queue_deadline": 700.0,
        }
    )
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: 101.0)
    tab = _Tab("https://ticketplus.com.tw/confirm/event/session")
    assert asyncio.run(
        ticketplus._ticketplus_handle_submission_watch(
            tab, _onsale_config(), _Debug(), force=True
        )
    )
    for key in (
        "submission_pending",
        "submission_started_at",
        "submission_deadline",
        "submission_next_probe_at",
        "queue_active",
        "queue_started_at",
        "queue_deadline",
    ):
        assert ticketplus._state[key] in (False, 0.0)


def test_failure_retry_uses_mode_specific_interval(monkeypatch) -> None:
    clock = {"now": 500.0}
    refresh_calls: list[str] = []
    _reset_state()
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: clock["now"])

    async def refresh(_tab, _config, _debug, reason):
        refresh_calls.append(reason)
        return True

    monkeypatch.setattr(ticketplus, "_ticketplus_refresh_inventory", refresh)
    ticketplus._ticketplus_schedule_failure_retry(_leak_config(), "sold out")
    assert asyncio.run(
        ticketplus._ticketplus_handle_failure_retry(_Tab(), _leak_config(), _Debug())
    )
    clock["now"] = 500.499
    assert asyncio.run(
        ticketplus._ticketplus_handle_failure_retry(_Tab(), _leak_config(), _Debug())
    )
    assert refresh_calls == []
    clock["now"] = 500.5
    assert asyncio.run(
        ticketplus._ticketplus_handle_failure_retry(_Tab(), _leak_config(), _Debug())
    )
    assert refresh_calls == ["ticketplus_failure_inventory_refresh"]


@pytest.mark.parametrize(
    "outcome",
    ["pending", "failure_blocked", "queue", "unknown"],
)
def test_pending_outcomes_never_duplicate_submit(monkeypatch, outcome: str) -> None:
    order_calls: list[int] = []
    _reset_state()
    monkeypatch.setattr(ticketplus.time, "monotonic", lambda: 100.0)
    ticketplus._ticketplus_arm_submission_watch()

    async def not_paused(_config):
        return False

    async def result(_tab, _config, _debug):
        return {"status": outcome, "dialog_text": "購票失敗"}

    async def outer_sanitizer(_tab, _debug):
        raise AssertionError("active submission must own the order route")

    async def order(*_args, **_kwargs):
        order_calls.append(1)

    monkeypatch.setattr(ticketplus, "check_and_handle_pause", not_paused)
    monkeypatch.setattr(ticketplus, "_ticketplus_probe_submission_outcome", result)
    monkeypatch.setattr(ticketplus, "_ticketplus_order_failure_action", outer_sanitizer)
    monkeypatch.setattr(ticketplus, "nodriver_ticketplus_order", order)
    tab = _Tab()
    for _ in range(3):
        asyncio.run(
            ticketplus.nodriver_ticketplus_main(
                tab, tab.url, _onsale_config(), None, None
            )
        )
    assert order_calls == []


def test_v051_submission_constants_are_isolated_from_user_refresh_settings() -> None:
    assert ticketplus.CONST_TICKETPLUS_SUBMISSION_SOFT_TIMEOUT == 30.0
    assert ticketplus.CONST_TICKETPLUS_FAILURE_BLOCKED_PROBE_INTERVAL == 0.15
    assert ticketplus.CONST_TICKETPLUS_SUBMISSION_PENDING_PROBE_INTERVAL == 0.20
    assert ticketplus.CONST_TICKETPLUS_QUEUE_PROBE_INTERVAL == 1.0
    assert ticketplus.CONST_TICKETPLUS_QUEUE_MONITOR_MAX == 600.0
