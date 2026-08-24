"""Fail-closed selector resolution for known, route-scoped DOM drift."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import runtime_health
from page_classifier import PageClass


MIN_FALLBACK_CONFIDENCE = 0.8


@dataclass(frozen=True)
class SelectorCandidate:
    selector: str
    confidence: float
    evidence: str
    allowed_pages: frozenset[PageClass]

    def allowed_on(self, page_class: PageClass) -> bool:
        return page_class in self.allowed_pages


@dataclass(frozen=True)
class SelectorResolution:
    element: Any | None
    selector: str
    used_fallback: bool
    confidence: float
    reason: str


async def resolve_selector(
    tab: Any,
    primary: SelectorCandidate,
    fallbacks: Iterable[SelectorCandidate],
    *,
    page_class: PageClass,
    config_dict: dict[str, Any] | None = None,
) -> SelectorResolution:
    """Resolve primary then high-confidence known fallbacks; never click here."""

    if not primary.allowed_on(page_class):
        return SelectorResolution(None, "", False, 0.0, "route_not_allowed")
    try:
        element = await tab.query_selector(primary.selector)
    except Exception as exc:
        runtime_health.raise_if_terminal_browser_error(exc)
        element = None
    if element is not None:
        return SelectorResolution(
            element,
            primary.selector,
            False,
            float(primary.confidence),
            "primary",
        )

    for candidate in fallbacks:
        if (
            not candidate.allowed_on(page_class)
            or float(candidate.confidence) < MIN_FALLBACK_CONFIDENCE
            or not str(candidate.evidence or "").strip()
        ):
            continue
        try:
            element = await tab.query_selector(candidate.selector)
        except Exception as exc:
            runtime_health.raise_if_terminal_browser_error(exc)
            element = None
        if element is None:
            continue
        runtime_health.runtime_log(
            "DOM_DRIFT",
            config_dict,
            reason="known_fallback_selected",
            page_class=page_class.value,
            selector=candidate.selector,
            evidence=candidate.evidence,
            confidence=float(candidate.confidence),
        )
        return SelectorResolution(
            element,
            candidate.selector,
            True,
            float(candidate.confidence),
            "known_fallback",
        )

    runtime_health.runtime_log(
        "DOM_DRIFT",
        config_dict,
        reason="no_confident_selector",
        page_class=page_class.value,
    )
    return SelectorResolution(None, "", False, 0.0, "no_confident_selector")
