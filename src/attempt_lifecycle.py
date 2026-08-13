"""Attempt lifecycle management for purchase flow recovery.

Each purchase attempt has a unique identity to prevent stale state from one
failed attempt poisoning the next attempt on the same or different event.

Attempt identity is based on:
- area code (if event is the same)
- creation timestamp
- deterministic ordering ensures new area = new attempt
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any


class AttemptState(str, Enum):
    """Lifecycle of a single purchase attempt."""
    AREA_READY = "area_ready"
    AREA_SELECTED = "area_selected"
    TICKET_FORM_ACTIVE = "ticket_form_active"
    SUBMIT_IN_FLIGHT = "submit_in_flight"
    ORDER_PENDING = "order_pending"
    CHECKOUT_REACHED = "checkout_reached"
    PAYMENT_REACHED = "payment_reached"
    RECOVERING_TO_AREA = "recovering_to_area"
    CLOSED = "closed"


@dataclass(frozen=True)
class PurchaseAttempt:
    """Immutable attempt identity and timeline.
    
    Once created, an attempt never changes state in-place. Transitions
    create new attempt objects to ensure clean state separation.
    """
    
    attempt_id: str
    area_code: str
    event_id: str
    created_at: float
    state: AttemptState = AttemptState.AREA_READY
    entered_ticket_page_at: float | None = None
    submitted_at: float | None = None
    checkout_reached_at: float | None = None
    recovery_started_at: float | None = None
    
    @classmethod
    def create_for_area(
        cls,
        area_code: str,
        event_id: str,
    ) -> PurchaseAttempt:
        """Create a new attempt when an area is selected."""
        return cls(
            attempt_id=str(uuid.uuid4()),
            area_code=area_code,
            event_id=event_id,
            created_at=time.monotonic(),
            state=AttemptState.AREA_READY,
        )
    
    def is_stale(
        self,
        timeout_seconds: float = 600.0,
        now: float | None = None,
    ) -> bool:
        """Check if this attempt should be abandoned."""
        current = now if now is not None else time.monotonic()
        elapsed = current - self.created_at
        return elapsed > timeout_seconds
    
    def with_state(self, new_state: AttemptState) -> PurchaseAttempt:
        """Transition to a new state, creating a new immutable attempt."""
        kwargs: dict[str, Any] = {
            "state": new_state,
        }
        
        if new_state == AttemptState.TICKET_FORM_ACTIVE and self.entered_ticket_page_at is None:
            kwargs["entered_ticket_page_at"] = time.monotonic()
        
        if new_state == AttemptState.SUBMIT_IN_FLIGHT and self.submitted_at is None:
            kwargs["submitted_at"] = time.monotonic()
        
        if new_state == AttemptState.CHECKOUT_REACHED and self.checkout_reached_at is None:
            kwargs["checkout_reached_at"] = time.monotonic()
        
        if new_state == AttemptState.RECOVERING_TO_AREA and self.recovery_started_at is None:
            kwargs["recovery_started_at"] = time.monotonic()
        
        return replace(self, **kwargs)
    
    def elapsed_since_creation(self, now: float | None = None) -> float:
        """Seconds since attempt was created."""
        current = now if now is not None else time.monotonic()
        return max(0.0, current - self.created_at)


class AttemptRegistry:
    """Track active attempt per tab to detect new attempts."""
    
    def __init__(self) -> None:
        self._attempts: dict[int, PurchaseAttempt] = {}
    
    def get_current(self, tab: Any) -> PurchaseAttempt | None:
        """Get the active attempt for a tab, if any."""
        return self._attempts.get(id(tab))
    
    def create_for_area(
        self,
        tab: Any,
        area_code: str,
        event_id: str,
    ) -> PurchaseAttempt:
        """Start a new attempt for this tab."""
        attempt = PurchaseAttempt.create_for_area(area_code, event_id)
        self._attempts[id(tab)] = attempt
        return attempt
    
    def transition(self, tab: Any, new_state: AttemptState) -> PurchaseAttempt | None:
        """Advance the current attempt to a new state."""
        current = self._attempts.get(id(tab))
        if current is None:
            return None
        updated = current.with_state(new_state)
        self._attempts[id(tab)] = updated
        return updated
    
    def clear(self, tab: Any) -> None:
        """Abandon the current attempt (e.g., on recovery or tab close)."""
        self._attempts.pop(id(tab), None)
    
    def close_attempt(self, tab: Any) -> None:
        """Mark current attempt as closed."""
        current = self._attempts.get(id(tab))
        if current is not None:
            self._attempts[id(tab)] = current.with_state(AttemptState.CLOSED)
    
    def clear_if_stale(
        self,
        tab: Any,
        timeout_seconds: float = 600.0,
        now: float | None = None,
    ) -> bool:
        """Clear if current attempt has timed out. Return True if cleared."""
        current = self._attempts.get(id(tab))
        if current is not None and current.is_stale(timeout_seconds, now):
            self._attempts.pop(id(tab), None)
            return True
        return False
