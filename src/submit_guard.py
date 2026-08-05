from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class SubmitGuard:
    pending_until: float = 0.0
    submitted_url: str = ""

    def can_submit(self, url: str = "") -> bool:
        del url
        return self.pending_until <= time.monotonic()

    def mark_submitted(self, url: str = "", pending_seconds: float = 1.5) -> None:
        self.submitted_url = url or ""
        self.pending_until = time.monotonic() + max(0.0, pending_seconds)

    def reset(self) -> None:
        self.pending_until = 0.0
        self.submitted_url = ""
