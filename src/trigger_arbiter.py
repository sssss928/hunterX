from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlsplit

from leak_watch import is_protected_url
from page_classifier import PageClass, classify_page


TRIGGER_ARBITER_HISTORY_CAPACITY = 128

ReloadCallable = Callable[..., Awaitable[bool]]


@dataclass(frozen=True)
class TriggerReloadDecision:
    attempted: bool
    reloaded: bool
    reason: str
    page_class: PageClass


def _cached_target_url(tab: Any) -> str:
    try:
        target = getattr(tab, "target", None)
        value = getattr(target, "url", "") if target is not None else ""
    except Exception:
        return ""
    return value.strip() if isinstance(value, str) else ""


def _is_http_navigation_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)


def _effective_http_url(cached_url: str, supplied_url: str) -> str:
    if _is_http_navigation_url(cached_url):
        return cached_url
    if _is_http_navigation_url(supplied_url):
        return supplied_url
    return cached_url or supplied_url


def _route_key(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    path = (parsed.path or "/").rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{port}{path}"


def _finite_monotonic(value: float | None = None) -> float:
    if value is not None:
        try:
            candidate = float(value)
        except (TypeError, ValueError):
            candidate = math.nan
        if math.isfinite(candidate):
            return candidate
    return time.monotonic()


def _runtime_block_reason(
    runtime_state: Mapping[str, Any] | None,
    *,
    now_monotonic: float,
) -> str:
    if not isinstance(runtime_state, Mapping):
        return ""
    if runtime_state.get("manual_intervention_required"):
        return "manual_intervention"
    if runtime_state.get("soft_block_recovery_in_progress"):
        return "soft_block_recovery"
    if runtime_state.get("soft_block_recovery_scan_pending"):
        return "recovery_scan_pending"
    if runtime_state.get("queue_it_enter_time") is not None:
        return "queue_active"

    try:
        block_until = float(runtime_state.get("ip_block_until", 0.0) or 0.0)
    except (TypeError, ValueError):
        block_until = 0.0
    if math.isfinite(block_until) and block_until > now_monotonic:
        return "soft_block_backoff"
    try:
        soft_block_until = float(
            runtime_state.get("soft_block_backoff_until", 0.0) or 0.0
        )
    except (TypeError, ValueError):
        soft_block_until = 0.0
    if (
        math.isfinite(soft_block_until)
        and soft_block_until > now_monotonic
    ):
        return "soft_block_backoff"
    try:
        recovery_retry_at = float(
            runtime_state.get(
                "soft_block_recovery_retry_at",
                0.0,
            )
            or 0.0
        )
    except (TypeError, ValueError):
        recovery_retry_at = 0.0
    if (
        math.isfinite(recovery_retry_at)
        and recovery_retry_at > now_monotonic
    ):
        return "soft_block_recovery"
    soft_block_phase = str(
        runtime_state.get("soft_block_phase", "") or ""
    ).strip().lower()
    if soft_block_phase == "backoff":
        return "soft_block_backoff"
    if soft_block_phase:
        return "soft_block_recovery"

    if runtime_state.get("pending_area_navigation") is not None:
        return "navigation_pending"
    if runtime_state.get("pending_date_navigation") is not None:
        return "navigation_pending"

    scheduler = runtime_state.get("leak_scheduler")
    if scheduler is not None:
        pending_fields = (
            "reload_pending",
            "dom_scan_pending",
            "area_click_pending",
            "ticket_form_pending",
            "submit_pending",
        )
        for field_name in pending_fields:
            try:
                is_pending = bool(getattr(scheduler, field_name, False))
            except Exception:
                is_pending = True
            if is_pending:
                return field_name
    return ""


@dataclass
class TriggerReloadArbiter:
    """Single-flight safety boundary for scheduled refresh requests.

    The arbiter does not decide *when* the public-sale target is reached. It
    only ensures that a target-time request cannot overlap another target-time
    request or reload a queue, post-ticket page, recovery scan, or pending
    browser transition.
    """

    history: deque[TriggerReloadDecision] = field(
        default_factory=lambda: deque(maxlen=TRIGGER_ARBITER_HISTORY_CAPACITY)
    )
    _reload_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.history, deque) or self.history.maxlen != TRIGGER_ARBITER_HISTORY_CAPACITY:
            self.history = deque(
                self.history,
                maxlen=TRIGGER_ARBITER_HISTORY_CAPACITY,
            )

    def _decision(
        self,
        *,
        attempted: bool,
        reloaded: bool,
        reason: str,
        url: str,
    ) -> TriggerReloadDecision:
        decision = TriggerReloadDecision(
            attempted=attempted,
            reloaded=reloaded,
            reason=reason,
            page_class=classify_page(url),
        )
        self.history.append(decision)
        return decision

    def can_reload(
        self,
        tab: Any,
        *,
        current_url: str = "",
        runtime_state: Mapping[str, Any] | None = None,
        now_monotonic: float | None = None,
    ) -> TriggerReloadDecision:
        cached_url = _cached_target_url(tab)
        supplied_url = current_url.strip() if isinstance(current_url, str) else ""
        valid_cached_url = cached_url if _is_http_navigation_url(cached_url) else ""
        valid_supplied_url = supplied_url if _is_http_navigation_url(supplied_url) else ""
        effective_url = _effective_http_url(valid_cached_url, valid_supplied_url)
        if not effective_url:
            return self._decision(
                attempted=False,
                reloaded=False,
                reason="unsupported_url" if cached_url or supplied_url else "empty_url",
                url="",
            )

        # A supplied URL and the CDP-cached URL can briefly disagree during
        # navigation. Treat either protected observation as authoritative.
        if (
            (valid_supplied_url and is_protected_url(valid_supplied_url))
            or (valid_cached_url and is_protected_url(valid_cached_url))
        ):
            protected_url = (
                valid_supplied_url
                if is_protected_url(valid_supplied_url)
                else valid_cached_url
            )
            return self._decision(
                attempted=False,
                reloaded=False,
                reason="protected_page",
                url=protected_url,
            )

        runtime_reason = _runtime_block_reason(
            runtime_state,
            now_monotonic=_finite_monotonic(now_monotonic),
        )
        if runtime_reason:
            return self._decision(
                attempted=False,
                reloaded=False,
                reason=runtime_reason,
                url=effective_url,
            )

        return TriggerReloadDecision(
            attempted=False,
            reloaded=False,
            reason="ready",
            page_class=classify_page(effective_url),
        )

    async def request_reload(
        self,
        tab: Any,
        *,
        current_url: str = "",
        runtime_state: Mapping[str, Any] | None = None,
        reason: str,
        reload_callable: ReloadCallable,
        config_dict: dict[str, Any] | None = None,
        now_monotonic: float | None = None,
        expected_route: str = "",
    ) -> TriggerReloadDecision:
        cached_url = _cached_target_url(tab)
        supplied_url = (
            current_url.strip() if isinstance(current_url, str) else ""
        )
        effective_url = _effective_http_url(cached_url, supplied_url)
        if self._reload_lock.locked():
            return self._decision(
                attempted=False,
                reloaded=False,
                reason="reload_in_flight",
                url=effective_url,
            )

        async with self._reload_lock:
            if expected_route:
                latest_cached_url = _cached_target_url(tab)
                if _route_key(latest_cached_url) != expected_route:
                    return self._decision(
                        attempted=False,
                        reloaded=False,
                        reason="route_changed",
                        url=latest_cached_url,
                    )
            allowed = self.can_reload(
                tab,
                current_url=current_url,
                runtime_state=runtime_state,
                now_monotonic=now_monotonic,
            )
            if allowed.reason != "ready":
                return allowed

            try:
                reloaded = bool(
                    await reload_callable(
                        tab,
                        reason=reason,
                        config_dict=config_dict,
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                return self._decision(
                    attempted=True,
                    reloaded=False,
                    reason="reload_exception",
                    url=effective_url,
                )

            return self._decision(
                attempted=True,
                reloaded=reloaded,
                reason="reloaded" if reloaded else "reload_failed",
                url=effective_url,
            )
