"""Authoritative platform-family registry.

This module deliberately separates URL ownership from feature claims.  A host
match only selects a platform adapter; it never implies that a capability was
implemented or validated.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from urllib.parse import urlsplit


class ValidationLevel(IntEnum):
    UNVERIFIED = 0
    SOURCE_REVIEWED = 1
    FIXTURE_TESTED = 2
    PUBLIC_PAGE_TESTED = 3


@dataclass(frozen=True)
class PlatformCapabilities:
    onsale: ValidationLevel
    leak_watch: ValidationLevel
    protected_pages: ValidationLevel
    recovery: ValidationLevel


@dataclass(frozen=True)
class PlatformFamily:
    key: str
    display_name: str
    hosts: tuple[str, ...]
    capabilities: PlatformCapabilities

    def matches_url(self, url: str) -> bool:
        try:
            hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
        except ValueError:
            return False
        return any(hostname == host or hostname.endswith(f".{host}") for host in self.hosts)


_PUBLIC = ValidationLevel.PUBLIC_PAGE_TESTED
_FIXTURE = ValidationLevel.FIXTURE_TESTED
_SOURCE = ValidationLevel.SOURCE_REVIEWED

PLATFORM_FAMILIES: tuple[PlatformFamily, ...] = (
    PlatformFamily(
        "tixcraft",
        "TixCraft / IndieVox / Ticketmaster",
        ("tixcraft.com", "indievox.com", "ticketmaster.sg"),
        PlatformCapabilities(_PUBLIC, _FIXTURE, _FIXTURE, _FIXTURE),
    ),
    PlatformFamily(
        "kktix",
        "KKTIX",
        ("kktix.com", "kktix.cc"),
        PlatformCapabilities(_SOURCE, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformFamily(
        "famiticket",
        "FamiTicket",
        ("famiticket.com.tw", "famiticket.com"),
        PlatformCapabilities(_SOURCE, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformFamily(
        "ibon",
        "iBon",
        ("ibon.com.tw", "ibon.com"),
        PlatformCapabilities(_SOURCE, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformFamily(
        "kham",
        "KHAM / ticket.com.tw / UDN",
        ("kham.com.tw", "ticket.com.tw", "tickets.udnfunlife.com"),
        PlatformCapabilities(_PUBLIC, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformFamily(
        "ticketplus",
        "TicketPlus",
        ("ticketplus.com.tw", "ticketplus.com"),
        PlatformCapabilities(_SOURCE, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformFamily(
        "cityline",
        "Cityline",
        ("cityline.com", "cityline.com.hk"),
        PlatformCapabilities(_SOURCE, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformFamily(
        "hkticketing",
        "HKTicketing / Galaxy Macau / Ticketek",
        ("hkticketing.com", "galaxymacau.com", "ticketek.com.sg", "ticketek.com"),
        PlatformCapabilities(_PUBLIC, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformFamily(
        "funone",
        "FunOne",
        ("tickets.funone.io",),
        PlatformCapabilities(_PUBLIC, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformFamily(
        "fansigo",
        "FANSI GO",
        ("go.fansi.me", "fansidev.auth.ap-southeast-1.amazoncognito.com"),
        PlatformCapabilities(_PUBLIC, _SOURCE, _FIXTURE, _SOURCE),
    ),
)


def platform_for_url(url: str) -> PlatformFamily | None:
    """Return the single owning platform family for an absolute URL."""

    matches = tuple(platform for platform in PLATFORM_FAMILIES if platform.matches_url(url))
    if len(matches) > 1:
        raise RuntimeError(f"Ambiguous platform ownership for URL: {url!r}")
    return matches[0] if matches else None


def platform_key_for_url(url: str) -> str | None:
    platform = platform_for_url(url)
    return platform.key if platform else None
