"""Attempt-scoped purchase lifecycle primitives.

The platform engine owns one :class:`PurchaseAttempt` per platform/tab state.
An attempt identity is never reused, and submit ownership cannot escape the
attempt that created it. The registry remains available to focused helpers,
but protects its non-weakref fallback against Python object-id reuse.
"""

from __future__ import annotations

import time
import uuid
import weakref
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any


class AttemptState(str, Enum):
    """Lifecycle of one purchase transaction."""

    IDLE = "idle"
    LOGIN_REQUIRED = "login_required"
    LOGIN_IN_PROGRESS = "login_in_progress"
    TARGET_RESTORE_PENDING = "target_restore_pending"
    ACTIVITY_READY = "activity_ready"
    DATE_READY = "date_ready"
    AREA_READY = "area_ready"
    AREA_SELECTED = "area_selected"
    TICKET_FORM_ACTIVE = "ticket_form_active"
    SUBMIT_IN_FLIGHT = "submit_in_flight"
    ORDER_PENDING = "order_pending"
    QUEUE = "queue"
    CHECKOUT_REACHED = "checkout_reached"
    PAYMENT_REACHED = "payment_reached"
    COMPLETED = "completed"
    FAILED_RETRYABLE = "failed_retryable"
    RECOVERING_TO_SAFE_ROUTE = "recovering_to_safe_route"
    RECOVERING_TO_AREA = "recovering_to_safe_route"  # v0.5.1 compatibility
    SUBMIT_OUTCOME_UNKNOWN = "submit_outcome_unknown"
    CLOSED = "closed"


SUBMIT_PROTECTED_STATES = frozenset(
    {
        AttemptState.SUBMIT_IN_FLIGHT,
        AttemptState.ORDER_PENDING,
        AttemptState.QUEUE,
        AttemptState.CHECKOUT_REACHED,
        AttemptState.PAYMENT_REACHED,
        AttemptState.COMPLETED,
        AttemptState.SUBMIT_OUTCOME_UNKNOWN,
    }
)
TERMINAL_STATES = frozenset(
    {
        AttemptState.COMPLETED,
        AttemptState.FAILED_RETRYABLE,
        AttemptState.SUBMIT_OUTCOME_UNKNOWN,
        AttemptState.CLOSED,
    }
)


@dataclass(frozen=True)
class PurchaseAttempt:
    """Immutable identity, ownership, and timeline for one purchase attempt."""

    attempt_id: str
    area_code: str
    event_id: str
    created_at: float
    state: AttemptState = AttemptState.AREA_READY
    platform: str = ""
    tab_identity: str = ""
    session_id: str = ""
    date_id: str = ""
    generation: int = 1
    submit_owner: str = ""
    submit_token: str = ""
    completion_reason: str = ""
    updated_at: float = 0.0
    entered_ticket_page_at: float | None = None
    submitted_at: float | None = None
    checkout_reached_at: float | None = None
    recovery_started_at: float | None = None
    completed_at: float | None = None

    @classmethod
    def create_for_context(
        cls,
        *,
        platform: str,
        tab_identity: str,
        event_id: str,
        generation: int,
        state: AttemptState,
        session_id: str = "",
        date_id: str = "",
        area_code: str = "",
        now: float | None = None,
    ) -> PurchaseAttempt:
        created_at = time.monotonic() if now is None else float(now)
        return cls(
            attempt_id=str(uuid.uuid4()),
            area_code=str(area_code or ""),
            event_id=str(event_id or ""),
            created_at=created_at,
            state=state,
            platform=str(platform or ""),
            tab_identity=str(tab_identity or ""),
            session_id=str(session_id or ""),
            date_id=str(date_id or ""),
            generation=max(1, int(generation)),
            updated_at=created_at,
        )

    @classmethod
    def create_for_area(cls, area_code: str, event_id: str) -> PurchaseAttempt:
        """Compatibility constructor for direct v0.5.1 helpers/tests."""

        return cls.create_for_context(
            platform="",
            tab_identity="",
            event_id=event_id,
            area_code=area_code,
            generation=1,
            state=AttemptState.AREA_READY,
        )

    @property
    def is_submit_protected(self) -> bool:
        return self.state in SUBMIT_PROTECTED_STATES or bool(self.submit_token)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def automation_allowed(self) -> bool:
        return self.state not in {
            AttemptState.COMPLETED,
            AttemptState.SUBMIT_OUTCOME_UNKNOWN,
            AttemptState.CLOSED,
        }

    def is_stale(
        self,
        timeout_seconds: float = 600.0,
        now: float | None = None,
    ) -> bool:
        current = now if now is not None else time.monotonic()
        return current - self.created_at > timeout_seconds

    def with_state(
        self,
        new_state: AttemptState,
        *,
        reason: str = "",
        now: float | None = None,
    ) -> PurchaseAttempt:
        """Return the same identity advanced to ``new_state``."""

        current = time.monotonic() if now is None else float(now)
        kwargs: dict[str, Any] = {"state": new_state, "updated_at": current}
        if new_state is AttemptState.TICKET_FORM_ACTIVE and self.entered_ticket_page_at is None:
            kwargs["entered_ticket_page_at"] = current
        if new_state is AttemptState.SUBMIT_IN_FLIGHT and self.submitted_at is None:
            kwargs["submitted_at"] = current
        if new_state is AttemptState.CHECKOUT_REACHED and self.checkout_reached_at is None:
            kwargs["checkout_reached_at"] = current
        if new_state is AttemptState.RECOVERING_TO_SAFE_ROUTE and self.recovery_started_at is None:
            kwargs["recovery_started_at"] = current
        if new_state is AttemptState.COMPLETED:
            kwargs["completed_at"] = self.completed_at or current
            kwargs["completion_reason"] = str(reason or self.completion_reason)
        elif reason:
            kwargs["completion_reason"] = str(reason)
        return replace(self, **kwargs)

    def claim_submit(
        self,
        owner: str,
        *,
        now: float | None = None,
    ) -> tuple[PurchaseAttempt, str] | None:
        """Atomically create one submit token for this attempt."""

        if self.submit_token or not self.automation_allowed or self.state in SUBMIT_PROTECTED_STATES:
            return None
        current = time.monotonic() if now is None else float(now)
        token = f"{self.attempt_id}:{uuid.uuid4()}"
        return (
            replace(
                self,
                state=AttemptState.SUBMIT_IN_FLIGHT,
                submit_owner=str(owner or "submission"),
                submit_token=token,
                submitted_at=self.submitted_at or current,
                updated_at=current,
            ),
            token,
        )

    def release_submit(
        self,
        token: str,
        *,
        state: AttemptState = AttemptState.TICKET_FORM_ACTIVE,
        now: float | None = None,
    ) -> PurchaseAttempt | None:
        """Release only a definitely-undispatched submit owned by ``token``."""

        if not token or token != self.submit_token:
            return None
        current = time.monotonic() if now is None else float(now)
        return replace(
            self,
            state=state,
            submit_owner="",
            submit_token="",
            submitted_at=None,
            updated_at=current,
        )

    def elapsed_since_creation(self, now: float | None = None) -> float:
        current = now if now is not None else time.monotonic()
        return max(0.0, current - self.created_at)


class AttemptRegistry:
    """Identity-safe attempt storage for helpers outside PlatformRuntimeState."""

    def __init__(self, fallback_capacity: int = 64) -> None:
        self._attempts: weakref.WeakKeyDictionary[Any, dict[str, PurchaseAttempt]] = (
            weakref.WeakKeyDictionary()
        )
        self._fallback_attempts: dict[int, tuple[Any, dict[str, PurchaseAttempt]]] = {}
        self._fallback_capacity = max(1, int(fallback_capacity))

    @staticmethod
    def _platform_key(platform: str | None) -> str:
        return str(platform or "default").casefold()

    def _for_tab(self, tab: Any, *, create: bool) -> dict[str, PurchaseAttempt] | None:
        try:
            attempts = self._attempts.get(tab)
            if attempts is None and create:
                attempts = {}
                self._attempts[tab] = attempts
            return attempts
        except TypeError:
            tab_id = id(tab)
            current = self._fallback_attempts.get(tab_id)
            if current is not None:
                if current[0] is tab:
                    return current[1]
                self._fallback_attempts.pop(tab_id, None)
            if not create:
                return None
            if len(self._fallback_attempts) >= self._fallback_capacity:
                self._fallback_attempts.clear()
            attempts = {}
            self._fallback_attempts[tab_id] = (tab, attempts)
            return attempts

    def get_current(
        self,
        tab: Any,
        platform: str | None = None,
    ) -> PurchaseAttempt | None:
        attempts = self._for_tab(tab, create=False)
        return None if attempts is None else attempts.get(self._platform_key(platform))

    def create_for_area(
        self,
        tab: Any,
        area_code: str,
        event_id: str,
        platform: str | None = None,
    ) -> PurchaseAttempt:
        attempts = self._for_tab(tab, create=True)
        assert attempts is not None
        key = self._platform_key(platform)
        previous = attempts.get(key)
        generation = previous.generation + 1 if previous is not None else 1
        attempt = PurchaseAttempt.create_for_context(
            platform="" if key == "default" else key,
            tab_identity=f"{type(tab).__name__}:{id(tab)}",
            area_code=area_code,
            event_id=event_id,
            generation=generation,
            state=AttemptState.AREA_READY,
        )
        attempts[key] = attempt
        return attempt

    def transition(
        self,
        tab: Any,
        new_state: AttemptState,
        platform: str | None = None,
    ) -> PurchaseAttempt | None:
        attempts = self._for_tab(tab, create=False)
        key = self._platform_key(platform)
        current = None if attempts is None else attempts.get(key)
        if current is None or attempts is None:
            return None
        updated = current.with_state(new_state)
        attempts[key] = updated
        return updated

    def clear(self, tab: Any, platform: str | None = None) -> None:
        attempts = self._for_tab(tab, create=False)
        if attempts is not None:
            attempts.pop(self._platform_key(platform), None)

    def close_attempt(self, tab: Any, platform: str | None = None) -> None:
        self.transition(tab, AttemptState.CLOSED, platform)

    def clear_if_stale(
        self,
        tab: Any,
        timeout_seconds: float = 600.0,
        now: float | None = None,
        platform: str | None = None,
    ) -> bool:
        current = self.get_current(tab, platform)
        if current is not None and current.is_stale(timeout_seconds, now):
            self.clear(tab, platform)
            return True
        return False

    @property
    def state_count(self) -> int:
        return sum(len(item) for item in self._attempts.values()) + sum(
            len(item[1]) for item in self._fallback_attempts.values()
        )
