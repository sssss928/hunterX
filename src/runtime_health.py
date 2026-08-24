from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import math
import os
import threading
import time
from collections import deque
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any, Awaitable, cast
from urllib.parse import urlsplit, urlunsplit

import util
from notification_context import redact_sensitive_text


DEFAULT_RELOAD_TIMEOUT_SECONDS = 10.0
DEFAULT_NAVIGATION_TIMEOUT_SECONDS = 10.0
DEFAULT_EVALUATE_TIMEOUT_SECONDS = 4.0
DEFAULT_READY_STATE_TIMEOUT_SECONDS = 6.0
HEARTBEAT_FILE = "heartbeat.txt"
RUNTIME_LOG_MAX_BYTES = 4 * 1024 * 1024
RUNTIME_LOG_BACKUP_COUNT = 2
RUNTIME_LOG_SIZE_RESYNC_WRITES = 256
RUNTIME_FILE_STATE_CAPACITY = 64
RUNTIME_DIAGNOSTIC_URL_MAX_LENGTH = 512
INTERACTIVE_HEARTBEAT_MIN_INTERVAL_SECONDS = 1.0
BROWSER_ACTION_CAPACITY = 256
EXPECTED_PROGRESS_CAPACITY = 32
EXPECTED_PROGRESS_HISTORY_CAPACITY = 128
EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH = 256
EXPECTED_PROGRESS_ROUTE_MAX_LENGTH = 512
_RUNTIME_LOG_LOCK = threading.RLock()
_RUNTIME_LOG_SIZE_STATE: dict[str, tuple[int, int]] = {}
_HEARTBEAT_LOCK = threading.Lock()
_HEARTBEAT_LAST_WRITE: dict[str, float] = {}
_BROWSER_ACTION_LOCK = threading.Lock()
_BROWSER_ACTIONS: dict[int, tuple[Any, int, str]] = {}
_BROWSER_ACTION_SEQUENCE = 0
_CRITICAL_TRACE_LOCK = threading.Lock()
_CRITICAL_TRACE_CAPACITY = 1000
_CRITICAL_TRACE: deque[dict[str, Any]] = deque(maxlen=_CRITICAL_TRACE_CAPACITY)
_BROWSER_CONNECTION_CLOSED_MARKERS = (
    "connectionclosed",
    "connection closed",
    "no close frame received",
    "websocket is not connected",
    "executor shutdown has been called",
    "browser is already gone",
)


class BrowserFailureKind(str, Enum):
    NONE = "none"
    TRANSIENT_URL_MISS = "transient_url_miss"
    TIMEOUT = "timeout"
    EXECUTION_CONTEXT_LOST = "execution_context_lost"
    TARGET_CLOSED = "target_closed"
    TRANSPORT_CLOSED = "transport_closed"
    RENDERER_STALLED = "renderer_stalled"
    UNKNOWN = "unknown"


class RecoveryLevel(IntEnum):
    NORMAL_RETRY = 0
    REACQUIRE = 1
    TRANSPORT_REBIND = 2
    SAFE_RESTART = 3
    FAIL_CLOSED = 4
    STOP = 5


class RuntimeHealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
    FAIL_CLOSED = "fail_closed"
    STOPPED = "stopped"


class ExpectedProgressKind(str, Enum):
    """A dispatched action whose owner expects bounded observable progress."""

    NAVIGATION = "navigation"
    RELOAD = "reload"
    CANDIDATE_CLICK = "candidate_click"
    SUBMIT = "submit"
    RECOVERY = "recovery"
    LOGIN_RESTORE = "login_restore"


class ExpectedProgressOutcome(str, Enum):
    """Passive observation result; no member performs an action itself."""

    CONFIRMED = "confirmed"
    STALLED_ACTION = "stalled_action"
    SUBMIT_OUTCOME_UNKNOWN = "submit_outcome_unknown"
    PROTECTED_NO_RECOVERY = "protected_no_recovery"
    STALE_OWNER = "stale_owner"
    CANCELLED = "cancelled"


_PROTECTED_PROGRESS_STATES = frozenset(
    {
        "queue",
        "order",
        "order_pending",
        "checkout",
        "checkout_reached",
        "payment",
        "payment_reached",
        "completed",
        "submit_outcome_unknown",
    }
)


def _bounded_progress_value(value: Any, maximum: int) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "")[:maximum]


def _bounded_progress_token(value: Any) -> str:
    """Keep fence equality exact without retaining an unbounded owner token."""

    raw_value = getattr(value, "value", value)
    raw = str(raw_value or "")
    if len(raw) <= EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH:
        return raw
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _bounded_progress_values(values: Iterable[Any] | None, maximum: int) -> frozenset[str]:
    if values is None:
        return frozenset()
    bounded: set[str] = set()
    for value in values:
        normalized = _bounded_progress_value(value, maximum)
        if normalized:
            bounded.add(normalized)
    return frozenset(bounded)


@dataclass(frozen=True, slots=True)
class ExpectedProgressExpectation:
    """Compact metadata mirrored from an existing action owner.

    The observer deliberately stores no tab, DOM node, callback, coroutine or
    platform state object. Owners keep all mutation/retry authority.
    """

    tab_identity: str
    platform_key: str
    attempt_id: str
    attempt_generation: int
    action_owner: str
    action_token: str
    kind: ExpectedProgressKind
    source_route: str
    source_route_generation: int
    acceptable_routes: frozenset[str]
    acceptable_states: frozenset[str]
    minimum_refresh_generation: int | None
    started_at: float
    deadline: float
    submit_sensitive: bool
    reconciliation_owner: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.tab_identity, self.action_owner, self.action_token)


@dataclass(frozen=True, slots=True)
class ExpectedProgressDecision:
    outcome: ExpectedProgressOutcome
    expectation: ExpectedProgressExpectation
    observed_at: float
    reason: str

    @property
    def requires_fail_closed(self) -> bool:
        return self.outcome is ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN

    @property
    def permits_recovery(self) -> bool:
        return self.outcome is ExpectedProgressOutcome.STALLED_ACTION


@dataclass(frozen=True, slots=True)
class ExpectedProgressBinding:
    supervisor: Any
    tab_identity: str
    platform_key: str
    attempt_id: str
    attempt_generation: int


_EXPECTED_PROGRESS_BINDING: contextvars.ContextVar[ExpectedProgressBinding | None] = (
    contextvars.ContextVar("hunterx_expected_progress_binding", default=None)
)
_NO_EXPECTED_PROGRESS_DECISIONS: tuple[ExpectedProgressDecision, ...] = ()


def current_expected_progress_binding() -> ExpectedProgressBinding | None:
    return _EXPECTED_PROGRESS_BINDING.get()


@contextmanager
def bind_expected_progress(
    supervisor: Any,
    *,
    tab_identity: Any,
    platform_key: Any = "",
    attempt_id: Any = "",
    attempt_generation: int = 0,
) -> Iterator[ExpectedProgressBinding]:
    """Bind observation metadata to the current async task only."""

    binding = ExpectedProgressBinding(
        supervisor=supervisor,
        tab_identity=_bounded_progress_value(
            tab_identity,
            EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
        ),
        platform_key=_bounded_progress_value(
            platform_key,
            EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
        ),
        attempt_id=_bounded_progress_value(
            attempt_id,
            EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
        ),
        attempt_generation=max(0, int(attempt_generation or 0)),
    )
    token = _EXPECTED_PROGRESS_BINDING.set(binding)
    try:
        yield binding
    finally:
        _EXPECTED_PROGRESS_BINDING.reset(token)


def _bound_expected_progress_fence_matches(
    binding: ExpectedProgressBinding,
    *,
    tab_identity: Any | None,
    attempt_id: Any | None,
    attempt_generation: int | None,
) -> bool:
    """Reject stale callbacks trying to act through a newer task mapping."""

    if tab_identity is not None and _bounded_progress_value(
        tab_identity,
        EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
    ) != binding.tab_identity:
        return False
    if attempt_id is not None and _bounded_progress_value(
        attempt_id,
        EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
    ) != binding.attempt_id:
        return False
    if attempt_generation is None:
        return True
    try:
        generation = max(0, int(attempt_generation or 0))
    except (TypeError, ValueError):
        return False
    return generation == binding.attempt_generation


def arm_bound_expected_progress(
    *,
    action_owner: Any,
    action_token: Any,
    kind: ExpectedProgressKind | str,
    deadline: float,
    tab_identity: Any | None = None,
    platform_key: Any | None = None,
    attempt_id: Any | None = None,
    attempt_generation: int | None = None,
    source_route: Any = "",
    source_route_generation: int = 0,
    acceptable_routes: Iterable[Any] | None = None,
    acceptable_states: Iterable[Any] | None = None,
    minimum_refresh_generation: int | None = None,
    submit_sensitive: bool = False,
    reconciliation_owner: Any = "",
    now: float | None = None,
) -> ExpectedProgressExpectation | None:
    """Arm the current task's passive observer with an exact owner fence.

    Platform owners may override attempt metadata only when they already own a
    stronger authoritative token (for example, PlatformEngine's submit token).
    The supervisor itself remains task-local and is never retained by an
    adapter, pending-navigation object or browser callback.
    """

    binding = current_expected_progress_binding()
    if binding is None or not _bound_expected_progress_fence_matches(
        binding,
        tab_identity=tab_identity,
        attempt_id=attempt_id,
        attempt_generation=attempt_generation,
    ):
        return None
    if platform_key is not None and _bounded_progress_value(
        platform_key,
        EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
    ) != binding.platform_key:
        return None
    expectation = binding.supervisor.arm_expected_progress(
        tab_identity=binding.tab_identity if tab_identity is None else tab_identity,
        platform_key=binding.platform_key if platform_key is None else platform_key,
        attempt_id=binding.attempt_id if attempt_id is None else attempt_id,
        attempt_generation=(
            binding.attempt_generation
            if attempt_generation is None
            else attempt_generation
        ),
        action_owner=action_owner,
        action_token=action_token,
        kind=kind,
        deadline=deadline,
        source_route=source_route,
        source_route_generation=source_route_generation,
        acceptable_routes=acceptable_routes,
        acceptable_states=acceptable_states,
        minimum_refresh_generation=minimum_refresh_generation,
        submit_sensitive=submit_sensitive,
        reconciliation_owner=reconciliation_owner,
        now=now,
    )
    return cast(ExpectedProgressExpectation | None, expectation)


def confirm_bound_expected_progress(
    *,
    action_owner: Any,
    action_token: Any,
    tab_identity: Any | None = None,
    attempt_id: Any | None = None,
    attempt_generation: int | None = None,
    reason: Any = "owner_confirmed",
    now: float | None = None,
) -> bool:
    binding = current_expected_progress_binding()
    if binding is None or not _bound_expected_progress_fence_matches(
        binding,
        tab_identity=tab_identity,
        attempt_id=attempt_id,
        attempt_generation=attempt_generation,
    ):
        return False
    return bool(
        binding.supervisor.confirm_expected_progress(
            tab_identity=(
                binding.tab_identity if tab_identity is None else tab_identity
            ),
            attempt_id=binding.attempt_id if attempt_id is None else attempt_id,
            attempt_generation=(
                binding.attempt_generation
                if attempt_generation is None
                else attempt_generation
            ),
            action_owner=action_owner,
            action_token=action_token,
            reason=reason,
            now=now,
        )
    )


def fail_bound_expected_progress(
    *,
    action_owner: Any,
    action_token: Any,
    tab_identity: Any | None = None,
    attempt_id: Any | None = None,
    attempt_generation: int | None = None,
    reason: Any = "owner_reported_failure",
    protected: bool = False,
    now: float | None = None,
) -> ExpectedProgressDecision | None:
    binding = current_expected_progress_binding()
    if binding is None or not _bound_expected_progress_fence_matches(
        binding,
        tab_identity=tab_identity,
        attempt_id=attempt_id,
        attempt_generation=attempt_generation,
    ):
        return None
    decision = binding.supervisor.fail_expected_progress(
        tab_identity=binding.tab_identity if tab_identity is None else tab_identity,
        attempt_id=binding.attempt_id if attempt_id is None else attempt_id,
        attempt_generation=(
            binding.attempt_generation
            if attempt_generation is None
            else attempt_generation
        ),
        action_owner=action_owner,
        action_token=action_token,
        reason=reason,
        protected=protected,
        now=now,
    )
    return cast(ExpectedProgressDecision | None, decision)


def cancel_bound_expected_progress(
    *,
    action_owner: Any,
    action_token: Any,
    tab_identity: Any | None = None,
    attempt_id: Any | None = None,
    attempt_generation: int | None = None,
    reason: Any = "owner_cancelled",
    now: float | None = None,
) -> bool:
    binding = current_expected_progress_binding()
    if binding is None or not _bound_expected_progress_fence_matches(
        binding,
        tab_identity=tab_identity,
        attempt_id=attempt_id,
        attempt_generation=attempt_generation,
    ):
        return False
    return bool(
        binding.supervisor.cancel_expected_progress_action(
            tab_identity=(
                binding.tab_identity if tab_identity is None else tab_identity
            ),
            attempt_id=binding.attempt_id if attempt_id is None else attempt_id,
            attempt_generation=(
                binding.attempt_generation
                if attempt_generation is None
                else attempt_generation
            ),
            action_owner=action_owner,
            action_token=action_token,
            reason=reason,
            now=now,
        )
    )


_TAB_FAILURE_KIND_ATTRIBUTE = "_hunterx_last_url_failure_kind"


def set_tab_failure_kind(tab: Any, kind: BrowserFailureKind) -> None:
    """Expose the last URL-read failure without changing the legacy return API."""

    if tab is None:
        return
    with suppress(Exception):
        setattr(tab, _TAB_FAILURE_KIND_ATTRIBUTE, kind)


def get_tab_failure_kind(tab: Any) -> BrowserFailureKind:
    if tab is None:
        return BrowserFailureKind.UNKNOWN
    try:
        value = getattr(tab, _TAB_FAILURE_KIND_ATTRIBUTE, BrowserFailureKind.NONE)
    except Exception:
        return BrowserFailureKind.UNKNOWN
    if isinstance(value, BrowserFailureKind):
        return value
    try:
        return BrowserFailureKind(str(value))
    except ValueError:
        return BrowserFailureKind.UNKNOWN


@dataclass(frozen=True)
class RecoveryPlan:
    level: RecoveryLevel
    reason: str


@dataclass(frozen=True)
class RuntimeHealthSnapshot:
    state: RuntimeHealthState
    consecutive_url_failures: int
    cdp_timeout_count: int
    reconnect_count: int
    recovery_count: int
    last_loop_at: float
    last_url_success_at: float
    last_dom_progress_at: float
    last_platform_dispatch_at: float
    last_refresh_attempt_at: float
    last_action_success_at: float
    last_failure_kind: BrowserFailureKind
    active_expected_progress_count: int
    unresolved_expected_progress_count: int
    expected_progress_stall_count: int
    expected_progress_submit_unknown_count: int
    last_expected_progress_outcome: str


def classify_browser_exception(exc: BaseException | None) -> BrowserFailureKind:
    """Classify browser failures without treating every exception as terminal."""

    if exc is None:
        return BrowserFailureKind.NONE
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = f"{type(current).__module__}.{type(current).__name__}".casefold()
        message = str(current).casefold()
        combined = f"{name} {message}"
        if any(marker in combined for marker in ("target closed", "tab closed")):
            return BrowserFailureKind.TARGET_CLOSED
        if any(
            marker in combined
            for marker in (
                "execution context was destroyed",
                "cannot find context",
                "context destroyed",
                "inspected target navigated or closed",
            )
        ):
            return BrowserFailureKind.EXECUTION_CONTEXT_LOST
        if isinstance(current, (asyncio.TimeoutError, TimeoutError)):
            return BrowserFailureKind.TIMEOUT
        if (
            ("websockets" in name and "connectionclosed" in name)
            or any(marker in combined for marker in _BROWSER_CONNECTION_CLOSED_MARKERS)
        ):
            return BrowserFailureKind.TRANSPORT_CLOSED
        current = current.__cause__ or current.__context__
    return BrowserFailureKind.UNKNOWN


class RuntimeHealthSupervisor:
    """Cheap monotonic state tracker and bounded recovery circuit breaker."""

    def __init__(
        self,
        *,
        url_failure_threshold: int = 3,
        max_recovery_attempts: int = 3,
        recovery_cooldown_seconds: float = 2.0,
    ) -> None:
        self.url_failure_threshold = max(1, int(url_failure_threshold))
        self.max_recovery_attempts = max(1, int(max_recovery_attempts))
        self.recovery_cooldown_seconds = max(0.0, float(recovery_cooldown_seconds))
        self.state = RuntimeHealthState.HEALTHY
        self.consecutive_url_failures = 0
        self.cdp_timeout_count = 0
        self.reconnect_count = 0
        self.recovery_count = 0
        self.last_loop_at = 0.0
        self.last_url_success_at = 0.0
        self.last_dom_progress_at = 0.0
        self.last_platform_dispatch_at = 0.0
        self.last_refresh_attempt_at = 0.0
        self.last_action_success_at = 0.0
        self.last_failure_kind = BrowserFailureKind.NONE
        self._consecutive_recovery_failures = 0
        self._next_recovery_at = 0.0
        self._recovery_generations: set[int] = set()
        self._expected_progress: dict[
            tuple[str, str, str], ExpectedProgressExpectation
        ] = {}
        self._expected_progress_faults: dict[
            tuple[str, str, str], ExpectedProgressDecision
        ] = {}
        self._expected_progress_history: deque[ExpectedProgressDecision] = deque(
            maxlen=EXPECTED_PROGRESS_HISTORY_CAPACITY
        )
        self._expected_progress_failure_count = 0
        self.expected_progress_stall_count = 0
        self.expected_progress_submit_unknown_count = 0
        self.last_expected_progress_outcome = ""

    @staticmethod
    def _now(now: float | None) -> float:
        return time.monotonic() if now is None else float(now)

    def record_loop(self, now: float | None = None) -> None:
        self.last_loop_at = time.monotonic() if now is None else now

    def record_url_success(self, now: float | None = None) -> None:
        current = self._now(now)
        self.last_url_success_at = current
        self.consecutive_url_failures = 0
        self.last_failure_kind = BrowserFailureKind.NONE
        if self._has_unresolved_progress_failure():
            self.last_failure_kind = BrowserFailureKind.RENDERER_STALLED
            if self.state not in {
                RuntimeHealthState.FAIL_CLOSED,
                RuntimeHealthState.STOPPED,
            }:
                self.state = RuntimeHealthState.DEGRADED
        elif self.state not in {
            RuntimeHealthState.FAIL_CLOSED,
            RuntimeHealthState.STOPPED,
        }:
            self.state = RuntimeHealthState.HEALTHY

    def record_url_failure(
        self,
        kind: BrowserFailureKind = BrowserFailureKind.TRANSIENT_URL_MISS,
        *,
        now: float | None = None,
    ) -> None:
        self.record_loop(now)
        self.consecutive_url_failures += 1
        self.last_failure_kind = kind
        if kind is BrowserFailureKind.TIMEOUT:
            self.cdp_timeout_count += 1
        self.state = RuntimeHealthState.DEGRADED

    def record_dom_progress(self, now: float | None = None) -> None:
        self.last_dom_progress_at = self._now(now)

    def record_platform_dispatch(self, now: float | None = None) -> None:
        self.last_platform_dispatch_at = self._now(now)

    def record_refresh_attempt(self, now: float | None = None) -> None:
        self.last_refresh_attempt_at = self._now(now)

    @staticmethod
    def _progress_kind(value: ExpectedProgressKind | str) -> ExpectedProgressKind:
        if isinstance(value, ExpectedProgressKind):
            return value
        return ExpectedProgressKind(str(value or ""))

    @staticmethod
    def _same_attempt(
        expectation: ExpectedProgressExpectation,
        *,
        tab_identity: str,
        attempt_id: str,
        attempt_generation: int,
    ) -> bool:
        return (
            expectation.tab_identity == tab_identity
            and expectation.attempt_id == attempt_id
            and expectation.attempt_generation == attempt_generation
        )

    @staticmethod
    def _attempt_fence_matches(
        expectation: ExpectedProgressExpectation,
        *,
        attempt_id: str,
        attempt_generation: int,
    ) -> bool:
        if expectation.attempt_id and expectation.attempt_id != attempt_id:
            return False
        if (
            expectation.attempt_generation > 0
            and expectation.attempt_generation != attempt_generation
        ):
            return False
        return True

    @staticmethod
    def _decision(
        expectation: ExpectedProgressExpectation,
        outcome: ExpectedProgressOutcome,
        observed_at: float,
        reason: str,
    ) -> ExpectedProgressDecision:
        return ExpectedProgressDecision(
            outcome=outcome,
            expectation=expectation,
            observed_at=observed_at,
            reason=_bounded_progress_value(
                reason,
                EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
            ),
        )

    def _record_progress_decision(self, decision: ExpectedProgressDecision) -> None:
        self._expected_progress_history.append(decision)
        self.last_expected_progress_outcome = decision.outcome.value

    def _has_unresolved_progress_failure(self) -> bool:
        return self._expected_progress_failure_count > 0

    def _refresh_state_after_progress_resolution(self) -> None:
        if self._has_unresolved_progress_failure():
            return
        if self.state in {
            RuntimeHealthState.FAIL_CLOSED,
            RuntimeHealthState.STOPPED,
        }:
            return
        if self.consecutive_url_failures:
            self.state = RuntimeHealthState.DEGRADED
            return
        self.last_failure_kind = BrowserFailureKind.NONE
        self.state = RuntimeHealthState.HEALTHY

    @property
    def active_expected_progress_count(self) -> int:
        return len(self._expected_progress)

    @property
    def unresolved_expected_progress_count(self) -> int:
        return len(self._expected_progress_faults)

    @property
    def expected_progress_history(self) -> tuple[ExpectedProgressDecision, ...]:
        return tuple(self._expected_progress_history)

    def bind_expected_progress(
        self,
        *,
        tab_identity: Any,
        platform_key: Any = "",
        attempt_id: Any = "",
        attempt_generation: int = 0,
    ) -> Any:
        return bind_expected_progress(
            self,
            tab_identity=tab_identity,
            platform_key=platform_key,
            attempt_id=attempt_id,
            attempt_generation=attempt_generation,
        )

    def arm_expected_progress(
        self,
        *,
        tab_identity: Any,
        action_owner: Any,
        action_token: Any,
        kind: ExpectedProgressKind | str,
        deadline: float,
        platform_key: Any = "",
        attempt_id: Any = "",
        attempt_generation: int = 0,
        source_route: Any = "",
        source_route_generation: int = 0,
        acceptable_routes: Iterable[Any] | None = None,
        acceptable_states: Iterable[Any] | None = None,
        minimum_refresh_generation: int | None = None,
        submit_sensitive: bool = False,
        reconciliation_owner: Any = "",
        now: float | None = None,
    ) -> ExpectedProgressExpectation | None:
        """Mirror one owner action without taking over its retry lifecycle."""

        current = self._now(now)
        bounded_tab = _bounded_progress_value(
            tab_identity,
            EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
        )
        bounded_owner = _bounded_progress_value(
            action_owner,
            EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
        )
        bounded_token = _bounded_progress_token(action_token)
        if not bounded_tab or not bounded_owner or not bounded_token:
            return None
        bounded_attempt = _bounded_progress_value(
            attempt_id,
            EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
        )
        generation = max(0, int(attempt_generation or 0))
        try:
            normalized_deadline = float(deadline)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(normalized_deadline):
            return None
        if normalized_deadline < current:
            normalized_deadline = current
        normalized_refresh_generation = None
        if minimum_refresh_generation is not None:
            try:
                normalized_refresh_generation = max(
                    0,
                    int(minimum_refresh_generation),
                )
            except (TypeError, ValueError):
                return None
        try:
            normalized_kind = self._progress_kind(kind)
        except ValueError:
            return None
        key = (bounded_tab, bounded_owner, bounded_token)
        existing = self._expected_progress.get(key)
        if existing is not None:
            # An owner token is immutable. Idempotent arms return the original
            # fence rather than silently changing its deadline/semantics.
            if (
                existing.attempt_id == bounded_attempt
                and existing.attempt_generation == generation
                and existing.kind is normalized_kind
                and existing.submit_sensitive is bool(submit_sensitive)
            ):
                return existing
            return None
        existing_fault = self._expected_progress_faults.get(key)
        if existing_fault is not None:
            expectation = existing_fault.expectation
            if (
                expectation.attempt_id == bounded_attempt
                and expectation.attempt_generation == generation
                and expectation.kind is normalized_kind
                and expectation.submit_sensitive is bool(submit_sensitive)
            ):
                return expectation
            return None

        same_attempt_active = [
            item
            for item in self._expected_progress.values()
            if self._same_attempt(
                item,
                tab_identity=bounded_tab,
                attempt_id=bounded_attempt,
                attempt_generation=generation,
            )
        ]
        same_attempt_faults = [
            decision.expectation
            for decision in self._expected_progress_faults.values()
            if self._same_attempt(
                decision.expectation,
                tab_identity=bounded_tab,
                attempt_id=bounded_attempt,
                attempt_generation=generation,
            )
        ]
        if any(
            item.submit_sensitive
            for item in (*same_attempt_active, *same_attempt_faults)
        ):
            # A submit fence is stronger than navigation/click/reload metadata
            # and may only be resolved by its exact owner/token.
            return None
        if submit_sensitive:
            # Submission supersedes weaker pre-submit expectations from this
            # exact attempt, but never expectations owned by another attempt.
            for item in same_attempt_active:
                self._expected_progress.pop(item.key, None)
                cancelled = self._decision(
                    item,
                    ExpectedProgressOutcome.CANCELLED,
                    current,
                    "superseded_by_submit",
                )
                self._record_progress_decision(cancelled)

        if (
            len(self._expected_progress) + len(self._expected_progress_faults)
            >= EXPECTED_PROGRESS_CAPACITY
        ):
            # Never evict an active fence merely to make room for a new one.
            return None
        expectation = ExpectedProgressExpectation(
            tab_identity=bounded_tab,
            platform_key=_bounded_progress_value(
                platform_key,
                EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
            ),
            attempt_id=bounded_attempt,
            attempt_generation=generation,
            action_owner=bounded_owner,
            action_token=bounded_token,
            kind=normalized_kind,
            source_route=_bounded_progress_value(
                source_route,
                EXPECTED_PROGRESS_ROUTE_MAX_LENGTH,
            ),
            source_route_generation=max(0, int(source_route_generation or 0)),
            acceptable_routes=_bounded_progress_values(
                acceptable_routes,
                EXPECTED_PROGRESS_ROUTE_MAX_LENGTH,
            ),
            acceptable_states=_bounded_progress_values(
                acceptable_states,
                EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
            ),
            minimum_refresh_generation=normalized_refresh_generation,
            started_at=current,
            deadline=normalized_deadline,
            submit_sensitive=bool(submit_sensitive),
            reconciliation_owner=_bounded_progress_value(
                reconciliation_owner or bounded_owner,
                EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
            ),
        )
        self._expected_progress[key] = expectation
        return expectation

    def observe_expected_progress(
        self,
        *,
        tab_identity: Any,
        attempt_id: Any = "",
        attempt_generation: int = 0,
        current_route: Any = "",
        route_generation: int = 0,
        page_state: Any = "",
        refresh_generation: int | None = None,
        protected: bool = False,
        now: float | None = None,
    ) -> tuple[ExpectedProgressDecision, ...]:
        """Observe already-available runtime metadata; never probe the browser."""

        # This branch is the 50 ms idle hot path. Keep it ahead of all string
        # conversion, URL parsing, allocation, logging and browser interaction.
        if not self._expected_progress and not self._expected_progress_faults:
            return _NO_EXPECTED_PROGRESS_DECISIONS

        current = self._now(now)
        bounded_tab = _bounded_progress_value(
            tab_identity,
            EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
        )
        bounded_attempt = _bounded_progress_value(
            attempt_id,
            EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
        )
        generation = max(0, int(attempt_generation or 0))
        route = _bounded_progress_value(
            current_route,
            EXPECTED_PROGRESS_ROUTE_MAX_LENGTH,
        )
        state = _bounded_progress_value(
            page_state,
            EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
        )
        route_generation_value = max(0, int(route_generation or 0))
        refresh_generation_value = None
        if refresh_generation is not None:
            try:
                refresh_generation_value = max(0, int(refresh_generation))
            except (TypeError, ValueError):
                refresh_generation_value = None
        decisions: list[ExpectedProgressDecision] = []

        # A fault belongs to the exact attempt that dispatched the action. Once
        # the authoritative engine presents a newer attempt on the same tab,
        # the old decision becomes diagnostic history only and must not affect
        # recovery planning for the new generation.
        stale_fault_removed = False
        for key, fault in tuple(self._expected_progress_faults.items()):
            expectation = fault.expectation
            if expectation.tab_identity != bounded_tab:
                continue
            if self._attempt_fence_matches(
                expectation,
                attempt_id=bounded_attempt,
                attempt_generation=generation,
            ):
                continue
            self._expected_progress_faults.pop(key, None)
            if fault.outcome in {
                ExpectedProgressOutcome.STALLED_ACTION,
                ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN,
            }:
                self._expected_progress_failure_count = max(
                    0,
                    self._expected_progress_failure_count - 1,
                )
            decision = self._decision(
                expectation,
                ExpectedProgressOutcome.STALE_OWNER,
                current,
                "fault_attempt_fence_changed",
            )
            self._record_progress_decision(decision)
            decisions.append(decision)
            stale_fault_removed = True
        if stale_fault_removed:
            self._refresh_state_after_progress_resolution()

        for key, expectation in tuple(self._expected_progress.items()):
            if expectation.tab_identity != bounded_tab:
                continue
            if not self._attempt_fence_matches(
                expectation,
                attempt_id=bounded_attempt,
                attempt_generation=generation,
            ):
                self._expected_progress.pop(key, None)
                decision = self._decision(
                    expectation,
                    ExpectedProgressOutcome.STALE_OWNER,
                    current,
                    "attempt_fence_changed",
                )
                self._record_progress_decision(decision)
                decisions.append(decision)
                continue

            route_confirmed = bool(
                expectation.acceptable_routes
                and route in expectation.acceptable_routes
            )
            state_confirmed = bool(
                expectation.acceptable_states
                and state in expectation.acceptable_states
            )
            refresh_confirmed = bool(
                expectation.minimum_refresh_generation is not None
                and refresh_generation_value is not None
                and refresh_generation_value
                >= expectation.minimum_refresh_generation
            )
            generation_confirmed = bool(
                not expectation.acceptable_routes
                and not expectation.acceptable_states
                and expectation.minimum_refresh_generation is None
                and route_generation_value > expectation.source_route_generation
            )
            if (
                route_confirmed
                or state_confirmed
                or refresh_confirmed
                or generation_confirmed
            ):
                self._expected_progress.pop(key, None)
                decision = self._decision(
                    expectation,
                    ExpectedProgressOutcome.CONFIRMED,
                    current,
                    "expected_progress_observed",
                )
                self._record_progress_decision(decision)
                self.last_dom_progress_at = current
                self.last_action_success_at = current
                decisions.append(decision)
                continue

            if current < expectation.deadline:
                continue

            self._expected_progress.pop(key, None)
            is_protected = bool(protected or state in _PROTECTED_PROGRESS_STATES)
            if is_protected:
                outcome = ExpectedProgressOutcome.PROTECTED_NO_RECOVERY
                reason = "protected_route_no_recovery"
            elif expectation.submit_sensitive:
                outcome = ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN
                reason = "submit_deadline_expired"
            else:
                outcome = ExpectedProgressOutcome.STALLED_ACTION
                reason = "expected_progress_deadline_expired"
            decision = self._decision(expectation, outcome, current, reason)
            self._expected_progress_faults[key] = decision
            self._record_progress_decision(decision)
            decisions.append(decision)
            if outcome is ExpectedProgressOutcome.STALLED_ACTION:
                self.expected_progress_stall_count += 1
            elif outcome is ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN:
                self.expected_progress_submit_unknown_count += 1
            if outcome in {
                ExpectedProgressOutcome.STALLED_ACTION,
                ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN,
            }:
                self._expected_progress_failure_count += 1
                self.last_failure_kind = BrowserFailureKind.RENDERER_STALLED
                if self.state not in {
                    RuntimeHealthState.FAIL_CLOSED,
                    RuntimeHealthState.STOPPED,
                }:
                    self.state = RuntimeHealthState.DEGRADED

        return tuple(decisions) if decisions else _NO_EXPECTED_PROGRESS_DECISIONS

    def confirm_expected_progress(
        self,
        *,
        tab_identity: Any,
        action_owner: Any,
        action_token: Any,
        attempt_id: Any = "",
        attempt_generation: int = 0,
        reason: Any = "owner_confirmed",
        now: float | None = None,
    ) -> bool:
        bounded_tab = _bounded_progress_value(
            tab_identity,
            EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
        )
        key = (
            bounded_tab,
            _bounded_progress_value(
                action_owner,
                EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
            ),
            _bounded_progress_token(action_token),
        )
        expectation = self._expected_progress.get(key)
        if expectation is None:
            fault = self._expected_progress_faults.get(key)
            expectation = fault.expectation if fault is not None else None
        if expectation is None or not self._attempt_fence_matches(
            expectation,
            attempt_id=_bounded_progress_value(
                attempt_id,
                EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
            ),
            attempt_generation=max(0, int(attempt_generation or 0)),
        ):
            return False
        self._expected_progress.pop(key, None)
        resolved_fault = self._expected_progress_faults.pop(key, None)
        if (
            resolved_fault is not None
            and resolved_fault.outcome
            in {
                ExpectedProgressOutcome.STALLED_ACTION,
                ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN,
            }
        ):
            self._expected_progress_failure_count = max(
                0,
                self._expected_progress_failure_count - 1,
            )
        current = self._now(now)
        decision = self._decision(
            expectation,
            ExpectedProgressOutcome.CONFIRMED,
            current,
            str(reason or "owner_confirmed"),
        )
        self._record_progress_decision(decision)
        self.last_action_success_at = current
        self._refresh_state_after_progress_resolution()
        return True

    def fail_expected_progress(
        self,
        *,
        tab_identity: Any,
        action_owner: Any,
        action_token: Any,
        attempt_id: Any = "",
        attempt_generation: int = 0,
        reason: Any = "owner_reported_failure",
        protected: bool = False,
        now: float | None = None,
    ) -> ExpectedProgressDecision | None:
        bounded_tab = _bounded_progress_value(
            tab_identity,
            EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
        )
        key = (
            bounded_tab,
            _bounded_progress_value(
                action_owner,
                EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
            ),
            _bounded_progress_token(action_token),
        )
        expectation = self._expected_progress.get(key)
        if expectation is None or not self._attempt_fence_matches(
            expectation,
            attempt_id=_bounded_progress_value(
                attempt_id,
                EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
            ),
            attempt_generation=max(0, int(attempt_generation or 0)),
        ):
            return None
        current = self._now(now)
        if protected:
            outcome = ExpectedProgressOutcome.PROTECTED_NO_RECOVERY
        elif expectation.submit_sensitive:
            outcome = ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN
        else:
            outcome = ExpectedProgressOutcome.STALLED_ACTION
        decision = self._decision(
            expectation,
            outcome,
            current,
            str(reason or "owner_reported_failure"),
        )
        self._expected_progress.pop(key, None)
        self._expected_progress_faults[key] = decision
        self._record_progress_decision(decision)
        if outcome is ExpectedProgressOutcome.STALLED_ACTION:
            self.expected_progress_stall_count += 1
        elif outcome is ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN:
            self.expected_progress_submit_unknown_count += 1
        if outcome in {
            ExpectedProgressOutcome.STALLED_ACTION,
            ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN,
        }:
            self._expected_progress_failure_count += 1
            self.last_failure_kind = BrowserFailureKind.RENDERER_STALLED
            if self.state not in {
                RuntimeHealthState.FAIL_CLOSED,
                RuntimeHealthState.STOPPED,
            }:
                self.state = RuntimeHealthState.DEGRADED
        return decision

    def cancel_expected_progress_action(
        self,
        *,
        tab_identity: Any,
        action_owner: Any,
        action_token: Any,
        attempt_id: Any = "",
        attempt_generation: int = 0,
        reason: Any = "owner_cancelled",
        now: float | None = None,
    ) -> bool:
        """Cancel one exact owner/token without weakening sibling fences."""

        bounded_tab = _bounded_progress_value(
            tab_identity,
            EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
        )
        key = (
            bounded_tab,
            _bounded_progress_value(
                action_owner,
                EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
            ),
            _bounded_progress_token(action_token),
        )
        expectation = self._expected_progress.get(key)
        fault = self._expected_progress_faults.get(key)
        if expectation is None and fault is not None:
            expectation = fault.expectation
        if expectation is None or not self._attempt_fence_matches(
            expectation,
            attempt_id=_bounded_progress_value(
                attempt_id,
                EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
            ),
            attempt_generation=max(0, int(attempt_generation or 0)),
        ):
            return False
        self._expected_progress.pop(key, None)
        resolved_fault = self._expected_progress_faults.pop(key, None)
        if (
            resolved_fault is not None
            and resolved_fault.outcome
            in {
                ExpectedProgressOutcome.STALLED_ACTION,
                ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN,
            }
        ):
            self._expected_progress_failure_count = max(
                0,
                self._expected_progress_failure_count - 1,
            )
        current = self._now(now)
        decision = self._decision(
            expectation,
            ExpectedProgressOutcome.CANCELLED,
            current,
            str(reason or "owner_cancelled"),
        )
        self._record_progress_decision(decision)
        self._refresh_state_after_progress_resolution()
        return True

    def cancel_expected_progress(
        self,
        *,
        tab_identity: Any,
        attempt_id: Any,
        attempt_generation: int,
        reason: Any = "attempt_cancelled",
        now: float | None = None,
    ) -> int:
        bounded_tab = _bounded_progress_value(
            tab_identity,
            EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
        )
        bounded_attempt = _bounded_progress_value(
            attempt_id,
            EXPECTED_PROGRESS_IDENTITY_MAX_LENGTH,
        )
        generation = max(0, int(attempt_generation or 0))
        current = self._now(now)
        removed = 0
        for key, expectation in tuple(self._expected_progress.items()):
            if not self._same_attempt(
                expectation,
                tab_identity=bounded_tab,
                attempt_id=bounded_attempt,
                attempt_generation=generation,
            ):
                continue
            self._expected_progress.pop(key, None)
            removed += 1
            decision = self._decision(
                expectation,
                ExpectedProgressOutcome.CANCELLED,
                current,
                str(reason or "attempt_cancelled"),
            )
            self._record_progress_decision(decision)
        for key, fault in tuple(self._expected_progress_faults.items()):
            expectation = fault.expectation
            if not self._same_attempt(
                expectation,
                tab_identity=bounded_tab,
                attempt_id=bounded_attempt,
                attempt_generation=generation,
            ):
                continue
            self._expected_progress_faults.pop(key, None)
            if fault.outcome in {
                ExpectedProgressOutcome.STALLED_ACTION,
                ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN,
            }:
                self._expected_progress_failure_count = max(
                    0,
                    self._expected_progress_failure_count - 1,
                )
            removed += 1
            decision = self._decision(
                expectation,
                ExpectedProgressOutcome.CANCELLED,
                current,
                str(reason or "attempt_cancelled"),
            )
            self._record_progress_decision(decision)
        self._refresh_state_after_progress_resolution()
        return removed

    def plan_recovery(
        self,
        page_class: Any,
        attempt_state: Any,
        *,
        manual_close: bool = False,
        now: float | None = None,
    ) -> RecoveryPlan:
        del now
        from attempt_lifecycle import AttemptState, SUBMIT_PROTECTED_STATES
        from page_classifier import PageClass

        if manual_close or self.last_failure_kind is BrowserFailureKind.TARGET_CLOSED:
            self.state = RuntimeHealthState.STOPPED
            return RecoveryPlan(RecoveryLevel.STOP, "manual_target_close")
        progress_fault = next(
            reversed(self._expected_progress_faults.values()),
            None,
        )
        if progress_fault is not None:
            if progress_fault.outcome in {
                ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN,
                ExpectedProgressOutcome.PROTECTED_NO_RECOVERY,
            }:
                return RecoveryPlan(
                    RecoveryLevel.FAIL_CLOSED,
                    progress_fault.outcome.value,
                )
            if progress_fault.outcome is ExpectedProgressOutcome.STALLED_ACTION:
                if progress_fault.expectation.submit_sensitive:
                    return RecoveryPlan(
                        RecoveryLevel.FAIL_CLOSED,
                        "submit_outcome_unknown",
                    )
                if page_class in {
                    PageClass.ACTIVITY,
                    PageClass.DATE,
                    PageClass.AREA,
                    PageClass.HOME,
                }:
                    return RecoveryPlan(
                        RecoveryLevel.REACQUIRE,
                        "expected_action_stalled",
                    )
                return RecoveryPlan(
                    RecoveryLevel.FAIL_CLOSED,
                    "stalled_action_unsafe_context",
                )
        if attempt_state in {
            AttemptState.SUBMIT_OUTCOME_UNKNOWN,
            AttemptState.COMPLETED,
        }:
            return RecoveryPlan(RecoveryLevel.FAIL_CLOSED, "protected_submit_outcome")
        if (
            attempt_state in SUBMIT_PROTECTED_STATES
            and self.last_failure_kind not in {
                BrowserFailureKind.TRANSPORT_CLOSED,
                BrowserFailureKind.EXECUTION_CONTEXT_LOST,
            }
        ):
            return RecoveryPlan(RecoveryLevel.FAIL_CLOSED, "protected_transaction")
        if self.last_failure_kind is BrowserFailureKind.TRANSPORT_CLOSED:
            return RecoveryPlan(RecoveryLevel.TRANSPORT_REBIND, "transport_closed")
        if self.last_failure_kind is BrowserFailureKind.EXECUTION_CONTEXT_LOST:
            return RecoveryPlan(RecoveryLevel.REACQUIRE, "execution_context_lost")
        if self.consecutive_url_failures < self.url_failure_threshold:
            return RecoveryPlan(RecoveryLevel.NORMAL_RETRY, "transient_url_miss")
        if page_class in {PageClass.ACTIVITY, PageClass.DATE, PageClass.AREA, PageClass.HOME}:
            return RecoveryPlan(RecoveryLevel.REACQUIRE, "persistent_url_miss")
        self.state = RuntimeHealthState.FAIL_CLOSED
        return RecoveryPlan(RecoveryLevel.FAIL_CLOSED, "unsafe_recovery_context")

    def begin_recovery(self, *, generation: int, now: float | None = None) -> bool:
        current = self._now(now)
        generation = int(generation)
        if (
            self._consecutive_recovery_failures >= self.max_recovery_attempts
            or current < self._next_recovery_at
            or generation in self._recovery_generations
        ):
            return False
        self._recovery_generations.add(generation)
        if len(self._recovery_generations) > 64:
            self._recovery_generations = {generation}
        self.state = RuntimeHealthState.RECOVERING
        self.recovery_count += 1
        return True

    def complete_recovery(self, success: bool, *, now: float | None = None) -> None:
        current = self._now(now)
        if success:
            self._consecutive_recovery_failures = 0
            self.consecutive_url_failures = 0
            self.last_failure_kind = BrowserFailureKind.NONE
            self.last_action_success_at = current
            self.reconnect_count += 1
            self.state = RuntimeHealthState.HEALTHY
            self._next_recovery_at = 0.0
            return
        self._consecutive_recovery_failures += 1
        self._next_recovery_at = current + self.recovery_cooldown_seconds
        self.state = (
            RuntimeHealthState.FAIL_CLOSED
            if self._consecutive_recovery_failures >= self.max_recovery_attempts
            else RuntimeHealthState.DEGRADED
        )

    def snapshot(self) -> RuntimeHealthSnapshot:
        return RuntimeHealthSnapshot(
            state=self.state,
            consecutive_url_failures=self.consecutive_url_failures,
            cdp_timeout_count=self.cdp_timeout_count,
            reconnect_count=self.reconnect_count,
            recovery_count=self.recovery_count,
            last_loop_at=self.last_loop_at,
            last_url_success_at=self.last_url_success_at,
            last_dom_progress_at=self.last_dom_progress_at,
            last_platform_dispatch_at=self.last_platform_dispatch_at,
            last_refresh_attempt_at=self.last_refresh_attempt_at,
            last_action_success_at=self.last_action_success_at,
            last_failure_kind=self.last_failure_kind,
            active_expected_progress_count=len(self._expected_progress),
            unresolved_expected_progress_count=len(
                self._expected_progress_faults
            ),
            expected_progress_stall_count=self.expected_progress_stall_count,
            expected_progress_submit_unknown_count=(
                self.expected_progress_submit_unknown_count
            ),
            last_expected_progress_outcome=self.last_expected_progress_outcome,
        )


def try_begin_browser_action(tab: Any, action: str) -> int | None:
    """Acquire the per-tab reload/navigation single-flight slot."""

    global _BROWSER_ACTION_SEQUENCE
    tab_key = id(tab)
    with _BROWSER_ACTION_LOCK:
        if tab_key in _BROWSER_ACTIONS:
            return None
        if len(_BROWSER_ACTIONS) >= BROWSER_ACTION_CAPACITY:
            return None
        _BROWSER_ACTION_SEQUENCE += 1
        token = _BROWSER_ACTION_SEQUENCE
        _BROWSER_ACTIONS[tab_key] = (tab, token, str(action or "browser_action"))
        return token


def finish_browser_action(tab: Any, token: int) -> None:
    """Release a slot only when it is still owned by the supplied token."""

    tab_key = id(tab)
    with _BROWSER_ACTION_LOCK:
        active = _BROWSER_ACTIONS.get(tab_key)
        if active is not None and active[0] is tab and active[1] == token:
            _BROWSER_ACTIONS.pop(tab_key, None)


def get_active_browser_action_count() -> int:
    with _BROWSER_ACTION_LOCK:
        return len(_BROWSER_ACTIONS)


def is_browser_connection_closed_error(exc: BaseException | None) -> bool:
    """Recognize a terminated CDP/WebSocket session without masking other bugs."""

    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        error_name = f"{type(current).__module__}.{type(current).__name__}".casefold()
        message = str(current).casefold()
        if "websockets" in error_name and "connectionclosed" in error_name:
            return True
        if any(marker in error_name or marker in message for marker in _BROWSER_CONNECTION_CLOSED_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def raise_if_terminal_browser_error(exc: BaseException) -> None:
    """Never let a broad platform fallback swallow terminal browser loss."""

    if classify_browser_exception(exc) in {
        BrowserFailureKind.TRANSPORT_CLOSED,
        BrowserFailureKind.TARGET_CLOSED,
        BrowserFailureKind.EXECUTION_CONTEXT_LOST,
    }:
        raise exc


def _instance_log_path() -> str:
    app_root = util.get_app_root()
    instance_id = util.get_instance_id()
    if instance_id == util.CONST_DEFAULT_INSTANCE_ID:
        log_dir = os.path.join(app_root, "logs")
        filename = "runtime_default.log"
    else:
        log_dir = os.path.join(app_root, "instances", instance_id)
        filename = "runtime.log"
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, filename)


def _diagnostic_field_value(key: str, value: Any) -> Any:
    """Keep route diagnostics useful without persisting URL credentials."""

    if "url" not in str(key).casefold() or not isinstance(value, str):
        return value
    safe_value = value.replace("\r", " ").replace("\n", " ").strip()
    try:
        parts = urlsplit(safe_value)
    except ValueError:
        return safe_value[:RUNTIME_DIAGNOSTIC_URL_MAX_LENGTH]
    if not parts.scheme or not parts.netloc:
        return safe_value[:RUNTIME_DIAGNOSTIC_URL_MAX_LENGTH]
    safe_netloc = parts.netloc.rsplit("@", 1)[-1]
    route = urlunsplit((parts.scheme, safe_netloc, parts.path, "", ""))
    return route[:RUNTIME_DIAGNOSTIC_URL_MAX_LENGTH]


def _get_runtime_log_size_state(log_path: str) -> tuple[int, int]:
    state = _RUNTIME_LOG_SIZE_STATE.get(log_path)
    if state is None or state[1] >= RUNTIME_LOG_SIZE_RESYNC_WRITES:
        try:
            file_size = max(0, os.path.getsize(log_path))
        except OSError:
            file_size = 0
        state = (file_size, 0)
        if (
            log_path not in _RUNTIME_LOG_SIZE_STATE
            and len(_RUNTIME_LOG_SIZE_STATE) >= RUNTIME_FILE_STATE_CAPACITY
        ):
            _RUNTIME_LOG_SIZE_STATE.clear()
        _RUNTIME_LOG_SIZE_STATE[log_path] = state
    return state


def _rotate_runtime_log(log_path: str) -> bool:
    """Bound each instance log with a cached size check and thread-safe rotation."""

    with _RUNTIME_LOG_LOCK:
        file_size, _ = _get_runtime_log_size_state(log_path)
        if file_size < RUNTIME_LOG_MAX_BYTES:
            return False

        try:
            oldest = f"{log_path}.{RUNTIME_LOG_BACKUP_COUNT}"
            if os.path.exists(oldest):
                os.remove(oldest)
            for index in range(RUNTIME_LOG_BACKUP_COUNT - 1, 0, -1):
                source = f"{log_path}.{index}"
                if os.path.exists(source):
                    os.replace(source, f"{log_path}.{index + 1}")
            os.replace(log_path, f"{log_path}.1")
        except OSError:
            return False
        _RUNTIME_LOG_SIZE_STATE[log_path] = (0, 0)
        return True


def runtime_log(message: str, config_dict: dict[str, Any] | None = None, **fields: Any) -> None:
    """Append a redacted runtime diagnostic line without depending on verbose mode."""

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    instance_id = util.get_instance_id()
    run_mode = ""
    try:
        run_mode = str((config_dict or {}).get("advanced", {}).get("run_mode", ""))
    except Exception:
        run_mode = ""

    parts = [f"{timestamp}", f"instance={instance_id}"]
    if run_mode:
        parts.append(f"run_mode={run_mode}")
    parts.append(str(message))
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={_diagnostic_field_value(key, value)}")
    line = redact_sensitive_text(" ".join(parts))

    trace_fields: dict[str, Any] = {}
    for key, value in fields.items():
        normalized_key = str(key)
        if any(
            marker in normalized_key.casefold()
            for marker in ("password", "token", "cookie", "sid", "auth", "identity")
        ):
            trace_fields[normalized_key] = "[REDACTED]"
        else:
            safe_value = _diagnostic_field_value(normalized_key, value)
            trace_fields[normalized_key] = (
                redact_sensitive_text(safe_value)
                if isinstance(safe_value, str)
                else safe_value
            )
    trace_event = {
        "monotonic": time.monotonic(),
        "wall_timestamp": timestamp,
        "instance": instance_id,
        "action": redact_sensitive_text(str(message)),
        **trace_fields,
    }
    with _CRITICAL_TRACE_LOCK:
        _CRITICAL_TRACE.append(trace_event)

    with suppress(Exception), _RUNTIME_LOG_LOCK:
        log_path = _instance_log_path()
        _rotate_runtime_log(log_path)
        rendered_line = line + "\n"
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(rendered_line)
        cached_size, cached_writes = _get_runtime_log_size_state(log_path)
        rendered_size = len((line + os.linesep).encode("utf-8"))
        _RUNTIME_LOG_SIZE_STATE[log_path] = (
            cached_size + rendered_size,
            cached_writes + 1,
        )


def critical_trace_snapshot() -> tuple[dict[str, Any], ...]:
    with _CRITICAL_TRACE_LOCK:
        return tuple(dict(event) for event in _CRITICAL_TRACE)


def export_debug_trace(destination: str | None = None) -> str:
    """Export the bounded redacted trace for support diagnostics."""

    if destination:
        output_path = os.path.abspath(destination)
    else:
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        output_path = os.path.join(
            util.get_app_root(),
            f"hunterX_debug_trace_{stamp}.json",
        )
    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = {
        "schema": 1,
        "capacity": _CRITICAL_TRACE_CAPACITY,
        "events": critical_trace_snapshot(),
    }
    temporary = f"{output_path}.tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    os.replace(temporary, output_path)
    return output_path


def touch_heartbeat(
    filename: str = HEARTBEAT_FILE,
    *,
    min_interval_seconds: float = 0.0,
) -> None:
    heartbeat_path = util.get_instance_state_path(filename)
    current = time.monotonic()
    try:
        minimum_interval = max(0.0, float(min_interval_seconds))
    except (TypeError, ValueError):
        minimum_interval = 0.0

    with _HEARTBEAT_LOCK:
        previous = _HEARTBEAT_LAST_WRITE.get(heartbeat_path)
        if previous is not None:
            elapsed = current - previous
            if minimum_interval > 0.0 and 0.0 <= elapsed < minimum_interval:
                return
        try:
            with open(heartbeat_path, "w", encoding="utf-8") as heartbeat_file:
                heartbeat_file.write(str(int(time.time())))
        except Exception:
            return
        if (
            heartbeat_path not in _HEARTBEAT_LAST_WRITE
            and len(_HEARTBEAT_LAST_WRITE) >= RUNTIME_FILE_STATE_CAPACITY
        ):
            _HEARTBEAT_LAST_WRITE.clear()
        _HEARTBEAT_LAST_WRITE[heartbeat_path] = current


async def sleep_with_heartbeat(
    seconds: float,
    config_dict: dict[str, Any] | None = None,
    *,
    reason: str = "wait",
    chunk_seconds: float = 5.0,
    stop_checker: Any | None = None,
    quit_checker: Any | None = None,
) -> str:
    """Sleep in small chunks so stop/quit and heartbeat remain responsive."""

    total = max(0.0, float(seconds or 0))
    deadline = time.monotonic() + total
    next_log_at = time.monotonic()
    while True:
        touch_heartbeat()
        if quit_checker is not None and await quit_checker(config_dict):
            runtime_log(f"[{reason}] quit requested", config_dict)
            return "quit"
        if stop_checker is not None and await stop_checker(config_dict):
            runtime_log(f"[{reason}] stop requested", config_dict)
            return "stop"

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        now = time.monotonic()
        if now >= next_log_at:
            runtime_log(f"[{reason}] waiting", config_dict, remaining_s=int(remaining))
            next_log_at = now + 60
        await asyncio.sleep(min(max(0.1, chunk_seconds), remaining))
    touch_heartbeat()
    return "done"


async def wait_for_operation(
    awaitable: Awaitable[Any],
    timeout_seconds: float,
    action: str,
    config_dict: dict[str, Any] | None = None,
    *,
    default: Any = None,
    raise_on_timeout: bool = False,
    log_success: bool = True,
) -> Any:
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(awaitable, timeout=max(0.1, float(timeout_seconds)))
        if log_success:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            runtime_log(f"[{action}] done", config_dict, elapsed_ms=elapsed_ms)
        return result
    except asyncio.TimeoutError:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        runtime_log(f"[{action}] timeout", config_dict, elapsed_ms=elapsed_ms)
        if raise_on_timeout:
            raise
        return default


async def guarded_get(
    tab: Any,
    url: str,
    config_dict: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_NAVIGATION_TIMEOUT_SECONDS,
    reason: str = "navigation",
) -> bool:
    from page_classifier import classify_page

    source_url = getattr(getattr(tab, "target", None), "url", "") or ""
    runtime_log(
        "[NAVIGATION] intent",
        config_dict,
        reason=reason,
        source_url=source_url,
        target_url=url,
        page_class=classify_page(source_url).value,
        attempt_id=None,
        generation=None,
        token=None,
    )
    action_token = try_begin_browser_action(tab, reason)
    if action_token is None:
        runtime_log(
            "[NAVIGATION] skipped",
            config_dict,
            reason="browser_action_in_flight",
            target_url=url,
        )
        return False
    progress_binding = current_expected_progress_binding()
    progress_expectation = None
    if progress_binding is not None:
        from navigation_context import canonicalize_target_url

        current = time.monotonic()
        progress_expectation = progress_binding.supervisor.arm_expected_progress(
            tab_identity=progress_binding.tab_identity,
            platform_key=progress_binding.platform_key,
            attempt_id=progress_binding.attempt_id,
            attempt_generation=progress_binding.attempt_generation,
            action_owner="guarded_navigation",
            action_token=f"{action_token}:{reason}",
            kind=ExpectedProgressKind.NAVIGATION,
            source_route=canonicalize_target_url(source_url),
            acceptable_routes=(canonicalize_target_url(url),),
            deadline=current + max(0.1, float(timeout_seconds)),
            reconciliation_owner="browser_session",
            now=current,
        )
    try:
        await wait_for_operation(
            tab.get(url),
            timeout_seconds,
            reason,
            config_dict,
            raise_on_timeout=True,
        )
        return True
    except TimeoutError:
        if progress_binding is not None and progress_expectation is not None:
            progress_binding.supervisor.fail_expected_progress(
                tab_identity=progress_binding.tab_identity,
                attempt_id=progress_binding.attempt_id,
                attempt_generation=progress_binding.attempt_generation,
                action_owner=progress_expectation.action_owner,
                action_token=progress_expectation.action_token,
                reason="guarded_navigation_timeout",
            )
        return False
    except BaseException:
        if progress_binding is not None and progress_expectation is not None:
            progress_binding.supervisor.fail_expected_progress(
                tab_identity=progress_binding.tab_identity,
                attempt_id=progress_binding.attempt_id,
                attempt_generation=progress_binding.attempt_generation,
                action_owner=progress_expectation.action_owner,
                action_token=progress_expectation.action_token,
                reason="guarded_navigation_error",
            )
        raise
    finally:
        finish_browser_action(tab, action_token)


async def guarded_driver_get(
    driver: Any,
    url: str,
    config_dict: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_NAVIGATION_TIMEOUT_SECONDS,
    reason: str = "initial_navigation",
) -> Any | None:
    """Single-flight browser startup navigation that returns the created tab."""

    runtime_log(
        "[NAVIGATION] intent",
        config_dict,
        reason=reason,
        source_url="",
        target_url=url,
        page_class="unknown",
        attempt_id=None,
        generation=None,
        token=None,
    )
    action_token = try_begin_browser_action(driver, reason)
    if action_token is None:
        runtime_log(
            "[NAVIGATION] skipped",
            config_dict,
            reason="browser_action_in_flight",
            target_url=url,
        )
        return None
    try:
        return await wait_for_operation(
            driver.get(url),
            timeout_seconds,
            reason,
            config_dict,
            raise_on_timeout=True,
        )
    except TimeoutError:
        return None
    finally:
        finish_browser_action(driver, action_token)


async def evaluate_with_timeout(
    tab: Any,
    script: str,
    config_dict: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_EVALUATE_TIMEOUT_SECONDS,
    reason: str = "evaluate",
    default: Any = None,
    log_success: bool = True,
) -> Any:
    return await wait_for_operation(
        tab.evaluate(script),
        timeout_seconds,
        reason,
        config_dict,
        default=default,
        log_success=log_success,
    )


async def query_selector_with_timeout(
    obj: Any,
    selector: str,
    config_dict: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_EVALUATE_TIMEOUT_SECONDS,
    reason: str = "query_selector",
    log_success: bool = True,
) -> Any:
    return await wait_for_operation(
        obj.query_selector(selector),
        timeout_seconds,
        reason,
        config_dict,
        default=None,
        log_success=log_success,
    )


async def query_selector_all_with_timeout(
    obj: Any,
    selector: str,
    config_dict: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_EVALUATE_TIMEOUT_SECONDS,
    reason: str = "query_selector_all",
    log_success: bool = True,
) -> list[Any] | None:
    return cast(
        list[Any] | None,
        await wait_for_operation(
            obj.query_selector_all(selector),
            timeout_seconds,
            reason,
            config_dict,
            default=None,
            log_success=log_success,
        ),
    )


async def read_document_ready_state(
    tab: Any,
    config_dict: dict[str, Any] | None = None,
    *,
    log_success: bool = True,
) -> str:
    result = await evaluate_with_timeout(
        tab,
        "document.readyState",
        config_dict,
        timeout_seconds=1.0,
        reason="ready_state",
        default="",
        log_success=log_success,
    )
    try:
        return str(util.parse_nodriver_result(result) or "")
    except Exception:
        return str(result or "")


async def wait_for_interactive_ready(
    tab: Any,
    config_dict: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_READY_STATE_TIMEOUT_SECONDS,
    log_success: bool = True,
) -> bool:
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    last_state = ""
    while time.monotonic() < deadline:
        touch_heartbeat(
            min_interval_seconds=INTERACTIVE_HEARTBEAT_MIN_INTERVAL_SECONDS
        )
        state = await read_document_ready_state(
            tab,
            config_dict,
            log_success=log_success,
        )
        last_state = state
        if state in {"interactive", "complete"}:
            if log_success:
                runtime_log("[LEAK] ready_state", config_dict, state=state)
            return True
        await asyncio.sleep(0.2)
    runtime_log("[LEAK] ready_state_timeout", config_dict, state=last_state)
    return False
