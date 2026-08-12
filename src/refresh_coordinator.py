"""Deterministic per-tab refresh arbitration.

Platform handlers submit refresh intents through :func:`guarded_reload`; this
module owns the monotonic minimum-interval and scheduled one-shot invariants.
It deliberately contains no site-specific DOM or purchase logic.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable


NS_PER_SECOND = 1_000_000_000
REFRESH_TRACE_CAPACITY = 256
SCHEDULE_TOKEN_CAPACITY = 32


@dataclass(frozen=True)
class RefreshDispatchDecision:
    allowed: bool
    reason: str
    token: int | None
    requested_ns: int
    next_allowed_ns: int
    lateness_ms: float | None = None


@dataclass
class RefreshCoordinator:
    """One authoritative refresh clock for one browser tab.

    ``configured_interval`` is a minimum interval between *dispatch starts*.
    A delayed event never catches up: the next deadline is always derived from
    the actual dispatch time.  Scheduled sale refreshes are one-shot and may
    take priority over the ordinary interval; ordinary intents inside the final
    interval window are coalesced into that scheduled refresh.
    """

    clock_ns: Callable[[], int] = time.monotonic_ns
    tab_identity: int = 0
    generation: int = 0
    last_dispatch_ns: int | None = None
    next_allowed_ns: int = 0
    pending_reason: str = ""
    pending_priority: str = ""
    in_flight_token: int | None = None
    soft_block_state: str = "CLEAR"
    soft_blocked_until_ns: int = 0
    scheduled_token: str = ""
    scheduled_deadline_ns: int = 0
    scheduled_target_wall: str = ""
    purchase_guard: bool = False
    cancel_reason: str = ""
    trace: deque[dict[str, object]] = field(
        default_factory=lambda: deque(maxlen=REFRESH_TRACE_CAPACITY)
    )
    _sequence: int = 0
    _completed_schedules: deque[str] = field(
        default_factory=lambda: deque(maxlen=SCHEDULE_TOKEN_CAPACITY)
    )
    _active_schedule_for_dispatch: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.trace, deque) or self.trace.maxlen != REFRESH_TRACE_CAPACITY:
            self.trace = deque(self.trace, maxlen=REFRESH_TRACE_CAPACITY)
        if (
            not isinstance(self._completed_schedules, deque)
            or self._completed_schedules.maxlen != SCHEDULE_TOKEN_CAPACITY
        ):
            self._completed_schedules = deque(
                self._completed_schedules,
                maxlen=SCHEDULE_TOKEN_CAPACITY,
            )

    @staticmethod
    def _interval_ns(value: float) -> int:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0
        if not math.isfinite(parsed) or parsed <= 0:
            return 0
        return max(0, round(parsed * NS_PER_SECOND))

    def bind_tab(self, tab: object) -> None:
        identity = id(tab)
        if self.tab_identity in {0, identity}:
            self.tab_identity = identity
            return
        # A coordinator must never migrate between tabs.
        raise RuntimeError("refresh coordinator tab identity changed")

    def arm_scheduled(
        self,
        token: str,
        deadline_ns: int,
        *,
        target_wall: str = "",
    ) -> bool:
        normalized = str(token or "")
        if not normalized or normalized in self._completed_schedules:
            return False
        if normalized == self._active_schedule_for_dispatch:
            return True
        self.scheduled_token = normalized
        self.scheduled_deadline_ns = max(0, int(deadline_ns))
        self.scheduled_target_wall = str(target_wall or "")
        return True

    def cancel_pending(self, reason: str, *, purchase_guard: bool = False) -> None:
        self.pending_reason = ""
        self.pending_priority = ""
        self.scheduled_token = ""
        self.scheduled_deadline_ns = 0
        self.scheduled_target_wall = ""
        self.cancel_reason = str(reason or "cancelled")
        if purchase_guard:
            self.purchase_guard = True

    def reset_purchase_guard(self) -> None:
        self.purchase_guard = False
        self.cancel_reason = ""

    def begin_soft_block(self, blocked_until_ns: int) -> None:
        deadline = max(0, int(blocked_until_ns))
        if self.soft_block_state == "CLEAR":
            self.soft_block_state = "CONFIRMED_WAIT"
            self.soft_blocked_until_ns = deadline
        elif self.soft_block_state == "CONFIRMED_WAIT":
            # Repeated observations cannot shorten or extend the immutable wait.
            return
        elif self.soft_block_state == "RECOVERING":
            # A failed guarded recovery starts a new immutable retry window.
            self.soft_block_state = "CONFIRMED_WAIT"
            self.soft_blocked_until_ns = deadline

    def mark_soft_block_recovering(self) -> None:
        self.soft_block_state = "RECOVERING"

    def clear_soft_block(self) -> None:
        self.soft_block_state = "CLEAR"
        self.soft_blocked_until_ns = 0

    def _record(
        self,
        *,
        now_ns: int,
        reason: str,
        allowed: bool,
        outcome: str,
        priority: str,
        lateness_ms: float | None = None,
        token: int | None = None,
    ) -> None:
        self.trace.append(
            {
                "reason": str(reason or "unspecified"),
                "priority": priority,
                "generation": self.generation,
                "requested_ns": now_ns,
                "start_ns": now_ns if allowed else None,
                "completed_ns": None,
                "allowed": allowed,
                "outcome": outcome,
                "token": token,
                "target_wall": self.scheduled_target_wall if priority == "scheduled" else "",
                "deadline_monotonic_ns": (
                    self.scheduled_deadline_ns if priority == "scheduled" else None
                ),
                "lateness_ms": lateness_ms,
                "next_allowed_ns": self.next_allowed_ns,
            }
        )

    def begin_dispatch(
        self,
        reason: str,
        configured_interval: float,
        *,
        priority: str = "periodic",
    ) -> RefreshDispatchDecision:
        now_ns = int(self.clock_ns())
        interval_ns = self._interval_ns(configured_interval)
        normalized_priority = (
            priority if priority in {"periodic", "scheduled", "recovery"} else "periodic"
        )

        def denied(outcome: str) -> RefreshDispatchDecision:
            self.pending_reason = str(reason or "unspecified")
            self.pending_priority = normalized_priority
            self._record(
                now_ns=now_ns,
                reason=reason,
                allowed=False,
                outcome=outcome,
                priority=normalized_priority,
            )
            return RefreshDispatchDecision(
                False,
                outcome,
                None,
                now_ns,
                self.next_allowed_ns,
            )

        if self.purchase_guard:
            return denied("purchase_guard")
        if self.in_flight_token is not None:
            return denied("refresh_in_flight")
        if self.soft_block_state == "CONFIRMED_WAIT" and normalized_priority != "recovery":
            return denied("soft_block_wait")
        if (
            normalized_priority == "recovery"
            and self.soft_blocked_until_ns > now_ns
        ):
            return denied("soft_block_wait")

        schedule_token = self.scheduled_token if normalized_priority == "scheduled" else ""
        if normalized_priority == "scheduled":
            if not schedule_token:
                return denied("scheduled_not_armed")
            if schedule_token in self._completed_schedules:
                return denied("scheduled_duplicate")
        elif (
            interval_ns > 0
            and self.scheduled_token
            and self.scheduled_deadline_ns > now_ns
            and self.scheduled_deadline_ns - now_ns <= interval_ns
        ):
            return denied("coalesced_into_scheduled")

        if (
            normalized_priority == "periodic"
            and interval_ns > 0
            and self.last_dispatch_ns is not None
            and now_ns < self.next_allowed_ns
        ):
            return denied("minimum_interval")

        self._sequence += 1
        token = self._sequence
        self.in_flight_token = token
        self.pending_reason = ""
        self.pending_priority = ""
        self.last_dispatch_ns = now_ns
        self.next_allowed_ns = now_ns + interval_ns
        self._active_schedule_for_dispatch = schedule_token
        lateness_ms = None
        if normalized_priority == "scheduled" and self.scheduled_deadline_ns:
            lateness_ms = (now_ns - self.scheduled_deadline_ns) / 1_000_000
        self._record(
            now_ns=now_ns,
            reason=reason,
            allowed=True,
            outcome="dispatch_started",
            priority=normalized_priority,
            lateness_ms=lateness_ms,
            token=token,
        )
        return RefreshDispatchDecision(
            True,
            "dispatch_started",
            token,
            now_ns,
            self.next_allowed_ns,
            lateness_ms,
        )

    def complete_dispatch(self, token: int | None, success: bool) -> None:
        if token is None or token != self.in_flight_token:
            return
        completed_ns = int(self.clock_ns())
        for entry in reversed(self.trace):
            if entry.get("token") == token:
                entry["completed_ns"] = completed_ns
                entry["outcome"] = "reloaded" if success else "reload_failed"
                break
        if success:
            self.generation += 1
            if self._active_schedule_for_dispatch:
                if self._active_schedule_for_dispatch not in self._completed_schedules:
                    self._completed_schedules.append(self._active_schedule_for_dispatch)
                if self.scheduled_token == self._active_schedule_for_dispatch:
                    self.scheduled_token = ""
                    self.scheduled_deadline_ns = 0
                    self.scheduled_target_wall = ""
        self._active_schedule_for_dispatch = ""
        self.in_flight_token = None

    def snapshot(self) -> dict[str, object]:
        now_ns = int(self.clock_ns())
        return {
            "tab_identity": self.tab_identity,
            "generation": self.generation,
            "last_dispatch_ns": self.last_dispatch_ns,
            "next_allowed_ns": self.next_allowed_ns,
            "seconds_since_last_dispatch": (
                None
                if self.last_dispatch_ns is None
                else max(0.0, (now_ns - self.last_dispatch_ns) / NS_PER_SECOND)
            ),
            "pending_reason": self.pending_reason,
            "pending_priority": self.pending_priority,
            "in_flight_token": self.in_flight_token,
            "soft_block_state": self.soft_block_state,
            "soft_blocked_until_ns": self.soft_blocked_until_ns,
            "scheduled_token": self.scheduled_token,
            "scheduled_deadline_ns": self.scheduled_deadline_ns,
            "purchase_guard": self.purchase_guard,
            "cancel_reason": self.cancel_reason,
        }
