from __future__ import annotations

from leak_watch import LeakWatchScheduler
from platforms import tixcraft


AREA_URL = "https://tixcraft.com/ticket/area/26_plave/5300"


def test_notification_lifecycle_transition_benchmark(benchmark) -> None:
    attempt = tixcraft.TixCraftPurchaseAttempt(
        session_id="benchmark",
        attempt_id=1,
        event_id="26_plave",
        game_id="5300",
        seat_area="2F Y1B-1區5300",
        area_url=AREA_URL,
    )

    def transition() -> str:
        attempt.phase = tixcraft.TixCraftAttemptPhase.TICKET_FORM_ACTIVE
        attempt.phase = tixcraft.TixCraftAttemptPhase.ORDER_PENDING
        attempt.phase = tixcraft.TixCraftAttemptPhase.CHECKOUT_REACHED
        return attempt.notification_id("checkout_reached")

    assert benchmark(transition).endswith(":checkout_reached")


def test_seat_row_parser_benchmark(benchmark) -> None:
    values = [
        "2F Y1B-1區5300 第4排10號 第4排11號",
        "訂單編號 202607250001，票價 5300",
        "第4排10號",
    ]
    assert benchmark(tixcraft._extract_tixcraft_seat_rows, values) == [
        "4排10號",
        "4排11號",
    ]


def test_area_recovery_url_resolver_benchmark(benchmark) -> None:
    tixcraft._state.clear()
    tixcraft._state["last_valid_area_url"] = AREA_URL
    result = benchmark(
        tixcraft._get_tixcraft_soft_block_recovery_url,
        {"homepage": "https://tixcraft.com/activity/detail/26_plave"},
        "https://tixcraft.com/ticket/area/other/event",
        "",
    )
    assert result == AREA_URL


def test_soft_block_normal_page_fast_negative_benchmark(benchmark) -> None:
    snapshot = {
        "readyState": "complete",
        "hasBody": True,
        "bodyText": "",
        "elementCount": 1,
        "hasKnownContent": True,
    }
    assert benchmark(tixcraft._is_tixcraft_blank_page_snapshot, snapshot) is False


def test_leak_scheduler_recovery_landing_benchmark(benchmark) -> None:
    scheduler = LeakWatchScheduler()
    config = {
        "advanced": {
            "run_mode": "leak_watch",
            "leak_refresh_interval_seconds": 5.0,
        }
    }

    def mark_landed() -> float:
        scheduler.mark_recovery_landed(config, now=100.0)
        return scheduler.next_cycle_at

    assert benchmark(mark_landed) == 105.0
