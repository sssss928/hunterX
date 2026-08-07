from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlsplit

from page_classifier import PageClass, classify_page, is_protected_after_ticket
from run_modes import get_leak_refresh_interval, is_leak_watch_mode


RECOVERY_LANDING_MIN_INTERVAL_SECONDS = 1.0
LEAK_WATCH_HISTORY_CAPACITY = 256
DOM_SCAN_PENDING_TIMEOUT_SECONDS = 15.0
AREA_CLICK_PENDING_MIN_SECONDS = 1.0
AREA_CLICK_PENDING_MAX_SECONDS = 10.0
# One full reload cycle can spend 10s in guarded reload plus 6s waiting for an
# interactive document. Keep a 4s watchdog margin so a legitimate cycle cannot
# be expired while its final ready-state check is still running.
RELOAD_PENDING_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class LeakWatchPolicy:
    platform: str
    hosts: tuple[str, ...]
    safe_markers: tuple[str, ...]
    protected_markers: tuple[str, ...] = ()


LEAK_WATCH_POLICIES: tuple[LeakWatchPolicy, ...] = (
    LeakWatchPolicy("TixCraft", ("tixcraft.com", "indievox.com", "ticketmaster."), ("/activity/", "/ticket/area/", "/ticket/check-captcha/"), ("/ticket/ticket/", "/ticket/order", "/ticket/checkout", "/payment")),
    LeakWatchPolicy("KKTIX", ("kktix.",), ("/events/", "/registrations/new", "/registrations/", "/events/"), ("/orders/", "/checkout", "/payment")),
    LeakWatchPolicy("TicketPlus", ("ticketplus.com",), ("/activity/", "/order/", "/ticket/"), ("/confirm/", "/checkout", "/payment")),
    LeakWatchPolicy("iBon", ("ibon.com",), ("/activity/", "/event/", "/ticket/", "/performance/"), ("/checkout", "/payment", "/order")),
    LeakWatchPolicy("KHAM", ("kham.com.tw",), ("/application/UTK", "/event/", "/performance/"), ("/checkout", "/payment", "/order")),
    LeakWatchPolicy("ticket.com.tw", ("ticket.com.tw",), ("/application/UTK", "/event/", "/performance/"), ("/checkout", "/payment", "/order")),
    LeakWatchPolicy("udnfunlife", ("tickets.udnfunlife.com",), ("/application/UTK", "/event/", "/performance/"), ("/checkout", "/payment", "/order")),
    LeakWatchPolicy("Cityline", ("cityline.com", "cityline.com.hk"), ("/event", "/performance", "/utsvInternet/"), ("/checkout", "/payment", "/order")),
    LeakWatchPolicy("FunOne", ("tickets.funone.io",), ("/events/", "/sales/", "/ticket"), ("/orders/", "/checkout", "/payment")),
    LeakWatchPolicy("HKTicketing", ("hkticketing.com", "galaxymacau.com", "ticketek.com"), ("/events/", "/event/", "/performance", "/secure/selection"), ("/checkout", "/payment", "/order")),
    LeakWatchPolicy("FamiTicket", ("famiticket.com",), ("/Activity/", "/Home/Activity", "/ticket"), ("/Order/", "/Checkout", "/Payment")),
    LeakWatchPolicy("FANSI GO", ("go.fansi.me",), ("/events/", "/event/", "/ticket"), ("/orders/", "/checkout", "/payment")),
)


def iter_policies() -> Iterable[LeakWatchPolicy]:
    return LEAK_WATCH_POLICIES


def get_policy_for_url(url: str) -> LeakWatchPolicy | None:
    try:
        hostname = (urlsplit(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return None
    if not hostname:
        return None
    for policy in LEAK_WATCH_POLICIES:
        for raw_host in policy.hosts:
            host = raw_host.casefold().strip(".")
            if raw_host.endswith("."):
                if hostname.startswith(host):
                    return policy
            elif hostname == host or hostname.endswith(f".{host}"):
                return policy
    return None


def is_protected_url(url: str) -> bool:
    try:
        from platform_adapters import adapter_for_url

        adapter = adapter_for_url(url)
        if adapter is not None:
            return adapter.is_protected_page(url)
    except ImportError:
        pass
    page_class = classify_page(url)
    if page_class == PageClass.QUEUE:
        return True
    if page_class != PageClass.UNKNOWN and is_protected_after_ticket(page_class):
        return True
    url_lower = (url or "").lower()
    policy = get_policy_for_url(url_lower)
    if not policy:
        return False
    return any(marker.lower() in url_lower for marker in policy.protected_markers)


def is_safe_page(url: str) -> bool:
    try:
        from platform_adapters import adapter_for_url

        adapter = adapter_for_url(url)
        if adapter is not None:
            return adapter.is_safe_watch_page(url)
    except ImportError:
        pass
    if is_protected_url(url):
        return False
    url_lower = (url or "").lower()
    policy = get_policy_for_url(url_lower)
    if not policy:
        return False
    return any(marker.lower() in url_lower for marker in policy.safe_markers)


def should_use_leak_watch(config_dict: dict | None, url: str = "") -> bool:
    return is_leak_watch_mode(config_dict) and (not url or is_safe_page(url))


@dataclass
class LeakWatchScheduler:
    """Stateful leak-watch cycle guard.

    The browser automation loop is single-threaded, but awaited browser
    operations can stall. These flags prevent a reload/DOM/click cycle from
    being stacked on top of a still-pending previous cycle.
    """

    reload_pending: bool = False
    dom_scan_pending: bool = False
    area_click_pending: bool = False
    ticket_form_pending: bool = False
    submit_pending: bool = False
    next_cycle_at: float = 0.0
    cycle_started_at: float = 0.0
    last_cycle_url: str = ""
    last_dom_read_at: float = 0.0
    dom_scan_started_at: float = 0.0
    # True after the current document has completed one leak-watch DOM scan.
    # A successful reload/recovery clears it so the fresh document can be read
    # exactly once before the scheduler waits for the next refresh cycle.
    dom_scan_completed_since_reload: bool = False
    last_area_click_at: float = 0.0
    last_clicked_url: str = ""
    history: deque[str] = field(
        default_factory=lambda: deque(maxlen=LEAK_WATCH_HISTORY_CAPACITY)
    )

    def __post_init__(self) -> None:
        if not isinstance(self.history, deque) or self.history.maxlen != LEAK_WATCH_HISTORY_CAPACITY:
            self.history = deque(
                self.history,
                maxlen=LEAK_WATCH_HISTORY_CAPACITY,
            )

    @staticmethod
    def _now(now: float | None = None) -> float:
        if now is not None:
            try:
                candidate = float(now)
            except (TypeError, ValueError):
                candidate = math.nan
            if math.isfinite(candidate):
                return candidate
        return time.monotonic()

    def _record(self, event: str) -> None:
        self.history.append(event)

    @staticmethod
    def _pending_expired(started_at: float, timeout_seconds: float, now: float) -> bool:
        try:
            elapsed = now - float(started_at)
        except (TypeError, ValueError):
            return True
        # A future timestamp means state from a different clock domain (for
        # example, an older wall-clock value restored into monotonic state).
        # It must fail open instead of pinning the single-flight guard forever.
        return (
            not math.isfinite(elapsed)
            or elapsed < 0.0
            or elapsed >= max(0.0, timeout_seconds)
        )

    @staticmethod
    def _area_click_timeout(config_dict: dict | None) -> float:
        interval = get_leak_refresh_interval(config_dict)
        return min(
            AREA_CLICK_PENDING_MAX_SECONDS,
            max(AREA_CLICK_PENDING_MIN_SECONDS, interval),
        )

    def _clear_pending_state(self) -> None:
        self.reload_pending = False
        self.cycle_started_at = 0.0
        self.dom_scan_pending = False
        self.dom_scan_started_at = 0.0
        self.dom_scan_completed_since_reload = False
        self.area_click_pending = False
        self.last_area_click_at = 0.0
        self.ticket_form_pending = False
        self.submit_pending = False
        self.last_clicked_url = ""

    def reset_for_recovery(self) -> None:
        """Clear an interrupted cycle before recovery navigation starts.

        This preserves the existing immediate-retry semantics for callers that
        have not reached a safe landing page yet. Once recovery has landed on a
        safe page, callers should use :meth:`mark_recovery_landed` so the normal
        refresh interval is respected before another leak-watch reload.
        """

        self._clear_pending_state()
        self.next_cycle_at = 0.0
        self._record("reset_for_recovery")

    def mark_recovery_landed(self, config_dict: dict | None, now: float | None = None) -> None:
        """Finish recovery on a safe page without immediately reloading it.

        Recovery navigation itself is already a fresh page load. Scheduling
        the next cycle from the landing timestamp prevents a second reload from
        firing in the same automation-loop iteration.
        """

        # Recovery callers normally already have a validated monotonic float.
        # Keep the non-finite/type fallback, but avoid the generic conversion
        # helper and two extra method calls on this measured hot path.
        if type(now) is float:
            landed_at = now if math.isfinite(now) else time.monotonic()
        else:
            landed_at = self._now(now)
        interval = get_leak_refresh_interval(config_dict)
        if interval < RECOVERY_LANDING_MIN_INTERVAL_SECONDS:
            interval = RECOVERY_LANDING_MIN_INTERVAL_SECONDS
        self.reload_pending = False
        self.cycle_started_at = 0.0
        self.dom_scan_pending = False
        self.dom_scan_started_at = 0.0
        self.dom_scan_completed_since_reload = False
        self.area_click_pending = False
        self.last_area_click_at = 0.0
        self.ticket_form_pending = False
        self.submit_pending = False
        self.last_clicked_url = ""
        self.next_cycle_at = landed_at + interval
        self.history.append("recovery_landed")

    def mark_dom_scan_start(self, now: float | None = None) -> bool:
        if self.dom_scan_pending:
            if not self.history or self.history[-1] != "dom_scan_skip_pending":
                self._record("dom_scan_skip_pending")
            return False
        self.dom_scan_pending = True
        self.dom_scan_started_at = self._now(now)
        self._record("dom_scan_start")
        return True

    def mark_dom_scan_end(self, now: float | None = None) -> None:
        self.dom_scan_pending = False
        self.dom_scan_started_at = 0.0
        self.last_dom_read_at = self._now(now)
        self.dom_scan_completed_since_reload = True
        self._record("dom_scan_end")

    def should_wait_for_reload_before_dom_scan(self, config_dict: dict | None) -> bool:
        """Return whether the current safe-page document was already scanned.

        A positive leak-watch interval represents a refresh/re-scan cycle. Once
        the current document has been read, repeatedly querying the same DOM in
        the hot main loop adds browser/CDP load without observing a new server
        response. Keep interval=0 behavior unchanged: it disables timed reloads
        and therefore does not apply this per-document scan gate.
        """

        return (
            get_leak_refresh_interval(config_dict) > 0.0
            and self.dom_scan_completed_since_reload
        )

    def mark_area_click_pending(self, url: str = "", now: float | None = None) -> bool:
        if self.area_click_pending:
            if not self.history or self.history[-1] != "area_click_skip_pending":
                self._record("area_click_skip_pending")
            return False
        self.area_click_pending = True
        self.last_area_click_at = self._now(now)
        self.last_clicked_url = url or ""
        self._record("area_click_pending")
        return True

    def clear_area_click_pending(self) -> bool:
        if not self.area_click_pending:
            return False
        self.area_click_pending = False
        self.last_area_click_at = 0.0
        self.last_clicked_url = ""
        self._record("area_click_clear")
        return True

    def maintenance(
        self,
        config_dict: dict | None,
        url: str = "",
        now: float | None = None,
    ) -> tuple[str, ...]:
        """Expire stale single-flight guards without requiring a reload attempt.

        Callers may invoke this cheap operation on every automation-loop
        iteration, including while the current page is protected or not ready.
        Normal ``finally`` cleanup remains the primary lifecycle mechanism;
        these deadlines are defensive liveness guards.
        """

        del url  # Reserved for page-specific diagnostics without affecting expiry.
        current = self._now(now)
        expired: list[str] = []
        interval = max(0.0, get_leak_refresh_interval(config_dict))

        try:
            next_cycle_at = float(self.next_cycle_at)
        except (TypeError, ValueError):
            next_cycle_at = math.nan
        if not math.isfinite(next_cycle_at):
            self.next_cycle_at = current
            self._record("next_cycle_deadline_repaired")
        else:
            self.next_cycle_at = next_cycle_at
            maximum_delay = max(RECOVERY_LANDING_MIN_INTERVAL_SECONDS, interval)
            if self.next_cycle_at - current > maximum_delay:
                self.next_cycle_at = current + maximum_delay
                self._record("next_cycle_deadline_repaired")

        if self.reload_pending and self._pending_expired(
            self.cycle_started_at,
            RELOAD_PENDING_TIMEOUT_SECONDS,
            current,
        ):
            self.reload_pending = False
            self.cycle_started_at = 0.0
            self.next_cycle_at = max(self.next_cycle_at, current + interval)
            expired.append("reload_pending_expired")
            self._record("reload_pending_expired")

        if self.dom_scan_pending and self._pending_expired(
            self.dom_scan_started_at,
            DOM_SCAN_PENDING_TIMEOUT_SECONDS,
            current,
        ):
            self.dom_scan_pending = False
            self.dom_scan_started_at = 0.0
            expired.append("dom_scan_pending_expired")
            self._record("dom_scan_pending_expired")

        if self.area_click_pending and self._pending_expired(
            self.last_area_click_at,
            self._area_click_timeout(config_dict),
            current,
        ):
            self.area_click_pending = False
            self.last_area_click_at = 0.0
            self.last_clicked_url = ""
            expired.append("area_click_pending_expired")
            self._record("area_click_pending_expired")

        return tuple(expired)

    def can_reload(self, config_dict: dict | None, url: str, now: float | None = None) -> tuple[bool, str]:
        current = self._now(now)
        self.maintenance(config_dict, url, current)
        if not should_use_leak_watch(config_dict, url):
            return False, "not_leak_safe_page"
        if is_protected_url(url):
            return False, "protected_page"
        if self.reload_pending:
            return False, "reload_pending"
        if self.dom_scan_pending:
            return False, "dom_scan_pending"
        if self.area_click_pending:
            return False, "area_click_pending"
        if current < self.next_cycle_at:
            return False, "interval_wait"
        return True, "ready"

    def begin_reload_cycle(self, url: str, now: float | None = None) -> bool:
        if self.reload_pending:
            if not self.history or self.history[-1] != "cycle_skip_pending":
                self._record("cycle_skip_pending")
            return False
        self.reload_pending = True
        self.cycle_started_at = self._now(now)
        self.last_cycle_url = url or ""
        self._record("cycle_start")
        return True

    def finish_reload_cycle(
        self,
        config_dict: dict | None,
        success: bool,
        now: float | None = None,
    ) -> None:
        interval = get_leak_refresh_interval(config_dict)
        finished_at = self._now(now)
        self.reload_pending = False
        self.cycle_started_at = 0.0
        if success:
            # The browser now owns a fresh document. Allow exactly one new DOM
            # scan on the next platform iteration; failed reloads keep the old
            # document marked as consumed to avoid returning to the hot loop.
            self.dom_scan_completed_since_reload = False
        self.next_cycle_at = finished_at + max(0.0, interval)
        self._record("cycle_done" if success else "cycle_timeout")
