from __future__ import annotations

import asyncio
import base64
import time
from types import SimpleNamespace

import pytest

from page_classifier import PageClass
from platform_engine import platform_engine
from platforms import tixcraft


AREA_URL = "https://tixcraft.com/ticket/area/event/game"
TICKET_URL = "https://tixcraft.com/ticket/ticket/event/game"


class _Tab:
    def __init__(self, url: str = TICKET_URL) -> None:
        self.target = SimpleNamespace(url=url)


def _seed(tab: _Tab) -> None:
    platform_engine.clear_tab(tab)
    tixcraft._state.clear()
    tixcraft._ensure_tixcraft_state_defaults()
    tixcraft._state.update(
        {
            "current_event_id": "event",
            "current_game_id": "game",
            "last_valid_area_url": AREA_URL,
            "attempt_last_page_class": PageClass.TICKET.value,
        }
    )


def _confirmed_area_health() -> dict[str, object]:
    return {
        "readyState": "complete",
        "hasBody": True,
        "bodyText": "area inventory",
        "title": "event",
        "knownAreaContent": True,
        "hasKnownContent": True,
        "probeFailed": False,
        "blocked": False,
        "whiteOverlay": False,
    }


@pytest.mark.asyncio
async def test_failed_attempt_same_url_and_area_starts_new_identity(monkeypatch) -> None:
    tab = _Tab()
    _seed(tab)
    first = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click", AREA_URL, "A1", force_new=True
    )
    tixcraft._mark_tixcraft_submit_started(TICKET_URL, tab=tab)
    coordinator = tixcraft._refresh_coordinator_for_tab(tab)
    coordinator.purchase_guard = True
    monkeypatch.setattr(
        tixcraft,
        "_read_tixcraft_page_health",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=_confirmed_area_health()),
    )

    assert not await tixcraft._reconcile_tixcraft_submit_ownership(
        tab, PageClass.AREA, AREA_URL, {}
    )
    assert tixcraft._get_tixcraft_purchase_attempt() is None
    assert tixcraft._state.get("submit_in_flight") is None
    assert not coordinator.purchase_guard

    second = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click", AREA_URL, "A1", force_new=True
    )
    assert second.attempt_id == first.attempt_id + 1
    assert second.area_url == first.area_url
    assert second.seat_area == first.seat_area


@pytest.mark.asyncio
async def test_ticket_unknown_area_recovery_releases_stale_submit(monkeypatch) -> None:
    tab = _Tab()
    _seed(tab)
    tixcraft._begin_tixcraft_purchase_attempt("ticket_page", TICKET_URL)
    tixcraft._mark_tixcraft_submit_started(TICKET_URL, tab=tab)
    context = tixcraft._state["submit_in_flight"]

    assert await tixcraft._reconcile_tixcraft_submit_ownership(
        tab, PageClass.UNKNOWN, "", {}
    )
    assert tixcraft._state["submit_in_flight"] is context

    monkeypatch.setattr(
        tixcraft,
        "_read_tixcraft_page_health",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=_confirmed_area_health()),
    )
    tab.target.url = AREA_URL
    assert not await tixcraft._reconcile_tixcraft_submit_ownership(
        tab, PageClass.AREA, AREA_URL, {}
    )
    assert tixcraft._get_tixcraft_purchase_attempt() is None


@pytest.mark.asyncio
async def test_slow_ticket_submit_is_not_cleared_by_elapsed_time() -> None:
    tab = _Tab()
    _seed(tab)
    tixcraft._begin_tixcraft_purchase_attempt("ticket_page", TICKET_URL)
    tixcraft._mark_tixcraft_submit_started(TICKET_URL, tab=tab)
    context = tixcraft._state["submit_in_flight"]
    object.__setattr__(
        context,
        "started_at_monotonic",
        time.monotonic() - (tixcraft._TIXCRAFT_SUBMIT_CONTEXT_MAX_SECONDS * 10),
    )

    assert await tixcraft._reconcile_tixcraft_submit_ownership(
        tab, PageClass.TICKET, TICKET_URL, {}
    )
    assert tixcraft._state["submit_in_flight"] is context


@pytest.mark.asyncio
async def test_mismatched_submit_generation_is_cleared_without_timer() -> None:
    tab = _Tab()
    _seed(tab)
    tixcraft._begin_tixcraft_purchase_attempt("ticket_page", TICKET_URL)
    tixcraft._mark_tixcraft_submit_started(TICKET_URL, tab=tab)
    tixcraft._state["submit_generation"] += 1

    assert not await tixcraft._reconcile_tixcraft_submit_ownership(
        tab, PageClass.TICKET, TICKET_URL, {}
    )
    assert tixcraft._state.get("submit_in_flight") is None


@pytest.mark.asyncio
async def test_order_and_checkout_keep_purchase_protection() -> None:
    tab = _Tab()
    _seed(tab)
    attempt = tixcraft._begin_tixcraft_purchase_attempt("ticket_page", TICKET_URL)
    tixcraft._mark_tixcraft_submit_started(TICKET_URL, tab=tab)

    assert await tixcraft._reconcile_tixcraft_submit_ownership(
        tab, PageClass.ORDER, "https://tixcraft.com/ticket/order", {}
    )
    tixcraft._track_tixcraft_attempt_page(
        PageClass.ORDER, "https://tixcraft.com/ticket/order"
    )
    assert attempt.phase is tixcraft.TixCraftAttemptPhase.ORDER_PENDING
    tixcraft._track_tixcraft_attempt_page(
        PageClass.CHECKOUT, "https://tixcraft.com/ticket/checkout"
    )
    assert attempt.phase is tixcraft.TixCraftAttemptPhase.CHECKOUT_REACHED
    assert tixcraft._get_tixcraft_purchase_attempt() is attempt


class _DelayedVerifyTab(_Tab):
    def __init__(self, results: list[object | None], url: str = TICKET_URL) -> None:
        super().__init__(url)
        self.results = list(results)
        self.calls = 0

    async def query_selector(self, _selector: str):
        index = min(self.calls, len(self.results) - 1)
        self.calls += 1
        return self.results[index]


@pytest.mark.asyncio
async def test_delayed_verification_input_uses_bounded_polling() -> None:
    element = object()
    tab = _DelayedVerifyTab([None, None, element])
    state = await tixcraft._wait_for_tixcraft_verify_input(
        tab, timeout=0.1, interval=0.001
    )
    assert state is tixcraft.TixCraftTicketFormState.READY
    assert tab.calls == 3


@pytest.mark.asyncio
async def test_selector_none_is_unavailable_not_ready() -> None:
    tab = _DelayedVerifyTab([None])
    state = await tixcraft._wait_for_tixcraft_verify_input(
        tab, timeout=0.003, interval=0.001
    )
    assert state is tixcraft.TixCraftTicketFormState.UNAVAILABLE
    assert tab.calls >= 2


@pytest.mark.asyncio
async def test_verify_poll_stops_immediately_on_invalid_page() -> None:
    tab = _DelayedVerifyTab([object()], AREA_URL)
    state = await tixcraft._wait_for_tixcraft_verify_input(
        tab, timeout=0.1, interval=0.001
    )
    assert state is tixcraft.TixCraftTicketFormState.INVALID_PAGE
    assert tab.calls == 0


@pytest.mark.asyncio
async def test_slow_ocr_does_not_block_event_loop() -> None:
    class Ocr:
        def classification(self, _payload: bytes) -> str:
            time.sleep(0.05)
            return "1234"

    class Captcha:
        def request_captcha(self) -> bytes:
            return base64.b64encode(b"image")

    ticks = 0
    running = True

    async def ticker() -> None:
        nonlocal ticks
        while running:
            ticks += 1
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    try:
        answer = await tixcraft.nodriver_tixcraft_get_ocr_answer(
            _Tab(),
            Ocr(),
            tixcraft.CONST_OCR_CAPTCH_IMAGE_SOURCE_NON_BROWSER,
            Captcha(),
            "tixcraft.com",
        )
    finally:
        running = False
        await task
    assert answer == "1234"
    assert ticks >= 10


@pytest.mark.asyncio
@pytest.mark.parametrize("force_submit", [False, True])
async def test_ocr_readiness_is_independent_of_force_submit(
    monkeypatch, force_submit: bool
) -> None:
    tab = _Tab()
    _seed(tab)
    tixcraft._begin_tixcraft_purchase_attempt("ticket_page", TICKET_URL)
    submitted_policies: list[bool] = []
    monkeypatch.setattr(
        tixcraft,
        "_wait_for_tixcraft_verify_input",
        lambda *_args, **_kwargs: asyncio.sleep(
            0, result=tixcraft.TixCraftTicketFormState.READY
        ),
    )
    monkeypatch.setattr(
        tixcraft,
        "nodriver_tixcraft_get_ocr_answer",
        lambda *_args, **_kwargs: asyncio.sleep(0, result="1234"),
    )
    monkeypatch.setattr(
        tixcraft,
        "nodriver_get_yii_captcha_hash",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=0),
    )

    async def keyin(*_args, **kwargs):
        submitted_policies.append(bool(kwargs["auto_submit"]))
        return True, force_submit

    monkeypatch.setattr(tixcraft, "nodriver_tixcraft_keyin_captcha_code", keyin)
    result = await tixcraft.nodriver_tixcraft_auto_ocr(
        tab,
        {"ocr_captcha": {"force_submit": force_submit}},
        object(),
        force_submit,
        None,
        None,
        tixcraft.CONST_OCR_CAPTCH_IMAGE_SOURCE_NON_BROWSER,
        "tixcraft.com",
    )
    assert submitted_policies == [force_submit]
    assert result[2] is force_submit


def test_attempt_scoped_completion_flags_do_not_survive_same_url_retry() -> None:
    tab = _Tab()
    _seed(tab)
    first = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click", AREA_URL, "A1", force_new=True
    )
    tixcraft._state["ocr_completed_url"] = TICKET_URL
    tixcraft._state["ocr_completed_attempt_id"] = first.attempt_id
    tixcraft._state[f"ticket_assigned_{first.attempt_id}_{TICKET_URL}_2_0"] = True

    second = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click", AREA_URL, "A1", force_new=True
    )
    assert second.attempt_id == first.attempt_id + 1
    assert tixcraft._state["ocr_completed_url"] == ""
    assert tixcraft._state["ocr_completed_attempt_id"] is None
    assert not any(str(key).startswith("ticket_assigned_") for key in tixcraft._state)
