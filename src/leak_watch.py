from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

from page_classifier import PageClass, classify_page, is_protected_after_ticket
from run_modes import get_leak_refresh_interval, is_leak_watch_mode


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
    url_lower = (url or "").lower()
    for policy in LEAK_WATCH_POLICIES:
        if any(host.lower() in url_lower for host in policy.hosts):
            return policy
    return None


def is_protected_url(url: str) -> bool:
    page_class = classify_page(url)
    if page_class != PageClass.UNKNOWN and is_protected_after_ticket(page_class):
        return True
    url_lower = (url or "").lower()
    policy = get_policy_for_url(url_lower)
    if not policy:
        return False
    return any(marker.lower() in url_lower for marker in policy.protected_markers)


def is_safe_page(url: str) -> bool:
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
    last_area_click_at: float = 0.0
    last_clicked_url: str = ""
    history: list[str] = field(default_factory=list)

    def _record(self, event: str) -> None:
        self.history.append(event)

    def reset_for_recovery(self) -> None:
        self.reload_pending = False
        self.dom_scan_pending = False
        self.area_click_pending = False
        self.ticket_form_pending = False
        self.submit_pending = False
        self.next_cycle_at = 0.0
        self.last_clicked_url = ""
        self._record("reset_for_recovery")

    def mark_dom_scan_start(self) -> bool:
        if self.dom_scan_pending:
            self._record("dom_scan_skip_pending")
            return False
        self.dom_scan_pending = True
        self._record("dom_scan_start")
        return True

    def mark_dom_scan_end(self) -> None:
        self.dom_scan_pending = False
        self.last_dom_read_at = time.time()
        self._record("dom_scan_end")

    def mark_area_click_pending(self, url: str = "") -> None:
        self.area_click_pending = True
        self.last_area_click_at = time.time()
        self.last_clicked_url = url or ""
        self._record("area_click_pending")

    def clear_area_click_pending(self) -> None:
        self.area_click_pending = False
        self._record("area_click_clear")

    def can_reload(self, config_dict: dict | None, url: str, now: float | None = None) -> tuple[bool, str]:
        now = time.time() if now is None else now
        if not should_use_leak_watch(config_dict, url):
            return False, "not_leak_safe_page"
        if is_protected_url(url):
            return False, "protected_page"
        if self.reload_pending:
            return False, "reload_pending"
        if self.dom_scan_pending:
            return False, "dom_scan_pending"
        if self.area_click_pending:
            interval = max(0.0, get_leak_refresh_interval(config_dict))
            if now - self.last_area_click_at < interval:
                return False, "area_click_pending"
            self.clear_area_click_pending()
        if now < self.next_cycle_at:
            return False, "interval_wait"
        return True, "ready"

    def begin_reload_cycle(self, url: str) -> None:
        self.reload_pending = True
        self.cycle_started_at = time.time()
        self.last_cycle_url = url or ""
        self._record("cycle_start")

    def finish_reload_cycle(self, config_dict: dict | None, success: bool) -> None:
        interval = get_leak_refresh_interval(config_dict)
        self.reload_pending = False
        self.next_cycle_at = time.time() + max(0.0, interval)
        self._record("cycle_done" if success else "cycle_timeout")
