"""Shared platform contract and declarative route policies.

The contract deliberately keeps platform DOM logic in the existing platform
modules.  It gives the central runtime one authoritative, fail-closed view of
page safety, capability evidence and per-instance leak-watch state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import inspect
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from leak_watch import LeakWatchScheduler
from page_classifier import PageClass
from reload_guard import ReloadGuard


class CapabilityStatus(str, Enum):
    MISSING = "missing"
    PARTIAL = "partial"
    COMPLETE = "complete"


@dataclass(frozen=True)
class PlatformCapabilities:
    onsale: CapabilityStatus
    leak_watch: CapabilityStatus
    inventory_scan: CapabilityStatus
    candidate_selection: CapabilityStatus
    protected_pages: CapabilityStatus
    recovery: CapabilityStatus
    watchdogs: CapabilityStatus

    @property
    def supports_leak_watch(self) -> bool:
        return self.leak_watch is CapabilityStatus.COMPLETE


@dataclass(frozen=True)
class RouteRule:
    page_class: PageClass
    markers: tuple[str, ...]


@dataclass
class PlatformRuntimeState:
    leak_scheduler: LeakWatchScheduler = field(default_factory=LeakWatchScheduler)
    reload_guard: ReloadGuard = field(default_factory=ReloadGuard)
    current_page: PageClass = PageClass.UNKNOWN
    previous_url: str = ""
    cycle_count: int = 0
    recovery_count: int = 0

    def backfill(self) -> None:
        """Repair state restored by hot reload without replacing live owners."""

        if not isinstance(self.leak_scheduler, LeakWatchScheduler):
            self.leak_scheduler = LeakWatchScheduler()
        if not isinstance(self.reload_guard, ReloadGuard):
            self.reload_guard = ReloadGuard()
        if not isinstance(self.current_page, PageClass):
            try:
                self.current_page = PageClass(self.current_page)
            except (TypeError, ValueError):
                self.current_page = PageClass.UNKNOWN
        self.previous_url = str(self.previous_url or "")
        self.cycle_count = max(0, int(self.cycle_count or 0))
        self.recovery_count = max(0, int(self.recovery_count or 0))

    def reset_attempt(self) -> None:
        self.leak_scheduler.reset_for_recovery()
        self.current_page = PageClass.UNKNOWN
        self.previous_url = ""
        self.recovery_count += 1


@runtime_checkable
class PlatformAdapter(Protocol):
    key: str
    display_name: str

    def matches_url(self, url: str) -> bool: ...
    def classify_page(self, url: str, text: str = "") -> PageClass: ...
    def is_safe_watch_page(self, url: str, text: str = "") -> bool: ...
    def is_protected_page(self, url: str, text: str = "") -> bool: ...
    def detect_queue(self, url: str, text: str = "") -> bool: ...
    def detect_retryable_failure(self, url: str, text: str = "") -> bool: ...
    def capabilities(self) -> PlatformCapabilities: ...
    async def wait_until_ready(self, context: BrowserContext) -> bool: ...
    async def scan_inventory(self, context: BrowserContext) -> Any: ...
    async def refresh_inventory(self, context: BrowserContext) -> bool: ...
    async def select_candidate(self, context: BrowserContext, config: dict[str, Any]) -> Any: ...
    async def recover(self, context: BrowserContext, reason: str) -> bool: ...
    def snapshot_attempt_metadata(self, context: BrowserContext) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DeclarativePlatformAdapter:
    key: str
    display_name: str
    hosts: tuple[str, ...]
    safe_rules: tuple[RouteRule, ...]
    protected_rules: tuple[RouteRule, ...]
    capability_set: PlatformCapabilities
    home_paths: tuple[str, ...] = ("/",)
    queue_markers: tuple[str, ...] = (
        "queue-it",
        "/queue",
        "/waiting-room",
        "/waitingroom",
        "queue=waiting",
        "queue=queued",
        "queue=true",
        "queue=1",
    )
    retryable_text: tuple[str, ...] = (
        "sold out",
        "unavailable",
        "not enough",
        "已售完",
        "售罄",
        "票數不足",
        "無可售",
        "請重新選擇",
        "別人搶先一步",
    )

    @staticmethod
    def _hostname(url: str) -> str:
        try:
            return (urlsplit(url).hostname or "").casefold().rstrip(".")
        except ValueError:
            return ""

    @staticmethod
    def _route(url: str) -> str:
        try:
            parsed = urlsplit(url)
        except ValueError:
            return ""
        route = parsed.path or "/"
        if parsed.query:
            route = f"{route}?{parsed.query}"
        return route.casefold()

    def matches_url(self, url: str) -> bool:
        hostname = self._hostname(url)
        return any(hostname == host or hostname.endswith(f".{host}") for host in self.hosts)

    def _match_rules(self, url: str, rules: tuple[RouteRule, ...]) -> PageClass | None:
        route = self._route(url)
        for rule in rules:
            if any(marker.casefold() in route for marker in rule.markers):
                return rule.page_class
        return None

    def detect_queue(self, url: str, text: str = "") -> bool:
        combined = f"{url}\n{text}".casefold()
        return any(marker.casefold() in combined for marker in self.queue_markers)

    def classify_page(self, url: str, text: str = "") -> PageClass:
        if not self.matches_url(url):
            return PageClass.UNKNOWN
        if self.detect_queue(url, text):
            return PageClass.QUEUE
        route = self._route(url)
        matches: list[tuple[int, bool, PageClass]] = []
        for protected, rules in ((False, self.safe_rules), (True, self.protected_rules)):
            for rule in rules:
                for marker in rule.markers:
                    if marker.casefold() in route:
                        matches.append((len(marker), protected, rule.page_class))
        if matches:
            # Most-specific route wins. Protection wins equal-specificity ties.
            return max(matches, key=lambda item: (item[0], item[1]))[2]
        if self._route(url).rstrip("/") in {path.casefold().rstrip("/") for path in self.home_paths}:
            return PageClass.HOME
        if self.detect_retryable_failure(url, text):
            return PageClass.REJECTED_ERROR
        return PageClass.UNKNOWN

    def is_safe_watch_page(self, url: str, text: str = "") -> bool:
        return self.classify_page(url, text) in {
            PageClass.ACTIVITY,
            PageClass.DATE,
            PageClass.AREA,
        }

    def is_protected_page(self, url: str, text: str = "") -> bool:
        return self.classify_page(url, text) in {
            PageClass.TICKET,
            PageClass.ORDER,
            PageClass.CHECKOUT,
            PageClass.PAYMENT,
            PageClass.QUEUE,
            PageClass.UNKNOWN,
        }

    def detect_retryable_failure(self, url: str, text: str = "") -> bool:
        del url
        lowered = (text or "").casefold()
        return bool(lowered) and any(marker.casefold() in lowered for marker in self.retryable_text)

    def capabilities(self) -> PlatformCapabilities:
        return self.capability_set

    @staticmethod
    async def _invoke_context(context: BrowserContext, name: str, *args: Any) -> Any:
        callback = context.get(name)
        if not callable(callback):
            return None
        result = callback(*args)
        if inspect.isawaitable(result):
            return await result
        return result

    async def wait_until_ready(self, context: BrowserContext) -> bool:
        from runtime_health import wait_for_interactive_ready

        tab = context.get("tab")
        if tab is None:
            return False
        return bool(
            await wait_for_interactive_ready(
                tab,
                context.get("config"),
                timeout_seconds=float(context.get("ready_timeout", 6.0)),
            )
        )

    async def scan_inventory(self, context: BrowserContext) -> Any:
        """Invoke the platform DOM/API scanner through a bounded engine hook."""

        from runtime_health import wait_for_operation

        callback = context.get("scan_inventory")
        if not callable(callback):
            return None
        return await wait_for_operation(
            self._invoke_context(context, "scan_inventory"),
            float(context.get("scan_timeout", 6.0)),
            f"{self.key}_inventory_scan",
            context.get("config"),
            default=None,
        )

    async def refresh_inventory(self, context: BrowserContext) -> bool:
        from reload_guard import guarded_reload

        tab = context.get("tab")
        url = str(context.get("url") or "")
        if tab is None or not self.is_safe_watch_page(url):
            return False
        return await guarded_reload(
            tab,
            reason=f"{self.key}_inventory_refresh",
            config_dict=context.get("config"),
        )

    async def select_candidate(self, context: BrowserContext, config: dict[str, Any]) -> Any:
        from runtime_health import wait_for_operation

        callback = context.get("select_candidate")
        if not callable(callback):
            return None
        return await wait_for_operation(
            self._invoke_context(context, "select_candidate", config),
            float(context.get("click_timeout", 6.0)),
            f"{self.key}_candidate_select",
            context.get("config"),
            default=None,
        )

    async def recover(self, context: BrowserContext, reason: str) -> bool:
        url = str(context.get("url") or "")
        if self.detect_queue(url, str(context.get("text") or "")):
            return False
        result = await self._invoke_context(context, "recover", reason)
        return bool(result)

    def snapshot_attempt_metadata(self, context: BrowserContext) -> dict[str, Any]:
        return {
            "platform": self.key,
            "page_class": self.classify_page(
                str(context.get("url") or ""),
                str(context.get("text") or ""),
            ).value,
            "event": str(context.get("event") or "")[:256],
            "candidate": str(context.get("candidate") or "")[:256],
        }


def complete_capabilities() -> PlatformCapabilities:
    return PlatformCapabilities(
        onsale=CapabilityStatus.COMPLETE,
        leak_watch=CapabilityStatus.COMPLETE,
        inventory_scan=CapabilityStatus.COMPLETE,
        candidate_selection=CapabilityStatus.COMPLETE,
        protected_pages=CapabilityStatus.COMPLETE,
        recovery=CapabilityStatus.COMPLETE,
        watchdogs=CapabilityStatus.COMPLETE,
    )


def partial_capabilities() -> PlatformCapabilities:
    return PlatformCapabilities(
        onsale=CapabilityStatus.COMPLETE,
        leak_watch=CapabilityStatus.PARTIAL,
        inventory_scan=CapabilityStatus.COMPLETE,
        candidate_selection=CapabilityStatus.COMPLETE,
        protected_pages=CapabilityStatus.COMPLETE,
        recovery=CapabilityStatus.PARTIAL,
        watchdogs=CapabilityStatus.PARTIAL,
    )


BrowserContext = dict[str, Any]
