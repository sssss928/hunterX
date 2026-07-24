from __future__ import annotations

import asyncio
import json

import pytest

import runtime_health
from browser_session import BrowserSessionManager
from notification_context import format_seat_rows, make_notification_context
from platforms import tixcraft


class _Owner:
    pass


class _ConnectionClosedError(Exception):
    pass


@pytest.mark.asyncio
async def test_protocol_timeout_does_not_cancel_or_orphan_driver_future(monkeypatch) -> None:
    monkeypatch.setattr(runtime_health, "runtime_log", lambda *_args, **_kwargs: None)
    owner = _Owner()
    release = asyncio.Event()
    cancelled = False

    async def protocol_operation() -> str:
        nonlocal cancelled
        try:
            await release.wait()
            return "complete"
        except asyncio.CancelledError:
            cancelled = True
            raise

    assert await runtime_health.wait_for_operation(
        protocol_operation(),
        0.01,
        "test",
        default="timeout",
        operation_owner=owner,
    ) == "timeout"
    assert runtime_health.pending_operation_count() == 1
    assert cancelled is False

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert runtime_health.pending_operation_count() == 0
    assert cancelled is False


@pytest.mark.asyncio
async def test_owner_single_flight_discards_unscheduled_followup(monkeypatch) -> None:
    monkeypatch.setattr(runtime_health, "runtime_log", lambda *_args, **_kwargs: None)
    owner = _Owner()
    release = asyncio.Event()
    followup_ran = False

    async def first_operation() -> None:
        await release.wait()

    async def followup_operation() -> None:
        nonlocal followup_ran
        followup_ran = True

    assert await runtime_health.wait_for_operation(
        first_operation(),
        0.01,
        "first",
        operation_owner=owner,
    ) is None
    assert await runtime_health.wait_for_operation(
        followup_operation(),
        0.01,
        "followup",
        default="busy",
        operation_owner=owner,
    ) == "busy"
    assert followup_ran is False

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_external_cancellation_propagates_without_cancelling_protocol_operation(monkeypatch) -> None:
    monkeypatch.setattr(runtime_health, "runtime_log", lambda *_args, **_kwargs: None)
    owner = _Owner()
    release = asyncio.Event()
    protocol_cancelled = False

    async def protocol_operation() -> None:
        nonlocal protocol_cancelled
        try:
            await release.wait()
        except asyncio.CancelledError:
            protocol_cancelled = True
            raise

    waiter = asyncio.create_task(
        runtime_health.wait_for_operation(
            protocol_operation(),
            10,
            "cancel",
            operation_owner=owner,
        )
    )
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert protocol_cancelled is False

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert runtime_health.pending_operation_count() == 0


@pytest.mark.asyncio
async def test_connection_closed_is_classified_and_owner_is_stopped(monkeypatch) -> None:
    monkeypatch.setattr(runtime_health, "runtime_log", lambda *_args, **_kwargs: None)
    owner = _Owner()

    async def closed_operation() -> None:
        raise _ConnectionClosedError("no close frame received or sent")

    assert await runtime_health.wait_for_operation(
        closed_operation(),
        1,
        "closed",
        default="closed",
        operation_owner=owner,
    ) == "closed"
    assert runtime_health.is_connection_available(owner) is False


class _FakeDriver:
    def __init__(self) -> None:
        self.stop_count = 0

    async def stop(self) -> None:
        self.stop_count += 1


@pytest.mark.asyncio
async def test_browser_stop_is_idempotent(monkeypatch) -> None:
    monkeypatch.setattr(runtime_health, "runtime_log", lambda *_args, **_kwargs: None)
    driver = _FakeDriver()
    manager = BrowserSessionManager({"advanced": {}})
    manager.attach(driver)

    await manager.stop_browser()
    await manager.stop_browser()

    assert driver.stop_count == 1
    assert manager.driver is None


def test_tixcraft_event_name_validation_and_normalization() -> None:
    title = "蕭景鴻Edy Hsiao《以我為名Who I Am》 New Album Live Tour @ 多個表演場地"
    assert tixcraft._normalize_tixcraft_event_name(title) == (
        "蕭景鴻Edy Hsiao《以我為名Who I Am》 New Album Live Tour"
    )
    assert not tixcraft._is_valid_tixcraft_event_name("26_edyhsiao", "26_edyhsiao")
    assert not tixcraft._is_valid_tixcraft_event_name("27_another_event")
    assert tixcraft._is_valid_tixcraft_event_name("LIVE @ Legacy")


class _EventTab:
    def __init__(self, name: str, source: str = "heading") -> None:
        self.name = name
        self.source = source
        self.calls = 0

    async def evaluate(self, _script: str) -> str:
        self.calls += 1
        return json.dumps([{"name": self.name, "source": self.source}], ensure_ascii=False)


class _EventCandidatesTab:
    async def evaluate(self, _script: str) -> str:
        return json.dumps(
            [
                {"name": "選擇區域", "source": "heading"},
                {"name": "26_edyhsiao", "source": "heading"},
                {
                    "name": "蕭景鴻Edy Hsiao《以我為名Who I Am》 New Album Live Tour | tixcraft拓元售票",
                    "source": "og:title",
                },
            ],
            ensure_ascii=False,
        )


@pytest.mark.asyncio
async def test_verified_event_metadata_replaces_slug_and_is_cached() -> None:
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "current_event_id": "26_edyhsiao",
            "event_name": "26_edyhsiao",
            "event_name_quality": 0,
            "event_metadata_cache": {},
        }
    )
    tab = _EventTab("蕭景鴻Edy Hsiao《以我為名Who I Am》 New Album Live Tour @ 多個表演場地")
    url = "https://tixcraft.com/activity/detail/26_edyhsiao"

    name = await tixcraft._remember_tixcraft_event_name(tab, url)
    name_again = await tixcraft._remember_tixcraft_event_name(tab, url)

    assert name == "蕭景鴻Edy Hsiao《以我為名Who I Am》 New Album Live Tour"
    assert name_again == name
    assert tab.calls == 1
    assert tixcraft._state["event_metadata_cache"]["26_edyhsiao"]["name"] == name


@pytest.mark.asyncio
async def test_generic_heading_and_slug_do_not_hide_valid_metadata_fallback() -> None:
    metadata = await tixcraft._read_tixcraft_event_name(
        _EventCandidatesTab(),
        "26_edyhsiao",
    )

    assert metadata["source"] == "og:title"
    assert metadata["name"] == "蕭景鴻Edy Hsiao《以我為名Who I Am》 New Album Live Tour"


class _UnreadableArea:
    @property
    def text(self):
        raise RuntimeError("detached")

    @property
    def inner_text(self):
        raise RuntimeError("detached")

    @property
    def text_content(self):
        raise RuntimeError("detached")


@pytest.mark.asyncio
async def test_selected_area_falls_back_to_matching_batch_cache() -> None:
    target = _UnreadableArea()
    area_name = await tixcraft._read_selected_area_name(
        target,
        [object(), target],
        [
            {"text": "A區 剩餘 0", "fontText": ""},
            {"text": "B區 剩餘 12", "fontText": "12"},
        ],
    )

    assert area_name == "B區"


def test_seat_extraction_is_ordered_deduplicated_and_strict() -> None:
    rows = tixcraft._extract_tixcraft_seat_rows(
        [
            "B區 4 排 10 號，票價 2,800",
            "B區4排10號\nB區4排11號",
            "訂單編號 202607240001，付款期限 12:00",
            "B區 4排12號",
        ]
    )

    assert rows == ["4排10號", "4排11號", "4排12號"]


def test_seat_numbering_supports_six_ten_and_eleven_rows() -> None:
    six = format_seat_rows([f"4排{number}號" for number in range(10, 16)])
    assert six.splitlines() == [
        "1️⃣ 4排10號",
        "2️⃣ 4排11號",
        "3️⃣ 4排12號",
        "4️⃣ 4排13號",
        "5️⃣ 4排14號",
        "6️⃣ 4排15號",
    ]

    eleven = format_seat_rows([f"4排{number}號" for number in range(1, 12)])
    assert eleven.splitlines()[9] == "🔟 4排10號"
    assert eleven.splitlines()[10] == "11. 4排11號"
    assert format_seat_rows("3\ufe0f\u20e34排12號") == "3️⃣ 4排12號"


class _NotificationTab:
    async def evaluate(self, script: str) -> str:
        if "mobile-select" in script:
            return "2"
        if "table.ticket-list" in script:
            return json.dumps(["B區 4排10號", "B區 4排11號"], ensure_ascii=False)
        return ""


@pytest.mark.asyncio
async def test_tixcraft_checkout_notification_snapshot() -> None:
    event_name = "蕭景鴻Edy Hsiao《以我為名Who I Am》 New Album Live Tour"
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "current_event_id": "26_edyhsiao",
            "current_game_id": "22393",
            "event_name": event_name,
            "event_name_quality": 100,
            "event_metadata_cache": {
                "26_edyhsiao": {"name": event_name, "source": "heading", "quality": 100}
            },
            "selected_area_metadata": {
                "name": "B區",
                "confirmed": True,
                "event_id": "26_edyhsiao",
                "game_id": "22393",
            },
            "last_ticket_count": "2",
            "last_valid_area_url": "https://tixcraft.com/ticket/area/26_edyhsiao/22393",
        }
    )

    context = await tixcraft._build_tixcraft_notification_context(
        _NotificationTab(),
        {"ticket_number": 2},
        "checkout_reached",
        "https://tixcraft.com/ticket/checkout",
    )

    assert context is not None
    assert context.format_message() == (
        "🎫 [TixCraft]\n\n"
        f"活動：{event_name}\n"
        "票數：2\n"
        "區域： B區\n"
        "排數：\n"
        "1️⃣ 4排10號\n"
        "2️⃣ 4排11號\n"
        "狀態：已進入結帳畫面，請立即付款！"
    )


@pytest.mark.asyncio
async def test_tixcraft_incomplete_metadata_is_not_sent_as_unknown() -> None:
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "current_event_id": "26_edyhsiao",
            "current_game_id": "22393",
            "event_name": "26_edyhsiao",
            "event_name_quality": 0,
            "event_metadata_cache": {},
            "selected_area_metadata": {},
            "last_ticket_count": "2",
        }
    )

    context = await tixcraft._build_tixcraft_notification_context(
        _NotificationTab(),
        {"ticket_number": 2},
        "checkout_reached",
        "https://tixcraft.com/ticket/checkout",
    )

    assert context is None


def test_new_event_resets_notification_and_area_state_without_cache_leak() -> None:
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "current_event_id": "old_event",
            "current_game_id": "1",
            "event_metadata_cache": {
                "old_event": {"name": "Old Event", "quality": 100},
                "new_event": {"name": "New Event", "quality": 100},
            },
            "last_selected_area": "A區",
            "selected_area_metadata": {"name": "A區", "confirmed": True},
            "notified_order_pending": True,
            "notified_checkout_reached": True,
        }
    )

    tixcraft._sync_tixcraft_notification_flow(
        "https://tixcraft.com/ticket/area/new_event/2"
    )

    assert tixcraft._state["current_event_id"] == "new_event"
    assert tixcraft._state["event_name"] == "New Event"
    assert tixcraft._state["last_selected_area"] == ""
    assert tixcraft._state["selected_area_metadata"] == {}
    assert tixcraft._state["notified_order_pending"] is False
    assert tixcraft._state["notified_checkout_reached"] is False


def test_order_pending_snapshot_never_combines_area_and_row() -> None:
    context = make_notification_context(
        platform="TixCraft",
        stage="order_pending",
        event_name="Example Live",
        ticket_count=2,
        seat_area="B區",
        seat_rows="訂單建立中﹍",
    )

    assert "區域： B區\n排數：\n訂單建立中﹍" in context.format_message()
