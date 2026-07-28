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

    def can_reload(self, url: str = "", reason: str = "", recovery: bool = False) -> ReloadDecision:
        page_class = classify_page(url)
        allowed = True
        if is_protected_after_ticket(page_class) and not recovery:
            allowed = False
        decision = ReloadDecision(allowed=allowed, reason=reason or "unspecified", page_class=page_class)
        self.history.append(decision)
        return decision

    async def reload(
        self,
        tab: Any,
        reason: str = "",
        recovery: bool = False,
        timeout_seconds: float = DEFAULT_RELOAD_TIMEOUT_SECONDS,
        config_dict: dict[str, Any] | None = None,
    ) -> bool:
        url = getattr(getattr(tab, "target", None), "url", "") or ""
        decision = self.can_reload(url=url, reason=reason, recovery=recovery)
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
        try:
            await wait_for_operation(
                tab.reload(),
                timeout_seconds,
                "RELOAD",
                config_dict,
                raise_on_timeout=True,
            )
            return True
        except TimeoutError:
            return False
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
    return await reload_guard.reload(
        tab,
        reason=reason,
        recovery=recovery,
        timeout_seconds=timeout_seconds,
        config_dict=config_dict,
    )
