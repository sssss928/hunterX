"""Cheap, read-only on-sale configuration preflight.

The preflight intentionally performs no DOM/CDP I/O. It is safe to run while
the timing gate is far from its deadline and never enters the purchase path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from page_classifier import PageClass, classify_page
from platform_registry import platform_key_for_url
from run_modes import RunMode, get_run_mode


def _route_without_query(url: str) -> str:
    try:
        parsed = urlsplit(str(url or ""))
    except ValueError:
        return ""
    if not parsed.hostname:
        return ""
    return f"{parsed.hostname}{parsed.path or '/'}"


def build_onsale_preflight(
    config_dict: dict[str, Any] | None,
    current_url: str,
    target: datetime | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    config = config_dict if isinstance(config_dict, dict) else {}
    mode = get_run_mode(config)
    if mode != RunMode.ONSALE.value:
        return {"mode": mode, "ready": True, "checks": (), "skipped": True}

    checks: list[dict[str, str]] = []

    def add(name: str, status: str, message: str) -> None:
        checks.append({"name": name, "status": status, "message": message})

    if target is None:
        add("sale_target", "error", "refresh_datetime is empty or invalid")
    elif target.tzinfo is None:
        add("sale_target", "error", "sale target must be timezone-aware")
    else:
        add("sale_target", "ok", target.isoformat(timespec="milliseconds"))
        if now is not None and now.tzinfo is not None and target <= now:
            add("sale_target_position", "warning", "target is already in the past")

    platform = platform_key_for_url(current_url)
    page_class = classify_page(current_url)
    if not current_url or platform is None:
        add("browser_route", "error", "browser is not on a supported platform route")
    elif page_class in {
        PageClass.TICKET,
        PageClass.ORDER,
        PageClass.CHECKOUT,
        PageClass.PAYMENT,
    }:
        add("browser_route", "warning", "purchase page is protected from scheduled reload")
    elif page_class is PageClass.QUEUE:
        add("browser_route", "warning", "waiting room owns page refresh behavior")
    else:
        add("browser_route", "ok", _route_without_query(current_url))

    try:
        ticket_number = int(config.get("ticket_number", 0))
    except (TypeError, ValueError):
        ticket_number = 0
    add(
        "ticket_number",
        "ok" if ticket_number > 0 else "error",
        str(ticket_number) if ticket_number > 0 else "ticket_number must be positive",
    )

    for name in ("date_auto_select", "area_auto_select"):
        value = config.get(name)
        add(
            name,
            "ok" if isinstance(value, dict) else "error",
            "configured" if isinstance(value, dict) else f"{name} section is missing",
        )

    route_lower = str(current_url or "").casefold()
    if any(marker in route_lower for marker in ("/login", "/sign_in", "/signin")):
        add("session", "warning", "login page detected; confirm the account manually")
    else:
        add("session", "manual", "confirm the visible signed-in account before sale")

    errors = [item for item in checks if item["status"] == "error"]
    return {
        "mode": mode,
        "ready": not errors,
        "checks": tuple(checks),
        "skipped": False,
        "platform": platform or "unknown",
        "page_class": page_class.value,
    }
