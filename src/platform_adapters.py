"""Authoritative runtime adapters for every supported ticketing family."""

from __future__ import annotations

from typing import cast

from page_classifier import PageClass
from platform_contract import (
    DeclarativePlatformAdapter,
    PlatformAdapter,
    RouteRule,
    complete_capabilities,
)


def _rules(page_class: PageClass, *markers: str) -> RouteRule:
    return RouteRule(page_class, tuple(marker.casefold() for marker in markers))


_COMPLETE = complete_capabilities()

ADAPTERS: tuple[DeclarativePlatformAdapter, ...] = (
    DeclarativePlatformAdapter(
        "tixcraft",
        "TixCraft / IndieVox / Ticketmaster",
        ("tixcraft.com", "indievox.com", "ticketmaster.sg"),
        (
            _rules(PageClass.AREA, "/ticket/area/"),
            _rules(PageClass.DATE, "/activity/game/", "/activity/game"),
            _rules(PageClass.ACTIVITY, "/activity/detail/", "/activity/detail"),
        ),
        (
            _rules(PageClass.PAYMENT, "/payment", "ecpay"),
            _rules(PageClass.CHECKOUT, "/checkout"),
            _rules(PageClass.ORDER, "/order"),
            _rules(PageClass.TICKET, "/ticket/ticket/", "/ticket/check-captcha/", "/ticket/verify/"),
        ),
        _COMPLETE,
        ("/", "/activity"),
    ),
    DeclarativePlatformAdapter(
        "kktix",
        "KKTIX",
        ("kktix.com", "kktix.cc"),
        (
            _rules(PageClass.AREA, "/registrations/new"),
            _rules(PageClass.DATE, "/events/"),
            _rules(PageClass.ACTIVITY, "/events/"),
        ),
        (
            _rules(PageClass.PAYMENT, "/payment"),
            _rules(PageClass.CHECKOUT, "/checkout"),
            _rules(PageClass.ORDER, "/orders/", "/registrations/"),
        ),
        _COMPLETE,
    ),
    DeclarativePlatformAdapter(
        "ticketplus",
        "TicketPlus",
        ("ticketplus.com.tw", "ticketplus.com"),
        (
            _rules(PageClass.AREA, "/order/"),
            _rules(PageClass.DATE, "/activity/"),
            _rules(PageClass.ACTIVITY, "/activity/"),
        ),
        (
            _rules(PageClass.PAYMENT, "/payment"),
            _rules(PageClass.CHECKOUT, "/checkout", "/confirm/"),
            _rules(PageClass.TICKET, "/ticket/"),
        ),
        _COMPLETE,
    ),
    DeclarativePlatformAdapter(
        "ibon",
        "iBon",
        ("ibon.com.tw", "ibon.com"),
        (
            _rules(PageClass.AREA, "/performance/", "/ticket/"),
            _rules(PageClass.DATE, "/activity/", "/activityinfo/", "/event/"),
            _rules(PageClass.ACTIVITY, "/activity/", "/activityinfo/", "/event/"),
        ),
        (
            _rules(PageClass.PAYMENT, "/payment"),
            _rules(PageClass.CHECKOUT, "/checkout"),
            _rules(PageClass.ORDER, "/order"),
        ),
        _COMPLETE,
    ),
    DeclarativePlatformAdapter(
        "kham",
        "KHAM / ticket.com.tw / UDN",
        ("kham.com.tw", "ticket.com.tw", "tickets.udnfunlife.com"),
        (
            _rules(PageClass.AREA, "performance", "salestable"),
            _rules(PageClass.DATE, "product", "activity"),
            _rules(PageClass.ACTIVITY, "/application/utk", "/event/"),
        ),
        (
            _rules(PageClass.PAYMENT, "payment", "pay.aspx"),
            _rules(PageClass.CHECKOUT, "checkout", "confirm"),
            _rules(
                PageClass.ORDER,
                "/application/utk02/utk0206",
                "utk0206",
                "order",
                "shoppingcart",
            ),
            _rules(PageClass.TICKET, "ticketseat", "seat.aspx"),
        ),
        _COMPLETE,
    ),
    DeclarativePlatformAdapter(
        "famiticket",
        "FamiTicket",
        ("famiticket.com.tw", "famiticket.com"),
        (
            _rules(PageClass.AREA, "/ticket", "/home/activity"),
            _rules(PageClass.DATE, "/activity/"),
            _rules(PageClass.ACTIVITY, "/activity/"),
        ),
        (
            _rules(PageClass.PAYMENT, "/payment"),
            _rules(PageClass.CHECKOUT, "/checkout"),
            _rules(PageClass.ORDER, "/order/"),
        ),
        _COMPLETE,
    ),
    DeclarativePlatformAdapter(
        "funone",
        "FunOne",
        ("tickets.funone.io",),
        (
            _rules(PageClass.AREA, "/sales/", "/ticket"),
            _rules(PageClass.DATE, "/events/"),
            _rules(PageClass.ACTIVITY, "/events/"),
        ),
        (
            _rules(PageClass.PAYMENT, "/payment"),
            _rules(PageClass.CHECKOUT, "/checkout"),
            _rules(PageClass.ORDER, "/orders/"),
        ),
        _COMPLETE,
    ),
    DeclarativePlatformAdapter(
        "fansigo",
        "FANSI GO",
        ("go.fansi.me", "fansidev.auth.ap-southeast-1.amazoncognito.com"),
        (
            _rules(PageClass.AREA, "/ticket"),
            _rules(PageClass.DATE, "/events/", "/event/"),
            _rules(PageClass.ACTIVITY, "/events/", "/event/"),
        ),
        (
            _rules(PageClass.PAYMENT, "/payment"),
            _rules(PageClass.CHECKOUT, "/checkout"),
            _rules(PageClass.ORDER, "/orders/"),
        ),
        _COMPLETE,
    ),
    DeclarativePlatformAdapter(
        "cityline",
        "Cityline",
        ("cityline.com", "cityline.com.hk"),
        (
            _rules(PageClass.AREA, "/performance", "/utsvinternet/"),
            _rules(PageClass.DATE, "/event"),
            _rules(PageClass.ACTIVITY, "/event"),
        ),
        (
            _rules(PageClass.PAYMENT, "/payment"),
            _rules(PageClass.CHECKOUT, "/checkout"),
            _rules(PageClass.ORDER, "/order"),
            _rules(PageClass.TICKET, "/secure/selection"),
        ),
        _COMPLETE,
    ),
    DeclarativePlatformAdapter(
        "hkticketing",
        "HKTicketing / Galaxy Macau / Ticketek",
        ("hkticketing.com", "galaxymacau.com", "ticketek.com.sg", "ticketek.com"),
        (
            _rules(PageClass.AREA, "/performance", "/secure/selection"),
            _rules(PageClass.DATE, "/events/", "/event/"),
            _rules(PageClass.ACTIVITY, "/events/", "/event/"),
        ),
        (
            _rules(PageClass.PAYMENT, "/payment"),
            _rules(PageClass.CHECKOUT, "/checkout"),
            _rules(PageClass.ORDER, "/order"),
        ),
        _COMPLETE,
    ),
)


def adapter_for_url(url: str) -> PlatformAdapter | None:
    matches = tuple(adapter for adapter in ADAPTERS if adapter.matches_url(url))
    if len(matches) > 1:
        raise RuntimeError(f"Ambiguous platform adapter for URL: {url!r}")
    return cast(PlatformAdapter | None, matches[0] if matches else None)


def adapter_for_key(key: str) -> PlatformAdapter | None:
    normalized = str(key or "").casefold()
    return cast(
        PlatformAdapter | None,
        next((adapter for adapter in ADAPTERS if adapter.key == normalized), None),
    )
