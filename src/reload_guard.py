from __future__ import annotations

from dataclasses import dataclass, field

from page_classifier import PageClass, classify_page, is_protected_after_ticket


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

    async def reload(self, tab, reason: str = "", recovery: bool = False) -> bool:
        url = getattr(getattr(tab, "target", None), "url", "") or ""
        decision = self.can_reload(url=url, reason=reason, recovery=recovery)
        if not decision.allowed:
            return False
        await tab.reload()
        return True


reload_guard = ReloadGuard()


async def guarded_reload(tab, reason: str = "", recovery: bool = False) -> bool:
    return await reload_guard.reload(tab, reason=reason, recovery=recovery)
