from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from page_classifier import PageClass, classify_page, is_protected_after_ticket
from run_modes import is_leak_watch_mode


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
