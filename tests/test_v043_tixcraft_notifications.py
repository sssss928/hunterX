from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import util
from notification_context import format_seat_rows
from page_classifier import PageClass
from platforms import tixcraft


EVENT_NAME = "2026 PLAVE World Tour [KEEP IT MANIC] in Taipei"
AREA_NAME = "2F Y1B-1區5300"
AREA_URL = "https://tixcraft.com/ticket/area/26_plave/5300"
EVENT_ORIGIN = "https://tixcraft.com"


class _NotificationTab:
    def __init__(self, seat_responses: list[list[str]] | None = None) -> None:
        self.seat_responses = list(seat_responses or [[f"{AREA_NAME} 4排10號"]])
        self.seat_reads = 0

    async def evaluate(self, script: str) -> str:
        if "mobile-select" in script:
            return "1"
        if "table.ticket-list" in script:
            index = min(self.seat_reads, len(self.seat_responses) - 1)
            self.seat_reads += 1
            return json.dumps(self.seat_responses[index], ensure_ascii=False)
        if "[data-area-name]" in script:
            return AREA_NAME
        return ""


class _NeverResolvingTab:
    async def evaluate(self, _script: str):
        await asyncio.Event().wait()


class _EventMetadataTab(_NotificationTab):
    def __init__(self, metadata_responses: list[list[dict[str, object]]]) -> None:
        super().__init__()
        self.metadata_responses = list(metadata_responses)
        self.metadata_reads = 0

    async def evaluate(self, script: str) -> str:
        if "const candidates" in script:
            index = min(self.metadata_reads, len(self.metadata_responses) - 1)
            self.metadata_reads += 1
            return json.dumps(self.metadata_responses[index], ensure_ascii=False)
        return await super().evaluate(script)


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


def _seed_notification_state() -> None:
    event_snapshot = tixcraft.TixCraftEventSnapshot(
        origin=EVENT_ORIGIN,
        event_id="26_plave",
        event_name=EVENT_NAME,
        source="heading",
        quality=100,
        captured_url="https://tixcraft.com/activity/detail/26_plave",
        captured_page_class=PageClass.ACTIVITY,
        flow_generation=0,
    )
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "notification_session_id": "session-v043",
            "attempt_sequence": 0,
            "purchase_attempt": None,
            "current_event_id": "26_plave",
            "current_game_id": "5300",
            "current_event_origin": EVENT_ORIGIN,
            "event_name": EVENT_NAME,
            "event_name_quality": 100,
            "event_snapshot": event_snapshot,
            "event_metadata_cache": {
                (EVENT_ORIGIN, "26_plave"): event_snapshot,
            },
            "event_metadata_next_probe_at": 0.0,
            "selected_area_metadata": {
                "name": AREA_NAME,
                "confirmed": True,
                "event_id": "26_plave",
                "game_id": "5300",
                "area_url": AREA_URL,
                "attempt_id": None,
                "flow_generation": 0,
            },
            "last_selected_area": AREA_NAME,
            "last_ticket_count": "1",
            "last_valid_area_url": AREA_URL,
            "notification_flow_url": "",
            "notification_flow_generation": 0,
            "notification_retry_at": {},
            "notified_order_pending": False,
            "notified_checkout_reached": False,
            "is_popup_checkout": False,
            "played_sound_order": False,
        }
    )


def test_event_name_normalization_keeps_topic_and_removes_multi_venue_suffix() -> None:
    edy = "蕭景鴻Edy Hsiao《以我為名Who I Am》 New Album Live Tour @ 多個表演場地"
    assert tixcraft._normalize_tixcraft_event_name(edy) == (
        "蕭景鴻Edy Hsiao《以我為名Who I Am》 New Album Live Tour"
    )
    assert tixcraft._normalize_tixcraft_event_name(EVENT_NAME) == EVENT_NAME
    assert not tixcraft._is_valid_tixcraft_event_name("26_edyhsiao", "26_edyhsiao")


@pytest.mark.parametrize(
    "generic_name",
    [
        "購票資訊",
        "【訂單資訊】",
        "訂單明細：",
        "會員登入",
        "載入中……",
        "ＰＵＲＣＨＡＳＥ　ＩＮＦＯＲＭＡＴＩＯＮ",
        "Order Details |",
        "Loading...",
    ],
)
def test_generic_event_names_are_rejected_after_punctuation_and_width_normalization(
    generic_name: str,
) -> None:
    assert not tixcraft._is_valid_tixcraft_event_name(
        generic_name,
        "26_edyhsiao",
    )
    assert tixcraft._is_valid_tixcraft_event_name(
        "2026 Loading World Tour [LIVE] in Taipei",
        "26_loading_live",
    )


@pytest.mark.asyncio
async def test_same_quality_wrapper_title_cannot_replace_specific_event_name() -> None:
    _seed_notification_state()
    tixcraft._state["event_name"] = ""
    tixcraft._state["event_name_quality"] = 0
    tixcraft._state["event_snapshot"] = None
    tixcraft._state["event_metadata_cache"] = {}
    tab = _EventMetadataTab(
        [
            [{"name": EVENT_NAME, "source": "document_title"}],
            [{"name": "TixCraft - Loading", "source": "document_title"}],
        ]
    )
    activity_url = "https://tixcraft.com/activity/detail/26_plave"

    assert await tixcraft._remember_tixcraft_event_name(tab, activity_url) == EVENT_NAME
    tixcraft._state["event_metadata_next_probe_at"] = 0.0
    assert await tixcraft._remember_tixcraft_event_name(tab, activity_url) == EVENT_NAME
    assert not tixcraft._is_valid_tixcraft_event_name(
        "TixCraft - Loading",
        "26_plave",
    )


@pytest.mark.asyncio
async def test_checkout_page_metadata_cannot_poison_a_confirmed_event_name() -> None:
    _seed_notification_state()
    tixcraft._state["event_name"] = ""
    tixcraft._state["event_name_quality"] = 0
    tixcraft._state["event_snapshot"] = None
    tixcraft._state["event_metadata_cache"] = {}
    tab = _EventMetadataTab(
        [
            [{"name": EVENT_NAME, "source": "document_title"}],
            [
                {"name": "購票須知", "source": "heading"},
                {"name": "結帳 | tixcraft拓元售票", "source": "document_title"},
            ],
        ]
    )

    assert await tixcraft._remember_tixcraft_event_name(
        tab,
        "https://tixcraft.com/activity/detail/26_plave",
    ) == EVENT_NAME
    assert await tixcraft._remember_tixcraft_event_name(
        tab,
        "https://tixcraft.com/ticket/checkout",
    ) == EVENT_NAME
    assert tixcraft._state["event_name"] == EVENT_NAME
    assert tab.metadata_reads == 1


@pytest.mark.asyncio
async def test_event_metadata_read_is_discarded_if_route_switches_to_another_event(
    monkeypatch,
) -> None:
    _seed_notification_state()
    source_url = "https://tixcraft.com/activity/detail/26_plave"
    next_url = "https://tixcraft.com/activity/detail/26_other"
    tab = SimpleNamespace(target=SimpleNamespace(url=source_url))
    tixcraft._state["event_name"] = ""
    tixcraft._state["event_name_quality"] = 0
    tixcraft._state["event_snapshot"] = None
    tixcraft._state["event_metadata_cache"] = {}

    async def read_after_navigation(_tab, *_args, **_kwargs):
        _tab.target.url = next_url
        return json.dumps(
            [{"name": "另一個活動的正式名稱", "source": "heading"}],
            ensure_ascii=False,
        )

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "evaluate_with_timeout",
        read_after_navigation,
    )

    assert await tixcraft._remember_tixcraft_event_name(tab, source_url) == ""
    assert tixcraft._state["event_snapshot"] is None
    assert tixcraft._state["event_metadata_cache"] == {}


@pytest.mark.asyncio
async def test_event_snapshot_upgrades_before_attempt_then_becomes_immutable(
    monkeypatch,
) -> None:
    _seed_notification_state()
    tixcraft._state["event_name"] = ""
    tixcraft._state["event_name_quality"] = 0
    tixcraft._state["event_snapshot"] = None
    tixcraft._state["event_metadata_cache"] = {}
    tixcraft._state["event_metadata_next_probe_at"] = 0.0
    clock = {"now": 100.0}
    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])
    tab = _EventMetadataTab(
        [
            [{"name": EVENT_NAME, "source": "document_title"}],
            [{"name": EVENT_NAME, "source": "heading"}],
            [{"name": "購票須知", "source": "heading"}],
        ]
    )
    activity_url = "https://tixcraft.com/activity/detail/26_plave"

    assert await tixcraft._remember_tixcraft_event_name(tab, activity_url) == EVENT_NAME
    assert tixcraft._state["event_snapshot"].quality == 70
    assert await tixcraft._remember_tixcraft_event_name(tab, activity_url) == EVENT_NAME
    assert tab.metadata_reads == 1

    clock["now"] += tixcraft._TIXCRAFT_EVENT_METADATA_RETRY_SECONDS
    assert await tixcraft._remember_tixcraft_event_name(tab, activity_url) == EVENT_NAME
    assert tixcraft._state["event_snapshot"].quality == 100
    assert tab.metadata_reads == 2

    attempt = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    assert attempt.event_snapshot is not None
    assert attempt.event_snapshot.quality == 100
    clock["now"] += 1.0
    assert await tixcraft._remember_tixcraft_event_name(
        tab,
        "https://tixcraft.com/ticket/checkout",
    ) == EVENT_NAME
    assert attempt.event_snapshot.event_name == EVENT_NAME
    assert tab.metadata_reads == 2


@pytest.mark.asyncio
async def test_provisional_snapshot_remains_upgradeable_until_first_notification(
    monkeypatch,
) -> None:
    _seed_notification_state()
    tixcraft._state["event_name"] = ""
    tixcraft._state["event_name_quality"] = 0
    tixcraft._state["event_snapshot"] = None
    tixcraft._state["event_metadata_cache"] = {}
    tixcraft._state["event_metadata_next_probe_at"] = 0.0
    clock = {"now": 200.0}
    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])
    tab = _EventMetadataTab(
        [
            [{"name": EVENT_NAME, "source": "document_title"}],
            [{"name": EVENT_NAME, "source": "heading"}],
        ]
    )
    activity_url = "https://tixcraft.com/activity/detail/26_plave"

    assert await tixcraft._remember_tixcraft_event_name(tab, activity_url) == EVENT_NAME
    assert tixcraft._state["event_snapshot"].quality == 70

    attempt = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    assert attempt.event_snapshot is None

    clock["now"] += tixcraft._TIXCRAFT_EVENT_METADATA_RETRY_SECONDS
    assert await tixcraft._remember_tixcraft_event_name(tab, activity_url) == EVENT_NAME
    assert attempt.event_snapshot is not None
    assert attempt.event_snapshot.quality == 100
    assert tab.metadata_reads == 2


@pytest.mark.asyncio
async def test_provisional_read_after_attempt_does_not_prevent_canonical_upgrade(
    monkeypatch,
) -> None:
    _seed_notification_state()
    tixcraft._state["event_name"] = ""
    tixcraft._state["event_name_quality"] = 0
    tixcraft._state["event_snapshot"] = None
    tixcraft._state["event_metadata_cache"] = {}
    tixcraft._state["event_metadata_next_probe_at"] = 0.0
    clock = {"now": 300.0}
    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])
    tab = _EventMetadataTab(
        [
            [{"name": EVENT_NAME, "source": "document_title"}],
            [{"name": EVENT_NAME, "source": "heading"}],
        ]
    )
    activity_url = "https://tixcraft.com/activity/detail/26_plave"
    attempt = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    assert attempt.event_snapshot is None

    assert await tixcraft._remember_tixcraft_event_name(tab, activity_url) == EVENT_NAME
    assert tixcraft._state["event_snapshot"].quality == 70
    assert attempt.event_snapshot is None

    clock["now"] += tixcraft._TIXCRAFT_EVENT_METADATA_RETRY_SECONDS
    assert await tixcraft._remember_tixcraft_event_name(tab, activity_url) == EVENT_NAME
    assert attempt.event_snapshot is not None
    assert attempt.event_snapshot.quality == 100
    assert tab.metadata_reads == 2


@pytest.mark.asyncio
async def test_equal_quality_provisional_name_updates_until_first_notification_freezes_it(
    monkeypatch,
) -> None:
    _seed_notification_state()
    tixcraft._state["event_name"] = ""
    tixcraft._state["event_name_quality"] = 0
    tixcraft._state["event_snapshot"] = None
    tixcraft._state["event_metadata_cache"] = {}
    tixcraft._state["event_metadata_next_probe_at"] = 0.0
    clock = {"now": 350.0}
    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])
    provisional_name = "Early Access Information"
    tab = _EventMetadataTab(
        [
            [{"name": provisional_name, "source": "document_title"}],
            [{"name": EVENT_NAME, "source": "document_title"}],
            [{"name": "Later Incorrect Title", "source": "document_title"}],
        ]
    )
    activity_url = "https://tixcraft.com/activity/detail/26_plave"

    assert (
        await tixcraft._remember_tixcraft_event_name(tab, activity_url)
        == provisional_name
    )
    assert tixcraft._state["event_snapshot"].quality == 70

    attempt = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    assert attempt.event_snapshot is None

    clock["now"] += tixcraft._TIXCRAFT_EVENT_METADATA_RETRY_SECONDS
    assert (
        await tixcraft._remember_tixcraft_event_name(tab, activity_url)
        == EVENT_NAME
    )
    assert tixcraft._state["event_snapshot"].quality == 70
    assert attempt.event_snapshot is None

    context = await tixcraft._build_tixcraft_notification_context(
        tab,
        {"ticket_number": 1},
        "order_pending",
        "https://tixcraft.com/ticket/order",
    )
    assert context is not None
    assert context.event_name == EVENT_NAME
    assert attempt.event_snapshot is not None
    assert attempt.event_snapshot.event_name == EVENT_NAME

    clock["now"] += tixcraft._TIXCRAFT_EVENT_METADATA_RETRY_SECONDS
    assert (
        await tixcraft._remember_tixcraft_event_name(tab, activity_url)
        == EVENT_NAME
    )
    assert tab.metadata_reads == 2


@pytest.mark.asyncio
async def test_json_ld_metadata_accepts_event_type_only() -> None:
    tab = _EventMetadataTab(
        [
            [
                {
                    "name": "tixCraft 拓元售票",
                    "source": "structured_data",
                    "types": ["WebSite"],
                },
                {
                    "name": EVENT_NAME,
                    "source": "structured_data",
                    "types": ["Event"],
                },
            ]
        ]
    )

    metadata = await tixcraft._read_tixcraft_event_name(tab, "26_plave")

    assert metadata["name"] == EVENT_NAME
    assert metadata["source"] == "structured_data"


def test_event_cache_is_isolated_by_origin_and_event_id() -> None:
    _seed_notification_state()
    other_origin = "https://ticketmaster.sg"
    other_name = "Different Ticketmaster Event"
    other_snapshot = tixcraft.TixCraftEventSnapshot(
        origin=other_origin,
        event_id="26_plave",
        event_name=other_name,
        source="structured_data",
        quality=95,
        captured_url=f"{other_origin}/activity/detail/26_plave",
        captured_page_class=PageClass.ACTIVITY,
        flow_generation=1,
    )
    tixcraft._state["event_metadata_cache"][
        (other_origin, "26_plave")
    ] = other_snapshot

    tixcraft._reset_tixcraft_notification_flow(
        "26_plave",
        "5300",
        other_origin,
    )

    assert tixcraft._state["event_name"] == other_name
    assert tixcraft._state["event_snapshot"].origin == other_origin
    assert {
        key for key in tixcraft._state["event_metadata_cache"] if isinstance(key, tuple)
    } == {
        (EVENT_ORIGIN, "26_plave"),
        (other_origin, "26_plave"),
    }


def test_origin_change_on_checkout_route_cannot_reuse_previous_attempt() -> None:
    _seed_notification_state()
    attempt = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    assert attempt.event_snapshot is not None
    tixcraft._state["selected_area_metadata"] = {
        "name": AREA_NAME,
        "confirmed": True,
        "event_id": "26_plave",
        "game_id": "5300",
        "flow_generation": 0,
    }

    tixcraft._sync_tixcraft_notification_flow(
        "https://ticketmaster.sg/ticket/checkout"
    )

    assert tixcraft._state["current_event_origin"] == "https://ticketmaster.sg"
    assert tixcraft._state["current_event_id"] == ""
    assert tixcraft._state["event_snapshot"] is None
    assert tixcraft._state["selected_area_metadata"] == {}
    assert tixcraft._get_tixcraft_purchase_attempt() is None


@pytest.mark.asyncio
async def test_trusted_event_json_ld_stops_repeated_metadata_probes() -> None:
    _seed_notification_state()
    tixcraft._state["event_snapshot"] = None
    tixcraft._state["event_metadata_cache"] = {}
    tixcraft._state["event_metadata_next_probe_at"] = 0.0
    tab = _EventMetadataTab(
        [
            [
                {
                    "name": EVENT_NAME,
                    "source": "structured_data",
                    "types": ["Event"],
                }
            ]
        ]
    )

    assert await tixcraft._remember_tixcraft_event_name(
        tab,
        "https://tixcraft.com/activity/detail/26_plave",
    ) == EVENT_NAME
    tixcraft._state["event_metadata_next_probe_at"] = 0.0
    assert await tixcraft._remember_tixcraft_event_name(
        tab,
        "https://tixcraft.com/activity/game/26_plave",
    ) == EVENT_NAME
    assert tab.metadata_reads == 1


def test_event_metadata_cache_is_bounded() -> None:
    _seed_notification_state()
    tixcraft._state["event_metadata_cache"] = {}

    for index in range(tixcraft._TIXCRAFT_EVENT_METADATA_CACHE_CAPACITY + 5):
        snapshot = tixcraft.TixCraftEventSnapshot(
            origin=EVENT_ORIGIN,
            event_id=f"event_{index}",
            event_name=f"Event {index}",
            source="structured_data",
            quality=95,
            captured_url=f"{EVENT_ORIGIN}/activity/detail/event_{index}",
            captured_page_class=PageClass.ACTIVITY,
            flow_generation=1,
        )
        tixcraft._cache_tixcraft_event_snapshot(snapshot)

    cache = tixcraft._state["event_metadata_cache"]
    assert len(cache) == tixcraft._TIXCRAFT_EVENT_METADATA_CACHE_CAPACITY
    assert (EVENT_ORIGIN, "event_0") not in cache
    assert (
        EVENT_ORIGIN,
        f"event_{tixcraft._TIXCRAFT_EVENT_METADATA_CACHE_CAPACITY + 4}",
    ) in cache


@pytest.mark.asyncio
async def test_selected_area_uses_batch_cache_when_clicked_element_detaches() -> None:
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


def test_seat_parser_is_strict_ordered_and_deduplicated() -> None:
    assert tixcraft._extract_tixcraft_seat_rows(
        [
            "2F Y1B-1區 4 排 10 號，票價 5,300",
            "4排10號\n4 排 11 號",
            "訂單編號 202607250001，付款期限 12:00",
            "第 4 排 第 12 號",
        ]
    ) == ["4排10號", "4排11號", "4排12號"]
    assert tixcraft._extract_tixcraft_seat_rows(["第４排第１０號"]) == ["4排10號"]
    assert tixcraft._extract_tixcraft_seat_rows(["本區為自由入場，未劃位"]) == [
        "自由入場／未劃位"
    ]


def test_seat_numbering_supports_one_through_ten_and_numeric_fallback() -> None:
    formatted = format_seat_rows([f"4排{number}號" for number in range(1, 12)])
    assert formatted.splitlines() == [
        "1️⃣ 4排1號",
        "2️⃣ 4排2號",
        "3️⃣ 4排3號",
        "4️⃣ 4排4號",
        "5️⃣ 4排5號",
        "6️⃣ 4排6號",
        "7️⃣ 4排7號",
        "8️⃣ 4排8號",
        "9️⃣ 4排9號",
        "🔟 4排10號",
        "11. 4排11號",
    ]
    assert format_seat_rows("1️⃣4排10號\n2️⃣ 4排11號") == (
        "1️⃣ 4排10號\n2️⃣ 4排11號"
    )
    assert format_seat_rows("自由入場／未劃位") == "自由入場／未劃位"
    assert format_seat_rows(tixcraft._TIXCRAFT_CHECKOUT_SEAT_FALLBACK) == (
        tixcraft._TIXCRAFT_CHECKOUT_SEAT_FALLBACK
    )


@pytest.mark.asyncio
async def test_checkout_seat_reader_uses_bounded_async_polling() -> None:
    tab = _NotificationTab([[], [], [f"{AREA_NAME} 4 排 10 號"]])
    rows = await tixcraft._read_tixcraft_seat_rows(
        tab,
        attempts=3,
        interval_seconds=0,
    )
    assert rows == ["4排10號"]
    assert tab.seat_reads == 3


@pytest.mark.asyncio
async def test_checkout_seat_reader_times_out_when_cdp_never_returns() -> None:
    started_at = time.perf_counter()
    rows = await tixcraft._read_tixcraft_seat_rows(
        _NeverResolvingTab(),
        attempts=2,
        interval_seconds=0,
        evaluate_timeout_seconds=0.01,
    )
    assert rows == []
    assert time.perf_counter() - started_at < 0.5


@pytest.mark.asyncio
async def test_checkout_notification_snapshot_has_real_seat_and_blank_line() -> None:
    _seed_notification_state()
    context = await tixcraft._build_tixcraft_notification_context(
        _NotificationTab(),
        {"ticket_number": 1},
        "checkout_reached",
        "https://tixcraft.com/ticket/checkout",
    )
    assert context is not None
    assert context.format_message() == (
        "🎫 [TixCraft]\n\n"
        f"活動：{EVENT_NAME}\n"
        "票數：1\n"
        f"區域： {AREA_NAME}\n"
        "排數：\n"
        "1️⃣ 4排10號\n"
        "\n"
        "狀態：已進入結帳畫面，請立即付款！"
    )


@pytest.mark.asyncio
async def test_attempt_keeps_immutable_notification_values_for_both_stages(
    monkeypatch,
) -> None:
    _seed_notification_state()
    attempt = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    assert attempt.event_snapshot is not None
    assert attempt.event_snapshot.event_name == EVENT_NAME

    poisoned_snapshot = tixcraft.TixCraftEventSnapshot(
        origin=EVENT_ORIGIN,
        event_id="26_plave",
        event_name="錯誤的結帳頁標題",
        source="heading",
        quality=100,
        captured_url="https://tixcraft.com/ticket/checkout",
        captured_page_class=PageClass.CHECKOUT,
        flow_generation=0,
    )
    tixcraft._state["event_snapshot"] = poisoned_snapshot
    tixcraft._state["event_name"] = poisoned_snapshot.event_name

    order_context = await tixcraft._build_tixcraft_notification_context(
        _NotificationTab(),
        {"ticket_number": 1},
        "order_pending",
        "https://tixcraft.com/ticket/order",
    )
    assert order_context is not None
    assert attempt.ticket_count == "1"

    async def stale_second_stage_count(*_args, **_kwargs):
        raise AssertionError("checkout must reuse the attempt ticket count")

    monkeypatch.setattr(
        tixcraft,
        "_read_tixcraft_ticket_count",
        stale_second_stage_count,
    )
    tixcraft._state["last_ticket_count"] = "9"
    tixcraft._state["selected_area_metadata"]["name"] = "錯誤區域"
    checkout_context = await tixcraft._build_tixcraft_notification_context(
        _NotificationTab(),
        {"ticket_number": 9},
        "checkout_reached",
        "https://tixcraft.com/ticket/checkout",
    )

    assert order_context is not None
    assert checkout_context is not None
    assert order_context.event_name == EVENT_NAME
    assert checkout_context.event_name == EVENT_NAME
    assert checkout_context.ticket_count == "1"
    assert checkout_context.seat_area == AREA_NAME
    assert attempt.started_at_monotonic > 0


@pytest.mark.asyncio
async def test_checkout_never_uses_dash_when_seat_dom_is_not_ready() -> None:
    _seed_notification_state()
    context = await tixcraft._build_tixcraft_notification_context(
        _NotificationTab([[]]),
        {"ticket_number": 1},
        "checkout_reached",
        "https://tixcraft.com/ticket/checkout",
    )
    assert context is not None
    assert "排數：\n-\n" not in context.format_message()
    assert tixcraft._TIXCRAFT_CHECKOUT_SEAT_FALLBACK in context.format_message()


@pytest.mark.asyncio
async def test_checkout_seat_fallback_never_emits_a_third_notification(
    monkeypatch,
) -> None:
    _seed_notification_state()
    tab = _NotificationTab([[], [], [], [], [], [], [f"{AREA_NAME} 4排10號"]])
    attempt = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    context = await tixcraft._build_tixcraft_notification_context(
        tab,
        {"ticket_number": 1},
        "checkout_reached",
        "https://tixcraft.com/ticket/checkout",
    )
    assert context is not None
    assert attempt.checkout_fallback_sent is True

    emitted: list[tuple[str, str, str]] = []

    def fake_discord(_config, stage, _platform, *, context, notification_id):
        emitted.append((stage, notification_id, context.format_message()))
        return notification_id

    monkeypatch.setattr(tixcraft, "send_discord_notification", fake_discord)
    monkeypatch.setattr(
        tixcraft,
        "send_telegram_notification",
        lambda *_args, **_kwargs: None,
    )
    config = {
        "ticket_number": 1,
        "advanced": {"discord_webhook_url": "test-webhook-enabled"},
    }

    assert not await tixcraft._maybe_emit_tixcraft_seat_supplement(
        tab,
        config,
        "https://tixcraft.com/ticket/checkout",
    )
    assert not await tixcraft._maybe_emit_tixcraft_seat_supplement(
        tab,
        config,
        "https://tixcraft.com/ticket/checkout",
    )
    assert emitted == []
    assert attempt.discord_stages == set()


@pytest.mark.asyncio
async def test_tixcraft_rejects_every_notification_stage_except_order_and_checkout(
    monkeypatch,
) -> None:
    _seed_notification_state()
    tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    called = False

    def forbidden_send(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("unsupported TixCraft stage must not be dispatched")

    monkeypatch.setattr(tixcraft, "send_discord_notification", forbidden_send)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )

    assert not await tixcraft._emit_tixcraft_attempt_notification(
        _NotificationTab(),
        {
            "ticket_number": 1,
            "advanced": {"discord_webhook_url": "test-webhook-enabled"},
        },
        "checkout_seat_update",
        "https://tixcraft.com/ticket/checkout",
    )
    assert called is False


@pytest.mark.asyncio
async def test_notification_aborts_if_attempt_enters_recovery_during_metadata_read(
    monkeypatch,
) -> None:
    _seed_notification_state()
    attempt = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    dispatched = False

    async def context_then_recover(*_args, **_kwargs):
        attempt.phase = tixcraft.TixCraftAttemptPhase.RECOVERING_TO_AREA
        return SimpleNamespace(format_message=lambda: "must not dispatch")

    def forbidden_send(*_args, **_kwargs):
        nonlocal dispatched
        dispatched = True
        raise AssertionError("recovering attempt must not emit a success notification")

    monkeypatch.setattr(
        tixcraft,
        "_build_tixcraft_notification_context",
        context_then_recover,
    )
    monkeypatch.setattr(tixcraft, "send_discord_notification", forbidden_send)

    assert not await tixcraft._emit_tixcraft_attempt_notification(
        _NotificationTab(),
        {
            "ticket_number": 1,
            "advanced": {"discord_webhook_url": "test-webhook-enabled"},
        },
        "order_pending",
        "https://tixcraft.com/ticket/order",
    )
    assert dispatched is False
    assert attempt.phase == tixcraft.TixCraftAttemptPhase.RECOVERING_TO_AREA
    assert attempt.discord_stages == set()


@pytest.mark.asyncio
async def test_notification_aborts_if_metadata_wait_crosses_to_another_event(
    monkeypatch,
) -> None:
    _seed_notification_state()
    attempt = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    tab = SimpleNamespace(
        target=SimpleNamespace(url="https://tixcraft.com/ticket/order")
    )
    dispatched = False

    async def context_then_switch_event(*_args, **_kwargs):
        tab.target.url = (
            "https://tixcraft.com/ticket/ticket/26_other/other_game"
        )
        return tixcraft.make_notification_context(
            platform="TixCraft",
            stage="order_pending",
            event_name=EVENT_NAME,
            ticket_count=1,
            seat_area=AREA_NAME,
            seat_rows="訂單建立中﹍",
        )

    def forbidden_send(*_args, **_kwargs):
        nonlocal dispatched
        dispatched = True
        raise AssertionError("cross-event route must not dispatch")

    monkeypatch.setattr(
        tixcraft,
        "_build_tixcraft_notification_context",
        context_then_switch_event,
    )
    monkeypatch.setattr(tixcraft, "send_discord_notification", forbidden_send)

    assert not await tixcraft._emit_tixcraft_attempt_notification(
        tab,
        {
            "ticket_number": 1,
            "advanced": {"discord_webhook_url": "test-webhook-enabled"},
        },
        "order_pending",
        "https://tixcraft.com/ticket/order",
    )
    assert dispatched is False
    assert attempt.discord_stages == set()


@pytest.mark.asyncio
async def test_checkout_without_purchase_attempt_does_not_invent_notifications(
    monkeypatch,
) -> None:
    _seed_notification_state()
    called = False

    def forbidden_send(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("checkout without attempt must not notify")

    monkeypatch.setattr(tixcraft, "send_discord_notification", forbidden_send)
    assert not await tixcraft._emit_tixcraft_attempt_notification(
        _NotificationTab(),
        {
            "ticket_number": 1,
            "advanced": {"discord_webhook_url": "test-webhook-enabled"},
        },
        "checkout_reached",
        "https://tixcraft.com/ticket/checkout",
    )
    assert called is False


@pytest.mark.asyncio
async def test_order_pending_dom_probe_requires_confirmed_submit(monkeypatch) -> None:
    _seed_notification_state()
    tab = _NotificationTab()
    evaluated = False

    async def fake_evaluate(*_args, **_kwargs):
        nonlocal evaluated
        evaluated = True
        return json.dumps({"pending": True, "marker": "訂單建立中", "overlay": ""})

    monkeypatch.setattr(tixcraft.runtime_health, "evaluate_with_timeout", fake_evaluate)
    assert not await tixcraft._detect_tixcraft_order_pending(
        tab,
        "https://tixcraft.com/ticket/ticket/26_plave/5300",
        now=100.0,
    )
    assert evaluated is False

    tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    tixcraft._state["notification_submit_started_at"] = 99.0
    assert await tixcraft._detect_tixcraft_order_pending(
        tab,
        "https://tixcraft.com/ticket/ticket/26_plave/5300",
        now=100.0,
    )
    assert evaluated is True


@pytest.mark.asyncio
async def test_notification_pending_and_retry_deadlines_use_monotonic_clock(
    monkeypatch,
) -> None:
    _seed_notification_state()
    tab = _NotificationTab()
    config = {
        "ticket_number": 1,
        "advanced": {"discord_webhook_url": "test-webhook-enabled"},
    }
    tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    clock = {"now": 400.0}

    def wall_clock_must_not_be_used() -> float:
        raise AssertionError("notification deadlines must use monotonic time")

    async def pending_probe(*_args, **_kwargs):
        return json.dumps(
            {"pending": True, "marker": "order processing", "overlay": ""}
        )

    monkeypatch.setattr(tixcraft.time, "time", wall_clock_must_not_be_used)
    monkeypatch.setattr(tixcraft.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "evaluate_with_timeout",
        pending_probe,
    )

    ticket_url = "https://tixcraft.com/ticket/ticket/26_plave/5300"
    tixcraft._mark_tixcraft_submit_started(ticket_url)
    assert tixcraft._state["notification_submit_started_at"] == 400.0
    assert tixcraft._has_confirmed_tixcraft_submit_context()
    assert await tixcraft._detect_tixcraft_order_pending(tab, ticket_url)
    assert tixcraft._state["notification_order_probe_next_at"] == pytest.approx(
        400.2
    )

    context_builds = 0

    async def unavailable_context(*_args, **_kwargs):
        nonlocal context_builds
        context_builds += 1
        return None

    monkeypatch.setattr(
        tixcraft,
        "_build_tixcraft_notification_context",
        unavailable_context,
    )
    tixcraft._state["notification_retry_at"]["order_pending"] = 401.0
    assert not await tixcraft._emit_tixcraft_attempt_notification(
        tab,
        config,
        "order_pending",
        ticket_url,
    )
    assert context_builds == 0

    clock["now"] = 401.0
    assert not await tixcraft._emit_tixcraft_attempt_notification(
        tab,
        config,
        "order_pending",
        ticket_url,
    )
    assert context_builds == 1
    assert tixcraft._state["notification_retry_at"]["order_pending"] == pytest.approx(
        401.35
    )


def test_submit_after_completed_checkout_starts_a_new_attempt_without_area_roundtrip() -> None:
    _seed_notification_state()
    previous = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    previous.phase = tixcraft.TixCraftAttemptPhase.CHECKOUT_REACHED
    previous.discord_stages.update({"order_pending", "checkout_reached"})

    tixcraft._mark_tixcraft_submit_started(
        "https://tixcraft.com/ticket/ticket/26_plave/5300"
    )

    current = tixcraft._get_tixcraft_purchase_attempt()
    assert current is not None
    assert current is not previous
    assert current.attempt_id == previous.attempt_id + 1
    assert current.phase == tixcraft.TixCraftAttemptPhase.TICKET_FORM_ACTIVE
    assert current.discord_stages == set()
    assert current.seat_area == AREA_NAME
    assert tixcraft._state["notification_submit_started_at"] > 0


@pytest.mark.asyncio
async def test_keydown_commits_new_attempt_when_keyup_fails(monkeypatch) -> None:
    _seed_notification_state()
    previous = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    previous.phase = tixcraft.TixCraftAttemptPhase.CHECKOUT_REACHED
    previous.discord_stages.update({"order_pending", "checkout_reached"})

    class Guard:
        def __init__(self) -> None:
            self.marked: list[tuple[str, float]] = []

        def mark_submitted(self, url: str, pending_seconds: float) -> None:
            self.marked.append((url, pending_seconds))

    class Tab:
        def __init__(self) -> None:
            self.send_count = 0
            self.target = SimpleNamespace(url=ticket_url)

        async def send(self, _event) -> None:
            self.send_count += 1
            if self.send_count == 2:
                raise RuntimeError("execution context replaced after submit")

    ticket_url = "https://tixcraft.com/ticket/ticket/26_plave/5300"
    tab = Tab()
    guard = Guard()

    assert await tixcraft._dispatch_tixcraft_enter_submit(
        tab,
        ticket_url,
        guard,
    )

    current = tixcraft._get_tixcraft_purchase_attempt()
    assert tab.send_count == 2
    assert guard.marked == [(ticket_url, 1.5)]
    assert current is not None
    assert current is not previous
    assert current.attempt_id == previous.attempt_id + 1
    assert current.phase == tixcraft.TixCraftAttemptPhase.TICKET_FORM_ACTIVE
    assert current.discord_stages == set()
    assert tixcraft._has_confirmed_tixcraft_submit_context()

    async def context_ready(_tab, config, stage, url):
        return tixcraft._build_tixcraft_metadata_failure_context(
            config,
            stage,
            url,
        )

    emitted: list[str] = []
    monkeypatch.setattr(
        tixcraft,
        "_build_tixcraft_notification_context",
        context_ready,
    )
    monkeypatch.setattr(
        tixcraft,
        "send_discord_notification",
        lambda _config, stage, _platform, **kwargs: (
            emitted.append(stage) or kwargs["notification_id"]
        ),
    )
    monkeypatch.setattr(
        tixcraft,
        "send_telegram_notification",
        lambda *_args, **_kwargs: None,
    )
    config = {
        "ticket_number": 1,
        "advanced": {"discord_webhook_url": "enabled"},
    }
    tab.target.url = "https://tixcraft.com/ticket/order"
    assert await tixcraft._emit_tixcraft_attempt_notification(
        tab,
        config,
        "order_pending",
        tab.target.url,
    )
    tab.target.url = "https://tixcraft.com/ticket/checkout"
    assert await tixcraft._emit_tixcraft_attempt_notification(
        tab,
        config,
        "checkout_reached",
        tab.target.url,
    )
    assert emitted == ["order_pending", "checkout_reached"]


def test_new_event_and_game_clear_attempt_area_and_ticket_metadata() -> None:
    _seed_notification_state()
    tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    next_snapshot = tixcraft.TixCraftEventSnapshot(
        origin=EVENT_ORIGIN,
        event_id="next_event",
        event_name="下一場活動",
        source="og:title",
        quality=90,
        captured_url="https://tixcraft.com/activity/detail/next_event",
        captured_page_class=PageClass.ACTIVITY,
        flow_generation=1,
    )
    tixcraft._state["event_metadata_cache"][
        (EVENT_ORIGIN, "next_event")
    ] = next_snapshot

    tixcraft._reset_tixcraft_notification_flow(
        "next_event",
        "next_game",
        EVENT_ORIGIN,
    )

    assert tixcraft._get_tixcraft_purchase_attempt() is None
    assert tixcraft._state["event_name"] == "下一場活動"
    assert tixcraft._state["last_selected_area"] == ""
    assert tixcraft._state["selected_area_metadata"] == {}
    assert tixcraft._state["last_ticket_count"] == ""
    assert tixcraft._state["last_valid_area_url"] == ""


@pytest.mark.asyncio
async def test_new_same_event_attempt_cannot_reuse_previous_ticket_count(
    monkeypatch,
) -> None:
    _seed_notification_state()
    tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )
    tixcraft._state["last_ticket_count"] = "4"
    tixcraft._state["attempt_last_page_class"] = PageClass.TICKET.value
    tixcraft._track_tixcraft_attempt_page(PageClass.AREA, AREA_URL)
    assert tixcraft._state["last_ticket_count"] == ""

    tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
        force_new=True,
    )

    async def no_ticket_count(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(
        tixcraft.runtime_health,
        "evaluate_with_timeout",
        no_ticket_count,
    )
    assert await tixcraft._read_tixcraft_ticket_count(
        _NotificationTab(),
        {"ticket_number": 2},
    ) == "2"


@pytest.mark.asyncio
@pytest.mark.parametrize("run_mode", ["onsale", "leak_watch"])
async def test_four_attempts_emit_only_order_and_checkout_with_same_event_name(
    monkeypatch,
    run_mode: str,
) -> None:
    _seed_notification_state()
    emitted: list[tuple[str, str, str]] = []

    def fake_discord(*_args, **kwargs):
        emitted.append(
            (
                kwargs["notification_id"],
                _args[1],
                kwargs["context"].event_name,
            )
        )
        return kwargs["notification_id"]

    monkeypatch.setattr(tixcraft, "send_discord_notification", fake_discord)
    monkeypatch.setattr(tixcraft, "send_telegram_notification", lambda *_args, **_kwargs: None)
    config = {
        "ticket_number": 1,
        "advanced": {
            "run_mode": run_mode,
            "discord_webhook_url": "test-webhook-enabled",
        },
    }
    tab = _NotificationTab()
    tixcraft._state["attempt_last_page_class"] = PageClass.AREA.value

    for _ in range(4):
        tixcraft._set_pending_area_navigation(
            tab,
            AREA_URL,
            AREA_NAME,
            config,
        )
        assert tixcraft._get_tixcraft_purchase_attempt() is None
        assert (
            tixcraft._state["selected_area_metadata"]["confirmed"] is False
        )
        assert tixcraft._reconcile_tixcraft_pending_navigation(
            tab,
            "https://tixcraft.com/ticket/order",
            PageClass.ORDER,
            config,
        )
        attempt = tixcraft._get_tixcraft_purchase_attempt()
        assert attempt is not None
        assert attempt.seat_area == AREA_NAME
        tixcraft._mark_tixcraft_submit_started(
            "https://tixcraft.com/ticket/ticket/26_plave/5300"
        )
        tixcraft._track_tixcraft_attempt_page(
            PageClass.ORDER,
            "https://tixcraft.com/ticket/order",
        )
        for _repeat in range(20):
            assert await tixcraft._emit_tixcraft_attempt_notification(
                tab,
                config,
                "order_pending",
                "https://tixcraft.com/ticket/order",
            )
        tixcraft._track_tixcraft_attempt_page(
            PageClass.CHECKOUT,
            "https://tixcraft.com/ticket/checkout",
        )
        for _repeat in range(20):
            assert await tixcraft._emit_tixcraft_attempt_notification(
                tab,
                config,
                "checkout_reached",
                "https://tixcraft.com/ticket/checkout",
            )
        assert attempt.discord_stages == {"order_pending", "checkout_reached"}
        tixcraft._track_tixcraft_attempt_page(PageClass.AREA, AREA_URL)
        assert tixcraft._get_tixcraft_purchase_attempt() is None

    assert [stage for _, stage, _ in emitted] == [
        "order_pending",
        "checkout_reached",
        "order_pending",
        "checkout_reached",
        "order_pending",
        "checkout_reached",
        "order_pending",
        "checkout_reached",
    ]
    assert {event_name for _, _, event_name in emitted} == {EVENT_NAME}
    assert len({notification_id for notification_id, _, _ in emitted}) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("run_mode", ["onsale", "leak_watch"])
async def test_main_dispatch_emits_order_and_checkout_for_three_attempts(
    monkeypatch,
    run_mode: str,
) -> None:
    _seed_notification_state()
    emitted: list[tuple[str, str]] = []

    def fake_discord(*_args, **kwargs):
        emitted.append((kwargs["notification_id"], _args[1]))
        return kwargs["notification_id"]

    async def no_op(*_args, **_kwargs):
        return None

    async def cached_event_name(*_args, **_kwargs):
        return EVENT_NAME

    async def false_check(*_args, **_kwargs) -> bool:
        return False

    async def classify_passthrough(_tab, _url, initial):
        return initial

    monkeypatch.setattr(tixcraft, "check_and_handle_pause", false_check)
    monkeypatch.setattr(tixcraft, "nodriver_tixcraft_home_close_window", no_op)
    monkeypatch.setattr(
        tixcraft,
        "nodriver_ticketmaster_check_ip_block",
        false_check,
    )
    monkeypatch.setattr(tixcraft, "_classify_recovery_page", classify_passthrough)
    monkeypatch.setattr(
        tixcraft,
        "_remember_tixcraft_event_name",
        cached_event_name,
    )
    monkeypatch.setattr(tixcraft, "nodriver_tixcraft_area_auto_select", no_op)
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(tixcraft, "send_discord_notification", fake_discord)
    monkeypatch.setattr(
        tixcraft,
        "send_telegram_notification",
        lambda *_args, **_kwargs: None,
    )

    config = {
        "homepage": "https://tixcraft.com/activity/detail/26_plave",
        "ticket_number": 1,
        "area_auto_select": {
            "enable": True,
            "area_keyword": "",
            "mode": "from top to bottom",
        },
        "date_auto_select": {"enable": False},
        "advanced": {
            "run_mode": run_mode,
            "auto_reload_page_interval": 3.0,
            "leak_refresh_interval_seconds": 3.0,
            "discord_webhook_url": "test-webhook-enabled",
            "headless": False,
            "play_sound": {"ticket": False, "order": False},
        },
    }
    tab = _NotificationTab()
    tab.target = SimpleNamespace(url=AREA_URL)
    tixcraft._state.update(
        {
            "alert_handler_registered": True,
            "queue_it_enter_time": None,
            "manual_intervention_required": False,
            "fail_list": [],
            "fail_promo_list": [],
            "start_time": None,
            "done_time": None,
            "elapsed_time": None,
            "printed_completed": False,
            "played_sound_ticket": False,
            "area_retry_count": 0,
            "ticketmaster_phase": "area_select",
            "ticketmaster_captcha_processed_url": "",
        }
    )
    tixcraft._state["attempt_last_page_class"] = PageClass.AREA.value

    order_url = "https://tixcraft.com/ticket/order"
    checkout_url = "https://tixcraft.com/ticket/checkout"
    for _attempt_index in range(3):
        tixcraft._set_pending_area_navigation(
            tab,
            AREA_URL,
            AREA_NAME,
            config,
        )
        assert tixcraft._get_tixcraft_purchase_attempt() is None

        if _attempt_index:
            tab.target.url = order_url
            for _repeat in range(2):
                assert not await tixcraft.nodriver_tixcraft_main(
                    tab,
                    order_url,
                    config,
                    None,
                    None,
                )
        else:
            assert not tixcraft._has_confirmed_tixcraft_submit_context()

        tab.target.url = checkout_url
        for _repeat in range(2):
            assert not await tixcraft.nodriver_tixcraft_main(
                tab,
                checkout_url,
                config,
                None,
                None,
            )

        attempt = tixcraft._get_tixcraft_purchase_attempt()
        assert attempt is not None
        assert attempt.discord_stages == {
            "order_pending",
            "checkout_reached",
        }

        tab.target.url = AREA_URL
        assert not await tixcraft.nodriver_tixcraft_main(
            tab,
            AREA_URL,
            config,
            None,
            None,
        )
        assert tixcraft._get_tixcraft_purchase_attempt() is None

    assert [stage for _, stage in emitted] == [
        "order_pending",
        "checkout_reached",
        "order_pending",
        "checkout_reached",
        "order_pending",
        "checkout_reached",
    ]
    assert len({notification_id for notification_id, _ in emitted}) == 6


@pytest.mark.asyncio
async def test_main_dispatch_backfills_partial_state_before_checkout_elapsed_time(
    monkeypatch,
) -> None:
    async def no_op(*_args, **_kwargs):
        return None

    async def false_check(*_args, **_kwargs) -> bool:
        return False

    async def cached_event_name(*_args, **_kwargs):
        return EVENT_NAME

    async def classify_passthrough(_tab, _url, initial):
        return initial

    monkeypatch.setattr(tixcraft, "check_and_handle_pause", false_check)
    monkeypatch.setattr(tixcraft, "nodriver_tixcraft_home_close_window", no_op)
    monkeypatch.setattr(
        tixcraft,
        "nodriver_ticketmaster_check_ip_block",
        false_check,
    )
    monkeypatch.setattr(tixcraft, "_classify_recovery_page", classify_passthrough)
    monkeypatch.setattr(
        tixcraft,
        "_remember_tixcraft_event_name",
        cached_event_name,
    )
    monkeypatch.setattr(
        tixcraft.runtime_health,
        "runtime_log",
        lambda *_args, **_kwargs: None,
    )

    checkout_url = "https://tixcraft.com/ticket/checkout"
    tab = _NotificationTab()
    tab.target = SimpleNamespace(url=checkout_url)
    tixcraft._state.clear()
    tixcraft._state.update(
        {
            "alert_handler_registered": True,
            "start_time": 100.0,
            "done_time": 101.25,
        }
    )
    config = {
        "homepage": "https://tixcraft.com/activity/detail/26_plave",
        "ticket_number": 1,
        "area_auto_select": {"enable": False},
        "date_auto_select": {"enable": False},
        "advanced": {
            "run_mode": "onsale",
            "auto_reload_page_interval": 3.0,
            "leak_refresh_interval_seconds": 3.0,
            "discord_webhook_url": "",
            "headless": False,
            "play_sound": {"ticket": False, "order": False},
        },
    }

    assert not await tixcraft.nodriver_tixcraft_main(
        tab,
        checkout_url,
        config,
        None,
        None,
    )
    assert tixcraft._state["elapsed_time"] == pytest.approx(1.25)
    assert tixcraft._state["is_popup_checkout"] is True
    assert tixcraft._state["fail_list"] == []


@pytest.mark.asyncio
async def test_confirmed_submit_that_jumps_to_checkout_backfills_order_first(
    monkeypatch,
) -> None:
    _seed_notification_state()
    emitted: list[str] = []

    def fake_discord(*_args, **kwargs):
        emitted.append(_args[1])
        return kwargs["notification_id"]

    monkeypatch.setattr(tixcraft, "send_discord_notification", fake_discord)
    monkeypatch.setattr(
        tixcraft,
        "send_telegram_notification",
        lambda *_args, **_kwargs: None,
    )
    tixcraft._mark_tixcraft_submit_started(
        "https://tixcraft.com/ticket/ticket/26_plave/5300"
    )
    assert tixcraft._has_confirmed_tixcraft_submit_context()
    config = {
        "ticket_number": 1,
        "advanced": {"discord_webhook_url": "test-webhook-enabled"},
    }
    checkout_url = "https://tixcraft.com/ticket/checkout"

    assert await tixcraft._emit_tixcraft_attempt_notification(
        _NotificationTab(),
        config,
        "order_pending",
        checkout_url,
    )
    assert await tixcraft._emit_tixcraft_attempt_notification(
        _NotificationTab(),
        config,
        "checkout_reached",
        checkout_url,
    )
    assert emitted == ["order_pending", "checkout_reached"]


@pytest.mark.asyncio
async def test_order_pending_rechecks_marker_after_context_await(
    monkeypatch,
) -> None:
    _seed_notification_state()
    attempt = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
    )
    tixcraft._state["notification_submit_started_at"] = time.monotonic()
    ticket_url = "https://tixcraft.com/ticket/ticket/26_plave/5300"

    class MarkerDisappearedTab(_NotificationTab):
        def __init__(self) -> None:
            super().__init__()
            self.target = SimpleNamespace(url=ticket_url)

        async def evaluate(self, script: str) -> str:
            if "order-processing" in script:
                return json.dumps({"pending": False})
            return await super().evaluate(script)

    async def context_then_marker_is_gone(*_args, **_kwargs):
        return tixcraft._build_tixcraft_metadata_failure_context(
            {"ticket_number": 1},
            "order_pending",
            ticket_url,
        )

    emitted: list[str] = []
    monkeypatch.setattr(
        tixcraft,
        "_build_tixcraft_notification_context",
        context_then_marker_is_gone,
    )
    monkeypatch.setattr(
        tixcraft,
        "send_discord_notification",
        lambda *_args, **_kwargs: emitted.append("sent"),
    )

    assert not await tixcraft._emit_tixcraft_attempt_notification(
        MarkerDisappearedTab(),
        {"ticket_number": 1, "advanced": {"discord_webhook_url": "enabled"}},
        "order_pending",
        ticket_url,
    )
    assert attempt.discord_stages == set()
    assert emitted == []


@pytest.mark.asyncio
async def test_order_pending_aborts_when_marker_probe_changes_attempt_and_route(
    monkeypatch,
) -> None:
    _seed_notification_state()
    attempt = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
    )
    tixcraft._state["notification_submit_started_at"] = time.monotonic()
    ticket_url = "https://tixcraft.com/ticket/ticket/26_plave/5300"

    class RouteChangingTab(_NotificationTab):
        def __init__(self) -> None:
            super().__init__()
            self.target = SimpleNamespace(url=ticket_url)

    tab = RouteChangingTab()

    async def context_ready(*_args, **_kwargs):
        return tixcraft._build_tixcraft_metadata_failure_context(
            {"ticket_number": 1},
            "order_pending",
            ticket_url,
        )

    async def marker_then_recovery(*_args, **_kwargs):
        attempt.phase = tixcraft.TixCraftAttemptPhase.RECOVERING_TO_AREA
        tab.target.url = AREA_URL
        return True

    emitted: list[str] = []
    monkeypatch.setattr(
        tixcraft,
        "_build_tixcraft_notification_context",
        context_ready,
    )
    monkeypatch.setattr(
        tixcraft,
        "_detect_tixcraft_order_pending",
        marker_then_recovery,
    )
    monkeypatch.setattr(
        tixcraft,
        "send_discord_notification",
        lambda *_args, **_kwargs: emitted.append("sent"),
    )

    assert not await tixcraft._emit_tixcraft_attempt_notification(
        tab,
        {"ticket_number": 1, "advanced": {"discord_webhook_url": "enabled"}},
        "order_pending",
        ticket_url,
    )
    assert attempt.discord_stages == set()
    assert emitted == []


@pytest.mark.asyncio
async def test_terminal_discord_failure_is_rearmed_once_and_then_delivered(
    monkeypatch,
) -> None:
    _seed_notification_state()
    attempt = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
    )
    responses = [
        SimpleNamespace(status_code=500, headers={}),
        SimpleNamespace(status_code=204, headers={}),
    ]
    post_calls: list[int] = []

    def post(*_args, **_kwargs):
        post_calls.append(1)
        return responses.pop(0)

    dispatcher = util.DiscordNotificationDispatcher(
        max_attempts=1,
        base_backoff_seconds=0,
        post_func=post,
    )
    monkeypatch.setattr(util, "_DISCORD_DISPATCHER", dispatcher)
    config = {
        "ticket_number": 1,
        "advanced": {"discord_webhook_url": "https://discord.com/api/webhooks/a/b"},
    }
    checkout_url = "https://tixcraft.com/ticket/checkout"

    assert await tixcraft._emit_tixcraft_attempt_notification(
        _NotificationTab(),
        config,
        "checkout_reached",
        checkout_url,
    )
    assert dispatcher.drain(timeout=1.0)
    first_status = dispatcher.get_status(
        attempt.notification_id("checkout_reached")
    )
    assert first_status is not None
    assert first_status.state == util.DiscordDeliveryState.FAILED.value

    assert not await tixcraft._emit_tixcraft_attempt_notification(
        _NotificationTab(),
        config,
        "checkout_reached",
        checkout_url,
    )
    tixcraft._state["notification_retry_at"] = {}
    assert await tixcraft._emit_tixcraft_attempt_notification(
        _NotificationTab(),
        config,
        "checkout_reached",
        checkout_url,
    )
    assert dispatcher.drain(timeout=1.0)
    delivered = dispatcher.get_status(
        attempt.notification_id("checkout_reached")
    )
    assert delivered is not None
    assert delivered.state == util.DiscordDeliveryState.DELIVERED.value

    assert await tixcraft._emit_tixcraft_attempt_notification(
        _NotificationTab(),
        config,
        "checkout_reached",
        checkout_url,
    )
    assert len(post_calls) == 2
    assert attempt.delivery_retry_counts == {"checkout_reached": 1}
    dispatcher.shutdown(timeout=1.0)


def test_direct_checkout_backfill_requires_recent_confirmed_submit() -> None:
    _seed_notification_state()
    tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
    )
    assert not tixcraft._has_confirmed_tixcraft_submit_context(now=100.0)

    tixcraft._state["notification_submit_started_at"] = 50.0
    assert not tixcraft._has_confirmed_tixcraft_submit_context(now=100.0)

    tixcraft._state["notification_submit_started_at"] = 99.0
    assert tixcraft._has_confirmed_tixcraft_submit_context(now=100.0)

    tixcraft._close_tixcraft_purchase_attempt("test")
    assert not tixcraft._has_confirmed_tixcraft_submit_context(now=100.0)


def test_completed_same_area_attempt_carries_only_confirmed_actual_ticket_count() -> None:
    _seed_notification_state()
    first = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        AREA_NAME,
    )
    first.phase = tixcraft.TixCraftAttemptPhase.CHECKOUT_REACHED
    first.ticket_count = "2"
    first.ticket_count_confirmed = True

    tixcraft._mark_tixcraft_submit_started(
        "https://tixcraft.com/ticket/ticket/26_plave/5300"
    )
    second = tixcraft._get_tixcraft_purchase_attempt()
    assert second is not None
    assert second is not first
    assert second.ticket_count == "2"
    assert second.ticket_count_confirmed is True

    second.phase = tixcraft.TixCraftAttemptPhase.CHECKOUT_REACHED
    second.ticket_count = "4"
    second.ticket_count_confirmed = False
    tixcraft._mark_tixcraft_submit_started(
        "https://tixcraft.com/ticket/ticket/26_plave/5300"
    )
    third = tixcraft._get_tixcraft_purchase_attempt()
    assert third is not None
    assert third.ticket_count == ""
    assert third.ticket_count_confirmed is False


@pytest.mark.asyncio
async def test_incomplete_metadata_is_delayed_instead_of_sending_unknown() -> None:
    _seed_notification_state()
    tixcraft._state["event_name"] = "26_plave"
    tixcraft._state["event_name_quality"] = 0
    tixcraft._state["event_snapshot"] = None
    tixcraft._state["event_metadata_cache"] = {}
    tixcraft._state["selected_area_metadata"] = {}
    context = await tixcraft._build_tixcraft_notification_context(
        _NotificationTab([[]]),
        {"ticket_number": 1},
        "checkout_reached",
        "https://tixcraft.com/ticket/checkout",
    )
    assert context is None


@pytest.mark.asyncio
async def test_metadata_retry_exhaustion_emits_explicit_failure_not_unknown(
    monkeypatch,
) -> None:
    _seed_notification_state()
    tixcraft._state["event_name"] = "26_plave"
    tixcraft._state["event_name_quality"] = 0
    tixcraft._state["event_snapshot"] = None
    tixcraft._state["event_metadata_cache"] = {}
    tixcraft._state["selected_area_metadata"] = {}
    attempt = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        "",
        force_new=True,
    )
    messages: list[str] = []

    def fake_discord(_config, _stage, _platform, *, context, notification_id):
        messages.append(context.format_message())
        return notification_id

    monkeypatch.setattr(tixcraft, "send_discord_notification", fake_discord)
    monkeypatch.setattr(
        tixcraft,
        "send_telegram_notification",
        lambda *_args, **_kwargs: None,
    )
    config = {
        "ticket_number": 1,
        "advanced": {"discord_webhook_url": "test-webhook-enabled"},
    }
    for retry_index in range(
        1,
        tixcraft._TIXCRAFT_NOTIFICATION_METADATA_MAX_ATTEMPTS + 1,
    ):
        tixcraft._state["notification_retry_at"] = {}
        result = await tixcraft._emit_tixcraft_attempt_notification(
            _NotificationTab(),
            config,
            "checkout_reached",
            "https://tixcraft.com/ticket/checkout",
        )
        assert result is (
            retry_index == tixcraft._TIXCRAFT_NOTIFICATION_METADATA_MAX_ATTEMPTS
        )

    assert len(messages) == 1
    assert "活動：活動資料讀取失敗" in messages[0]
    assert "狀態：已進入結帳畫面，請立即付款！" in messages[0]
    assert "Unknown Event" not in messages[0]
    assert "Unknown Area" not in messages[0]
    assert attempt.metadata_failure_stages == {"checkout_reached"}


def test_metadata_failure_context_keeps_attempt_scoped_area_and_count() -> None:
    _seed_notification_state()
    attempt = tixcraft._begin_tixcraft_purchase_attempt(
        "area_click",
        AREA_URL,
        "2F Y1B-1區5300",
        force_new=True,
    )
    attempt.ticket_count = "1"
    tixcraft._state["last_ticket_count"] = "9"
    tixcraft._state["selected_area_metadata"] = {
        "confirmed": True,
        "event_id": attempt.event_id,
        "game_id": attempt.game_id,
        "flow_generation": tixcraft._state["notification_flow_generation"],
        "attempt_id": attempt.attempt_id + 1,
        "name": "別次嘗試的區域",
    }

    context = tixcraft._build_tixcraft_metadata_failure_context(
        {"ticket_number": 7},
        "checkout_reached",
        "https://tixcraft.com/ticket/checkout",
    )

    assert context.ticket_count == "1"
    assert context.seat_area == "2F Y1B-1區5300"
    message = context.format_message()
    assert "別次嘗試的區域" not in message
    assert "票數：1" in message
