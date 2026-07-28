from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field


ACTION_LEDGER_HISTORY_CAPACITY = 256


@dataclass
class ActionEntry:
    name: str
    value: str = ""
    timestamp: float = field(default_factory=time.time)


class ActionLedger:
    VALID_ACTIONS = {
        "entered_area_page",
        "area_dom_read",
        "area_candidate_selected",
        "area_clicked",
        "area_click_dispatched",
        "area_navigation_cleared",
        "area_navigation_confirmed",
        "date_navigation_reconciled",
        "entered_ticket_page",
        "ticket_count_selected",
        "captcha_input_attempted",
        "agreement_checked",
        "submit_clicked",
        "order_pending",
        "checkout_reached",
        "payment_reached",
        "rejected_recover_to_area",
        "manual_cancel_recover_to_area",
        "attempt_started",
        "attempt_closed",
        "notification_enqueued",
    }

    def __init__(self) -> None:
        self.entries: deque[ActionEntry] = deque(
            maxlen=ACTION_LEDGER_HISTORY_CAPACITY
        )

    def record(self, name: str, value: str = "") -> None:
        if name not in self.VALID_ACTIONS:
            raise ValueError(f"Unknown action ledger entry: {name}")
        self.entries.append(ActionEntry(name=name, value=value or ""))

    def __iter__(self) -> Iterator[ActionEntry]:
        return iter(self.entries)

    def names(self) -> list[str]:
        return [entry.name for entry in self.entries]
