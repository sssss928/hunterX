from __future__ import annotations

import time
from dataclasses import dataclass, field


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
    }

    def __init__(self) -> None:
        self.entries: list[ActionEntry] = []

    def record(self, name: str, value: str = "") -> None:
        if name not in self.VALID_ACTIONS:
            raise ValueError(f"Unknown action ledger entry: {name}")
        self.entries.append(ActionEntry(name=name, value=value or ""))

    def names(self) -> list[str]:
        return [entry.name for entry in self.entries]
