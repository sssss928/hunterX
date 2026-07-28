from __future__ import annotations

import tracemalloc

from action_ledger import (
    ACTION_LEDGER_HISTORY_CAPACITY,
    ActionLedger,
)


def test_tixcraft_navigation_and_notification_actions_are_valid() -> None:
    ledger = ActionLedger()
    action_names = (
        "attempt_started",
        "attempt_closed",
        "notification_enqueued",
        "area_click_dispatched",
        "area_navigation_cleared",
        "area_navigation_confirmed",
        "date_navigation_reconciled",
    )

    for action_name in action_names:
        ledger.record(action_name, "offline-fixture")

    assert ledger.names() == list(action_names)
    assert [entry.name for entry in ledger] == list(action_names)


def test_action_ledger_100000_record_soak_is_bounded() -> None:
    ledger = ActionLedger()

    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()
    for index in range(100_000):
        ledger.record("entered_area_page", f"offline-area-{index}")
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert len(ledger.entries) == ACTION_LEDGER_HISTORY_CAPACITY
    assert ledger.entries[0].value == (
        f"offline-area-{100_000 - ACTION_LEDGER_HISTORY_CAPACITY}"
    )
    assert ledger.entries[-1].value == "offline-area-99999"
    assert len(ledger.names()) == ACTION_LEDGER_HISTORY_CAPACITY
    assert current_bytes - baseline_current < 2 * 1024 * 1024
    assert peak_bytes - baseline_current < 2 * 1024 * 1024
