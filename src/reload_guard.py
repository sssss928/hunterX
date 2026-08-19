from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from page_classifier import PageClass, classify_page, is_protected_after_ticket
from runtime_health import (
    DEFAULT_RELOAD_TIMEOUT_SECONDS,
    finish_browser_action,
    runtime_log,
    try_begin_browser_action,
    wait_for_operation,
)


RELOAD_GUARD_HISTORY_CAPACITY = 256


@dataclass
class ReloadDecision:
    allowed: bool
    reason: str
    page_class: PageClass


@dataclass
class ReloadGuard:
    history: deque[ReloadDecision] = field(
        default_factory=lambda: deque(maxlen=RELOAD_GUARD_HISTORY_CAPACITY)
    )

    def __post_init__(self) -> None:
        if not isinstance(self.history, deque) or self.history.maxlen != RELOAD_GUARD_HISTORY_CAPACITY:
            self.history = deque(
                self.history,
                maxlen=RELOAD_GUARD_HISTORY_CAPACITY,
            )

    def can_reload(
        self,
        url: str = "",
        reason: str = "",
        recovery: bool = False,
        page_class: PageClass | None = None,
    ) -> ReloadDecision:
        """Return whether ``url`` may be reloaded.

        ``page_class`` lets the owning platform adapter resolve routes whose
        names have platform-specific meaning. TicketPlus ``/order/...`` is a
        ticket-selection page, for example, while TixCraft ``/ticket/order``
        is a protected post-submit page. Callers without an adapter keep the
        conservative shared-classifier fallback.
        """

        resolved_page_class = (
            page_class if isinstance(page_class, PageClass) else classify_page(url)
        )
        protected = is_protected_after_ticket(resolved_page_class)
        # Queue documents must never be refreshed out from under the provider.
        # An adapter-supplied UNKNOWN is also deliberately fail-closed: it is a
        # known ticketing host on a route the adapter cannot prove is safe.
        if resolved_page_class is PageClass.QUEUE:
            protected = True
        if page_class is not None and resolved_page_class is PageClass.UNKNOWN:
            protected = True
        allowed = not protected or recovery
        decision = ReloadDecision(
            allowed=allowed,
            reason=reason or "unspecified",
            page_class=resolved_page_class,
        )
        self.history.append(decision)
        return decision

    async def reload(
        self,
        tab: Any,
        reason: str = "",
        recovery: bool = False,
        timeout_seconds: float = DEFAULT_RELOAD_TIMEOUT_SECONDS,
        config_dict: dict[str, Any] | None = None,
        coordinator: Any | None = None,
        priority: str = "periodic",
        page_class: PageClass | None = None,
    ) -> bool:
        url = getattr(getattr(tab, "target", None), "url", "") or ""
        decision = self.can_reload(
            url=url,
            reason=reason,
            recovery=recovery,
            page_class=page_class,
        )
        if not decision.allowed:
            runtime_log(
                "[RELOAD] blocked",
                config_dict,
                reason=decision.reason,
                page_class=decision.page_class.value,
                current_url=url,
            )
            return False
        action_token = try_begin_browser_action(tab, reason or "reload")
        if action_token is None:
            runtime_log(
                "[RELOAD] blocked",
                config_dict,
                reason="browser_action_in_flight",
                page_class=decision.page_class.value,
                current_url=url,
            )
            return False
        dispatch_token = None
        dispatch_started_ns = None
        try:
            if coordinator is not None:
                from run_modes import get_effective_reload_interval

                dispatch = coordinator.begin_dispatch(
                    reason or "reload",
                    get_effective_reload_interval(config_dict, 0.0),
                    priority=priority,
                )
                if not dispatch.allowed:
                    runtime_log(
                        "[REFRESH] suppressed",
                        config_dict,
                        reason=reason or "reload",
                        suppressed_reason=dispatch.reason,
                        page_class=decision.page_class.value,
                        generation=coordinator.generation,
                        next_allowed_monotonic_ns=dispatch.next_allowed_ns,
                        current_url=url,
                    )
                    return False
                dispatch_token = dispatch.token
                dispatch_started_ns = dispatch.requested_ns
                runtime_log(
                    "[REFRESH] dispatch",
                    config_dict,
                    reason=reason or "reload",
                    priority=priority,
                    page_class=decision.page_class.value,
                    generation=coordinator.generation,
                    requested_monotonic_ns=dispatch.requested_ns,
                    start_monotonic_ns=dispatch.requested_ns,
                    lateness_ms=dispatch.lateness_ms,
                    current_url=url,
                )
            await wait_for_operation(
                tab.reload(),
                timeout_seconds,
                "RELOAD",
                config_dict,
                raise_on_timeout=True,
            )
            if coordinator is not None:
                coordinator.complete_dispatch(dispatch_token, True)
                runtime_log(
                    "[REFRESH] completed",
                    config_dict,
                    reason=reason or "reload",
                    generation=coordinator.generation,
                    start_monotonic_ns=dispatch_started_ns,
                    completed_monotonic_ns=coordinator.clock_ns(),
                    current_url=url,
                )
            return True
        except TimeoutError:
            if coordinator is not None:
                coordinator.complete_dispatch(dispatch_token, False)
            return False
        except BaseException:
            if coordinator is not None:
                coordinator.complete_dispatch(dispatch_token, False)
            raise
        finally:
            finish_browser_action(tab, action_token)


reload_guard = ReloadGuard()


async def guarded_reload(
    tab: Any,
    reason: str = "",
    recovery: bool = False,
    timeout_seconds: float = DEFAULT_RELOAD_TIMEOUT_SECONDS,
    config_dict: dict[str, Any] | None = None,
) -> bool:
    scheduler = None
    scheduler_started = False
    # Import lazily: the platform contract owns a ReloadGuard instance and
    # importing the adapter registry at module load time would be circular.
    from platform_adapters import adapter_for_url
    from platform_engine import platform_engine
    from run_modes import is_leak_watch_mode

    url = getattr(getattr(tab, "target", None), "url", "") or ""
    adapter = adapter_for_url(url)
    coordinator = platform_engine.refresh_coordinator_for(tab)
    effective_config = config_dict
    runtime_state = None
    adapter_page_class = None
    if adapter is not None:
        runtime_state = platform_engine.state_for(tab, adapter)
        if effective_config is None:
            effective_config = runtime_state.config_snapshot
        # Trust an adapter override only for routes it explicitly declares
        # safe or protected. Unknown routes on known hosts still use the
        # conservative shared classifier.
        if adapter.is_safe_watch_page(url) or adapter.is_protected_page(url):
            adapter_page_class = adapter.classify_page(url)
    leak_mode = is_leak_watch_mode(effective_config)
    if leak_mode and adapter is None:
        runtime_log(
            "[LEAK WATCH] reload_blocked",
            effective_config,
            reason="unknown_platform_or_route",
            current_url=url,
        )
        return False
    if adapter is not None and adapter.key != "tixcraft" and leak_mode:
        try:
            state = runtime_state or platform_engine.state_for(tab, adapter)
            scheduler = state.leak_scheduler
            can_reload, scheduler_reason = scheduler.can_reload(
                effective_config,
                url,
            )
            if not can_reload:
                runtime_log(
                    "[LEAK WATCH] reload_blocked",
                    effective_config,
                    platform=adapter.key,
                    reason=scheduler_reason,
                    current_url=url,
                )
                return False
            scheduler_started = scheduler.begin_reload_cycle(url)
            if not scheduler_started:
                return False
        except (AttributeError, TypeError, ValueError) as exc:
            runtime_log(
                "[LEAK WATCH] reload_blocked",
                effective_config,
                platform=adapter.key,
                reason="scheduler_state_invalid",
                error_type=type(exc).__name__,
                current_url=url,
            )
            return False

    success = False
    try:
        active_guard = reload_guard
        if runtime_state is not None:
            active_guard = runtime_state.reload_guard
        priority = "periodic"
        if str(reason or "").startswith("refresh_datetime"):
            priority = "scheduled"
        elif recovery:
            priority = "recovery"
        success = await active_guard.reload(
            tab,
            reason=reason,
            recovery=recovery,
            timeout_seconds=timeout_seconds,
            config_dict=effective_config,
            coordinator=coordinator,
            priority=priority,
            page_class=adapter_page_class,
        )
        return success
    finally:
        if scheduler is not None and scheduler_started:
            scheduler.finish_reload_cycle(effective_config, success)
