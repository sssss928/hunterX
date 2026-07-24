from __future__ import annotations

from dataclasses import dataclass, field

from page_classifier import PageClass, classify_page, is_protected_after_ticket
from runtime_health import DEFAULT_RELOAD_TIMEOUT_SECONDS, runtime_log, wait_for_operation


@dataclass
class ReloadDecision:
    allowed: bool
    reason: str
    page_class: PageClass


@dataclass
class ReloadGuard:
    history: list[ReloadDecision] = field(default_factory=list)

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
        tab,
        reason: str = "",
        recovery: bool = False,
        timeout_seconds: float = DEFAULT_RELOAD_TIMEOUT_SECONDS,
        config_dict: dict | None = None,
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
        failed = object()
        try:
            result = await wait_for_operation(
                tab.reload(),
                timeout_seconds,
                "RELOAD",
                config_dict,
                default=failed,
                raise_on_timeout=True,
                operation_owner=tab,
            )
            return result is not failed
        except TimeoutError:
            return False


reload_guard = ReloadGuard()


async def guarded_reload(
    tab,
    reason: str = "",
    recovery: bool = False,
    timeout_seconds: float = DEFAULT_RELOAD_TIMEOUT_SECONDS,
    config_dict: dict | None = None,
) -> bool:
    return await reload_guard.reload(
        tab,
        reason=reason,
        recovery=recovery,
        timeout_seconds=timeout_seconds,
        config_dict=config_dict,
    )
