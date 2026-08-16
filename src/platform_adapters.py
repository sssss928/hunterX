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
from platform_registry import platform_spec_for_key


def _rules(page_class: PageClass, *markers: str) -> RouteRule:
    return RouteRule(page_class, tuple(marker.casefold() for marker in markers))


_COMPLETE = complete_capabilities()


def _identity(key: str) -> tuple[str, tuple[str, ...]]:
    spec = platform_spec_for_key(key)
    if spec is None:
        raise RuntimeError(f"Missing PlatformSpec for adapter: {key}")
    return spec.display_name, spec.hosts

ADAPTERS: tuple[DeclarativePlatformAdapter, ...] = (
    DeclarativePlatformAdapter(
        "tixcraft",
        *_identity("tixcraft"),
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
        *_identity("kktix"),
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
        *_identity("ticketplus"),
        (
            _rules(PageClass.AREA, "/order/"),
            _rules(PageClass.DATE, "/activity/"),
            _rules(PageClass.ACTIVITY, "/activity/"),
        ),
        (
            _rules(PageClass.PAYMENT, "/payment"),
            _rules(
                PageClass.CHECKOUT,
                "/checkout",
                "/confirm/",
                "/confirmseat/",
            ),
            _rules(PageClass.TICKET, "/ticket/"),
        ),
        _COMPLETE,
    ),
    DeclarativePlatformAdapter(
        "ibon",
        *_identity("ibon"),
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
        *_identity("kham"),
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
        *_identity("famiticket"),
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
        *_identity("funone"),
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
        *_identity("fansigo"),
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
        *_identity("cityline"),
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
        *_identity("hkticketing"),
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
