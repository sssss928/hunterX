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
class PlatformValidation:
    onsale: ValidationLevel
    leak_watch: ValidationLevel
    protected_pages: ValidationLevel
    recovery: ValidationLevel


@dataclass(frozen=True)
class PlatformSpec:
    key: str
    display_name: str
    hosts: tuple[str, ...]
    validation: PlatformValidation

    def matches_url(self, url: str) -> bool:
        try:
            hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
        except ValueError:
            return False
        return any(hostname == host or hostname.endswith(f".{host}") for host in self.hosts)


_PUBLIC = ValidationLevel.PUBLIC_PAGE_TESTED
_FIXTURE = ValidationLevel.FIXTURE_TESTED
_SOURCE = ValidationLevel.SOURCE_REVIEWED

PLATFORM_SPECS: tuple[PlatformSpec, ...] = (
    PlatformSpec(
        "tixcraft",
        "TixCraft / IndieVox / Ticketmaster",
        ("tixcraft.com", "indievox.com", "ticketmaster.sg"),
        PlatformValidation(_PUBLIC, _FIXTURE, _FIXTURE, _FIXTURE),
    ),
    PlatformSpec(
        "kktix",
        "KKTIX",
        ("kktix.com", "kktix.cc"),
        PlatformValidation(_SOURCE, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformSpec(
        "famiticket",
        "FamiTicket",
        ("famiticket.com.tw", "famiticket.com"),
        PlatformValidation(_SOURCE, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformSpec(
        "ibon",
        "iBon",
        ("ibon.com.tw", "ibon.com"),
        PlatformValidation(_SOURCE, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformSpec(
        "kham",
        "KHAM / ticket.com.tw / UDN",
        ("kham.com.tw", "ticket.com.tw", "tickets.udnfunlife.com"),
        PlatformValidation(_PUBLIC, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformSpec(
        "ticketplus",
        "TicketPlus",
        ("ticketplus.com.tw", "ticketplus.com"),
        PlatformValidation(_SOURCE, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformSpec(
        "cityline",
        "Cityline",
        ("cityline.com", "cityline.com.hk"),
        PlatformValidation(_SOURCE, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformSpec(
        "hkticketing",
        "HKTicketing / Galaxy Macau / Ticketek",
        ("hkticketing.com", "galaxymacau.com", "ticketek.com.sg", "ticketek.com"),
        PlatformValidation(_PUBLIC, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformSpec(
        "funone",
        "FunOne",
        ("tickets.funone.io",),
        PlatformValidation(_PUBLIC, _SOURCE, _FIXTURE, _SOURCE),
    ),
    PlatformSpec(
        "fansigo",
        "FANSI GO",
        ("go.fansi.me", "fansidev.auth.ap-southeast-1.amazoncognito.com"),
        PlatformValidation(_PUBLIC, _SOURCE, _FIXTURE, _SOURCE),
    ),
)


PLATFORM_FAMILIES = PLATFORM_SPECS


def platform_for_url(url: str) -> PlatformSpec | None:
    """Return the single owning platform family for an absolute URL."""

    matches = tuple(platform for platform in PLATFORM_SPECS if platform.matches_url(url))
    if len(matches) > 1:
        raise RuntimeError(f"Ambiguous platform ownership for URL: {url!r}")
    return matches[0] if matches else None


def platform_key_for_url(url: str) -> str | None:
    platform = platform_for_url(url)
    return platform.key if platform else None


def platform_spec_for_key(key: str) -> PlatformSpec | None:
    normalized = str(key or "").casefold()
    return next((spec for spec in PLATFORM_SPECS if spec.key == normalized), None)
