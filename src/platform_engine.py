"""Shared runtime lifecycle for all ticket-platform adapters."""

from __future__ import annotations

import time
import weakref
from typing import Any, NamedTuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import runtime_health
from attempt_lifecycle import AttemptState, PurchaseAttempt
from navigation_context import (
    NavigationIntent,
    TargetContext,
    canonicalize_target_url,
)
from page_classifier import PageClass, classify_page
from platform_adapters import adapter_for_key, adapter_for_url
from platform_contract import (
    PlatformAdapter,
    PlatformRuntimeState,
    activate_platform_state,
    clear_active_platform_state,
)
from refresh_coordinator import RefreshCoordinator
from run_modes import is_leak_watch_mode


_ATTEMPT_STATE_FOR_PAGE = {
    PageClass.ACTIVITY: AttemptState.ACTIVITY_READY,
    PageClass.DATE: AttemptState.DATE_READY,
    PageClass.AREA: AttemptState.AREA_READY,
    PageClass.TICKET: AttemptState.TICKET_FORM_ACTIVE,
    PageClass.ORDER: AttemptState.ORDER_PENDING,
    PageClass.CHECKOUT: AttemptState.CHECKOUT_REACHED,
    PageClass.PAYMENT: AttemptState.PAYMENT_REACHED,
    PageClass.QUEUE: AttemptState.QUEUE,
}
_SAFE_PAGES = frozenset({PageClass.ACTIVITY, PageClass.DATE, PageClass.AREA})
_PROTECTED_PAGES = frozenset(
    {
        PageClass.TICKET,
        PageClass.ORDER,
        PageClass.CHECKOUT,
        PageClass.PAYMENT,
    }
)


class DispatchDecision(NamedTuple):
    """Immutable dispatch result with tuple-speed construction on the hot path."""

    allowed: bool
    reason: str
    page_class: PageClass
    adapter: PlatformAdapter | None
    attempt_id: str | None = None
    attempt_generation: int = 0
    automation_allowed: bool = True
    new_attempt_started: bool = False
    route_generation: int = 0
    route_transition_reasons: tuple[str, ...] = ()

    @property
    def platform_key(self) -> str | None:
        return self.adapter.key if self.adapter is not None else None


class SafeRearmProof(NamedTuple):
    """Exact central submit owner that requires adapter-positive safe proof."""

    attempt_id: str
    attempt_generation: int
    submit_token: str
    owner: str


class PlatformEngine:
    """Own bounded per-tab state and apply fail-closed dispatch policy."""

    def __init__(self) -> None:
        self._states: weakref.WeakKeyDictionary[Any, dict[str, PlatformRuntimeState]] = (
            weakref.WeakKeyDictionary()
        )
        self._fallback_states: dict[int, tuple[Any, dict[str, PlatformRuntimeState]]] = {}
        self._refresh_coordinators: weakref.WeakKeyDictionary[Any, RefreshCoordinator] = (
            weakref.WeakKeyDictionary()
        )
        self._fallback_refresh_coordinators: dict[
            int, tuple[Any, RefreshCoordinator]
        ] = {}
        self._fallback_capacity = 64

    @staticmethod
    def _tab_identity(tab: Any) -> str:
        target = getattr(tab, "target", None)
        target_id = getattr(target, "target_id", None) or getattr(target, "id", None)
        if target_id:
            return f"target:{target_id}"
        return f"{type(tab).__name__}:{id(tab)}"

    @staticmethod
    def _target_marker(tab: Any) -> str:
        """Cheap target replacement marker for the 50 ms stable-route path."""

        target = getattr(tab, "target", None)
        marker = getattr(target, "target_id", None) or getattr(target, "id", None)
        if marker:
            return str(marker)
        return str(id(target if target is not None else tab))

    @staticmethod
    def _event_identity(url: str) -> str:
        """Canonical route identity without tracking-only query parameters."""

        try:
            parts = urlsplit(str(url or ""))
        except ValueError:
            return str(url or "")[:512]
        ignored = {"fbclid", "gclid", "_", "timestamp", "ts"}
        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.casefold() not in ignored and not key.casefold().startswith("utm_")
        ]
        normalized_path = parts.path.rstrip("/") or "/"
        return urlunsplit(
            (
                parts.scheme.casefold(),
                parts.netloc.casefold(),
                normalized_path,
                urlencode(sorted(query)),
                "",
            )
        )[:512]

    @staticmethod
    def _attempt_state_for_page(page_class: PageClass) -> AttemptState:
        return _ATTEMPT_STATE_FOR_PAGE.get(page_class, AttemptState.IDLE)

    def _start_attempt(
        self,
        state: PlatformRuntimeState,
        tab: Any,
        adapter: PlatformAdapter,
        url: str,
        page_class: PageClass,
        now: float,
    ) -> PurchaseAttempt:
        state.attempt_generation += 1
        state.attempt = PurchaseAttempt.create_for_context(
            platform=adapter.key,
            tab_identity=self._tab_identity(tab),
            event_id=self._event_identity(url),
            generation=state.attempt_generation,
            state=self._attempt_state_for_page(page_class),
            now=now,
        )
        state.last_transition_at = now
        return state.attempt

    @staticmethod
    def _transition_attempt_for_page(
        state: PlatformRuntimeState,
        page_class: PageClass,
        now: float,
    ) -> None:
        attempt = state.attempt
        if attempt is None or not attempt.automation_allowed:
            return
        new_state = PlatformEngine._attempt_state_for_page(page_class)
        if new_state is AttemptState.IDLE or new_state is attempt.state:
            return
        state.attempt = attempt.with_state(new_state, now=now)
        state.last_transition_at = now

    def _tab_states(self, tab: Any) -> dict[str, PlatformRuntimeState]:
        try:
            states = self._states.get(tab)
            if states is None:
                states = {}
                self._states[tab] = states
            return states
        except TypeError:
            tab_id = id(tab)
            current = self._fallback_states.get(tab_id)
            if current is not None and current[0] is tab:
                return current[1]
            if len(self._fallback_states) >= self._fallback_capacity:
                self._fallback_states.clear()
            states = {}
            self._fallback_states[tab_id] = (tab, states)
            return states

    def state_for(self, tab: Any, adapter: PlatformAdapter) -> PlatformRuntimeState:
        states = self._tab_states(tab)
        state = states.get(adapter.key)
        if state is None:
            state = PlatformRuntimeState()
            states[adapter.key] = state
        elif getattr(state, "_schema_version", 0) != 2:
            state.backfill()
        return state

    def capture_navigation_intent(
        self,
        tab: Any,
        adapter: PlatformAdapter | str,
        target_url: str,
        config_dict: dict[str, Any] | None,
        *,
        reason: str,
    ) -> TargetContext | None:
        resolved = adapter_for_key(adapter) if isinstance(adapter, str) else adapter
        target_adapter = adapter_for_url(target_url)
        if (
            resolved is None
            or target_adapter is None
            or target_adapter.key != resolved.key
        ):
            return None
        normalized = canonicalize_target_url(target_url)
        if not normalized:
            return None
        state = self.state_for(tab, resolved)
        current = state.target_context
        if (
            current is not None
            and current.intent.normalized_target_url == normalized
            and not current.intent.is_expired()
        ):
            return current
        config = config_dict or {}
        advanced = config.get("advanced", {}) if isinstance(config, dict) else {}
        if not isinstance(advanced, dict):
            advanced = {}
        try:
            generation = int(config.get("_config_generation", 0) or 0)
        except (TypeError, ValueError):
            generation = 0
        intent = NavigationIntent.create(
            target_url,
            platform=resolved.key,
            event_id=self._event_identity(target_url),
            mode=str(advanced.get("run_mode", "onsale") or "onsale"),
            instance_id=str(
                config.get("_instance_id", advanced.get("instance_id", "default"))
                or "default"
            ),
            tab_identity=self._tab_identity(tab),
            config_generation=generation,
            reason=reason,
        )
        state.target_context = TargetContext(intent=intent)
        return state.target_context

    def target_context_for(
        self,
        tab: Any,
        adapter: PlatformAdapter | str,
    ) -> TargetContext | None:
        resolved = adapter_for_key(adapter) if isinstance(adapter, str) else adapter
        if resolved is None:
            return None
        return self.state_for(tab, resolved).target_context

    def mark_target_restored(
        self,
        tab: Any,
        adapter: PlatformAdapter | str,
        *,
        retry_seconds: float = 0.0,
    ) -> bool:
        context = self.target_context_for(tab, adapter)
        if context is None:
            return False
        context.record_restore(True, retry_seconds=retry_seconds)
        return True

    def current_attempt(
        self,
        tab: Any,
        adapter: PlatformAdapter | str,
    ) -> PurchaseAttempt | None:
        resolved = adapter_for_key(adapter) if isinstance(adapter, str) else adapter
        if resolved is None:
            return None
        return self.state_for(tab, resolved).attempt

    def transition_attempt(
        self,
        tab: Any,
        adapter: PlatformAdapter | str,
        new_state: AttemptState,
        *,
        reason: str = "",
    ) -> PurchaseAttempt | None:
        resolved = adapter_for_key(adapter) if isinstance(adapter, str) else adapter
        if resolved is None:
            return None
        state = self.state_for(tab, resolved)
        if state.attempt is None:
            return None
        state.attempt = state.attempt.with_state(new_state, reason=reason)
        state.last_transition_at = state.attempt.updated_at
        return state.attempt

    def claim_submit(
        self,
        tab: Any,
        adapter: PlatformAdapter | str,
        *,
        owner: str,
    ) -> str | None:
        resolved = adapter_for_key(adapter) if isinstance(adapter, str) else adapter
        if resolved is None:
            return None
        state = self.state_for(tab, resolved)
        if state.attempt is None:
            return None
        claimed = state.attempt.claim_submit(owner)
        if claimed is None:
            return None
        state.attempt, token = claimed
        state.last_transition_at = state.attempt.updated_at
        return str(token)

    def release_submit(
        self,
        tab: Any,
        adapter: PlatformAdapter | str,
        *,
        token: str,
    ) -> bool:
        resolved = adapter_for_key(adapter) if isinstance(adapter, str) else adapter
        if resolved is None:
            return False
        state = self.state_for(tab, resolved)
        if state.attempt is None:
            return False
        released = state.attempt.release_submit(token)
        if released is None:
            return False
        state.attempt = released
        state.last_transition_at = released.updated_at
        return True

    def release_rejected_submit_if_owned(
        self,
        tab: Any,
        adapter: PlatformAdapter | str,
        *,
        attempt_id: str,
        attempt_generation: int,
        token: str,
        reason: str,
    ) -> PurchaseAttempt | None:
        """Release only an exact submit whose rejection is authoritative.

        This is deliberately stricter than ``release_submit``.  A delayed
        platform callback must match the current attempt identity, generation,
        and submit token before it can reopen the ticket form or remove the
        matching positive-safe-route fence.
        """

        resolved = adapter_for_key(adapter) if isinstance(adapter, str) else adapter
        if resolved is None:
            return None
        state = self.state_for(tab, resolved)
        current = state.attempt
        if (
            current is None
            or not attempt_id
            or current.attempt_id != str(attempt_id)
            or current.generation != int(attempt_generation or 0)
            or not token
            or current.submit_token != str(token)
        ):
            return None
        released = current.release_submit(str(token))
        if released is None:
            return None
        proof = state.platform_data.get("_central_safe_rearm_proof")
        if (
            isinstance(proof, SafeRearmProof)
            and proof.attempt_id == current.attempt_id
            and proof.attempt_generation == current.generation
            and proof.submit_token == current.submit_token
        ):
            state.platform_data.pop("_central_safe_rearm_proof", None)
        state.attempt = released
        state.last_transition_at = released.updated_at
        runtime_health.runtime_log(
            "[LIFECYCLE] rejected_submit_released",
            state.config_snapshot,
            platform=resolved.key,
            attempt_id=current.attempt_id,
            generation=current.generation,
            reason=str(reason or "confirmed_rejection"),
        )
        return released

    def mark_attempt_completed(
        self,
        tab: Any,
        adapter: PlatformAdapter | str,
        *,
        reason: str,
    ) -> PurchaseAttempt | None:
        completed = self.transition_attempt(
            tab,
            adapter,
            AttemptState.COMPLETED,
            reason=reason,
        )
        if completed is not None:
            self.refresh_coordinator_for(tab).cancel_pending(
                "attempt_completed",
                purchase_guard=True,
            )
        return completed

    def mark_submit_outcome_unknown(
        self,
        tab: Any,
        adapter: PlatformAdapter | str,
        *,
        reason: str,
    ) -> PurchaseAttempt | None:
        unknown = self.transition_attempt(
            tab,
            adapter,
            AttemptState.SUBMIT_OUTCOME_UNKNOWN,
            reason=reason,
        )
        if unknown is not None:
            self.refresh_coordinator_for(tab).cancel_pending(
                "submit_outcome_unknown",
                purchase_guard=True,
            )
        return unknown

    def mark_submit_outcome_unknown_if_owned(
        self,
        tab: Any,
        adapter: PlatformAdapter | str,
        *,
        attempt_id: str,
        token: str,
        reason: str,
    ) -> PurchaseAttempt | None:
        """Fail closed only when the caller still owns this exact submit.

        A delayed watcher from an earlier attempt must never mutate the current
        attempt before discovering that its identity is stale.
        """

        resolved = adapter_for_key(adapter) if isinstance(adapter, str) else adapter
        if resolved is None:
            return None
        state = self.state_for(tab, resolved)
        current = state.attempt
        if (
            current is None
            or not attempt_id
            or current.attempt_id != str(attempt_id)
            or not token
            or current.submit_token != str(token)
        ):
            return None
        state.attempt = current.with_state(
            AttemptState.SUBMIT_OUTCOME_UNKNOWN,
            reason=reason,
        )
        state.last_transition_at = state.attempt.updated_at
        self.refresh_coordinator_for(tab).cancel_pending(
            "submit_outcome_unknown",
            purchase_guard=True,
        )
        return state.attempt

    @staticmethod
    def _owned_safe_rearm_proof(
        state: PlatformRuntimeState,
    ) -> SafeRearmProof | None:
        proof = state.platform_data.get("_central_safe_rearm_proof")
        attempt = state.attempt
        if not isinstance(proof, SafeRearmProof) or attempt is None:
            return None
        if (
            proof.attempt_id != attempt.attempt_id
            or proof.attempt_generation != attempt.generation
            or not proof.submit_token
            or proof.submit_token != attempt.submit_token
        ):
            return None
        return proof

    def require_positive_safe_rearm_proof(
        self,
        tab: Any,
        adapter: PlatformAdapter | str,
        *,
        attempt_id: str,
        attempt_generation: int,
        token: str,
        owner: str,
    ) -> bool:
        """Fence a protected→safe reset behind an adapter's positive proof."""

        resolved = adapter_for_key(adapter) if isinstance(adapter, str) else adapter
        if resolved is None:
            return False
        state = self.state_for(tab, resolved)
        current = state.attempt
        if (
            current is None
            or current.attempt_id != str(attempt_id or "")
            or current.generation != int(attempt_generation or 0)
            or not token
            or current.submit_token != str(token)
        ):
            return False
        state.platform_data["_central_safe_rearm_proof"] = SafeRearmProof(
            current.attempt_id,
            current.generation,
            current.submit_token,
            str(owner or "platform_submit"),
        )
        return True

    def confirm_positive_safe_rearm_if_owned(
        self,
        tab: Any,
        adapter: PlatformAdapter | str,
        *,
        attempt_id: str,
        attempt_generation: int,
        token: str,
        url: str,
        page_class: PageClass,
        now: float | None = None,
    ) -> PurchaseAttempt | None:
        """Start the next attempt only after an exact owner proves a safe page."""

        resolved = adapter_for_key(adapter) if isinstance(adapter, str) else adapter
        if resolved is None or page_class not in _SAFE_PAGES:
            return None
        state = self.state_for(tab, resolved)
        proof = self._owned_safe_rearm_proof(state)
        if (
            proof is None
            or proof.attempt_id != str(attempt_id or "")
            or proof.attempt_generation != int(attempt_generation or 0)
            or proof.submit_token != str(token or "")
        ):
            return None
        current = time.monotonic() if now is None else float(now)
        state.reset_attempt()
        state.current_page = page_class
        state.previous_url = str(url or "")
        state.target_identity = self._target_marker(tab)
        state.normalized_route = canonicalize_target_url(url)
        state.route_generation += 1
        self.refresh_coordinator_for(tab).reset_purchase_guard()
        return self._start_attempt(state, tab, resolved, url, page_class, current)

    def mark_attempt_failed(
        self,
        tab: Any,
        adapter: PlatformAdapter | str,
        *,
        reason: str,
    ) -> PurchaseAttempt | None:
        return self.transition_attempt(
            tab,
            adapter,
            AttemptState.FAILED_RETRYABLE,
            reason=reason,
        )

    def refresh_coordinator_for(self, tab: Any) -> RefreshCoordinator:
        """Return the single refresh owner for ``tab`` across platform families."""

        try:
            coordinator = self._refresh_coordinators.get(tab)
            if coordinator is None:
                coordinator = RefreshCoordinator()
                coordinator.bind_tab(tab)
                self._refresh_coordinators[tab] = coordinator
            return coordinator
        except TypeError:
            tab_id = id(tab)
            current = self._fallback_refresh_coordinators.get(tab_id)
            if current is not None and current[0] is tab:
                return current[1]
            if len(self._fallback_refresh_coordinators) >= self._fallback_capacity:
                self._fallback_refresh_coordinators.clear()
            coordinator = RefreshCoordinator()
            coordinator.bind_tab(tab)
            self._fallback_refresh_coordinators[tab_id] = (tab, coordinator)
            return coordinator

    def _reset_inactive_families(self, tab: Any, active_key: str | None) -> None:
        for key, state in self._tab_states(tab).items():
            if key == active_key:
                continue
            state.backfill()
            if (
                state.previous_url
                or state.current_page is not PageClass.UNKNOWN
                or state.platform_data
            ):
                state.reset_attempt()
                state.target_context = None

    def _external_queue_owner(self, tab: Any) -> PlatformAdapter | None:
        """Return one existing per-tab owner for a protected external queue.

        External waiting-room hosts are intentionally absent from the platform
        registry.  Preserve ownership only when exactly one platform on this
        same tab already has an unresolved submit or guarded retry.  This keeps
        state bounded without assigning arbitrary queue pages to a platform.
        """

        owners: list[PlatformAdapter] = []
        for key, state in self._tab_states(tab).items():
            state.backfill()
            data = state.platform_data
            if not state.previous_url or not (
                data.get("submission_pending", False)
                or data.get("failure_retry_pending", False)
            ):
                continue
            adapter = adapter_for_key(key)
            if adapter is not None:
                owners.append(adapter)
        return owners[0] if len(owners) == 1 else None

    def before_dispatch(
        self,
        tab: Any,
        url: str,
        config_dict: dict[str, Any] | None,
        *,
        text: str = "",
        dom_signature: str = "",
        now: float | None = None,
    ) -> DispatchDecision:
        adapter = adapter_for_url(url)
        is_external_queue = (
            adapter is None and classify_page(url, text) is PageClass.QUEUE
        )
        if is_external_queue:
            adapter = self._external_queue_owner(tab)
        if adapter is None:
            self._reset_inactive_families(tab, None)
            clear_active_platform_state()
            return DispatchDecision(False, "unsupported_host", PageClass.UNKNOWN, None)

        page_class = (
            PageClass.QUEUE
            if is_external_queue
            else adapter.classify_page(url, text)
        )
        self._reset_inactive_families(tab, adapter.key)
        state = self.state_for(tab, adapter)
        previous_page = state.current_page
        previous_url = state.previous_url
        current_url = str(url or "")
        stable_route = (
            state.route_generation > 0
            and previous_url == current_url
            and previous_page is page_class
            and not dom_signature
        )
        configured_target = ""
        if not stable_route and isinstance(config_dict, dict):
            configured_target = str(config_dict.get("homepage", "") or "")
        if (
            configured_target
            and canonicalize_target_url(configured_target)
            != canonicalize_target_url(current_url)
        ):
            self.capture_navigation_intent(
                tab,
                adapter,
                configured_target,
                config_dict,
                reason="configured_homepage_after_login",
            )
        route_reasons: tuple[str, ...] = ()
        if not stable_route:
            current_target_identity = self._target_marker(tab)
            normalized_route = (
                state.normalized_route
                if previous_url == current_url and state.normalized_route
                else canonicalize_target_url(current_url)
            )
            bounded_dom_signature = str(dom_signature or "")[:256]
            route_reason_list: list[str] = []
            if state.route_generation == 0:
                route_reason_list.append("initial")
            else:
                if state.target_identity and state.target_identity != current_target_identity:
                    route_reason_list.append("target_replaced")
                if state.normalized_route and state.normalized_route != normalized_route:
                    route_reason_list.append("url_changed")
                if state.current_page is not page_class:
                    route_reason_list.append("page_class_changed")
                if (
                    bounded_dom_signature
                    and state.dom_signature
                    and state.dom_signature != bounded_dom_signature
                ):
                    route_reason_list.append("same_url_dom_rerender")
            route_reasons = tuple(route_reason_list)
            if route_reason_list:
                state.route_generation += 1
            state.target_identity = current_target_identity
            state.normalized_route = normalized_route
            if bounded_dom_signature:
                state.dom_signature = bounded_dom_signature
        starts_new_attempt = False
        if not stable_route and previous_url:
            starts_new_attempt = bool(
                (previous_page in _PROTECTED_PAGES and page_class in _SAFE_PAGES)
                or (
                    state.attempt is not None
                    and state.attempt.state is AttemptState.FAILED_RETRYABLE
                    and page_class in _SAFE_PAGES
                )
                or (
                    page_class is PageClass.ACTIVITY
                    and previous_page in _SAFE_PAGES
                    and previous_url != current_url
                    and self._event_identity(previous_url)
                    != self._event_identity(current_url)
                )
            )
        positive_safe_rearm_pending = bool(
            starts_new_attempt and self._owned_safe_rearm_proof(state) is not None
        )
        if starts_new_attempt and not positive_safe_rearm_pending:
            state.reset_attempt()
        elif positive_safe_rearm_pending:
            starts_new_attempt = False
        activate_platform_state(adapter.key, state.platform_data)
        state.current_page = page_class
        state.previous_url = current_url
        state.cycle_count += 1
        # ``reload_config`` replaces the runtime dictionary atomically. Keep
        # that generation's object so legacy platform helpers which do not
        # thread config through their call signature still obey the same
        # per-tab refresh interval. Never clear live cooldown/deadline state.
        state.config_snapshot = config_dict
        coordinator = self.refresh_coordinator_for(tab)
        if page_class in _PROTECTED_PAGES or page_class is PageClass.QUEUE:
            coordinator.cancel_pending(
                f"protected_{page_class.value}",
                purchase_guard=page_class in _PROTECTED_PAGES,
            )
        elif starts_new_attempt:
            coordinator.reset_purchase_guard()
        current = time.monotonic() if now is None else float(now)
        attempt_started = False
        if state.attempt is None and (
            page_class in _SAFE_PAGES
            or page_class in _PROTECTED_PAGES
            or page_class is PageClass.QUEUE
        ):
            self._start_attempt(state, tab, adapter, url, page_class, current)
            attempt_started = True
        elif (
            not stable_route
            and not positive_safe_rearm_pending
            and state.attempt is not None
            and state.attempt.state is not self._attempt_state_for_page(page_class)
        ):
            self._transition_attempt_for_page(state, page_class, current)
        attempt = state.attempt
        automation_allowed = attempt.automation_allowed if attempt is not None else True
        attempt_id = attempt.attempt_id if attempt is not None else None
        attempt_generation = attempt.generation if attempt is not None else 0
        expired = state.leak_scheduler.maintenance(config_dict, url, current)
        for event in expired:
            runtime_health.runtime_log(
                "[PLATFORM] watchdog_recovered",
                config_dict,
                platform=adapter.key,
                event=event,
                current_url=url,
            )
        if route_reasons or attempt_started:
            runtime_health.runtime_log(
                "[LIFECYCLE] transition",
                config_dict,
                platform=adapter.key,
                current_url=url,
                page_class=page_class.value,
                attempt_id=attempt_id,
                generation=attempt_generation,
                previous_state=(
                    previous_page.value
                    if isinstance(previous_page, PageClass)
                    else str(previous_page)
                ),
                next_state=(attempt.state.value if attempt is not None else "idle"),
                route_reasons=",".join(route_reasons),
                automation_allowed=automation_allowed,
            )

        if is_leak_watch_mode(config_dict):
            if not adapter.capabilities().supports_leak_watch:
                return DispatchDecision(
                    False,
                    "leak_watch_not_contract_complete",
                    page_class,
                    adapter,
                    attempt_id,
                    attempt_generation,
                    automation_allowed,
                    attempt_started,
                    state.route_generation,
                    route_reasons,
                )
            if adapter.is_protected_page(url, text):
                # Platform selection/checkout handlers may still inspect this
                # page. Automatic leak-watch refresh remains fail-closed in
                # ReloadGuard; protected status does not disable normal
                # purchase progression.
                return DispatchDecision(
                    True,
                    "protected_no_reload",
                    page_class,
                    adapter,
                    attempt_id,
                    attempt_generation,
                    automation_allowed,
                    attempt_started,
                    state.route_generation,
                    route_reasons,
                )
            if not adapter.is_safe_watch_page(url, text):
                return DispatchDecision(
                    True,
                    "unknown_no_reload",
                    page_class,
                    adapter,
                    attempt_id,
                    attempt_generation,
                    automation_allowed,
                    attempt_started,
                    state.route_generation,
                    route_reasons,
                )

        return DispatchDecision(
            True,
            "ready",
            page_class,
            adapter,
            attempt_id,
            attempt_generation,
            automation_allowed,
            attempt_started,
            state.route_generation,
            route_reasons,
        )

    def clear_tab(self, tab: Any) -> None:
        try:
            self._states.pop(tab, None)
        except TypeError:
            pass
        try:
            self._refresh_coordinators.pop(tab, None)
        except TypeError:
            pass
        current = self._fallback_states.get(id(tab))
        if current is not None and current[0] is tab:
            self._fallback_states.pop(id(tab), None)
        refresh_current = self._fallback_refresh_coordinators.get(id(tab))
        if refresh_current is not None and refresh_current[0] is tab:
            self._fallback_refresh_coordinators.pop(id(tab), None)

    @property
    def state_count(self) -> int:
        return sum(len(states) for states in self._states.values()) + sum(
            len(item[1]) for item in self._fallback_states.values()
        )

    @property
    def refresh_coordinator_count(self) -> int:
        return len(self._refresh_coordinators) + len(
            self._fallback_refresh_coordinators,
        )


platform_engine = PlatformEngine()
