#encoding=utf-8
# =============================================================================
# TixCraft + Ticketmaster Platform Module
# Extracted from nodriver_tixcraft.py during modularization (Phase 1)
# Contains: tixcraft.com, indievox.com, ticketmaster.* family
# =============================================================================

import asyncio
import base64
import contextvars
from contextlib import contextmanager
import inspect
import json
import os
import random
import re
import time
import traceback
import unicodedata
import uuid
import webbrowser
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import unquote, urlsplit, urlunsplit

try:
    import ddddocr
except Exception:
    pass

from zendriver import cdp

import util
import performance
import runtime_health
from leak_watch import LeakWatchScheduler, should_use_leak_watch
from platforms.common_async import bounded_poll, get_auto_reload_interval, run_cpu_bound
from action_ledger import ActionLedger
from notification_context import clean_event_name, make_notification_context
from page_classifier import PageClass, classify_page
from platform_contract import platform_state_for_tab
from platform_registry import platform_key_for_url
from reload_guard import guarded_reload
from run_modes import (
    get_effective_reload_interval,
    get_leak_refresh_interval,
    is_leak_watch_mode,
)
from submit_guard import SubmitGuard
from tab_ownership import close_owned_tab, register_owned_tab
from nodriver_common import (
    check_and_handle_pause,
    check_and_handle_quit,
    sleep_with_pause_check,
    convert_remote_object,
    nodriver_check_checkbox,
    nodriver_check_checkbox_enhanced,
    nodriver_current_url,
    nodriver_get_text_by_selector,
    is_discord_notification_enabled,
    play_sound_while_ordering,
    send_discord_notification,
    send_telegram_notification,
    write_question_to_file,
    CONST_MAXBOT_ANSWER_ONLINE_FILE,
    CONST_MAXBOT_INT28_FILE,
    CONST_OCR_CAPTCH_IMAGE_SOURCE_CANVAS,
    CONST_OCR_CAPTCH_IMAGE_SOURCE_NON_BROWSER,
)

__all__ = [
    "nodriver_tixcraft_home_close_window",
    "nodriver_tixcraft_redirect",
    "nodriver_ticketmaster_parse_zone_info",
    "get_ticketmaster_target_area",
    "nodriver_ticketmaster_get_ticketPriceList",
    "nodriver_ticketmaster_date_auto_select",
    "nodriver_ticketmaster_area_auto_select",
    "nodriver_ticketmaster_assign_ticket_number",
    "nodriver_ticketmaster_captcha",
    "nodriver_ticketmaster_promo",
    "nodriver_tixcraft_verify",
    "nodriver_fill_verify_form",
    "nodriver_tixcraft_input_check_code",
    "nodriver_tixcraft_date_auto_select",
    "nodriver_tixcraft_area_auto_select",
    "nodriver_get_tixcraft_target_area",
    "nodriver_ticket_number_select_fill",
    "nodriver_tixcraft_assign_ticket_number",
    "nodriver_tixcraft_ticket_main_agree",
    "nodriver_tixcraft_ticket_main",
    "nodriver_tixcraft_keyin_captcha_code",
    "nodriver_tixcraft_toast",
    "nodriver_tixcraft_reload_captcha",
    "nodriver_tixcraft_get_ocr_answer",
    "nodriver_tixcraft_auto_ocr",
    "nodriver_tixcraft_ticket_main_ocr",
    "nodriver_tixcraft_main",
    "nodriver_ticketmaster_check_ip_block",
]

# Direct helper tests use the default mapping. Production dispatch binds this
# proxy to PlatformEngine-owned per-tab data for the lifetime of each task.
_default_state = {}
_state_context = contextvars.ContextVar("tixcraft_runtime_state", default=None)


class _TixCraftStateProxy(dict):
    def current(self):
        state = _state_context.get()
        return _default_state if state is None else state

    def has_active_binding(self):
        return _state_context.get() is not None

    def bind(self, state):
        return _state_context.set(state)

    def reset_binding(self, token):
        _state_context.reset(token)

    def __getitem__(self, key):
        return self.current()[key]

    def __setitem__(self, key, value):
        self.current()[key] = value

    def __delitem__(self, key):
        del self.current()[key]

    def __iter__(self):
        return iter(self.current())

    def __len__(self):
        return len(self.current())

    def __contains__(self, key):
        return key in self.current()

    def get(self, key, default=None):
        return self.current().get(key, default)

    def setdefault(self, key, default=None):
        return self.current().setdefault(key, default)

    def pop(self, key, *default):
        return self.current().pop(key, *default)

    def clear(self):
        self.current().clear()

    def update(self, *args, **kwargs):
        self.current().update(*args, **kwargs)

    def keys(self):
        return self.current().keys()

    def items(self):
        return self.current().items()

    def values(self):
        return self.current().values()


_state = _TixCraftStateProxy()


def _state_for_tab(tab):
    from platform_adapters import adapter_for_key
    from platform_engine import platform_engine

    adapter = adapter_for_key("tixcraft")
    if adapter is None:
        raise RuntimeError("TixCraft adapter is unavailable")
    return platform_engine.state_for(tab, adapter).platform_data


async def _guarded_tixcraft_get(
    tab,
    target_url,
    config_dict=None,
    *,
    reason="navigation",
):
    source_url = _get_cached_tab_url(tab)
    context = _state.get("submit_in_flight")
    runtime_health.runtime_log(
        "[TIXCRAFT] navigation_intent",
        config_dict,
        reason=reason,
        source_url=source_url,
        target_url=target_url,
        page_class=classify_page(source_url).value,
        attempt_id=getattr(_get_tixcraft_purchase_attempt(), "attempt_id", None),
        generation=int(_state.get("notification_flow_generation", 0) or 0),
        token=getattr(context, "token", None),
    )
    return await runtime_health.guarded_get(
        tab,
        target_url,
        config_dict,
        reason=reason,
    )


def _dispatch_state_for_tab(tab):
    bound = _state_context.get()
    if bound is not None:
        return bound
    # Preserve compatibility with a pre-v0.4.7 in-process state during a hot
    # reload. Normal production entrypoints leave the default mapping empty.
    if _default_state:
        return _default_state
    return _state_for_tab(tab)


def _refresh_coordinator_for_tab(tab):
    # Lazy import avoids the platform registry -> adapter -> platform cycle.
    from platform_engine import platform_engine

    return platform_engine.refresh_coordinator_for(tab)


@contextmanager
def _bind_tixcraft_tab_state(tab):
    token = _state.bind(_state_for_tab(tab))
    try:
        yield _state
    finally:
        _state.reset_binding(token)


class TixCraftAttemptPhase(str, Enum):
    AREA_READY = "area_ready"
    AREA_SELECTED = "area_selected"
    TICKET_FORM_ACTIVE = "ticket_form_active"
    SUBMIT_IN_FLIGHT = "submit_in_flight"
    ORDER_PENDING = "order_pending"
    CHECKOUT_REACHED = "checkout_reached"
    PAYMENT_REACHED = "payment_reached"
    RECOVERING_TO_AREA = "recovering_to_area"
    CLOSED = "closed"


class TixCraftTicketFormState(str, Enum):
    """Readiness evidence for the dynamically rendered verification form."""

    NOT_RENDERED_YET = "not_rendered_yet"
    READY = "ready"
    UNAVAILABLE = "unavailable"
    INVALID_PAGE = "invalid_page"


class TixCraftAreaOutcome(str, Enum):
    PAGE_NOT_READY = "page_not_ready"
    DOM_SCAN_BUSY = "dom_scan_busy"
    ZONE_MISSING = "zone_missing"
    DOM_QUERY_FAILED = "dom_query_failed"
    NO_AVAILABLE_AREA = "no_available_area"
    CLICK_DISPATCHED = "click_dispatched"
    CLICK_WAITING_NAVIGATION = "click_waiting_navigation"
    CLICK_NOT_NAVIGATED = "click_not_navigated"
    NAVIGATION_CONFIRMED = "navigation_confirmed"


@dataclass(frozen=True)
class TixCraftEventSnapshot:
    """Canonical event identity captured before the protected purchase pages."""

    origin: str
    event_id: str
    event_name: str
    source: str
    quality: int
    captured_url: str
    captured_page_class: PageClass
    flow_generation: int
    validated_at_monotonic: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class TixCraftPurchaseAttempt:
    session_id: str
    attempt_id: int
    event_id: str = ""
    game_id: str = ""
    seat_area: str = ""
    ticket_count: str = ""
    area_url: str = ""
    event_snapshot: TixCraftEventSnapshot | None = None
    phase: TixCraftAttemptPhase = TixCraftAttemptPhase.AREA_READY
    started_at: float = field(default_factory=time.time)
    started_at_monotonic: float = field(default_factory=time.monotonic)
    discord_stages: set[str] = field(default_factory=set)
    auxiliary_stages: set[str] = field(default_factory=set)
    checkout_fallback_sent: bool = False
    checkout_seat_poll_started_at: float = 0.0
    checkout_seat_poll_next_at: float = 0.0
    checkout_seat_poll_exhausted: bool = False
    metadata_retry_counts: dict[str, int] = field(default_factory=dict)
    metadata_failure_stages: set[str] = field(default_factory=set)
    delivery_retry_counts: dict[str, int] = field(default_factory=dict)
    delivery_failure_stages: set[str] = field(default_factory=set)
    ticket_count_confirmed: bool = False

    def notification_id(self, stage: str) -> str:
        return f"{self.session_id}:{self.attempt_id}:TixCraft:{stage}"


@dataclass(frozen=True)
class TixCraftPendingNavigation:
    kind: str
    source_url: str
    target_url: str = ""
    seat_area: str = ""
    event_id: str = ""
    game_id: str = ""
    flow_generation: int = 0
    token: int = 0
    tab_identity: int = 0
    started_at: float = 0.0
    deadline: float = 0.0


@dataclass(frozen=True, slots=True)
class TixCraftSubmitInFlight:
    attempt_id: int
    flow_generation: int
    token: int
    tab_identity: int
    source_url: str
    started_at_monotonic: float


_TIXCRAFT_CHECKOUT_SEAT_FALLBACK = "未能讀取座位資料，請立即查看結帳頁"
_TIXCRAFT_SEAT_READ_ATTEMPTS = 6
_TIXCRAFT_SEAT_READ_INTERVAL_SECONDS = 0.12
_TIXCRAFT_NOTIFICATION_METADATA_MAX_ATTEMPTS = 8
_TIXCRAFT_NOTIFICATION_DELIVERY_REARM_ATTEMPTS = 2
_TIXCRAFT_NOTIFICATION_DELIVERY_RETRY_SECONDS = 0.75
_TIXCRAFT_DISCORD_STAGES = frozenset({"order_pending", "checkout_reached"})
_TIXCRAFT_EVENT_METADATA_PAGES = frozenset(
    {PageClass.ACTIVITY, PageClass.DATE, PageClass.AREA}
)
_TIXCRAFT_EVENT_METADATA_RETRY_SECONDS = 0.5
_TIXCRAFT_EVENT_CANONICAL_QUALITY = 90
_TIXCRAFT_EVENT_METADATA_CACHE_CAPACITY = 64
_TIXCRAFT_BLANK_PAGE_GRACE_SECONDS = 1.5
_TIXCRAFT_RECOVERY_MIN_RELOAD_GUARD_SECONDS = 1.0
_TIXCRAFT_EVALUATE_TIMEOUT_SECONDS = 0.75
_TIXCRAFT_SOFT_BLOCK_NORMAL_PROBE_INTERVAL_SECONDS = 0.25
_TIXCRAFT_CLICK_DISPATCH_TIMEOUT_SECONDS = 1.0
_TIXCRAFT_SEAT_EVALUATE_TIMEOUT_SECONDS = 0.4
_TIXCRAFT_SUBMIT_CONTEXT_MAX_SECONDS = 20.0
_TIXCRAFT_VERIFY_READY_TIMEOUT_SECONDS = 0.75
_TIXCRAFT_VERIFY_READY_INTERVAL_SECONDS = 0.05
_TIXCRAFT_SOFT_BLOCK_EVIDENCE_WINDOW_SECONDS = 5.0
_TIXCRAFT_NAVIGATION_CONFIRMATION_DEFAULT_SECONDS = 3.0
_TIXCRAFT_NAVIGATION_CONFIRMATION_MIN_SECONDS = 1.0
_TIXCRAFT_NAVIGATION_CONFIRMATION_MAX_SECONDS = 10.0
_TIXCRAFT_DIAGNOSTIC_LOG_INTERVAL_SECONDS = 1.0
_TIXCRAFT_OPERATION_TIMEOUT = object()
_TIXCRAFT_WHITESPACE_RE = re.compile(r"\s+")


def _parse_tixcraft_row_htmls(raw_value):
    """Normalize NoDriver evaluate() output into a list of row HTML strings."""
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except Exception:
            return None
    if isinstance(raw_value, list):
        row_htmls = []
        for item in raw_value:
            if item is None:
                row_htmls.append("")
            else:
                row_htmls.append(str(item))
        return row_htmls
    return None


def _parse_tixcraft_area_text_cache(raw_value):
    """Normalize area text cache from NoDriver evaluate() into dict rows."""
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except Exception:
            return None
    if not isinstance(raw_value, list):
        return None

    normalized = []
    for item in raw_value:
        if not isinstance(item, dict):
            return None
        normalized.append({
            "text": str(item.get("text", "")),
            "fontText": str(item.get("fontText", "")),
        })
    return normalized


def _tixcraft_text_contains_keyword(row_text, keyword):
    normalized_row = util.normalize_keyword_text(row_text or "")
    normalized_keyword = util.normalize_keyword_text(keyword or "")
    if not normalized_keyword:
        return True
    if normalized_keyword in normalized_row:
        return True

    compact_row = re.sub(r"\s+", "", normalized_row)
    compact_keyword = re.sub(r"\s+", "", normalized_keyword)
    return bool(compact_keyword and compact_keyword in compact_row)


# Keywords that identify serial-number / membership-code style verify prompts.
# Used as a guard for the discount_code fallback in nodriver_tixcraft_input_check_code
# to avoid wasting an attempt on unrelated questions (math, common knowledge, etc.).
# Match is case-insensitive substring on question_text; covers zh-TW/zh-CN/en/ja/ko.
_SERIAL_CODE_QUESTION_KEYWORDS = (
    "會員",        # zh-TW: member (會員)
    "会员",        # zh-CN: member (会员)
    "序號",        # zh-TW: serial (序號)
    "序号",        # zh-CN: serial (序号)
    "編號",        # zh-TW: ID/number (編號)
    "编号",        # zh-CN: ID/number (编号)
    "員編",        # zh-TW: member ID (員編)
    "会員番号",  # ja: member number (会員番号)
    "シリアル",  # ja: serial (シリアル)
    "회원",        # ko: member (회원)
    "멤버십",  # ko: membership (멤버십)
    "membership",
    "member id",
    "member no",
    "serial",
    "weverse",
)

_TIXCRAFT_SOFT_BLOCK_SCOPE_HOSTS = (
    "tixcraft.com",
    "teamear.com",
    "indievox.com",
    "ticketmaster.sg",
    "ticketmaster.com",
)

_TIXCRAFT_CUSTOM_SOFT_BLOCK_DELAY_HOSTS = (
    "tixcraft.com",
    "teamear.com",
    "indievox.com",
)

_TIXCRAFT_SOFT_BLOCK_TEXT_MARKERS = (
    "your browsing activity has been paused",
    "browsing activity has been paused",
    "we've detected unusual behavior",
    "detected unusual behavior",
    "unusual behavior on either your network or your browser",
    "活動已暫停",
    "瀏覽活動已暫停",
    "偵測到異常行為",
    "異常行為",
)


def _is_serial_code_question(question_text):
    if not question_text:
        return False
    text_lower = question_text.lower()
    for kw in _SERIAL_CODE_QUESTION_KEYWORDS:
        if kw.lower() in text_lower:
            return True
    return False


def _is_tixcraft_soft_block_scope(url):
    try:
        return _is_tixcraft_family_host(urlsplit(str(url or "")).hostname)
    except ValueError:
        return False


def _is_tixcraft_custom_soft_block_delay_scope(url):
    try:
        hostname = urlsplit(str(url or "")).hostname
    except ValueError:
        return False
    host = str(hostname or "").lower().split(":", 1)[0].rstrip(".")
    return any(
        host == root or host.endswith(f".{root}")
        for root in _TIXCRAFT_CUSTOM_SOFT_BLOCK_DELAY_HOSTS
    )


def _is_tixcraft_soft_block_text(text):
    text_lower = re.sub(r"\s+", " ", str(text or "").lower())
    return any(marker in text_lower for marker in _TIXCRAFT_SOFT_BLOCK_TEXT_MARKERS)


def _is_tixcraft_family_host(hostname):
    host = str(hostname or "").lower().split(":", 1)[0].rstrip(".")
    return any(host == root or host.endswith(f".{root}") for root in _TIXCRAFT_SOFT_BLOCK_SCOPE_HOSTS)


def _normalize_tixcraft_area_url(value):
    """Return a same-family, absolute /ticket/area/ URL or an empty string."""
    text = unquote(str(value or "").strip())
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if parts.scheme.lower() not in {"http", "https"} or not _is_tixcraft_family_host(parts.hostname):
        return ""
    if not re.fullmatch(r"/ticket/area/[^/?#]+/[^/?#]+/?", parts.path, flags=re.IGNORECASE):
        return ""
    canonical_path = parts.path.rstrip("/")
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), canonical_path, "", "")
    )


def _normalize_tixcraft_entry_url(value):
    """Return a safe, observed TixCraft activity route for controlled routing."""
    text = unquote(str(value or "").strip())
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if parts.scheme.lower() not in {"http", "https"} or not _is_tixcraft_family_host(parts.hostname):
        return ""
    if not re.fullmatch(
        r"/activity/(?:detail|game)/[^/?#]+/?",
        parts.path,
        flags=re.IGNORECASE,
    ):
        return ""
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, "", ""))


def _tixcraft_route_key(value):
    """Return a route identity that ignores query strings and fragments."""
    try:
        parts = urlsplit(str(value or ""))
    except ValueError:
        return ""
    if not parts.scheme or not parts.netloc:
        return ""
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/") or "/",
            "",
            "",
        )
    )


def _get_tixcraft_navigation_confirmation_seconds(config_dict):
    interval = float(
        get_effective_reload_interval(
            config_dict,
            _TIXCRAFT_NAVIGATION_CONFIRMATION_DEFAULT_SECONDS,
        )
    )
    if interval <= 0:
        interval = _TIXCRAFT_NAVIGATION_CONFIRMATION_DEFAULT_SECONDS
    return min(
        _TIXCRAFT_NAVIGATION_CONFIRMATION_MAX_SECONDS,
        max(_TIXCRAFT_NAVIGATION_CONFIRMATION_MIN_SECONDS, interval),
    )


def _get_cached_tab_url(tab):
    try:
        value = getattr(getattr(tab, "target", None), "url", "") or ""
    except Exception:
        return ""
    return value if isinstance(value, str) else ""


def _runtime_log_rate_limited(
    state_key,
    event,
    config_dict,
    *,
    now=None,
    interval_seconds=_TIXCRAFT_DIAGNOSTIC_LOG_INTERVAL_SECONDS,
    identity="",
    **fields,
):
    """Emit repetitive hot-loop diagnostics at most once per interval."""
    current = time.monotonic() if now is None else float(now)
    previous = _state.get(state_key)
    if isinstance(previous, tuple) and len(previous) == 2:
        previous_identity, previous_at = previous
    else:
        previous_identity, previous_at = "", 0.0
    if previous_identity == identity and current - float(previous_at or 0.0) < interval_seconds:
        return False
    _state[state_key] = (identity, current)
    runtime_health.runtime_log(event, config_dict, **fields)
    return True


def _clear_tixcraft_recovery_scan_guard():
    _state["soft_block_recovery_scan_pending"] = False
    _state["soft_block_recovery_landing_url"] = ""
    _state["soft_block_recovery_scan_deadline"] = 0.0


def _is_tixcraft_blank_page_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        return False
    ready_state = str(snapshot.get("readyState", "")).lower()
    if ready_state not in {"interactive", "complete"} or not snapshot.get("hasBody", False):
        return False
    if snapshot.get("hasKnownContent", False):
        return False
    body_text = _TIXCRAFT_WHITESPACE_RE.sub(
        "",
        str(snapshot.get("bodyText", "")),
    )
    try:
        element_count = int(snapshot.get("elementCount", 0) or 0)
    except (TypeError, ValueError):
        element_count = 999
    return len(body_text) <= 8 and element_count <= 25


def _update_tixcraft_blank_page_state(url, snapshot, now=None, grace_seconds=None):
    """Require a stable blank DOM before classifying a white screen as blocked."""
    now = time.monotonic() if now is None else float(now)
    grace_seconds = (
        _TIXCRAFT_BLANK_PAGE_GRACE_SECONDS
        if grace_seconds is None
        else max(0.0, float(grace_seconds))
    )
    if not _is_tixcraft_soft_block_scope(url) or not _is_tixcraft_blank_page_snapshot(snapshot):
        _state["soft_block_blank_since"] = 0.0
        _state["soft_block_blank_url"] = ""
        return False

    normalized_url = _normalize_tixcraft_area_url(url) or str(url or "")
    if _state.get("soft_block_blank_url") != normalized_url:
        _state["soft_block_blank_url"] = normalized_url
        _state["soft_block_blank_since"] = now
        return grace_seconds <= 0
    blank_since = float(_state.get("soft_block_blank_since", now) or now)
    return now - blank_since >= grace_seconds


def _update_tixcraft_probe_failure_state(
    url,
    probe_failed,
    now=None,
    grace_seconds=None,
    minimum_failures=2,
):
    """Classify consecutive health-probe timeouts on a known family URL."""
    now = time.monotonic() if now is None else float(now)
    grace_seconds = (
        _TIXCRAFT_BLANK_PAGE_GRACE_SECONDS
        if grace_seconds is None
        else max(0.0, float(grace_seconds))
    )
    normalized_url = _normalize_tixcraft_area_url(url) or str(url or "")
    if (
        not probe_failed
        or not _is_tixcraft_soft_block_scope(url)
        or not normalized_url
    ):
        _state["soft_block_probe_failure_since"] = 0.0
        _state["soft_block_probe_failure_url"] = ""
        _state["soft_block_probe_failure_count"] = 0
        return False

    if _state.get("soft_block_probe_failure_url") != normalized_url:
        _state["soft_block_probe_failure_url"] = normalized_url
        _state["soft_block_probe_failure_since"] = now
        _state["soft_block_probe_failure_count"] = 1
        return False

    failure_count = int(_state.get("soft_block_probe_failure_count", 0) or 0) + 1
    _state["soft_block_probe_failure_count"] = failure_count
    failure_since = float(_state.get("soft_block_probe_failure_since", now) or now)
    return (
        failure_count >= max(2, int(minimum_failures))
        and now - failure_since >= grace_seconds
    )


async def _read_tixcraft_page_health(tab, config_dict=None):
    try:
        result = await runtime_health.evaluate_with_timeout(
            tab,
            r"""
                (function() {
                    try {
                        if (
                            typeof action !== "undefined" &&
                            action === "block" &&
                            typeof rr !== "undefined"
                        ) {
                            return JSON.stringify({
                                blocked: true,
                                kind: "eps_js",
                                rr: rr || "",
                                client_ip: typeof client_ip !== "undefined" ? client_ip : ""
                            });
                        }
                    } catch(e) {}
                    const body = document.body;
                    const orderProcessingSelectors = [
                        '#loadingmap',
                        '#loading.order-processing',
                        '.order-processing .spinner-border',
                        '.order-processing .spinner-grow',
                        '.loading-overlay.order-processing',
                        '[data-order-processing][aria-busy="true"]'
                    ];
                    const knownOrderProcessing = Boolean(
                        body && orderProcessingSelectors.some(selector =>
                            Array.from(body.querySelectorAll(selector)).some(el => {
                                const style = window.getComputedStyle(el);
                                const rect = el.getBoundingClientRect();
                                return style.display !== 'none' &&
                                    style.visibility !== 'hidden' &&
                                    rect.width > 0 &&
                                    rect.height > 0;
                            })
                        )
                    );
                    const viewportWidth = Math.max(
                        document.documentElement ? document.documentElement.clientWidth : 0,
                        window.innerWidth || 0
                    );
                    const viewportHeight = Math.max(
                        document.documentElement ? document.documentElement.clientHeight : 0,
                        window.innerHeight || 0
                    );
                    let whiteOverlay = false;
                    if (body && viewportWidth > 0 && viewportHeight > 0) {
                        const candidate = document.elementFromPoint(
                            Math.floor(viewportWidth / 2),
                            Math.floor(viewportHeight / 2)
                        );
                        if (
                            candidate &&
                            candidate !== body &&
                            candidate !== document.documentElement
                        ) {
                            const style = window.getComputedStyle(candidate);
                            const rect = candidate.getBoundingClientRect();
                            const color = (style.backgroundColor || '').match(
                                /rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?/
                            );
                            const nearlyWhite = color &&
                                Number(color[1]) >= 245 &&
                                Number(color[2]) >= 245 &&
                                Number(color[3]) >= 245 &&
                                Number(color[4] === undefined ? 1 : color[4]) >= 0.9;
                            const coversViewport =
                                rect.width >= viewportWidth * 0.95 &&
                                rect.height >= viewportHeight * 0.95 &&
                                rect.left <= viewportWidth * 0.025 &&
                                rect.top <= viewportHeight * 0.025;
                            const fixedLayer = ['fixed', 'absolute', 'sticky'].includes(
                                style.position
                            );
                            const overlayText = (candidate.innerText || '')
                                .replace(/\s+/g, '');
                            whiteOverlay = Boolean(
                                nearlyWhite &&
                                coversViewport &&
                                fixedLayer &&
                                overlayText.length <= 8 &&
                                style.display !== 'none' &&
                                style.visibility !== 'hidden'
                            );
                        }
                    }
                    const knownAreaContent = Boolean(
                        body && body.querySelector('.zone, [data-area-name]')
                    );
                    const knownActivityContent = Boolean(
                        body && body.querySelector('.activity-info, .game-title')
                    );
                    const knownTicketContent = Boolean(
                        body && body.querySelector('#TicketForm, .ticket-info')
                    );
                    const knownOrderContent = Boolean(
                        body && body.querySelector(
                            '.order-info, table.ticket-list, table.order-list'
                        )
                    );
                    const hasKnownContent = Boolean(
                        knownAreaContent ||
                        knownActivityContent ||
                        knownTicketContent ||
                        knownOrderContent
                    );
                    if (hasKnownContent) {
                        return JSON.stringify({
                            blocked: false,
                            readyState: document.readyState || '',
                            hasBody: true,
                            bodyText: '',
                            title: document.title || '',
                            elementCount: 1,
                            hasKnownContent: true,
                            knownAreaContent,
                            knownActivityContent,
                            knownTicketContent,
                            knownOrderContent,
                            whiteOverlay,
                            knownOrderProcessing
                        });
                    }
                    return JSON.stringify({
                        blocked: false,
                        readyState: document.readyState || '',
                        hasBody: !!body,
                        bodyText: body ? (body.innerText || '').slice(0, 5000) : '',
                        title: document.title || '',
                        elementCount: body ? body.querySelectorAll('*').length : 0,
                        hasKnownContent,
                        knownAreaContent,
                        knownActivityContent,
                        knownTicketContent,
                        knownOrderContent,
                        whiteOverlay,
                        knownOrderProcessing
                    });
                })()
            """,
            config_dict,
            timeout_seconds=_TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
            reason="SOFT_BLOCK_PAGE_HEALTH",
            default={"probeFailed": True},
            log_success=False,
        )
        result = util.parse_nodriver_result(result)
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except Exception:
                return {
                    "readyState": "complete",
                    "hasBody": True,
                    "bodyText": result,
                    "title": "",
                    "elementCount": 1,
                    "hasKnownContent": False,
                }
            return parsed if isinstance(parsed, dict) else {}
        return result if isinstance(result, dict) else {}
    except asyncio.CancelledError:
        raise
    except Exception:
        return {"probeFailed": True}


def _parse_tixcraft_soft_block_delay(config_dict):
    advanced = (config_dict or {}).get("advanced", {})
    raw_value = advanced.get("tixcraft_soft_block_delay", "")

    if raw_value is None:
        return None

    text = str(raw_value).strip()
    if not text:
        return None

    try:
        delay_seconds = int(text)
    except (TypeError, ValueError):
        return None

    if delay_seconds <= 0:
        return None

    return min(delay_seconds, 600)


def _resolve_soft_block_wait_seconds(config_dict, scope_url, default_wait_seconds=None):
    custom_delay = _parse_tixcraft_soft_block_delay(config_dict)
    if custom_delay is not None and _is_tixcraft_custom_soft_block_delay_scope(
        scope_url,
    ):
        return custom_delay, True

    if default_wait_seconds is None:
        default_wait_seconds = random.randint(240, 420)

    return default_wait_seconds, False


def _confirm_tixcraft_soft_block_evidence(url, kind, now=None):
    current = time.monotonic() if now is None else float(now)
    signature = f"{_tixcraft_route_key(url)}:{kind}"
    previous = str(_state.get("soft_block_evidence_signature", "") or "")
    first_at = float(_state.get("soft_block_evidence_first_at", 0.0) or 0.0)
    count = int(_state.get("soft_block_evidence_count", 0) or 0)
    if (
        signature != previous
        or first_at <= 0
        or current - first_at > _TIXCRAFT_SOFT_BLOCK_EVIDENCE_WINDOW_SECONDS
    ):
        first_at = current
        count = 1
    else:
        count += 1
    _state["soft_block_evidence_signature"] = signature
    _state["soft_block_evidence_first_at"] = first_at
    _state["soft_block_evidence_count"] = count
    confirmed = count >= 2
    if not confirmed and str(_state.get("soft_block_state", "CLEAR")) == "CLEAR":
        _state["soft_block_state"] = "SUSPECTED"
    return confirmed


def _clear_tixcraft_soft_block_evidence():
    _state["soft_block_evidence_signature"] = ""
    _state["soft_block_evidence_first_at"] = 0.0
    _state["soft_block_evidence_count"] = 0


async def _detect_tixcraft_soft_block(tab, url, config_dict=None):
    """Detect TixCraft-family soft-block pages without taking recovery action."""
    if not _is_tixcraft_soft_block_scope(url):
        _update_tixcraft_blank_page_state(url, {})
        _update_tixcraft_probe_failure_state(url, False)
        _state["soft_block_known_good_url"] = ""
        _state["soft_block_known_good_at"] = 0.0
        return {"blocked": False, "health_confirmed": True}

    route_key = _tixcraft_route_key(url)
    now = time.monotonic()
    if (
        route_key
        and _state.get("soft_block_known_good_url") == route_key
        and now - float(_state.get("soft_block_known_good_at", 0.0) or 0.0)
        < _TIXCRAFT_SOFT_BLOCK_NORMAL_PROBE_INTERVAL_SECONDS
    ):
        return {"blocked": False, "health_confirmed": True}

    snapshot = await _read_tixcraft_page_health(tab, config_dict)
    if snapshot.get("knownOrderProcessing", False):
        _clear_tixcraft_soft_block_evidence()
        return {
            "blocked": False,
            "health_confirmed": False,
            "inconclusive": True,
            "kind": "order_processing",
        }
    if snapshot.get("blocked", False):
        _state["soft_block_known_good_url"] = ""
        _state["soft_block_known_good_at"] = 0.0
        kind = snapshot.get("kind") or "eps_js"
        confirmed = _confirm_tixcraft_soft_block_evidence(url, kind, now)
        return {
            "blocked": confirmed,
            "health_confirmed": False,
            "inconclusive": not confirmed,
            "kind": kind,
            "original_url": snapshot.get("rr", "") or "",
            "client_ip": snapshot.get("client_ip", "") or "unknown",
        }
    if snapshot.get("probeFailed", False):
        _state["soft_block_known_good_url"] = ""
        _state["soft_block_known_good_at"] = 0.0
        _update_tixcraft_probe_failure_state(url, True)
        return {
            "blocked": False,
            "health_confirmed": False,
            "inconclusive": True,
        }
    _update_tixcraft_probe_failure_state(url, False)
    if snapshot.get("whiteOverlay") and not snapshot.get("knownOrderProcessing"):
        # A viewport-covering white overlay can hide an otherwise healthy DOM.
        # Normalize this rare case once here so the normal-page blank classifier
        # keeps its v0.4.3 fast path and both subsequent blank checks agree.
        snapshot = dict(snapshot)
        snapshot["bodyText"] = ""
        snapshot["elementCount"] = 0
        snapshot["hasKnownContent"] = False
    page_text = f"{snapshot.get('title', '')}\n{snapshot.get('bodyText', '')}"
    if _is_tixcraft_soft_block_text(page_text):
        _state["soft_block_known_good_url"] = ""
        _state["soft_block_known_good_at"] = 0.0
        _state["soft_block_blank_since"] = 0.0
        _state["soft_block_blank_url"] = ""
        confirmed = _confirm_tixcraft_soft_block_evidence(url, "text_marker", now)
        return {
            "blocked": confirmed,
            "health_confirmed": False,
            "inconclusive": not confirmed,
            "kind": "text_marker",
            "original_url": "",
            "client_ip": "unknown",
        }
    blank_candidate = _is_tixcraft_blank_page_snapshot(snapshot)
    if _update_tixcraft_blank_page_state(url, snapshot):
        _state["soft_block_known_good_url"] = ""
        _state["soft_block_known_good_at"] = 0.0
        return {
            "blocked": False,
            "kind": "stable_blank",
            "health_confirmed": False,
            "inconclusive": True,
        }
    if blank_candidate:
        return {
            "blocked": False,
            "health_confirmed": False,
            "inconclusive": True,
        }

    if snapshot.get("hasKnownContent", False) and route_key:
        _clear_tixcraft_soft_block_evidence()
        _state["soft_block_known_good_url"] = route_key
        _state["soft_block_known_good_at"] = now
    else:
        _state["soft_block_known_good_url"] = ""
        _state["soft_block_known_good_at"] = 0.0
    health_confirmed = _is_tixcraft_recovery_health_confirmed(snapshot)
    return {
        "blocked": False,
        "health_confirmed": health_confirmed,
        "inconclusive": not health_confirmed,
    }


def _get_tixcraft_soft_block_recovery_url(config_dict, current_url="", original_url=""):
    candidates = (
        _state.get("last_valid_area_url", ""),
        original_url,
        _state.get("recent_area_route_url", ""),
        current_url,
        (config_dict or {}).get("homepage", ""),
    )
    for candidate in candidates:
        area_url = _normalize_tixcraft_area_url(candidate)
        if area_url:
            return area_url
    return ""


def _get_tixcraft_controlled_routing_url(config_dict):
    """Return the configured activity route when no real area URL was observed."""
    return _normalize_tixcraft_entry_url((config_dict or {}).get("homepage", ""))


def _mark_tixcraft_recovery_landed(config_dict, recovery_url, now=None):
    landed_at = time.monotonic() if now is None else float(now)
    interval = max(
        _TIXCRAFT_RECOVERY_MIN_RELOAD_GUARD_SECONDS,
        float(get_effective_reload_interval(config_dict, 0.0)),
    )
    _state["soft_block_recovery_landing_url"] = recovery_url
    _state["soft_block_recovery_landed_at"] = landed_at
    _state["soft_block_recovery_scan_pending"] = True
    _state["soft_block_recovery_scan_deadline"] = landed_at + interval
    _state["soft_block_blank_since"] = 0.0
    _state["soft_block_blank_url"] = ""
    _state["soft_block_probe_failure_since"] = 0.0
    _state["soft_block_probe_failure_url"] = ""
    _state["soft_block_probe_failure_count"] = 0
    _state["soft_block_known_good_url"] = ""
    _state["soft_block_known_good_at"] = 0.0
    _state["tixcraft_area_reload_url"] = recovery_url
    _state["tixcraft_area_reload_next_at"] = landed_at + interval
    if "leak_scheduler" in _state:
        _state["leak_scheduler"].mark_recovery_landed(config_dict, now=landed_at)


def _clear_tixcraft_soft_block_backoff(tab=None):
    _state["soft_block_phase"] = ""
    _state["soft_block_state"] = "CLEAR"
    _state["soft_block_backoff_until"] = 0.0
    _state["soft_block_recovery_retry_at"] = 0.0
    _state["soft_block_retry_wait_seconds"] = 0.0
    _state["soft_block_incident_signature"] = ""
    _state["ip_block_until"] = 0.0
    if tab is not None:
        _refresh_coordinator_for_tab(tab).clear_soft_block()


def _defer_tixcraft_soft_block_recovery(
    config_dict=None,
    scope_url="",
    *,
    tab=None,
    now=None,
):
    current = time.monotonic() if now is None else float(now)
    wait_seconds = float(
        _state.get("soft_block_retry_wait_seconds", 0.0) or 0.0
    )
    if wait_seconds <= 0:
        wait_seconds, _ = _resolve_soft_block_wait_seconds(
            config_dict,
            scope_url,
        )
        _state["soft_block_retry_wait_seconds"] = wait_seconds
    retry_at = current + wait_seconds
    _state["soft_block_phase"] = "recovering"
    _state["soft_block_state"] = "CONFIRMED_WAIT"
    _state["soft_block_recovery_retry_at"] = retry_at
    _state["ip_block_until"] = retry_at
    if tab is not None:
        _refresh_coordinator_for_tab(tab).begin_soft_block(
            round(retry_at * 1_000_000_000)
        )


def _is_tixcraft_recovery_health_confirmed(snapshot, expected_page=None):
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("probeFailed") or snapshot.get("blocked"):
        return False
    if snapshot.get("whiteOverlay") and not snapshot.get("knownOrderProcessing"):
        return False
    ready_state = str(snapshot.get("readyState", "")).lower()
    if ready_state not in {"interactive", "complete"} or not snapshot.get("hasBody"):
        return False
    page_text = f"{snapshot.get('title', '')}\n{snapshot.get('bodyText', '')}"
    if _is_tixcraft_soft_block_text(page_text):
        return False
    if _is_tixcraft_blank_page_snapshot(snapshot):
        return False
    # Recovery must prove a route-specific TixCraft DOM marker. A cached target
    # URL can update before the renderer replaces the previous document, so a
    # ticket-form marker cannot confirm an /area/ recovery (and vice versa).
    if expected_page is not None:
        try:
            page_class = PageClass(expected_page)
        except (TypeError, ValueError):
            return False
        marker_by_page = {
            PageClass.AREA: "knownAreaContent",
            PageClass.ACTIVITY: "knownActivityContent",
            PageClass.DATE: "knownActivityContent",
            PageClass.TICKET: "knownTicketContent",
            PageClass.ORDER: "knownOrderContent",
            PageClass.CHECKOUT: "knownOrderContent",
            PageClass.PAYMENT: "knownOrderContent",
        }
        marker = marker_by_page.get(page_class)
        if marker is None:
            return False
        return bool(snapshot.get(marker))
    # Generic soft-block detection only needs proof of any known TixCraft
    # document. Arbitrary non-empty 403/WAF/error HTML is never sufficient.
    return bool(snapshot.get("hasKnownContent"))


async def _handle_tixcraft_soft_block(tab, config_dict, current_url="", detection=None):
    if _state.get("soft_block_recovery_in_progress", False):
        return True

    debug = util.create_debug_logger(config_dict)
    detection = detection or {"kind": "unknown", "original_url": "", "client_ip": "unknown"}
    original_url = detection.get("original_url", "") or ""
    scope_url = current_url if _is_tixcraft_soft_block_scope(current_url) else original_url
    kind = detection.get("kind", "unknown")
    incident_signature = f"{_tixcraft_route_key(scope_url)}:{kind}"
    stored_signature = str(
        _state.get("soft_block_incident_signature", "") or ""
    )
    stored_wait = float(
        _state.get("soft_block_retry_wait_seconds", 0.0) or 0.0
    )
    if stored_signature == incident_signature and stored_wait > 0:
        wait_seconds = stored_wait
        is_custom_delay = (
            _parse_tixcraft_soft_block_delay(config_dict) == int(stored_wait)
            and _is_tixcraft_custom_soft_block_delay_scope(scope_url)
        )
    else:
        wait_seconds, is_custom_delay = _resolve_soft_block_wait_seconds(
            config_dict,
            scope_url,
        )
        _state["soft_block_incident_signature"] = incident_signature
        _state["soft_block_retry_wait_seconds"] = wait_seconds
    _state["soft_block_recovery_in_progress"] = True
    try:
        _set_tixcraft_attempt_phase(TixCraftAttemptPhase.RECOVERING_TO_AREA)
        _reset_tixcraft_submit_state()
        _reset_tixcraft_area_retry_state()

        if kind == "text_marker":
            debug.log(f"[EPS BLOCK] Soft-block page detected by text marker; waiting {wait_seconds}s")
        elif kind == "stable_blank":
            debug.log(f"[EPS BLOCK] Stable blank/white page detected; waiting {wait_seconds}s")
        elif kind == "health_probe_timeout":
            debug.log(
                f"[EPS BLOCK] Page health probe repeatedly timed out; waiting {wait_seconds}s"
            )
        elif is_custom_delay:
            debug.log(f"[EPS BLOCK] Soft block detected; using configured delay: {wait_seconds}s")
        else:
            debug.log(f"[EPS BLOCK] Soft block detected; using default delay: {wait_seconds}s before retry")
        debug.log(
            "[EPS BLOCK] Automation is backing off; browser remains open and no page request will run during this wait"
        )

        now = time.monotonic()
        backoff_until = float(
            _state.get("soft_block_backoff_until", 0.0) or 0.0
        )
        if backoff_until <= 0:
            backoff_until = now + wait_seconds
            _state["soft_block_backoff_until"] = backoff_until
        _state["soft_block_phase"] = "backoff"
        _state["soft_block_state"] = "CONFIRMED_WAIT"
        _state["ip_block_until"] = backoff_until
        _refresh_coordinator_for_tab(tab).begin_soft_block(
            round(backoff_until * 1_000_000_000)
        )
        remaining_wait = max(0.0, backoff_until - now)
        if remaining_wait > 0:
            wait_result = await runtime_health.sleep_with_heartbeat(
                remaining_wait,
                config_dict,
                reason="SOFT_BLOCK",
                chunk_seconds=10,
                stop_checker=check_and_handle_pause,
                quit_checker=check_and_handle_quit,
            )
            if wait_result == "quit":
                debug.log("[EPS BLOCK] Quit requested during soft-block wait; returning control to main loop")
                return True
            if wait_result == "stop":
                debug.log("[EPS BLOCK] Automation stopped/paused during soft-block wait; browser remains open")
                return True

        _state["ip_block_until"] = 0
        _state["soft_block_phase"] = "recovering"
        _state["soft_block_state"] = "RECOVERING"
        _refresh_coordinator_for_tab(tab).mark_soft_block_recovering()
        recovery_url = _get_tixcraft_soft_block_recovery_url(config_dict, current_url, original_url)
        if not recovery_url:
            routing_url = _get_tixcraft_controlled_routing_url(config_dict)
            if not routing_url:
                debug.log(
                    "[EPS BLOCK] No observed /ticket/area/ URL or safe configured activity route is available; "
                    "navigation skipped"
                )
                _defer_tixcraft_soft_block_recovery(
                    config_dict,
                    scope_url,
                    tab=tab,
                )
                return True
            debug.log(
                "[EPS BLOCK] No area route was observed before the block; resuming once through the configured "
                "activity route"
            )
            try:
                routed = await _guarded_tixcraft_get(
                    tab,
                    routing_url,
                    config_dict,
                    reason="SOFT_BLOCK_CONTROLLED_ROUTING",
                )
            except Exception as exc:
                debug.log(
                    f"[EPS BLOCK] Controlled activity-route recovery failed: {type(exc).__name__}"
                )
                _defer_tixcraft_soft_block_recovery(
                    config_dict,
                    scope_url,
                    tab=tab,
                )
            else:
                ready = await runtime_health.wait_for_interactive_ready(
                    tab,
                    config_dict,
                )
                landed_target_url = _get_cached_tab_url(tab)
                route_matches = (
                    _tixcraft_route_key(landed_target_url)
                    == _tixcraft_route_key(routing_url)
                )
                recovery_snapshot = await _read_tixcraft_page_health(
                    tab,
                    config_dict,
                )
                if (
                    not ready
                    or not route_matches
                    or not _is_tixcraft_recovery_health_confirmed(
                        recovery_snapshot,
                        classify_page(routing_url),
                    )
                ):
                    runtime_health.runtime_log(
                        "[EPS BLOCK] controlled_routing_not_confirmed",
                        config_dict,
                        expected_url=routing_url,
                        current_url=landed_target_url,
                        guarded_result=bool(routed),
                        interactive=bool(ready),
                    )
                    _defer_tixcraft_soft_block_recovery(
                        config_dict,
                        scope_url,
                        tab=tab,
                    )
                else:
                    _clear_tixcraft_soft_block_backoff(tab)
            return True

        debug.log(f"[EPS BLOCK] Soft-block wait finished, navigating once to area: {recovery_url}")
        try:
            navigated = await _guarded_tixcraft_get(
                tab,
                recovery_url,
                config_dict,
                reason="SOFT_BLOCK_RECOVERY",
            )
        except Exception as exc:
            debug.log(f"[EPS BLOCK] Soft-block recovery navigation failed: {exc}")
            _defer_tixcraft_soft_block_recovery(
                config_dict,
                scope_url,
                tab=tab,
            )
            return True
        ready = await runtime_health.wait_for_interactive_ready(tab, config_dict)
        landed_target_url = _get_cached_tab_url(tab)
        if _normalize_tixcraft_area_url(landed_target_url) != recovery_url:
            runtime_health.runtime_log(
                "[EPS BLOCK] recovery_redirected",
                config_dict,
                expected_url=recovery_url,
                current_url=landed_target_url,
                guarded_result=bool(navigated),
            )
            _defer_tixcraft_soft_block_recovery(
                config_dict,
                scope_url,
                tab=tab,
            )
            return True
        if not ready:
            runtime_health.runtime_log(
                "[EPS BLOCK] recovery_not_interactive",
                config_dict,
                current_url=recovery_url,
            )
            _defer_tixcraft_soft_block_recovery(
                config_dict,
                scope_url,
                tab=tab,
            )
            return True
        recovery_snapshot = await _read_tixcraft_page_health(tab, config_dict)
        if not _is_tixcraft_recovery_health_confirmed(
            recovery_snapshot,
            PageClass.AREA,
        ):
            runtime_health.runtime_log(
                "[EPS BLOCK] recovery_document_not_confirmed",
                config_dict,
                current_url=recovery_url,
                guarded_result=bool(navigated),
            )
            _defer_tixcraft_soft_block_recovery(
                config_dict,
                scope_url,
                tab=tab,
            )
            return True
        _mark_tixcraft_recovery_landed(config_dict, recovery_url)
        _clear_tixcraft_soft_block_backoff(tab)
        runtime_health.runtime_log(
            "[EPS BLOCK] recovery_landed",
            config_dict,
            current_url=recovery_url,
            interactive_ready=bool(ready),
        )
        return True
    finally:
        _state["soft_block_recovery_in_progress"] = False


def _process_queue_it_state(url, state, current_time):
    """Update queue-it tracking state and decide whether the main loop should pause.

    Returns:
        (should_pause: bool, elapsed_seconds: float | None)
        - should_pause=True means the caller must short-circuit the main loop
          (we're still inside *.queue-it.net).
        - elapsed_seconds is set to the wait duration when this call detects
          the queue has been passed; otherwise None.
    """
    url_lower = (url or "").lower()
    if 'queue-it.net' in url_lower:
        if state.get("queue_it_enter_time") is None:
            state["queue_it_enter_time"] = current_time
        return True, None
    if state.get("queue_it_enter_time") is not None:
        elapsed = current_time - state["queue_it_enter_time"]
        state["queue_it_enter_time"] = None
        return False, elapsed
    return False, None


def _get_auto_reload_interval(config_dict):
    return get_auto_reload_interval(config_dict)


_TIXCRAFT_RETRYABLE_ALERT_KEYWORDS = (
    "請重新選擇",
    "請返回重新選擇",
    "請重新選取",
    "請重新訂購",
    "e0024",
    "已售完",
    "已無足夠",
    "選購一空",
    "票券已被選購一空",
    "票券已售完",
    "座位已售出",
    "座位已被選走",
    "不足票數",
    "票數不足",
    "剩餘票券不足",
    "無足夠",
    "沒有足夠",
    "目前無法取得",
    "暫無可售",
    "無可售",
    "sold out",
    "unavailable",
    "not enough tickets",
    "insufficient",
    "no ticket",
)


def _tixcraft_state_defaults():
    return {
        "fail_list": [],
        "fail_promo_list": [],
        "start_time": None,
        "done_time": None,
        "elapsed_time": None,
        "is_popup_checkout": False,
        "area_retry_count": 0,
        "played_sound_ticket": False,
        "played_sound_order": False,
        "notified_order_pending": False,
        "notified_checkout_reached": False,
        "alert_handler_registered": False,
        "captcha_alert_detected": False,
        "captcha_submit_until": 0,
        "ocr_completed_url": "",
        "ocr_completed_attempt_id": None,
        "last_valid_area_url": "",
        "recent_area_route_url": "",
        "last_selected_area": "",
        "selected_area_candidate": "",
        "selected_area_metadata": {},
        "pending_area_navigation": None,
        "pending_date_navigation": None,
        "area_navigation_token": 0,
        "date_navigation_token": 0,
        "area_navigation_retry_due": False,
        "date_navigation_retry_due": False,
        "current_event_id": "",
        "current_game_id": "",
        "current_event_origin": "",
        "event_name": "",
        "event_name_quality": 0,
        "event_snapshot": None,
        "event_metadata_cache": {},
        "event_metadata_next_probe_at": 0.0,
        "last_ticket_count": "",
        "last_ticket_count_confirmed": False,
        "notification_flow_generation": 0,
        "notification_flow_url": "",
        "last_notification_metadata_warning": None,
        "notification_session_id": uuid.uuid4().hex,
        "attempt_sequence": 0,
        "purchase_attempt": None,
        "attempt_last_page_class": "",
        "notification_submit_started_at": 0.0,
        "submit_in_flight": None,
        "submit_generation": 0,
        "notification_order_probe_next_at": 0.0,
        "notification_retry_at": {},
        "soft_block_blank_since": 0.0,
        "soft_block_blank_url": "",
        "soft_block_probe_failure_since": 0.0,
        "soft_block_probe_failure_url": "",
        "soft_block_probe_failure_count": 0,
        "soft_block_evidence_signature": "",
        "soft_block_evidence_first_at": 0.0,
        "soft_block_evidence_count": 0,
        "soft_block_known_good_url": "",
        "soft_block_known_good_at": 0.0,
        "soft_block_recovery_in_progress": False,
        "soft_block_recovery_landing_url": "",
        "soft_block_recovery_landed_at": 0.0,
        "soft_block_recovery_scan_pending": False,
        "soft_block_recovery_scan_deadline": 0.0,
        "manual_intervention_required": False,
        "last_homepage_redirect_time": 0,
        "sold_out_cooldown_until": 0,
        "printed_completed": False,
        "ticketmaster_phase": "area_select",
        "ticketmaster_captcha_processed_url": "",
        "ticketmaster_area_reload_next_at": 0,
        "ticketmaster_date_reload_next_at": 0,
        "tixcraft_detail_reload_next_at": 0,
        "tixcraft_detail_reload_url": "",
        "tixcraft_detail_reload_last_wait_log": 0,
        "tixcraft_area_reload_next_at": 0,
        "tixcraft_area_reload_url": "",
        "tixcraft_date_reload_next_at": 0,
        "tixcraft_ticket_reload_next_at": 0,
        "cookie_accepted": False,
        "html_lang": "en-US",
        "ip_block_until": 0,
        "ip_block_count": 0,
        "soft_block_phase": "",
        "soft_block_state": "CLEAR",
        "soft_block_backoff_until": 0.0,
        "soft_block_recovery_retry_at": 0.0,
        "soft_block_retry_wait_seconds": 0.0,
        "soft_block_incident_signature": "",
        "queue_it_enter_time": None,
    }


def _ensure_tixcraft_state_defaults():
    for key, value in _tixcraft_state_defaults().items():
        _state.setdefault(key, value)
    _ensure_runtime_helpers()


def _ensure_runtime_helpers():
    if "action_ledger" not in _state:
        _state["action_ledger"] = ActionLedger()
    if "submit_guard" not in _state:
        _state["submit_guard"] = SubmitGuard()
    if "leak_scheduler" not in _state:
        _state["leak_scheduler"] = LeakWatchScheduler()


def _get_leak_scheduler():
    _ensure_runtime_helpers()
    return _state["leak_scheduler"]


def should_prefer_cached_url_during_leak_wait(tab, config_dict):
    """Use TargetInfo.url only in the exact, idle TixCraft area wait state.

    Re-entering ``window.location.href`` on every 50 ms main-loop tick creates
    CDP transactions even though a consumed document cannot gain inventory
    before its scheduled reload. This predicate fails closed for every known
    navigation, purchase, submit, recovery or manual-intervention transition.
    """

    if not is_leak_watch_mode(config_dict):
        return False
    if get_leak_refresh_interval(config_dict) <= 0.0:
        return False
    cached_url = _get_cached_tab_url(tab)
    if (
        platform_key_for_url(cached_url) != "tixcraft"
        or classify_page(cached_url) is not PageClass.AREA
    ):
        return False
    try:
        state = platform_state_for_tab(tab, "tixcraft")
    except (RuntimeError, TypeError):
        return False
    scheduler = state.get("leak_scheduler")
    if not isinstance(scheduler, LeakWatchScheduler):
        return False
    if any(
        (
            scheduler.reload_pending,
            scheduler.dom_scan_pending,
            scheduler.area_click_pending,
            scheduler.ticket_form_pending,
            scheduler.submit_pending,
            state.get("pending_area_navigation") is not None,
            state.get("pending_date_navigation") is not None,
            bool(state.get("area_navigation_retry_due")),
            state.get("submit_in_flight") is not None,
            state.get("purchase_attempt") is not None,
            bool(state.get("manual_intervention_required")),
            bool(state.get("soft_block_recovery_in_progress")),
        )
    ):
        return False
    return bool(
        scheduler.fresh_document_after_reload
        or scheduler.should_wait_for_reload_before_dom_scan(config_dict)
    )


def _record_action(name, value=""):
    try:
        _ensure_runtime_helpers()
        _state["action_ledger"].record(name, value)
    except Exception:
        pass


def _get_tixcraft_purchase_attempt():
    attempt = _state.get("purchase_attempt")
    return attempt if isinstance(attempt, TixCraftPurchaseAttempt) else None


def _clear_tixcraft_attempt_scoped_actions():
    """Discard completion evidence that must never cross attempt boundaries."""

    _state["ocr_completed_url"] = ""
    _state["ocr_completed_attempt_id"] = None
    for key in list(_state.keys()):
        if str(key).startswith("ticket_assigned_"):
            _state.pop(key, None)


def _close_tixcraft_purchase_attempt(reason="closed"):
    attempt = _get_tixcraft_purchase_attempt()
    if attempt is None:
        return
    attempt.phase = TixCraftAttemptPhase.CLOSED
    _record_action("attempt_closed", f"{attempt.attempt_id}:{reason}")
    _clear_tixcraft_submit_in_flight(reason)
    _state["purchase_attempt"] = None
    _state["last_ticket_count"] = ""
    _state["last_ticket_count_confirmed"] = False
    _state["notification_submit_started_at"] = 0.0
    _state["notification_order_probe_next_at"] = 0.0
    _clear_tixcraft_attempt_scoped_actions()


def _begin_tixcraft_purchase_attempt(trigger, url="", seat_area="", force_new=False):
    event_id = _state.get("current_event_id", "") or _extract_tixcraft_event_id(url)
    game_id = _state.get("current_game_id", "") or _extract_tixcraft_game_id(url)
    event_snapshot = _get_current_tixcraft_event_snapshot(event_id=event_id)
    if (
        event_snapshot is not None
        and event_snapshot.quality < _TIXCRAFT_EVENT_CANONICAL_QUALITY
    ):
        # A document title is useful as a fallback, but it remains upgradeable
        # until the first notification for this purchase attempt is composed.
        event_snapshot = None
    current = _get_tixcraft_purchase_attempt()
    if current is not None and force_new:
        _close_tixcraft_purchase_attempt("forced_new_attempt")
        current = None
    if current is not None and not force_new and current.phase != TixCraftAttemptPhase.CLOSED:
        same_identity = current.event_id == event_id and current.game_id == game_id
        same_area_click = (
            trigger == "area_click"
            and current.area_url == url
            and current.phase == TixCraftAttemptPhase.AREA_SELECTED
        )
        if same_identity and (trigger != "area_click" or same_area_click):
            if seat_area and not current.seat_area:
                current.seat_area = seat_area
            if current.event_snapshot is None and event_snapshot is not None:
                current.event_snapshot = event_snapshot
            return current
        _close_tixcraft_purchase_attempt("superseded")

    sequence = int(_state.get("attempt_sequence", 0) or 0) + 1
    _state["attempt_sequence"] = sequence
    session_id = str(_state.get("notification_session_id", "") or uuid.uuid4().hex)
    _state["notification_session_id"] = session_id
    attempt = TixCraftPurchaseAttempt(
        session_id=session_id,
        attempt_id=sequence,
        event_id=event_id,
        game_id=game_id,
        seat_area=_clean_tixcraft_area_name(seat_area),
        area_url=_normalize_tixcraft_area_url(url),
        event_snapshot=event_snapshot,
        phase=(
            TixCraftAttemptPhase.AREA_SELECTED
            if trigger == "area_click"
            else TixCraftAttemptPhase.TICKET_FORM_ACTIVE
        ),
    )
    _state["purchase_attempt"] = attempt
    _state["last_ticket_count"] = ""
    _state["last_ticket_count_confirmed"] = False
    _state["notified_order_pending"] = False
    _state["notified_checkout_reached"] = False
    _state["is_popup_checkout"] = False
    _state["played_sound_order"] = False
    _state["notification_retry_at"] = {}
    _clear_tixcraft_attempt_scoped_actions()
    _record_action("attempt_started", f"{sequence}:{trigger}")
    return attempt


def _set_tixcraft_attempt_phase(phase):
    attempt = _get_tixcraft_purchase_attempt()
    if attempt is not None:
        attempt.phase = TixCraftAttemptPhase(phase)


def _track_tixcraft_attempt_page(page_class, url):
    previous = _state.get("attempt_last_page_class", "")
    current = PageClass(page_class)
    if current != PageClass.AREA:
        _clear_tixcraft_recovery_scan_guard()
    submit_in_flight = _is_tixcraft_submit_in_flight()
    if (
        current == PageClass.AREA
        and previous
        and previous != PageClass.AREA.value
        and not submit_in_flight
    ):
        _close_tixcraft_purchase_attempt("returned_to_area")
        _state["last_selected_area"] = ""
        _state["selected_area_candidate"] = ""
        _state["selected_area_metadata"] = {}
    elif current == PageClass.TICKET:
        attempt = _begin_tixcraft_purchase_attempt("ticket_page", url)
        if not submit_in_flight:
            attempt.phase = TixCraftAttemptPhase.TICKET_FORM_ACTIVE
    elif current == PageClass.ORDER:
        attempt = _begin_tixcraft_purchase_attempt("order_page", url)
        _clear_tixcraft_submit_in_flight("order_route")
        attempt.phase = TixCraftAttemptPhase.ORDER_PENDING
    elif current == PageClass.CHECKOUT:
        _clear_tixcraft_submit_in_flight("checkout_route")
        attempt = _get_tixcraft_purchase_attempt()
        if attempt is not None:
            attempt.phase = TixCraftAttemptPhase.CHECKOUT_REACHED
    elif current == PageClass.PAYMENT:
        _clear_tixcraft_submit_in_flight("payment_route")
        attempt = _get_tixcraft_purchase_attempt()
        if attempt is not None:
            attempt.phase = TixCraftAttemptPhase.PAYMENT_REACHED
    _state["attempt_last_page_class"] = current.value


_TIXCRAFT_CONFIRMED_PURCHASE_PAGES = {
    PageClass.TICKET,
    PageClass.ORDER,
    PageClass.CHECKOUT,
    PageClass.PAYMENT,
}
_TIXCRAFT_CONFIRMED_DATE_TARGET_PAGES = {
    PageClass.AREA,
    *_TIXCRAFT_CONFIRMED_PURCHASE_PAGES,
}


def _pending_navigation_expired(pending, now=None):
    if not isinstance(pending, TixCraftPendingNavigation):
        return True
    current = time.monotonic() if now is None else float(now)
    return current >= pending.deadline


def _clear_pending_area_navigation(reason="", config_dict=None):
    pending = _state.pop("pending_area_navigation", None)
    if "leak_scheduler" in _state:
        _state["leak_scheduler"].clear_area_click_pending()
    if pending is not None and reason:
        _record_action("area_navigation_cleared", reason)
        runtime_health.runtime_log(
            "[AREA] navigation_cleared",
            config_dict,
            reason=reason,
            source_url=getattr(pending, "source_url", ""),
        )
    return pending


def _set_pending_area_navigation(tab, url, area_text, config_dict, now=None):
    current = time.monotonic() if now is None else float(now)
    token = int(_state.get("area_navigation_token", 0) or 0) + 1
    _state["area_navigation_token"] = token
    pending = TixCraftPendingNavigation(
        kind="area",
        source_url=_normalize_tixcraft_area_url(url) or _tixcraft_route_key(url),
        seat_area=_clean_tixcraft_area_name(area_text),
        event_id=_state.get("current_event_id", ""),
        game_id=_state.get("current_game_id", ""),
        flow_generation=int(_state.get("notification_flow_generation", 0) or 0),
        token=token,
        tab_identity=id(tab),
        started_at=current,
        deadline=current + _get_tixcraft_navigation_confirmation_seconds(config_dict),
    )
    _state["pending_area_navigation"] = pending
    _state["selected_area_metadata"] = {
        "name": pending.seat_area,
        "confirmed": False,
        "event_id": pending.event_id,
        "game_id": pending.game_id,
        "area_url": pending.source_url,
        "attempt_id": None,
        "flow_generation": pending.flow_generation,
        "click_token": pending.token,
    }
    return pending


def _set_pending_date_navigation(tab, url, target_url, config_dict, now=None):
    current = time.monotonic() if now is None else float(now)
    token = int(_state.get("date_navigation_token", 0) or 0) + 1
    _state["date_navigation_token"] = token
    pending = TixCraftPendingNavigation(
        kind="date",
        source_url=_tixcraft_route_key(url),
        target_url=_tixcraft_route_key(target_url),
        event_id=_state.get("current_event_id", ""),
        game_id=_state.get("current_game_id", ""),
        flow_generation=int(_state.get("notification_flow_generation", 0) or 0),
        token=token,
        tab_identity=id(tab),
        started_at=current,
        deadline=current + _get_tixcraft_navigation_confirmation_seconds(config_dict),
    )
    _state["pending_date_navigation"] = pending
    return pending


def _is_confirmed_navigation(
    source_url,
    current_url,
    page_class,
    allowed_pages=None,
):
    source_key = _tixcraft_route_key(source_url)
    current_key = _tixcraft_route_key(current_url)
    allowed = (
        _TIXCRAFT_CONFIRMED_PURCHASE_PAGES
        if allowed_pages is None
        else allowed_pages
    )
    return (
        bool(source_key)
        and bool(current_key)
        and source_key != current_key
        and PageClass(page_class) in allowed
    )


def _reconcile_tixcraft_pending_navigation(tab, url, page_class, config_dict):
    """Confirm dispatched clicks from cached URL state without extra CDP calls."""
    current_page = PageClass(page_class)
    current_route = _tixcraft_route_key(url)
    now = time.monotonic()

    date_pending = _state.get("pending_date_navigation")
    if isinstance(date_pending, TixCraftPendingNavigation):
        if date_pending.tab_identity and date_pending.tab_identity != id(tab):
            _state.pop("pending_date_navigation", None)
        elif current_route != _tixcraft_route_key(date_pending.source_url):
            outcome = (
                "navigation_confirmed"
                if current_page in _TIXCRAFT_CONFIRMED_PURCHASE_PAGES
                else "navigation_left_date"
            )
            _state.pop("pending_date_navigation", None)
            _record_action("date_navigation_reconciled", outcome)
        elif _pending_navigation_expired(date_pending, now):
            _state.pop("pending_date_navigation", None)
            _state["date_navigation_retry_due"] = True
            runtime_health.runtime_log(
                "[DATE] click_not_navigated",
                config_dict,
                source_url=date_pending.source_url,
            )

    area_pending = _state.get("pending_area_navigation")
    if not isinstance(area_pending, TixCraftPendingNavigation):
        if current_page != PageClass.AREA and "leak_scheduler" in _state:
            _state["leak_scheduler"].clear_area_click_pending()
        return False
    if area_pending.tab_identity and area_pending.tab_identity != id(tab):
        _clear_pending_area_navigation("tab_changed", config_dict)
        return False

    source_route = _tixcraft_route_key(area_pending.source_url)
    if _is_confirmed_navigation(source_route, current_route, current_page):
        _state["last_selected_area"] = area_pending.seat_area
        attempt = _begin_tixcraft_purchase_attempt(
            "area_click",
            area_pending.source_url,
            area_pending.seat_area,
        )
        _state["selected_area_metadata"] = {
            "name": area_pending.seat_area,
            "confirmed": bool(area_pending.seat_area),
            "event_id": area_pending.event_id,
            "game_id": area_pending.game_id,
            "area_url": area_pending.source_url,
            "attempt_id": attempt.attempt_id,
            "flow_generation": area_pending.flow_generation,
            "click_token": area_pending.token,
        }
        _state.pop("pending_area_navigation", None)
        if "leak_scheduler" in _state:
            _state["leak_scheduler"].clear_area_click_pending()
        _record_action("area_navigation_confirmed", area_pending.seat_area)
        runtime_health.runtime_log(
            "[AREA] navigation_confirmed",
            config_dict,
            seat_area=area_pending.seat_area,
            current_url=url,
        )
        return True

    if current_page == PageClass.AREA and current_route == source_route:
        if _pending_navigation_expired(area_pending, now):
            _clear_pending_area_navigation("click_not_navigated", config_dict)
            _state["area_navigation_retry_due"] = True
        return False

    _clear_pending_area_navigation("unexpected_route", config_dict)
    return False


def _mark_tixcraft_submit_started(url="", tab=None):
    current = _get_tixcraft_purchase_attempt()
    completed_attempt = bool(
        current is not None
        and (
            current.phase
            in {
                TixCraftAttemptPhase.CHECKOUT_REACHED,
                TixCraftAttemptPhase.CLOSED,
            }
            or _TIXCRAFT_DISCORD_STAGES.issubset(current.discord_stages)
        )
    )
    previous_area = current.seat_area if completed_attempt and current else ""
    previous_ticket_count = (
        current.ticket_count
        if completed_attempt
        and current is not None
        and current.ticket_count_confirmed
        else ""
    )
    previous_event_id = current.event_id if completed_attempt and current else ""
    previous_game_id = current.game_id if completed_attempt and current else ""
    attempt = _begin_tixcraft_purchase_attempt(
        "ticket_submit",
        url,
        seat_area=previous_area,
        force_new=completed_attempt,
    )
    if (
        previous_ticket_count
        and attempt.event_id == previous_event_id
        and attempt.game_id == previous_game_id
        and attempt.seat_area == _clean_tixcraft_area_name(previous_area)
    ):
        attempt.ticket_count = previous_ticket_count
        attempt.ticket_count_confirmed = True
        _state["last_ticket_count"] = previous_ticket_count
        _state["last_ticket_count_confirmed"] = True
    started_at = time.monotonic()
    token = int(_state.get("submit_generation", 0) or 0) + 1
    _state["submit_generation"] = token
    attempt.phase = TixCraftAttemptPhase.SUBMIT_IN_FLIGHT
    _state["submit_in_flight"] = TixCraftSubmitInFlight(
        attempt_id=attempt.attempt_id,
        flow_generation=int(_state.get("notification_flow_generation", 0) or 0),
        token=token,
        tab_identity=id(tab) if tab is not None else 0,
        source_url=_tixcraft_route_key(url),
        started_at_monotonic=started_at,
    )
    _state["notification_submit_started_at"] = started_at
    _state["notification_order_probe_next_at"] = 0.0
    scheduler = _state.get("leak_scheduler")
    if scheduler is not None:
        scheduler.submit_pending = True
        scheduler.ticket_form_pending = True
    _record_action("submit_armed", f"{attempt.attempt_id}:{token}")


def _is_tixcraft_submit_in_flight(tab=None):
    context = _state.get("submit_in_flight")
    attempt = _get_tixcraft_purchase_attempt()
    if not isinstance(context, TixCraftSubmitInFlight) or attempt is None:
        return False
    return bool(
        attempt.attempt_id == context.attempt_id
        and attempt.phase
        in {
            TixCraftAttemptPhase.SUBMIT_IN_FLIGHT,
            TixCraftAttemptPhase.ORDER_PENDING,
        }
        and context.flow_generation
        == int(_state.get("notification_flow_generation", 0) or 0)
        and context.token == int(_state.get("submit_generation", 0) or 0)
        and (
            tab is None
            or not context.tab_identity
            or context.tab_identity == id(tab)
        )
    )


def _clear_tixcraft_submit_in_flight(reason=""):
    context = _state.pop("submit_in_flight", None)
    _state["notification_submit_started_at"] = 0.0
    _state["notification_order_probe_next_at"] = 0.0
    guard = _state.get("submit_guard")
    if guard is not None:
        guard.reset()
    scheduler = _state.get("leak_scheduler")
    if scheduler is not None:
        scheduler.submit_pending = False
        scheduler.ticket_form_pending = False
    if context is not None:
        _record_action(
            "submit_cleared",
            f"{getattr(context, 'attempt_id', '')}:{reason or 'unspecified'}",
        )


def _tixcraft_submit_owner_is_invalid(tab=None):
    """Return True only when identity evidence disproves submit ownership."""

    context = _state.get("submit_in_flight")
    if not isinstance(context, TixCraftSubmitInFlight):
        return context is not None
    attempt = _get_tixcraft_purchase_attempt()
    if attempt is None or attempt.attempt_id != context.attempt_id:
        return True
    if context.flow_generation != int(
        _state.get("notification_flow_generation", 0) or 0
    ):
        return True
    if context.token != int(_state.get("submit_generation", 0) or 0):
        return True
    return bool(tab is not None and context.tab_identity and context.tab_identity != id(tab))


async def _reconcile_tixcraft_submit_ownership(tab, page_class, url, config_dict):
    """Keep slow legitimate submits, but release ownership on proven recovery.

    Elapsed time alone is deliberately insufficient. A submit is cleared when
    its attempt/generation/tab identity is impossible, or when an interactive
    AREA document is positively confirmed after the browser returned there.
    """

    context = _state.get("submit_in_flight")
    if context is None:
        return False
    if _tixcraft_submit_owner_is_invalid(tab):
        _clear_tixcraft_submit_in_flight("identity_invalid")
        return False
    current_page = PageClass(page_class)
    if current_page is not PageClass.AREA:
        return True
    snapshot = await _read_tixcraft_page_health(tab, config_dict)
    if not _is_tixcraft_recovery_health_confirmed(snapshot, PageClass.AREA):
        return True
    attempt = _get_tixcraft_purchase_attempt()
    if attempt is not None:
        attempt.phase = TixCraftAttemptPhase.RECOVERING_TO_AREA
    _close_tixcraft_purchase_attempt("confirmed_area_recovery")
    _state["last_selected_area"] = ""
    _state["selected_area_candidate"] = ""
    _state["selected_area_metadata"] = {}
    _refresh_coordinator_for_tab(tab).reset_purchase_guard()
    _record_action("submit_owner_released", _tixcraft_route_key(url))
    return False


def _has_confirmed_tixcraft_submit_context(now=None):
    """Return True only while a real, recent form submission can backfill order."""
    if _is_tixcraft_submit_in_flight():
        return True
    attempt = _get_tixcraft_purchase_attempt()
    started_at = float(_state.get("notification_submit_started_at", 0.0) or 0.0)
    if attempt is None or started_at <= 0:
        return False
    now = time.monotonic() if now is None else float(now)
    elapsed = now - started_at
    return 0.0 <= elapsed <= _TIXCRAFT_SUBMIT_CONTEXT_MAX_SECONDS


async def _detect_tixcraft_order_pending(tab, url, now=None, force=False):
    if "/ticket/order" in str(url or "").lower():
        return True
    now = time.monotonic() if now is None else float(now)
    if not _has_confirmed_tixcraft_submit_context(now):
        return False
    next_probe_at = float(_state.get("notification_order_probe_next_at", 0.0) or 0.0)
    if not force and now < next_probe_at:
        return False
    _state["notification_order_probe_next_at"] = now + 0.2
    try:
        raw_value = await runtime_health.evaluate_with_timeout(
            tab,
            """
                (function() {
                    const visible = (el) => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.display !== 'none' && style.visibility !== 'hidden' &&
                            rect.width > 0 && rect.height > 0;
                    };
                    const text = (document.body && document.body.innerText || '').slice(0, 3000);
                    const markers = [
                        '訂單建立中', '請稍後，並避免進行任何操作',
                        '即將前往結帳', '即將轉跳，請稍後',
                        'creating order', 'processing your order'
                    ];
                    const marker = markers.find(item => text.toLowerCase().includes(item.toLowerCase())) || '';
                    const selectors = [
                        '#loadingmap',
                        '#loading.order-processing',
                        '.order-processing .spinner-border',
                        '.order-processing .spinner-grow',
                        '.loading-overlay.order-processing',
                        '[data-order-processing][aria-busy="true"]'
                    ];
                    const overlay = selectors.find(selector =>
                        Array.from(document.querySelectorAll(selector)).some(visible)
                    ) || '';
                    return JSON.stringify({pending: !!(marker || overlay), marker, overlay});
                })()
            """,
            reason="ORDER_PENDING_PROBE",
        )
        raw_value = util.parse_nodriver_result(raw_value)
        if isinstance(raw_value, str):
            raw_value = json.loads(raw_value)
        return bool(isinstance(raw_value, dict) and raw_value.get("pending"))
    except asyncio.CancelledError:
        raise
    except Exception:
        return False


async def _emit_tixcraft_attempt_notification(tab, config_dict, stage, url):
    if stage not in _TIXCRAFT_DISCORD_STAGES:
        runtime_health.runtime_log(
            "[TIXCRAFT] notification_stage_rejected",
            config_dict,
            stage=stage,
            current_url=url,
        )
        return False
    attempt = _get_tixcraft_purchase_attempt()
    if attempt is None:
        if stage == "order_pending" and "/ticket/order" in str(url or "").lower():
            attempt = _begin_tixcraft_purchase_attempt("order_page", url)
        else:
            runtime_health.runtime_log(
                "[TIXCRAFT] notification_skipped_without_attempt",
                config_dict,
                stage=stage,
                current_url=url,
            )
            return False
    attempt_commit_token = (
        attempt.session_id,
        attempt.attempt_id,
        int(_state.get("notification_flow_generation", 0) or 0),
    )
    if stage in attempt.delivery_failure_stages:
        return False
    if stage in attempt.discord_stages and stage in attempt.auxiliary_stages:
        delivery_status = util.get_discord_delivery_status(
            attempt.notification_id(stage)
        )
        if (
            delivery_status is not None
            and delivery_status.state == util.DiscordDeliveryState.FAILED.value
        ):
            if not util.DiscordNotificationDispatcher._is_retryable_terminal_failure(
                delivery_status.error
            ):
                attempt.delivery_failure_stages.add(stage)
                runtime_health.runtime_log(
                    "[TIXCRAFT] notification_delivery_permanent_failure",
                    config_dict,
                    attempt_id=attempt.attempt_id,
                    stage=stage,
                    error=delivery_status.error,
                )
                return False
            retry_count = attempt.delivery_retry_counts.get(stage, 0)
            if retry_count >= _TIXCRAFT_NOTIFICATION_DELIVERY_REARM_ATTEMPTS:
                attempt.delivery_failure_stages.add(stage)
                runtime_health.runtime_log(
                    "[TIXCRAFT] notification_delivery_retry_exhausted",
                    config_dict,
                    attempt_id=attempt.attempt_id,
                    stage=stage,
                    error=delivery_status.error,
                )
                return False
            attempt.delivery_retry_counts[stage] = retry_count + 1
            attempt.discord_stages.discard(stage)
            _state.setdefault("notification_retry_at", {})[stage] = (
                time.monotonic()
                + _TIXCRAFT_NOTIFICATION_DELIVERY_RETRY_SECONDS
            )
            runtime_health.runtime_log(
                "[TIXCRAFT] notification_delivery_rearmed",
                config_dict,
                attempt_id=attempt.attempt_id,
                stage=stage,
                retry=retry_count + 1,
            )
            return False
        return True

    retry_at = (_state.get("notification_retry_at") or {}).get(stage, 0.0)
    if time.monotonic() < float(retry_at or 0.0):
        return False
    context = await _build_tixcraft_notification_context(tab, config_dict, stage, url)
    active_attempt = _get_tixcraft_purchase_attempt()
    if (
        active_attempt is not attempt
        or attempt.phase
        in {
            TixCraftAttemptPhase.CLOSED,
            TixCraftAttemptPhase.RECOVERING_TO_AREA,
        }
    ):
        runtime_health.runtime_log(
            "[TIXCRAFT] notification_aborted_after_state_change",
            config_dict,
            attempt_id=attempt.attempt_id,
            stage=stage,
            current_url=_get_cached_tab_url(tab) or url,
        )
        return False
    cached_url = _get_cached_tab_url(tab)
    if cached_url:
        cached_page = classify_page(cached_url)
        allowed_pages = (
            {PageClass.TICKET, PageClass.ORDER, PageClass.CHECKOUT}
            if stage == "order_pending"
            else {PageClass.CHECKOUT}
        )
        if cached_page not in allowed_pages:
            runtime_health.runtime_log(
                "[TIXCRAFT] notification_aborted_after_route_change",
                config_dict,
                attempt_id=attempt.attempt_id,
                stage=stage,
                current_url=cached_url,
                page_class=cached_page.value,
            )
            return False
        if (
            stage == "order_pending"
            and cached_page == PageClass.TICKET
            and not await _detect_tixcraft_order_pending(
                tab,
                cached_url,
                force=True,
            )
        ):
            runtime_health.runtime_log(
                "[TIXCRAFT] notification_aborted_after_pending_marker_disappeared",
                config_dict,
                attempt_id=attempt.attempt_id,
                stage=stage,
                current_url=cached_url,
            )
            return False
        cached_event_id = _extract_tixcraft_event_id(cached_url)
        cached_game_id = _extract_tixcraft_game_id(cached_url)
        cached_origin = _extract_tixcraft_origin(cached_url)
        attempt_origin = (
            attempt.event_snapshot.origin
            if attempt.event_snapshot is not None
            else _state.get("current_event_origin", "")
        )
        identity_mismatch = bool(
            (
                cached_event_id
                and attempt.event_id
                and cached_event_id.casefold() != attempt.event_id.casefold()
            )
            or (
                cached_game_id
                and attempt.game_id
                and cached_game_id.casefold() != attempt.game_id.casefold()
            )
            or (
                cached_origin
                and attempt_origin
                and cached_origin != attempt_origin
            )
        )
        if identity_mismatch:
            runtime_health.runtime_log(
                "[TIXCRAFT] notification_aborted_after_identity_change",
                config_dict,
                attempt_id=attempt.attempt_id,
                stage=stage,
                current_url=cached_url,
            )
            return False
    if context is None:
        retry_count = attempt.metadata_retry_counts.get(stage, 0) + 1
        attempt.metadata_retry_counts[stage] = retry_count
        if retry_count < _TIXCRAFT_NOTIFICATION_METADATA_MAX_ATTEMPTS:
            _state.setdefault("notification_retry_at", {})[stage] = (
                time.monotonic() + 0.35
            )
            return False
        context = _build_tixcraft_metadata_failure_context(config_dict, stage, url)
        attempt.metadata_failure_stages.add(stage)
        runtime_health.runtime_log(
            "[TIXCRAFT] notification_metadata_retry_exhausted",
            config_dict,
            attempt_id=attempt.attempt_id,
            stage=stage,
            current_url=url,
        )

    # The order-pending marker probe above awaits CDP. Recovery or a new flow
    # may replace the active attempt/route during that await, so perform one
    # final synchronous commit check immediately before either notifier sees
    # the immutable payload.
    commit_attempt = _get_tixcraft_purchase_attempt()
    commit_url = _get_cached_tab_url(tab) or str(url or "")
    commit_page = classify_page(commit_url)
    commit_allowed_pages = (
        {PageClass.TICKET, PageClass.ORDER, PageClass.CHECKOUT}
        if stage == "order_pending"
        else {PageClass.CHECKOUT}
    )
    commit_token = (
        getattr(commit_attempt, "session_id", ""),
        getattr(commit_attempt, "attempt_id", -1),
        int(_state.get("notification_flow_generation", 0) or 0),
    )
    commit_event_id = _extract_tixcraft_event_id(commit_url)
    commit_game_id = _extract_tixcraft_game_id(commit_url)
    commit_origin = _extract_tixcraft_origin(commit_url)
    attempt_origin = (
        attempt.event_snapshot.origin
        if attempt.event_snapshot is not None
        else _state.get("current_event_origin", "")
    )
    commit_identity_mismatch = bool(
        (
            commit_event_id
            and attempt.event_id
            and commit_event_id.casefold() != attempt.event_id.casefold()
        )
        or (
            commit_game_id
            and attempt.game_id
            and commit_game_id.casefold() != attempt.game_id.casefold()
        )
        or (
            commit_origin
            and attempt_origin
            and commit_origin != attempt_origin
        )
    )
    if (
        commit_attempt is not attempt
        or commit_token != attempt_commit_token
        or attempt.phase
        in {
            TixCraftAttemptPhase.CLOSED,
            TixCraftAttemptPhase.RECOVERING_TO_AREA,
        }
        or commit_page not in commit_allowed_pages
        or commit_identity_mismatch
    ):
        runtime_health.runtime_log(
            "[TIXCRAFT] notification_aborted_before_commit",
            config_dict,
            attempt_id=attempt.attempt_id,
            stage=stage,
            current_url=commit_url,
            page_class=commit_page.value,
        )
        return False

    discord_enabled = is_discord_notification_enabled(config_dict)
    if stage not in attempt.discord_stages:
        if discord_enabled:
            queued_id = send_discord_notification(
                config_dict,
                stage,
                "TixCraft",
                context=context,
                notification_id=attempt.notification_id(stage),
            )
            if queued_id:
                attempt.discord_stages.add(stage)
            else:
                _state.setdefault("notification_retry_at", {})[stage] = (
                    time.monotonic() + 0.35
                )
        else:
            attempt.discord_stages.add(stage)

    if stage not in attempt.auxiliary_stages:
        send_telegram_notification(config_dict, stage, "TixCraft", context=context)
        attempt.auxiliary_stages.add(stage)

    delivered_to_dispatcher = stage in attempt.discord_stages
    if delivered_to_dispatcher:
        if stage == "order_pending":
            attempt.phase = TixCraftAttemptPhase.ORDER_PENDING
            _state["notified_order_pending"] = True
        elif stage == "checkout_reached":
            attempt.phase = TixCraftAttemptPhase.CHECKOUT_REACHED
            _state["notified_checkout_reached"] = True
        _record_action("notification_enqueued", f"{attempt.attempt_id}:{stage}")
    return delivered_to_dispatcher


def _is_retryable_alert(message):
    text = (message or "").lower()
    return any(keyword in text for keyword in _TIXCRAFT_RETRYABLE_ALERT_KEYWORDS)


def _reset_tixcraft_submit_state():
    _state["captcha_submit_until"] = 0
    _clear_tixcraft_attempt_scoped_actions()
    _state["captcha_alert_detected"] = False
    _state["manual_intervention_required"] = False
    _clear_tixcraft_submit_in_flight("reset")


def _reset_tixcraft_area_retry_state():
    _state["area_retry_count"] = 0
    _state["ticketmaster_phase"] = "area_select"
    for key in (
        "tixcraft_area_reload_next_at",
        "tixcraft_area_reload_last_wait_log",
        "tixcraft_date_reload_next_at",
        "tixcraft_date_reload_last_wait_log",
    ):
        _state[key] = 0
    for key in (
        "tixcraft_area_reload_url",
        "tixcraft_date_reload_url",
    ):
        _state[key] = ""
    _state.pop("pending_area_navigation", None)
    _state.pop("pending_date_navigation", None)
    _state["area_navigation_retry_due"] = False
    _state["date_navigation_retry_due"] = False
    if "leak_scheduler" in _state:
        _state["leak_scheduler"].reset_for_recovery()


def _clean_tixcraft_area_name(value):
    from notification_context import clean_seat_area

    return clean_seat_area(value, "")


async def _read_selected_area_name(
    target_area,
    area_list_cache=None,
    area_text_cache=None,
    config_dict=None,
):
    metadata_timed_out = False
    for attribute_name in ("text", "inner_text", "text_content"):
        try:
            value = getattr(target_area, attribute_name, "")
            if callable(value):
                value = value()
            if inspect.isawaitable(value):
                value = await _run_bounded_tixcraft_operation(
                    value,
                    _TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
                    f"AREA_NAME_{attribute_name.upper()}",
                    config_dict,
                )
            cleaned = _clean_tixcraft_area_name(str(value or "")[:240])
            if cleaned:
                return cleaned
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            metadata_timed_out = True
            break
        except Exception:
            continue

    if not metadata_timed_out:
        try:
            attributes = getattr(target_area, "attrs", {}) or {}
            if inspect.isawaitable(attributes):
                attributes = await _run_bounded_tixcraft_operation(
                    attributes,
                    _TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
                    "AREA_NAME_ATTRS",
                    config_dict,
                )
            if isinstance(attributes, dict):
                for key in ("data-area-name", "aria-label", "title"):
                    cleaned = _clean_tixcraft_area_name(attributes.get(key, ""))
                    if cleaned:
                        return cleaned
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    if area_list_cache and area_text_cache:
        for index, item in enumerate(area_list_cache):
            try:
                is_target = item is target_area or item == target_area
            except Exception:
                is_target = item is target_area
            if not is_target or index >= len(area_text_cache):
                continue
            cached = area_text_cache[index] or {}
            cleaned = _clean_tixcraft_area_name(cached.get("text") or cached.get("fontText") or "")
            if cleaned:
                return cleaned
    return ""


_TIXCRAFT_EVENT_SOURCE_QUALITY = {
    "heading": 100,
    "structured_data": 95,
    "og:title": 90,
    "document_title": 70,
}


def _extract_tixcraft_event_id(url):
    match = re.search(
        r"/(?:activity/(?:detail|game)|ticket/(?:area|ticket|verify|check-captcha))/([^/?#]+)",
        url or "",
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else ""


def _extract_tixcraft_game_id(url):
    match = re.search(
        r"/ticket/(?:area|ticket|verify|check-captcha)/[^/?#]+/([^/?#]+)",
        url or "",
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else ""


def _extract_tixcraft_origin(url):
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return ""
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not parts.netloc
        or not _is_tixcraft_family_host(parts.hostname)
    ):
        return ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def _normalize_tixcraft_event_name(value):
    text = clean_event_name(value, "")
    text = re.sub(
        r"^\s*(?:選擇區域|選擇票券)\s*[:：]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+@\s*多個表演場地\s*$", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _tixcraft_event_name_comparison_key(value):
    """Normalize presentation-only differences before generic-name checks."""
    text = unicodedata.normalize(
        "NFKC",
        _normalize_tixcraft_event_name(value),
    ).casefold()
    return "".join(
        character
        for character in text
        if not character.isspace()
        and unicodedata.category(character)[0] not in {"P", "S"}
    )


_TIXCRAFT_GENERIC_EVENT_NAMES = (
    "unknown event",
    "tixcraft",
    "tixcraft拓元售票",
    "拓元售票",
    "拓元售票系統",
    "選擇區域",
    "選擇票券",
    "日期",
    "場次",
    "區域",
    "訂單",
    "訂單資訊",
    "訂單信息",
    "訂單明細",
    "結帳",
    "購票須知",
    "注意事項",
    "票券資訊",
    "購票資訊",
    "活動資訊",
    "付款",
    "付款資訊",
    "會員登入",
    "載入中",
    "訂單建立中",
    "處理中",
    "select area",
    "select tickets",
    "date",
    "session",
    "area",
    "order",
    "order information",
    "order details",
    "checkout",
    "ticket information",
    "purchase information",
    "event information",
    "payment",
    "payment information",
    "member login",
    "login",
    "loading",
    "processing",
)
_TIXCRAFT_GENERIC_EVENT_NAME_KEYS = frozenset(
    _tixcraft_event_name_comparison_key(name)
    for name in _TIXCRAFT_GENERIC_EVENT_NAMES
)
_TIXCRAFT_PLATFORM_EVENT_NAME_KEYS = frozenset(
    {
        _tixcraft_event_name_comparison_key("TixCraft"),
        _tixcraft_event_name_comparison_key("拓元售票"),
    }
)
_TIXCRAFT_GENERIC_EVENT_HINT_KEYS = frozenset(
    {
        _tixcraft_event_name_comparison_key(name)
        for name in (
            "loading",
            "processing",
            "event information",
            "ticket information",
            "purchase information",
            "order information",
            "order details",
            "checkout",
            "payment information",
        )
    }
)


def _is_valid_tixcraft_event_name(value, event_id=""):
    text = _normalize_tixcraft_event_name(value)
    if not text:
        return False
    normalized_text = unicodedata.normalize("NFKC", text)
    normalized_event_id = unicodedata.normalize(
        "NFKC",
        str(event_id or "").strip(),
    )
    if normalized_event_id and normalized_text.casefold() == normalized_event_id.casefold():
        return False
    if re.fullmatch(r"\d{2,4}_[A-Za-z0-9_-]+", normalized_text):
        return False
    comparison_key = _tixcraft_event_name_comparison_key(normalized_text)
    for platform_key in _TIXCRAFT_PLATFORM_EVENT_NAME_KEYS:
        if not platform_key or platform_key not in comparison_key:
            continue
        without_platform = comparison_key.replace(platform_key, "")
        if (
            not without_platform
            or without_platform in _TIXCRAFT_GENERIC_EVENT_NAME_KEYS
            or without_platform in _TIXCRAFT_GENERIC_EVENT_HINT_KEYS
        ):
            return False
    return bool(
        comparison_key
        and comparison_key not in _TIXCRAFT_GENERIC_EVENT_NAME_KEYS
    )


def _tixcraft_event_name_specificity(value):
    comparison_key = _tixcraft_event_name_comparison_key(value)
    score = len(comparison_key)
    for hint in _TIXCRAFT_GENERIC_EVENT_HINT_KEYS:
        if hint and hint in comparison_key:
            score -= len(hint)
    for platform_key in _TIXCRAFT_PLATFORM_EVENT_NAME_KEYS:
        if platform_key and platform_key in comparison_key:
            score -= len(platform_key)
    return max(0, score)


def _tixcraft_event_cache_key(origin, event_id):
    normalized_origin = _extract_tixcraft_origin(origin) or str(origin or "").rstrip("/").lower()
    return normalized_origin, str(event_id or "").strip().casefold()


def _coerce_tixcraft_event_snapshot(value, origin, event_id, flow_generation):
    if isinstance(value, TixCraftEventSnapshot):
        snapshot = value
    elif isinstance(value, dict):
        try:
            captured_page = PageClass(value.get("captured_page_class", PageClass.UNKNOWN))
        except (TypeError, ValueError):
            captured_page = PageClass.UNKNOWN
        snapshot = TixCraftEventSnapshot(
            origin=_extract_tixcraft_origin(value.get("origin", "")) or origin,
            event_id=str(value.get("event_id", "") or event_id),
            event_name=_normalize_tixcraft_event_name(value.get("name", "")),
            source=str(value.get("source", "") or "legacy_cache"),
            quality=int(value.get("quality", 0) or 0),
            captured_url=str(value.get("captured_url", "") or ""),
            captured_page_class=captured_page,
            flow_generation=int(value.get("flow_generation", flow_generation) or 0),
            validated_at_monotonic=float(
                value.get("validated_at_monotonic", time.monotonic())
                or time.monotonic()
            ),
        )
    else:
        return None
    if (
        snapshot.origin != origin
        or snapshot.event_id.casefold() != str(event_id or "").casefold()
        or not _is_valid_tixcraft_event_name(snapshot.event_name, event_id)
    ):
        return None
    if snapshot.flow_generation == flow_generation:
        return snapshot
    return TixCraftEventSnapshot(
        origin=snapshot.origin,
        event_id=snapshot.event_id,
        event_name=snapshot.event_name,
        source=snapshot.source,
        quality=snapshot.quality,
        captured_url=snapshot.captured_url,
        captured_page_class=snapshot.captured_page_class,
        flow_generation=flow_generation,
        validated_at_monotonic=snapshot.validated_at_monotonic,
    )


def _cache_tixcraft_event_snapshot(snapshot):
    cache = _state.setdefault("event_metadata_cache", {})
    cache_key = _tixcraft_event_cache_key(snapshot.origin, snapshot.event_id)
    cache[cache_key] = snapshot
    while len(cache) > _TIXCRAFT_EVENT_METADATA_CACHE_CAPACITY:
        oldest_key = next(iter(cache))
        if oldest_key == cache_key and len(cache) > 1:
            oldest_key = next(key for key in cache if key != cache_key)
        cache.pop(oldest_key, None)


def _get_cached_tixcraft_event_snapshot(origin, event_id, flow_generation):
    if not origin or not event_id:
        return None
    cache = _state.get("event_metadata_cache") or {}
    cache_key = _tixcraft_event_cache_key(origin, event_id)
    cached = cache.get(cache_key)
    snapshot = _coerce_tixcraft_event_snapshot(
        cached,
        origin,
        event_id,
        flow_generation,
    )
    if snapshot is not None:
        cache[cache_key] = snapshot
    return snapshot


def _set_current_tixcraft_event_snapshot(snapshot):
    _state["event_snapshot"] = snapshot
    _state["event_name"] = snapshot.event_name if snapshot is not None else ""
    _state["event_name_quality"] = snapshot.quality if snapshot is not None else 0
    if snapshot is not None:
        _cache_tixcraft_event_snapshot(snapshot)


def _get_current_tixcraft_event_snapshot(event_id=""):
    event_id = str(event_id or _state.get("current_event_id", "") or "")
    origin = (
        _state.get("current_event_origin", "")
        or _extract_tixcraft_origin(_state.get("last_valid_area_url", ""))
        or _extract_tixcraft_origin(_state.get("notification_flow_url", ""))
    )
    flow_generation = int(_state.get("notification_flow_generation", 0) or 0)
    snapshot = _coerce_tixcraft_event_snapshot(
        _state.get("event_snapshot"),
        origin,
        event_id,
        flow_generation,
    )
    if snapshot is None:
        snapshot = _get_cached_tixcraft_event_snapshot(
            origin,
            event_id,
            flow_generation,
        )
    if snapshot is not None:
        _set_current_tixcraft_event_snapshot(snapshot)
    return snapshot


def _reset_tixcraft_notification_flow(event_id="", game_id="", origin=""):
    previous_event_id = _state.get("current_event_id", "")
    previous_game_id = _state.get("current_game_id", "")
    previous_origin = _state.get("current_event_origin", "")
    origin = _extract_tixcraft_origin(origin) or previous_origin
    next_generation = int(_state.get("notification_flow_generation", 0) or 0) + 1
    _state["current_event_id"] = event_id
    _state["current_game_id"] = game_id
    _state["current_event_origin"] = origin
    _state["notification_flow_generation"] = next_generation
    _set_current_tixcraft_event_snapshot(
        _get_cached_tixcraft_event_snapshot(
            origin,
            event_id,
            next_generation,
        )
    )
    _state["last_selected_area"] = ""
    _state["selected_area_candidate"] = ""
    _state["selected_area_metadata"] = {}
    _state.pop("pending_area_navigation", None)
    _state.pop("pending_date_navigation", None)
    if "leak_scheduler" in _state:
        _state["leak_scheduler"].clear_area_click_pending()
    _state["last_ticket_count"] = ""
    _state["last_ticket_count_confirmed"] = False
    _state["last_notification_metadata_warning"] = None
    _state["event_metadata_next_probe_at"] = 0.0
    if (
        event_id != previous_event_id
        or origin != previous_origin
        or (game_id and game_id != previous_game_id)
    ):
        _close_tixcraft_purchase_attempt("event_or_game_changed")
        _state["last_valid_area_url"] = ""
        _state["recent_area_route_url"] = ""


def _sync_tixcraft_notification_flow(url):
    if url == _state.get("notification_flow_url", ""):
        return
    _state["notification_flow_url"] = url
    event_id = _extract_tixcraft_event_id(url)
    game_id = _extract_tixcraft_game_id(url)
    origin = _extract_tixcraft_origin(url)
    current_event_id = _state.get("current_event_id", "")
    current_game_id = _state.get("current_game_id", "")
    current_origin = _state.get("current_event_origin", "")
    if origin and current_origin and origin != current_origin:
        _reset_tixcraft_notification_flow(event_id, game_id, origin)
        return
    if event_id and (
        event_id != current_event_id
        or (origin and origin != current_origin)
    ):
        _reset_tixcraft_notification_flow(event_id, game_id, origin)
        return
    if game_id and current_game_id and game_id != current_game_id:
        _reset_tixcraft_notification_flow(
            event_id or current_event_id,
            game_id,
            origin or current_origin,
        )
        return
    if event_id and not current_event_id:
        _state["current_event_id"] = event_id
    if game_id and not current_game_id:
        _state["current_game_id"] = game_id
    if origin and not current_origin:
        _state["current_event_origin"] = origin


async def _read_tixcraft_event_name(tab, event_id=""):
    js = """
        (function() {
            const results = [];
            const candidates = [
                '.activity-info h1',
                '.activity-info h2',
                '.game-title h2',
                '.game-title',
                '.event-info h1',
                '.event-info h2',
                '.activity-title'
            ];
            for (const selector of candidates) {
                const el = document.querySelector(selector);
                const text = el && el.innerText ? el.innerText.trim() : '';
                if (text) results.push({name: text, source: 'heading'});
            }
            for (const script of Array.from(document.querySelectorAll('script[type="application/ld+json"]'))) {
                try {
                    const data = JSON.parse(script.textContent || '{}');
                    const visit = (item) => {
                        if (Array.isArray(item)) {
                            item.forEach(visit);
                            return;
                        }
                        if (!item || typeof item !== 'object') return;
                        const rawTypes = Array.isArray(item['@type'])
                            ? item['@type'] : [item['@type']];
                        const types = rawTypes
                            .filter(value => typeof value === 'string')
                            .map(value => value.trim());
                        if (
                            types.some(value => value.toLowerCase() === 'event') &&
                            typeof item.name === 'string' &&
                            item.name.trim()
                        ) {
                            results.push({
                                name: item.name.trim(),
                                source: 'structured_data',
                                types
                            });
                        }
                        if (Array.isArray(item['@graph'])) visit(item['@graph']);
                    };
                    visit(data);
                } catch (_) {
                    // Ignore unrelated or malformed JSON-LD blocks.
                }
            }
            const ogTitle = document.querySelector('meta[property="og:title"]');
            const ogText = ogTitle && ogTitle.content ? ogTitle.content.trim() : '';
            if (ogText) results.push({name: ogText, source: 'og:title'});
            if (document.title) results.push({name: document.title, source: 'document_title'});
            return JSON.stringify(results);
        })();
    """
    try:
        raw_value = await runtime_health.evaluate_with_timeout(
            tab,
            js,
            timeout_seconds=_TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
            reason="TIXCRAFT_EVENT_METADATA",
            default="",
        )
        raw_value = util.parse_nodriver_result(raw_value)
        if isinstance(raw_value, str):
            try:
                raw_value = json.loads(raw_value)
            except Exception:
                raw_value = [{"name": raw_value, "source": "document_title"}]
        if isinstance(raw_value, dict):
            raw_value = [raw_value]
        if isinstance(raw_value, list):
            candidates = []
            for item in raw_value:
                if not isinstance(item, dict):
                    continue
                source = str(item.get("source", "document_title"))
                if source == "structured_data":
                    raw_types = item.get("types", [])
                    if isinstance(raw_types, str):
                        raw_types = [raw_types]
                    if not (
                        isinstance(raw_types, list)
                        and any(
                            str(value).strip().casefold() == "event"
                            for value in raw_types
                        )
                    ):
                        continue
                candidates.append(
                    {
                        "name": _normalize_tixcraft_event_name(item.get("name", "")),
                        "source": source,
                        "quality": _TIXCRAFT_EVENT_SOURCE_QUALITY.get(source, 0),
                    }
                )
            candidates.sort(key=lambda item: item["quality"], reverse=True)
            for metadata in candidates:
                if _is_valid_tixcraft_event_name(metadata["name"], event_id):
                    return metadata
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        runtime_health.runtime_log(
            "[TIXCRAFT] event_metadata_read_failed",
            error_type=type(exc).__name__,
        )
    return {"name": "", "source": "", "quality": 0}


async def _remember_tixcraft_event_name(tab, url):
    _sync_tixcraft_notification_flow(url)
    event_id = _state.get("current_event_id", "") or _extract_tixcraft_event_id(url)
    origin = (
        _state.get("current_event_origin", "")
        or _extract_tixcraft_origin(url)
    )
    attempt = _get_tixcraft_purchase_attempt()
    if attempt is not None and attempt.event_snapshot is not None:
        return attempt.event_snapshot.event_name
    current = _get_current_tixcraft_event_snapshot(event_id=event_id)
    if (
        current is not None
        and current.quality >= _TIXCRAFT_EVENT_CANONICAL_QUALITY
    ):
        if attempt is not None and attempt.event_snapshot is None:
            attempt.event_snapshot = current
        return current.event_name
    page_class = classify_page(url)
    if page_class not in _TIXCRAFT_EVENT_METADATA_PAGES:
        return current.event_name if current is not None else ""
    now = time.monotonic()
    next_probe_at = float(_state.get("event_metadata_next_probe_at", 0.0) or 0.0)
    if now < next_probe_at:
        return current.event_name if current is not None else ""
    _state["event_metadata_next_probe_at"] = (
        now + _TIXCRAFT_EVENT_METADATA_RETRY_SECONDS
    )
    metadata = await _read_tixcraft_event_name(tab, event_id)
    observed_url = _get_cached_tab_url(tab)
    if observed_url:
        observed_page = classify_page(observed_url)
        observed_event_id = _extract_tixcraft_event_id(observed_url)
        observed_origin = _extract_tixcraft_origin(observed_url)
        if (
            observed_page not in _TIXCRAFT_EVENT_METADATA_PAGES
            or (
                observed_event_id
                and observed_event_id.casefold() != str(event_id).casefold()
            )
            or (observed_origin and observed_origin != origin)
        ):
            runtime_health.runtime_log(
                "[TIXCRAFT] event_metadata_discarded_after_route_change",
                event_id=event_id,
                source_url=url,
                current_url=observed_url,
            )
            return current.event_name if current is not None else ""
    if (
        str(_state.get("current_event_id", "")).casefold()
        != str(event_id).casefold()
        or (
            origin
            and _state.get("current_event_origin", "")
            and _state.get("current_event_origin", "") != origin
        )
    ):
        runtime_health.runtime_log(
            "[TIXCRAFT] event_metadata_discarded_after_flow_change",
            event_id=event_id,
            source_url=url,
        )
        return current.event_name if current is not None else ""
    event_name = metadata["name"]
    quality = int(metadata["quality"])
    same_quality_provisional_update = bool(
        current is not None
        and current.quality < _TIXCRAFT_EVENT_CANONICAL_QUALITY
        and quality == current.quality
        and event_name != current.event_name
        and _tixcraft_event_name_specificity(event_name)
        > _tixcraft_event_name_specificity(current.event_name)
    )
    if (
        origin
        and event_id
        and _is_valid_tixcraft_event_name(event_name, event_id)
        and quality > 0
        and (
            current is None
            or quality > current.quality
            or same_quality_provisional_update
        )
    ):
        snapshot = TixCraftEventSnapshot(
            origin=origin,
            event_id=event_id,
            event_name=event_name,
            source=metadata["source"],
            quality=quality,
            captured_url=url,
            captured_page_class=page_class,
            flow_generation=int(
                _state.get("notification_flow_generation", 0) or 0
            ),
        )
        _set_current_tixcraft_event_snapshot(snapshot)
        if (
            attempt is not None
            and attempt.event_snapshot is None
            and snapshot.quality >= _TIXCRAFT_EVENT_CANONICAL_QUALITY
        ):
            attempt.event_snapshot = snapshot
        return snapshot.event_name
    return current.event_name if current is not None else ""


async def _read_tixcraft_ticket_count(tab, config_dict):
    try:
        result = await runtime_health.evaluate_with_timeout(
            tab,
            """
                (function() {
                    const selects = Array.from(document.querySelectorAll(
                        '.mobile-select, select[id*="TicketForm_ticketPrice_"]'
                    )).filter(s => s && !s.disabled && s.value && s.value !== "0");
                    const selected = selects.map(s => s.value).filter(Boolean);
                    return selected.length ? selected.join(',') : '';
                })();
            """,
            config_dict,
            timeout_seconds=_TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
            reason="TIXCRAFT_TICKET_COUNT",
            default="",
        )
        result = util.parse_nodriver_result(result)
        if result:
            _state["last_ticket_count"] = str(result)
            _state["last_ticket_count_confirmed"] = True
            return str(result)
    except Exception:
        pass
    return str(_state.get("last_ticket_count") or config_dict.get("ticket_number", "-"))


def _extract_tixcraft_seat_rows(text_values):
    found = []
    seen = set()
    pattern = re.compile(r"(?<!\d)(?:第\s*)?(\d{1,4})\s*排\s*(?:第\s*)?(\d{1,4})\s*號")
    unreserved_markers = (
        "自由入場",
        "自由席",
        "未劃位",
        "未划位",
        "general admission",
        "unreserved seating",
    )
    for text in text_values or []:
        normalized_text = str(text)
        for row_number, seat_number in pattern.findall(normalized_text):
            seat = f"{int(row_number)}排{int(seat_number)}號"
            if seat not in seen:
                seen.add(seat)
                found.append(seat)
    if found:
        return found
    combined = " ".join(str(text or "").casefold() for text in text_values or [])
    if any(marker in combined for marker in unreserved_markers):
        return ["自由入場／未劃位"]
    return found


async def _read_tixcraft_seat_rows_once(
    tab,
    config_dict=None,
    timeout_seconds=_TIXCRAFT_SEAT_EVALUATE_TIMEOUT_SECONDS,
):
    try:
        result = await runtime_health.evaluate_with_timeout(
            tab,
            """
                (function() {
                    const values = [];
                    const seenElements = new Set();
                    const selectors = [
                        'table.ticket-list tbody tr',
                        'table.order-list tbody tr',
                        '.ticket-info',
                        '.seat-info',
                        '[data-seat]',
                        '[data-seat-number]',
                        '.checkout-ticket',
                        '.ticket-unit',
                        '.order-ticket',
                        '.order-info li',
                        '.order-info .seat',
                        '.order-info .ticket'
                    ];
                    for (const selector of selectors) {
                        for (const el of Array.from(document.querySelectorAll(selector))) {
                            if (seenElements.has(el)) continue;
                            seenElements.add(el);
                            const text = (el.innerText || el.textContent || '').trim();
                            if (text) values.push(text);
                        }
                    }
                    return JSON.stringify(values.slice(0, 100));
                })();
            """,
            config_dict,
            timeout_seconds=timeout_seconds,
            reason="TIXCRAFT_CHECKOUT_SEATS",
            default="[]",
        )
        result = util.parse_nodriver_result(result)
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                result = []
        if isinstance(result, list):
            return _extract_tixcraft_seat_rows(result)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        runtime_health.runtime_log(
            "[TIXCRAFT] seat_rows_read_failed",
            error_type=type(exc).__name__,
        )
    return []


async def _read_tixcraft_seat_rows(
    tab,
    attempts=_TIXCRAFT_SEAT_READ_ATTEMPTS,
    interval_seconds=_TIXCRAFT_SEAT_READ_INTERVAL_SECONDS,
    config_dict=None,
    evaluate_timeout_seconds=_TIXCRAFT_SEAT_EVALUATE_TIMEOUT_SECONDS,
):
    attempts = max(1, int(attempts))
    for attempt_index in range(attempts):
        rows = await _read_tixcraft_seat_rows_once(
            tab,
            config_dict,
            timeout_seconds=evaluate_timeout_seconds,
        )
        if rows:
            return rows
        if attempt_index + 1 < attempts and interval_seconds > 0:
            await asyncio.sleep(interval_seconds)
    return []


async def _read_tixcraft_seat_area(tab, config_dict=None):
    try:
        raw_value = await runtime_health.evaluate_with_timeout(
            tab,
            """
                (function() {
                    const selectors = [
                        '[data-area-name]',
                        '.area-name',
                        '.zone-name',
                        '.ticket-area',
                        '.order-info .area',
                        '.ticket-info .area'
                    ];
                    for (const selector of selectors) {
                        const el = document.querySelector(selector);
                        if (!el) continue;
                        const value = el.getAttribute('data-area-name') ||
                            el.innerText || el.textContent || '';
                        if (value.trim()) return value.trim();
                    }
                    return '';
                })();
            """,
            config_dict,
            timeout_seconds=_TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
            reason="TIXCRAFT_SEAT_AREA",
            default="",
        )
        raw_value = util.parse_nodriver_result(raw_value)
        return _clean_tixcraft_area_name(raw_value)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        runtime_health.runtime_log(
            "[TIXCRAFT] seat_area_read_failed",
            error_type=type(exc).__name__,
        )
        return ""


async def _build_tixcraft_notification_context(
    tab,
    config_dict,
    stage,
    url,
    *,
    seat_rows_override=None,
):
    attempt = _get_tixcraft_purchase_attempt()
    event_snapshot = attempt.event_snapshot if attempt is not None else None
    if event_snapshot is None:
        await _remember_tixcraft_event_name(tab, url)
        event_snapshot = _get_current_tixcraft_event_snapshot()
        if (
            attempt is not None
            and event_snapshot is not None
            and (not attempt.event_id or attempt.event_id == event_snapshot.event_id)
        ):
            # First notification is the freeze point for a still-provisional
            # snapshot. All later stages in the same attempt reuse it.
            attempt.event_snapshot = event_snapshot
    event_name = event_snapshot.event_name if event_snapshot is not None else ""
    ticket_count = attempt.ticket_count if attempt is not None else ""
    if not ticket_count:
        ticket_count = await _read_tixcraft_ticket_count(tab, config_dict)
        if attempt is not None:
            attempt.ticket_count = ticket_count
            attempt.ticket_count_confirmed = bool(
                _state.get("last_ticket_count_confirmed", False)
            )
    event_id = (
        attempt.event_id
        if attempt is not None and attempt.event_id
        else _state.get("current_event_id", "")
    )
    game_id = (
        attempt.game_id
        if attempt is not None and attempt.game_id
        else _state.get("current_game_id", "")
    )
    area_metadata = _state.get("selected_area_metadata") or {}
    metadata_attempt_id = area_metadata.get("attempt_id")
    seat_area = attempt.seat_area if attempt is not None else ""
    if (
        not seat_area
        and area_metadata.get("confirmed")
        and area_metadata.get("event_id", "") == event_id
        and area_metadata.get("game_id", "") == game_id
        and area_metadata.get("flow_generation", 0)
        == _state.get("notification_flow_generation", 0)
        and (
            attempt is None
            or metadata_attempt_id in {None, attempt.attempt_id}
        )
    ):
        seat_area = _clean_tixcraft_area_name(area_metadata.get("name", ""))
    if not seat_area:
        seat_area = await _read_tixcraft_seat_area(tab, config_dict)
    if attempt is not None and seat_area and not attempt.seat_area:
        attempt.seat_area = seat_area

    missing_fields = []
    if not _is_valid_tixcraft_event_name(event_name, event_id):
        missing_fields.append("event_name")
    if not seat_area:
        missing_fields.append("seat_area")
    if missing_fields:
        warning_key = (event_id, game_id, tuple(missing_fields))
        if _state.get("last_notification_metadata_warning") != warning_key:
            runtime_health.runtime_log(
                "[TIXCRAFT] notification_metadata_incomplete",
                config_dict,
                fields=",".join(missing_fields),
                current_url=url,
            )
            _state["last_notification_metadata_warning"] = warning_key
        return None
    _state["last_notification_metadata_warning"] = None

    seat_rows = "訂單建立中﹍" if stage == "order_pending" else "-"
    if seat_rows_override is not None:
        seat_rows = seat_rows_override
    elif stage in {"checkout_reached", "payment_reached"}:
        rows = await _read_tixcraft_seat_rows(tab, config_dict=config_dict)
        seat_rows = rows or _TIXCRAFT_CHECKOUT_SEAT_FALLBACK
        attempt = _get_tixcraft_purchase_attempt()
        if attempt is not None:
            attempt.checkout_fallback_sent = not bool(rows)
            if attempt.checkout_fallback_sent and attempt.checkout_seat_poll_started_at <= 0:
                attempt.checkout_seat_poll_started_at = time.monotonic()
                attempt.checkout_seat_poll_next_at = attempt.checkout_seat_poll_started_at
    return make_notification_context(
        platform="TixCraft",
        stage=stage,
        event_name=event_name,
        ticket_count=ticket_count,
        seat_area=seat_area,
        seat_rows=seat_rows,
        current_url=url,
        page_class=classify_page(url).value,
        last_valid_area_url=_state.get("last_valid_area_url", ""),
    )


def _build_tixcraft_metadata_failure_context(config_dict, original_stage, url):
    """Build an explicit diagnostic notification without fake success metadata."""
    attempt = _get_tixcraft_purchase_attempt()
    event_id = (
        attempt.event_id
        if attempt is not None and attempt.event_id
        else _state.get("current_event_id", "")
    )
    event_snapshot = attempt.event_snapshot if attempt is not None else None
    if event_snapshot is None:
        event_snapshot = _get_current_tixcraft_event_snapshot(event_id=event_id)
    event_name = event_snapshot.event_name if event_snapshot is not None else ""
    if not _is_valid_tixcraft_event_name(event_name, event_id):
        event_name = "活動資料讀取失敗"
    seat_area = attempt.seat_area if attempt is not None else ""
    area_metadata = _state.get("selected_area_metadata") or {}
    metadata_matches = (
        area_metadata.get("confirmed")
        and area_metadata.get("event_id", "") == event_id
        and area_metadata.get("game_id", "") == _state.get("current_game_id", "")
        and area_metadata.get("flow_generation", 0)
        == _state.get("notification_flow_generation", 0)
        and (
            attempt is None
            or area_metadata.get("attempt_id") == attempt.attempt_id
        )
    )
    if not seat_area and metadata_matches:
        seat_area = _clean_tixcraft_area_name(area_metadata.get("name", ""))
    if not seat_area:
        seat_area = "區域資料讀取失敗"
    ticket_count = attempt.ticket_count if attempt is not None else ""
    if not ticket_count and attempt is None:
        ticket_count = str(_state.get("last_ticket_count") or "")
    if not ticket_count:
        ticket_count = str((config_dict or {}).get("ticket_number") or "-")
    return make_notification_context(
        platform="TixCraft",
        stage=original_stage,
        event_name=event_name,
        ticket_count=ticket_count,
        seat_area=seat_area,
        seat_rows="資料擷取失敗，請立即查看目前頁面",
        current_url=url,
        page_class=classify_page(url).value,
        last_valid_area_url=_state.get("last_valid_area_url", ""),
    )


async def _maybe_emit_tixcraft_seat_supplement(tab, config_dict, url):
    """Compatibility no-op: TixCraft emits exactly two notification stages."""
    return False


async def _recover_to_last_valid_area(tab, config_dict, reason):
    debug = util.create_debug_logger(config_dict)
    if _is_tixcraft_submit_in_flight(tab) and reason not in {
        "confirmed_rejected_error",
        "confirmed_canceled_order",
        "confirmed_continue_shopping",
        "retryable_alert",
    }:
        source_url = _get_cached_tab_url(tab)
        runtime_health.runtime_log(
            "[TIXCRAFT RECOVERY] blocked_submit_in_flight",
            config_dict,
            reason=reason,
            source_url=source_url,
            target_url=_state.get("last_valid_area_url", ""),
            page_class=classify_page(source_url).value,
            attempt_id=getattr(_get_tixcraft_purchase_attempt(), "attempt_id", None),
            generation=int(_state.get("notification_flow_generation", 0) or 0),
            token=int(_state.get("submit_generation", 0) or 0),
        )
        return False
    last_area_url = _normalize_tixcraft_area_url(_state.get("last_valid_area_url", ""))
    if not last_area_url:
        debug.log(f"[TIXCRAFT RECOVERY] No last_valid_area_url for {reason}")
        _reset_tixcraft_submit_state()
        _reset_tixcraft_area_retry_state()
        return False
    _set_tixcraft_attempt_phase(TixCraftAttemptPhase.RECOVERING_TO_AREA)
    _reset_tixcraft_submit_state()
    _reset_tixcraft_area_retry_state()
    debug.log(f"[TIXCRAFT RECOVERY] {reason}; navigating back to area: {last_area_url}")
    try:
        navigated = await _guarded_tixcraft_get(
            tab,
            last_area_url,
            config_dict,
            reason="RECOVERY_TO_AREA",
        )
        ready = await runtime_health.wait_for_interactive_ready(tab, config_dict)
        if not ready:
            runtime_health.runtime_log(
                "[TIXCRAFT RECOVERY] landing_not_interactive",
                config_dict,
                current_url=last_area_url,
            )
            return False
        landed_url = _get_cached_tab_url(tab)
        if _normalize_tixcraft_area_url(landed_url) != last_area_url:
            runtime_health.runtime_log(
                "[TIXCRAFT RECOVERY] landing_not_confirmed",
                config_dict,
                expected_url=last_area_url,
                current_url=landed_url,
                guarded_result=bool(navigated),
            )
            return False
        recovery_snapshot = await _read_tixcraft_page_health(tab, config_dict)
        if not _is_tixcraft_recovery_health_confirmed(
            recovery_snapshot,
            PageClass.AREA,
        ):
            runtime_health.runtime_log(
                "[TIXCRAFT RECOVERY] landing_document_not_confirmed",
                config_dict,
                expected_url=last_area_url,
                current_url=landed_url,
                guarded_result=bool(navigated),
            )
            return False
        _mark_tixcraft_recovery_landed(config_dict, last_area_url)
        _refresh_coordinator_for_tab(tab).reset_purchase_guard()
        _record_action("rejected_recover_to_area" if reason != "manual_cancel" else "manual_cancel_recover_to_area")
        return True
    except Exception as exc:
        debug.log(f"[TIXCRAFT RECOVERY] Failed to recover to area: {exc}")
        return False


async def _classify_recovery_page(tab, url, initial_page_class):
    if initial_page_class in {PageClass.AREA, PageClass.CHECKOUT, PageClass.PAYMENT}:
        return initial_page_class
    try:
        page_text = await runtime_health.evaluate_with_timeout(tab, '''
            (function() {
                return (document.body && document.body.innerText || '').slice(0, 5000);
            })()
        ''', reason="RECOVERY_PAGE_TEXT")
    except Exception:
        page_text = ""

    text = (page_text or "").lower()
    if any(item in text for item in ("訂單已取消", "已取消訂單", "取消訂單成功", "order canceled", "order cancelled")):
        return PageClass.CANCELED_ORDER
    if "繼續選購" in text or "continue shopping" in text:
        return PageClass.CONTINUE_SHOPPING
    if _is_retryable_alert(text):
        return PageClass.REJECTED_ERROR
    if _is_tixcraft_submit_in_flight() and initial_page_class in {
        PageClass.HOME,
        PageClass.ACTIVITY,
        PageClass.DATE,
        PageClass.AREA,
        PageClass.TICKET,
        PageClass.UNKNOWN,
    }:
        return PageClass.UNKNOWN
    return initial_page_class


async def _reload_page_when_due(tab, config_dict, state_key, log_prefix):
    """Throttle reloads without blocking the ticket-purchase polling loop."""
    debug = util.create_debug_logger(config_dict)
    interval = _get_auto_reload_interval(config_dict)
    now = time.monotonic()
    current_url = getattr(getattr(tab, "target", None), "url", "") or ""
    current_url_key = _normalize_tixcraft_area_url(current_url) or current_url
    url_key = f"{state_key}_url"
    next_key = f"{state_key}_next_at"
    log_key = f"{state_key}_last_wait_log"

    if _state.get(url_key) != current_url_key:
        _state[url_key] = current_url_key
        _state[next_key] = 0

    if interval <= 0:
        return False

    if is_leak_watch_mode(config_dict):
        # An empty cached target URL is not proof that we are still on an
        # eligible AREA page.  Treat it as unknown and wait for the URL
        # reconciler instead of falling through to a blind reload.
        if not current_url or not should_use_leak_watch(config_dict, current_url):
            _runtime_log_rate_limited(
                f"{state_key}_unsafe_runtime_log",
                "[LEAK] reload_skipped",
                config_dict,
                now=now,
                identity=f"not_leak_safe_page:{_tixcraft_route_key(current_url)}",
                reason="not_leak_safe_page",
                current_url=_tixcraft_route_key(current_url),
            )
            return False
        scheduler = _get_leak_scheduler()
        can_reload, reason = scheduler.can_reload(config_dict, current_url_key, now)
        if not can_reload:
            _runtime_log_rate_limited(
                f"{state_key}_runtime_wait_log",
                "[LEAK] reload_skipped",
                config_dict,
                now=now,
                identity=f"{reason}:{current_url_key}",
                reason=reason,
                current_url=current_url,
            )
            if reason == "interval_wait" and now - _state.get(log_key, 0) >= 1:
                _state[log_key] = now
                debug.log(f"{log_prefix} Waiting {scheduler.next_cycle_at - now:.1f}s until next leak reload")
            return False

        if not scheduler.begin_reload_cycle(current_url_key, now=now):
            return False
        runtime_health.runtime_log("[LEAK] cycle_start", config_dict, current_url=current_url)
        debug.log(f"{log_prefix} Leak-watch reload starting")
        reload_success = False
        try:
            reload_success = await guarded_reload(
                tab,
                reason=state_key,
                timeout_seconds=runtime_health.DEFAULT_RELOAD_TIMEOUT_SECONDS,
                config_dict=config_dict,
            )
            if reload_success:
                await runtime_health.wait_for_interactive_ready(tab, config_dict)
            return reload_success
        except Exception as exc:
            debug.log(f"{log_prefix} Leak-watch reload failed: {exc}")
            runtime_health.runtime_log(
                "[LEAK] reload_error",
                config_dict,
                error_type=type(exc).__name__,
            )
            return False
        finally:
            scheduler.finish_reload_cycle(
                config_dict,
                reload_success,
                now=time.monotonic(),
            )
            runtime_health.runtime_log("[LEAK] cycle_end", config_dict, current_url=current_url)

    next_at = _state.get(next_key, 0)
    if now >= next_at:
        _state[next_key] = now + interval
        debug.log(f"{log_prefix} Reloading page now")
        try:
            return await guarded_reload(tab, reason=state_key, config_dict=config_dict)
        except Exception as exc:
            debug.log(f"{log_prefix} Reload failed: {exc}")
            return False

    if now - _state.get(log_key, 0) >= 1:
        _state[log_key] = now
        debug.log(f"{log_prefix} Waiting {next_at - now:.1f}s until next reload; purchase loop remains active")
    return False


async def _is_tixcraft_ticket_count_ready(tab, config_dict):
    ticket_number = str(config_dict.get("ticket_number", 1))
    allow_less_tickets = config_dict.get("tixcraft", {}).get("allow_less_tickets", False)
    try:
        result = await tab.evaluate(f'''
            (function() {{
                if (window.location.href.includes('ticketmaster')) return true;
                const target = parseInt("{ticket_number}");
                const allowLess = {str(bool(allow_less_tickets)).lower()};
                const selects = Array.from(document.querySelectorAll(
                    '.mobile-select, select[id*="TicketForm_ticketPrice_"]'
                )).filter(s => s && !s.disabled);
                return selects.some(s => {{
                    if (s.value === "0" || s.value === "") return false;
                    const current = parseInt(s.value);
                    if (isNaN(current) || isNaN(target)) return false;
                    return allowLess ? (current > 0 && current <= target) : (current === target);
                }});
            }})();
        ''')
        return bool(util.parse_nodriver_result(result))
    except Exception:
        return False


def _extract_remaining_count(text):
    if not text:
        return None
    digits = re.findall(r'\d+', str(text))
    if not digits:
        return None
    try:
        return max(int(item) for item in digits)
    except Exception:
        return None


def _get_tixcraft_most_remaining_area(matched_blocks, area_list_cache, area_text_cache):
    best_row = None
    best_count = -1
    if not matched_blocks:
        return None

    for row in matched_blocks:
        count = None
        if area_list_cache is not None and area_text_cache is not None:
            for idx, cached_row in enumerate(area_list_cache):
                if cached_row is row and idx < len(area_text_cache):
                    count = _extract_remaining_count(area_text_cache[idx].get('fontText', ''))
                    break
        if count is None:
            count = 0
        if count > best_count:
            best_count = count
            best_row = row

    return best_row or matched_blocks[0]


async def nodriver_tixcraft_home_close_window(tab):
    if _state.get('cookie_accepted'):
        return
    try:
        accept_all_cookies_btn = await tab.query_selector('#onetrust-accept-btn-handler')
        if accept_all_cookies_btn:
            await accept_all_cookies_btn.click()
            _state['cookie_accepted'] = True
    except Exception:
        pass

async def nodriver_tixcraft_redirect(tab, url, config_dict=None):
    """Enter the date page when its purchase control appears, otherwise refresh."""

    ret = False
    game_name = ""
    url_split = url.split("/")
    if len(url_split) >= 6:
        game_name = url_split[5].split("#")[0].split("?")[0]
    if len(game_name) > 0:
        if "/activity/detail/%s" % (game_name,) in url:
            # Issue #278: detail pages are server-side rendered. Until the sale
            # opens there is no anchor to /activity/game/{id}. Redirecting
            # blindly drops the user on an empty gameList page and makes manual
            # F5 look broken. Only redirect once the buy link actually exists.
            selector = (
                'a[href*="/activity/game/%s"],'
                '[data-href*="/activity/game/%s"]'
            ) % (game_name, game_name)
            js = 'document.querySelector(%s) !== null' % json.dumps(selector)
            has_buy_link = False
            try:
                has_buy_link = await tab.evaluate(js)
            except Exception:
                has_buy_link = False
            if not has_buy_link:
                if config_dict is not None:
                    await _reload_page_when_due(
                        tab,
                        config_dict,
                        "tixcraft_detail_reload",
                        "[ACTIVITY DETAIL]",
                    )
                return ret
            entry_url = url.replace("/activity/detail/","/activity/game/").split("#")[0].split("?")[0]
            print("redirec to new url:", entry_url)
            try:
                await _guarded_tixcraft_get(
                    tab,
                    entry_url,
                    config_dict,
                    reason="TIXCRAFT_DETAIL_REDIRECT",
                )
                # 等待日期列表出現，確保頁面載入完成
                try:
                    await tab.wait_for('#gameList > table > tbody > tr', timeout=5)
                except Exception:
                    pass  # timeout 沒關係，讓後續邏輯處理
                ret = True
                _state["tixcraft_detail_reload_next_at"] = 0
            except Exception as exec1:
                pass
    return ret


def _should_redirect_tixcraft_detail(url):
    normalized = str(url or "").casefold()
    return "/activity/detail/" in normalized and "ticketmaster" not in normalized


def _is_ticketmaster_date_page(url):
    normalized = str(url or "").casefold()
    if "ticketmaster" not in normalized:
        return False
    return (
        "/activity/detail/" in normalized
        or "/activity/game/" in normalized
        or ("/artist/" in normalized and len(normalized.split("/")) == 6)
    )

# ============================================
# Ticketmaster.com NoDriver Platform Migration
# ============================================
# Foundation Functions (T004-T007)
#

# T004: Parse zone_info JSON from #mapSelectArea
async def nodriver_ticketmaster_parse_zone_info(tab, config_dict):
    """
    Parse zone_info JavaScript variable from #mapSelectArea element.
    Returns: zone_info dict or None if parsing fails
    """
    debug = util.create_debug_logger(config_dict)

    zone_info = None

    # Try method 1: String extraction from innerHTML (get_attribute('innerHTML') returns None
    # because innerHTML is a DOM property, not an HTML attribute — use evaluate instead)
    try:
        mapSelectArea_html = await tab.evaluate(
            "document.getElementById('mapSelectArea')?.innerHTML || ''"
        )

        tag_start = "var zone ="
        tag_end = "fieldImageType"
        if mapSelectArea_html and tag_start in mapSelectArea_html and tag_end in mapSelectArea_html:
            zone_string = mapSelectArea_html.split(tag_start)[1]
            zone_string = zone_string.split(tag_end)[0]
            zone_string = zone_string.strip().rstrip('\n,')

            import json
            zone_info = json.loads(zone_string)
            if debug.enabled:
                debug.log(f"[TICKETMASTER ZONE] Parsed zone_info via string extraction ({len(zone_info)} zones)")
                if len(zone_info) > 0:
                    sample_id = list(zone_info.keys())[0]
                    sample = zone_info[sample_id]
                    if isinstance(sample, dict) and "groupName" in sample:
                        debug.log(f"[TICKETMASTER ZONE] Sample zone '{sample_id}' groupName: {sample['groupName']}")
            return zone_info

    except Exception as exc:
        debug.log(f"[TICKETMASTER ZONE] String extraction failed: {exc}")

    # Try method 2: Direct JavaScript evaluation (fallback)
    try:
        result = await tab.evaluate('''
            (function() {
                // Check if zone variable exists in global scope
                if (typeof zone !== 'undefined') {
                    // IMPORTANT: Use JSON.parse(JSON.stringify()) to serialize RemoteObject to plain JSON
                    // Without this, NoDriver returns CDP RemoteObject format:
                    // {"type": "object", "value": [["key", {"type": "string", "value": "..."}]]}
                    // Instead of standard JSON: {"key": "value"}
                    try {
                        return JSON.parse(JSON.stringify(zone));
                    } catch(e) {
                        console.error('Zone serialization failed:', e);
                        return zone;  // Fallback to RemoteObject if serialization fails
                    }
                }

                // Fallback: Extract from #mapSelectArea innerHTML
                const el = document.querySelector('#mapSelectArea');
                if (!el) return null;

                const html = el.innerHTML;
                const match = html.match(/var zone = ({[\\s\\S]*?});/);
                if (!match) return null;

                try {
                    return JSON.parse(match[1]);
                } catch(e) {
                    console.error('JSON parse failed:', e);
                    return null;
                }
            })();
        ''')

        if result:
            # Convert RemoteObject to standard Python types
            zone_info = convert_remote_object(result)

            if debug.enabled:
                zone_type = "dict" if isinstance(zone_info, dict) else "list"
                debug.log(f"[TICKETMASTER ZONE] Successfully parsed zone_info ({len(zone_info)} zones, type: {zone_type})")
                debug.log(f"[TICKETMASTER ZONE] RemoteObject converted to standard format")

                # Print detailed structure for debugging
                if len(zone_info) > 0:
                    try:
                        # Print sample zone keys (BEFORE json.dumps to avoid serialization issues)
                        if isinstance(zone_info, list):
                            sample = zone_info[0]
                            sample_id = "index_0"

                            # Diagnostic for List format
                            debug.log(f"[TICKETMASTER ZONE] List first item type: {type(sample)}")

                            if isinstance(sample, dict):
                                sample_keys = list(sample.keys())[:10]  # First 10 keys
                                debug.log(f"[TICKETMASTER ZONE] List item keys (first 10): {sample_keys}")

                                # Check for zone_id fields
                                zone_id_field = None
                                for field in ["sectionCode", "id", "zoneId", "areaNo"]:
                                    if field in sample:
                                        zone_id_field = field
                                        debug.log(f"[TICKETMASTER ZONE] Found zone_id field: '{field}' = '{sample.get(field)}'")
                                        break

                                if not zone_id_field:
                                    debug.log(f"[TICKETMASTER ZONE] WARNING: No zone_id field found (sectionCode, id, zoneId, areaNo)")

                            elif isinstance(sample, (list, tuple)):
                                debug.log(f"[TICKETMASTER ZONE] List item is tuple/list with {len(sample)} elements")
                                if len(sample) > 0:
                                    debug.log(f"[TICKETMASTER ZONE] First element type: {type(sample[0])}")
                                    if isinstance(sample[0], str):
                                        debug.log(f"[TICKETMASTER ZONE] First element (zone_id): {sample[0]}")
                                if len(sample) > 1:
                                    debug.log(f"[TICKETMASTER ZONE] Second element type: {type(sample[1])}")
                                    zone_data = sample[1]
                                    if isinstance(zone_data, dict):
                                        # Check if conversion successful
                                        if "groupName" in zone_data:
                                            debug.log(f"[TICKETMASTER ZONE] [OK] groupName found: {zone_data.get('groupName')}")
                                        elif "type" in zone_data and "value" in zone_data:
                                            debug.log(f"[TICKETMASTER ZONE] [FAIL] Still RemoteObject format (has 'type' and 'value' keys)")
                                            # Try to convert again
                                            zone_data = convert_remote_object(zone_data)
                                            # Update in the list
                                            sample[1] = zone_data
                                            zone_info[0] = sample
                                            if "groupName" in zone_data:
                                                debug.log(f"[TICKETMASTER ZONE] [OK] After re-conversion, groupName found: {zone_data.get('groupName')}")
                                        else:
                                            debug.log(f"[TICKETMASTER ZONE] zone_data keys: {list(zone_data.keys())[:10]}")
                            else:
                                debug.log(f"[TICKETMASTER ZONE] WARNING: Unknown list item format")
                        else:
                            # Dict format
                            sample_id = list(zone_info.keys())[0]
                            sample = zone_info[sample_id]
                            debug.log(f"[TICKETMASTER ZONE] Sample zone_id: {sample_id}")

                        sample_keys = list(sample.keys()) if isinstance(sample, dict) else []
                        if sample_keys:
                            debug.log(f"[TICKETMASTER ZONE] Sample structure keys: {sample_keys[:10]}")  # First 10 keys
                    except Exception as diag_exc:
                        debug.log(f"[TICKETMASTER ZONE] Diagnostic logging failed: {diag_exc}")

    except Exception as exc:
        debug.log(f"[TICKETMASTER ZONE] JavaScript evaluation failed: {exc}")

    return zone_info

# T005: Get target area from zone_info (Pure function - no DOM access)
def get_ticketmaster_target_area(config_dict, area_keyword_item, zone_info):
    """
    Match areas from zone_info based on keyword.
    Returns: (is_need_refresh, matched_blocks)
    """
    debug = util.create_debug_logger(config_dict)

    area_auto_select_mode = config_dict.get("area_auto_select", {}).get("mode", "from top to bottom")

    is_need_refresh = False
    matched_blocks = []

    if not zone_info or len(zone_info) == 0:
        return True, None

    # Normalize zone_info to uniform iteration format
    # Dict format: {"zone_id": {...}} → [("zone_id", {...}), ...]
    # List format (3 types):
    #   Type A: [{"sectionCode": "field_C1_B", ...}, {...}] → extract sectionCode as zone_id
    #   Type B: [["field_C1_B", {...}], ...] → unpack tuple/list
    #   Type C: [(zone_id, {...}), ...] → already in correct format

    if isinstance(zone_info, dict):
        # Dict format: standard case
        zone_items = list(zone_info.items())
    elif isinstance(zone_info, list):
        # List format: need to detect which type
        if len(zone_info) == 0:
            zone_items = []
        else:
            first_item = zone_info[0]

            if isinstance(first_item, dict):
                # Type A: List of dicts - extract zone_id from dict
                zone_items = []
                for z in zone_info:
                    if not isinstance(z, dict):
                        continue
                    zone_id = z.get("sectionCode") or z.get("id") or z.get("zoneId") or z.get("areaNo")
                    zone_items.append((zone_id, z))

            elif isinstance(first_item, (list, tuple)) and len(first_item) >= 2:
                # Type B: List of [id, data] pairs
                zone_items = []
                for item in zone_info:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        zone_id = item[0]
                        zone_data = item[1]
                        # Convert RemoteObject if needed
                        if isinstance(zone_data, dict) and "type" in zone_data and "value" in zone_data:
                            zone_data = convert_remote_object(zone_data)
                        zone_items.append((zone_id, zone_data))

            else:
                # Unknown format - fallback to old logic
                debug.log(f"[TICKETMASTER AREA] Unknown zone_info list format, first item type: {type(first_item)}")
                zone_items = [(None, z) for z in zone_info]
    else:
        # Unexpected type
        debug.log(f"[TICKETMASTER AREA] Unexpected zone_info type: {type(zone_info)}")
        zone_items = []

    for zone_id, zone_data in zone_items:
        # Validate zone_data is dict-like (has .get() method)
        if not hasattr(zone_data, 'get'):
            debug.log(f"[TICKETMASTER AREA] zone_data is not dict-like: {type(zone_data)}, skipping")
            continue

        # Fallback: extract zone_id if still None
        if zone_id is None:
            zone_id = zone_data.get("sectionCode") or zone_data.get("id") or zone_data.get("zoneId") or zone_data.get("areaNo")

        row_is_enabled = zone_data.get("areaStatus") != "UNAVAILABLE"

        if not row_is_enabled:
            continue

        # Build row text from zone info
        row_text = ""
        group_name = ""
        description = ""
        try:
            group_name = zone_data.get("groupName", "")
            description = zone_data.get("description", "")
            row_text = group_name + " " + description
            if "price" in zone_data and len(zone_data["price"]) > 0:
                row_text += " " + zone_data["price"][0].get("ticketPrice", "")
        except Exception:
            pass

        if debug.enabled:
            # Show human-readable zone info instead of just zone_id
            display_name = f"{group_name} {description}".strip() if group_name or description else zone_id
            debug.log(f"[TICKETMASTER AREA] Processing zone: {zone_id} ({display_name})")

        if not row_text.strip():
            continue

        # Check exclude keywords
        if util.reset_row_text_if_match_keyword_exclude(config_dict, row_text):
            continue

        # Format and match keywords
        row_text = util.format_keyword_string(row_text)

        is_append_this_row = False
        if area_keyword_item:
            # Must match all keywords (AND logic)
            area_keyword_array = area_keyword_item.split(' ')

            # Word boundary matching function
            import re
            def word_boundary_match(keyword, text):
                """
                Match keyword with word boundary awareness.
                - Single char keywords (like 'I') require word boundaries
                - Multi-char keywords use substring match for flexibility
                """
                formatted_kw = util.format_keyword_string(keyword)
                if len(formatted_kw) <= 2:
                    # Short keywords need word boundary to avoid false positives
                    # e.g., 'I' should not match 'CIRCLE'
                    pattern = r'\b' + re.escape(formatted_kw) + r'\b'
                    return bool(re.search(pattern, text, re.IGNORECASE))
                else:
                    # Longer keywords use substring match
                    return formatted_kw in text

            # Detailed AND logic matching with PASS/FAIL logs
            if debug.enabled:
                keyword_results = []
                for kw in area_keyword_array:
                    match_result = word_boundary_match(kw, row_text)
                    status = "PASS" if match_result else "FAIL"
                    keyword_results.append(f"'{kw}':{status}")

                all_matched = all(
                    word_boundary_match(kw, row_text)
                    for kw in area_keyword_array
                )
                overall_status = "MATCHED" if all_matched else "REJECTED"
                debug.log(f"[TICKETMASTER AREA] AND Match: {zone_id} [{', '.join(keyword_results)}] -> {overall_status}")

            is_append_this_row = all(
                word_boundary_match(kw, row_text)
                for kw in area_keyword_array
            )
        else:
            # No keyword = match all
            is_append_this_row = True

        if is_append_this_row:
            matched_blocks.append(zone_id)

            if area_auto_select_mode == "from top to bottom":
                # Only need first match
                break

    if len(matched_blocks) == 0:
        matched_blocks = None
        is_need_refresh = True

    if matched_blocks:
        debug.log(f"[TICKETMASTER AREA] Matched {len(matched_blocks)} areas: {matched_blocks}")

    return is_need_refresh, matched_blocks

# T006: Get ticket price list (wait for page load)
async def nodriver_ticketmaster_get_ticketPriceList(tab, config_dict):
    """
    Wait for ticketPriceList to load and return the table element.
    Uses official NoDriver API (stable, recommended approach).
    Returns: table element or None

    References:
    - Fixed based on famiticket_nodriver_fixes.md (Phase 4: NoDriver Official API Migration)
    - Issue: tab.evaluate() returns None due to JavaScript Context failure
    - Solution: Use tab.wait_for() and tab.query_selector() instead
    """
    debug = util.create_debug_logger(config_dict)

    try:
        # Phase 1: Wait for mapContainer (basic page load)
        await tab.wait_for(selector='#mapContainer', timeout=5)

        # Ensure DOM references are synchronized (official recommendation)
        await tab

        debug.log("[TICKETMASTER TICKET] mapContainer found")

        # Phase 2: Wait for loading to finish (check if loadingmap disappears)
        max_wait = 10  # 10 seconds max
        for i in range(max_wait):
            loading = await tab.query_selector('#loadingmap')
            if not loading:
                if i > 0:
                    debug.log(f"[TICKETMASTER TICKET] Loading finished after {i}s")
                break
            await tab.sleep(1)
        else:
            # Timeout after 10 seconds
            debug.log("[TICKETMASTER TICKET] Loading timeout after 10s")

        # Phase 3: Try to find ticketPriceList
        table_element = await tab.query_selector('#ticketPriceList')

        if table_element:
            debug.log("[TICKETMASTER TICKET] Found ticketPriceList table")
            return table_element
        else:
            debug.log("[TICKETMASTER TICKET] ticketPriceList not found, will use zone_info")
            return None

    except asyncio.TimeoutError:
        debug.log("[TICKETMASTER TICKET] Timeout waiting for mapContainer")
        return None
    except Exception as e:
        debug.log(f"[TICKETMASTER TICKET] Error: {e}")
        return None

# ============================================
# User Story 1: Date Auto Select (T009)
# ============================================

async def nodriver_ticketmaster_date_auto_select(tab, config_dict):
    """
    Automatically select event date on Ticketmaster artist page.
    Returns: True if date was clicked, False otherwise
    """
    debug = util.create_debug_logger(config_dict)

    # Read config
    auto_select_mode = config_dict.get("date_auto_select", {}).get("mode", "from top to bottom")
    date_keyword = config_dict.get("date_auto_select", {}).get("date_keyword", "").strip()
    pass_date_is_sold_out_enable = config_dict.get("tixcraft", {}).get("pass_date_is_sold_out", False)
    auto_reload_coming_soon_page_enable = config_dict.get("kktix", {}).get("auto_reload_coming_soon_page", False)

    sold_out_text_list = ["Sold out", "No tickets available"]
    find_ticket_text_list = ['Find tickets', 'See Tickets']

    # Query date list
    # Ticketmaster.sg uses a table structure: #gameList tbody tr
    # Wait for dynamic content to load (max 5 seconds)
    area_list = None
    max_attempts = 10
    for attempt in range(max_attempts):
        try:
            area_list = await tab.query_selector_all('#gameList tbody tr')
            if area_list and len(area_list) > 0:
                debug.log(f"[TICKETMASTER DATE] Found date list after {attempt * 0.5}s")
                break
            await asyncio.sleep(0.5)
        except Exception as exc:
            if attempt == 0:
                debug.log(f"[TICKETMASTER DATE] Waiting for date list to load... ({exc})")
            await asyncio.sleep(0.5)

    if not area_list:
        debug.log(f"[TICKETMASTER DATE] Failed to find date list after {max_attempts * 0.5}s")
        return False

    matched_blocks = None
    formated_area_list = []

    if not area_list or len(area_list) == 0:
        debug.log("[TICKETMASTER DATE] No dates found on page")
        return False

    debug.log(f"[TICKETMASTER DATE] Found {len(area_list)} date blocks")

    # Filter date blocks
    for row in area_list:
        try:
            row_html = await row.get_html()
            row_text = util.remove_html_tags(row_html)
        except Exception:
            break

        if not row_text:
            continue

        row_is_enabled = False

        # Must contain "See Tickets"
        for text_item in find_ticket_text_list:
            if text_item in row_text:
                row_is_enabled = True
                break

        # Check sold out
        if row_is_enabled and pass_date_is_sold_out_enable:
            for sold_out_item in sold_out_text_list:
                if sold_out_item in row_text:
                    row_is_enabled = False
                    debug.log(f"[TICKETMASTER DATE] Skipping sold out event: {row_text[:60]}...")
                    break

        if row_is_enabled:
            formated_area_list.append(row)

    debug.log(f"[TICKETMASTER DATE] {len(formated_area_list)} available dates after filtering")

    # Get date_auto_fallback setting (default: False = strict mode)
    date_auto_fallback = config_dict.get('date_auto_fallback', False)

    # Build text list for keyword matching
    formated_area_list_text = []
    for row in formated_area_list:
        try:
            row_html = await row.get_html()
            row_text = util.remove_html_tags(row_html)
            formated_area_list_text.append(row_text)
        except Exception:
            formated_area_list_text.append("")

    # T004-T008: Early return pattern (Feature 003)
    if not date_keyword:
        matched_blocks = formated_area_list
        debug.log(f"[TICKETMASTER DATE KEYWORD] No keyword specified, using all {len(formated_area_list)} dates")
    else:
        # Early return pattern - iterate keywords in priority order
        matched_blocks = []
        target_row_found = False
        keyword_matched_index = -1

        try:
            import json
            import re
            keyword_array = json.loads("[" + date_keyword + "]")

            # T005: Start checking keywords log
            debug.log(f"[TICKETMASTER DATE KEYWORD] Start checking keywords in order: {keyword_array}")
            debug.log(f"[TICKETMASTER DATE KEYWORD] Total keyword groups: {len(keyword_array)}")
            debug.log(f"[TICKETMASTER DATE KEYWORD] Checking against {len(formated_area_list_text)} available dates...")

            # Iterate keywords in priority order (early return)
            for keyword_index, keyword_item_set in enumerate(keyword_array):
                debug.log(f"[TICKETMASTER DATE KEYWORD] Checking keyword #{keyword_index + 1}: {keyword_item_set}")

                # Check all rows for this keyword
                for i, row_text in enumerate(formated_area_list_text):
                    normalized_row_text = re.sub(r'\s+', ' ', row_text)
                    is_match = False

                    if isinstance(keyword_item_set, str):
                        # OR logic: single keyword
                        normalized_keyword = re.sub(r'\s+', ' ', keyword_item_set)
                        is_match = normalized_keyword in normalized_row_text
                    elif isinstance(keyword_item_set, list):
                        # AND logic: all keywords must match
                        normalized_keywords = [re.sub(r'\s+', ' ', kw) for kw in keyword_item_set]
                        match_results = [kw in normalized_row_text for kw in normalized_keywords]
                        is_match = all(match_results)

                        # Detailed AND logic log
                        if debug.enabled:
                            result_strs = [f"'{kw}':{('PASS' if r else 'FAIL')}" for kw, r in zip(keyword_item_set, match_results)]
                            overall = "MATCHED" if is_match else "REJECTED"
                            debug.log(f"[TICKETMASTER DATE KEYWORD] AND Match: [{', '.join(result_strs)}] -> {overall}")

                    if is_match:
                        # T006: Keyword matched - IMMEDIATELY select and stop
                        matched_blocks = [formated_area_list[i]]
                        target_row_found = True
                        keyword_matched_index = keyword_index
                        debug.log(f"[TICKETMASTER DATE KEYWORD] Keyword #{keyword_index + 1} matched: '{keyword_item_set}'")
                        debug.log(f"[TICKETMASTER DATE SELECT] Selected date: {row_text[:80]} (keyword match)")
                        break  # Early Return - stop checking other rows

                if target_row_found:
                    # EARLY RETURN: Stop checking further keywords
                    break

            # T007: All keywords failed log
            if not target_row_found:
                debug.log(f"[TICKETMASTER DATE KEYWORD] All keywords failed to match")

        except Exception as e:
            debug.log(f"[TICKETMASTER DATE KEYWORD] Parsing error: {e}")
            matched_blocks = []

    # Match result summary
    debug.log(f"[TICKETMASTER DATE KEYWORD] ========================================")
    debug.log(f"[TICKETMASTER DATE KEYWORD] Match Summary:")
    debug.log(f"[TICKETMASTER DATE KEYWORD]   Total dates available: {len(formated_area_list) if formated_area_list else 0}")
    debug.log(f"[TICKETMASTER DATE KEYWORD]   Total dates matched: {len(matched_blocks) if matched_blocks else 0}")
    debug.log(f"[TICKETMASTER DATE KEYWORD] ========================================")

    # T018-T020: Conditional fallback based on date_auto_fallback switch
    if matched_blocks is not None and len(matched_blocks) == 0 and date_keyword and formated_area_list is not None and len(formated_area_list) > 0:
        if date_auto_fallback:
            # T018: Fallback enabled - use auto_select_mode
            debug.log(f"[TICKETMASTER DATE FALLBACK] date_auto_fallback=true, triggering auto fallback")
            debug.log(f"[TICKETMASTER DATE FALLBACK] Selecting available date based on date_select_order='{auto_select_mode}'")
            matched_blocks = formated_area_list
        else:
            # T019: Fallback disabled - strict mode
            debug.log(f"[TICKETMASTER DATE FALLBACK] date_auto_fallback=false, fallback is disabled")
            debug.log(f"[TICKETMASTER DATE SELECT] No date selected, will reload page and retry")

    # Select target
    if formated_area_list is None or len(formated_area_list) == 0:
        target_area = None
    elif matched_blocks is None or len(matched_blocks) == 0:
        target_area = None
    else:
        target_area = util.get_target_item_from_matched_list(matched_blocks, auto_select_mode)

    is_date_clicked = False
    if target_area:
        try:
            existing_tabs = tuple(getattr(tab.browser, "tabs", ()))
            # Click "See Tickets" link
            link_element = await target_area.query_selector('a')
            if link_element:
                await link_element.click()
                is_date_clicked = True
                debug.log("[TICKETMASTER DATE] Clicked 'See Tickets' link")

                # Handle new tab (close if opened)
                await tab.sleep(0.3)
                new_tabs = [
                    candidate
                    for candidate in getattr(tab.browser, "tabs", ())
                    if all(candidate is not existing for existing in existing_tabs)
                ]
                if new_tabs:
                    # Only tabs observed as a direct result of this bot click
                    # enter the ownership registry. User-opened tabs that were
                    # already present are never closed.
                    for extra_tab in new_tabs:
                        register_owned_tab(
                            extra_tab,
                            "ticketmaster_date_click_popup",
                        )
                        await close_owned_tab(extra_tab)
                    await tab.sleep(0.2)

        except Exception as exc:
            debug.log(f"[TICKETMASTER DATE] Failed to click link: {exc}")

    # Auto reload if no match
    if auto_reload_coming_soon_page_enable and not is_date_clicked and len(formated_area_list) == 0:
        await _reload_page_when_due(tab, config_dict, "ticketmaster_date_reload", "[TICKETMASTER DATE]")
    elif is_date_clicked:
        _state["ticketmaster_date_reload_next_at"] = 0

    return is_date_clicked

# ============================================
# User Story 2: Area Auto Select (T012)
# ============================================

async def nodriver_ticketmaster_area_auto_select(tab, config_dict, zone_info):
    """
    Automatically select seat area on Ticketmaster ticket page.
    """
    debug = util.create_debug_logger(config_dict)

    area_keyword = config_dict.get("area_auto_select", {}).get("area_keyword", "").strip()

    debug.log(f"[TICKETMASTER AREA] area_keyword: {area_keyword}")

    is_need_refresh = False
    matched_blocks = None

    # Get area_auto_fallback setting (default: False = strict mode)
    area_auto_fallback = config_dict.get("area_auto_fallback", False)

    if area_keyword:
        area_keyword_array = util.parse_keyword_string_to_array(area_keyword)

        debug.log(f"[TICKETMASTER AREA] Parsed keyword groups: {area_keyword_array}")

        # Early Return Pattern: Try each keyword group with priority
        for idx, area_keyword_item in enumerate(area_keyword_array):
            debug.log(f"[TICKETMASTER AREA] Trying keyword group {idx + 1}/{len(area_keyword_array)}: '{area_keyword_item}'")

            is_need_refresh, matched_blocks = get_ticketmaster_target_area(config_dict, area_keyword_item, zone_info)

            if not is_need_refresh and matched_blocks:
                # Found match - Early Return
                debug.log(f"[TICKETMASTER AREA] Early Return: keyword group {idx + 1} matched {len(matched_blocks)} area(s)")
                break
            else:
                debug.log(f"[TICKETMASTER AREA] Keyword group {idx + 1} had no matches, trying next...")

        # Conditional fallback: only match all if area_auto_fallback is enabled
        if is_need_refresh:
            if area_auto_fallback:
                debug.log("[TICKETMASTER AREA] Fallback enabled: selecting from all available areas")
                is_need_refresh, matched_blocks = get_ticketmaster_target_area(config_dict, "", zone_info)
            else:
                debug.log("[TICKETMASTER AREA] Strict mode: no keyword match, will refresh page")
                # Keep is_need_refresh = True, matched_blocks = None
    else:
        # Empty keyword = match all
        is_need_refresh, matched_blocks = get_ticketmaster_target_area(config_dict, "", zone_info)

    # Select target
    auto_select_mode = config_dict.get("area_auto_select", {}).get("mode", "from top to bottom")
    target_area = util.get_target_item_from_matched_list(matched_blocks, auto_select_mode)

    if target_area:
        try:
            # Execute JavaScript to select area
            click_area_javascript = f'areaTicket("{target_area}", "map");'
            debug.log(f"[TICKETMASTER AREA] Executing: {click_area_javascript}")

            await tab.evaluate(click_area_javascript)

            # Wait for AJAX to load ticketPriceList (areaTicket executes AJAX request)
            max_wait = 5  # 5 seconds max
            for i in range(max_wait):
                await tab.sleep(1)

                # Check if ticketPriceList has loaded
                price_list = await tab.query_selector('#ticketPriceList')
                if price_list:
                    debug.log(f"[TICKETMASTER AREA] ticketPriceList loaded after {i+1}s")
                    break
            else:
                debug.log("[TICKETMASTER AREA] Timeout waiting for ticketPriceList (5s)")

            debug.log(f"[TICKETMASTER AREA] Selected zone: {target_area}")

        except Exception as exc:
            debug.log(f"[TICKETMASTER AREA] Failed to execute JavaScript: {exc}")

    # Auto refresh if needed (only when keyword is specified but no match)
    if is_need_refresh:
        # Check if area_keyword is empty (empty = should match all areas)
        area_keyword = config_dict.get("area_auto_select", {}).get("area_keyword", "").strip()

        if area_keyword:
            # Keyword specified but no match → might need to wait for availability
            debug.log("[TICKETMASTER AREA] No areas matched keyword")
            await _reload_page_when_due(tab, config_dict, "ticketmaster_area_reload", "[TICKETMASTER AREA]")
        else:
            # No keyword but no areas → likely a data parsing issue, don't reload
            debug.log("[TICKETMASTER AREA] No areas available (possible zone_info parsing issue)")
            # Let next function (assign_ticket_number) handle it
    else:
        _state["ticketmaster_area_reload_next_at"] = 0

# ============================================
# User Story 3: Ticket Number Assignment (T015-T016)
# ============================================

async def nodriver_ticketmaster_assign_ticket_number(tab, config_dict):
    """
    Automatically set ticket number on Ticketmaster ticket page.
    Caller is responsible for area selection before calling this function.
    Returns True if ticket number was set, False otherwise.
    """
    debug = util.create_debug_logger(config_dict)

    # Get ticket price list (area must already be selected by caller)
    table_select = await nodriver_ticketmaster_get_ticketPriceList(tab, config_dict)
    if not table_select:
        return False

    # Find select element
    select_element = None
    try:
        select_element = await table_select.query_selector('select')
    except Exception as exc:
        debug.log(f"[TICKETMASTER TICKET] Failed to find select: {exc}")
        return False

    if not select_element:
        debug.log("[TICKETMASTER TICKET] No select element found")
        return False

    # Update element to sync attributes
    try:
        await select_element.update()
    except Exception:
        pass

    # Check if element is enabled
    try:
        select_attrs = select_element.attrs or {}
        is_disabled = 'disabled' in select_attrs
        if is_disabled:
            debug.log("[TICKETMASTER TICKET] Select element is disabled")
            return False
    except Exception as exc:
        debug.log(f"[TICKETMASTER TICKET] Failed to check disabled status: {exc}")
        pass

    ticket_number = str(config_dict.get("ticket_number", 1))
    allow_less_tickets = bool(
        config_dict.get("tixcraft", {}).get("allow_less_tickets", False)
    )

    # Check current value (zendriver evaluate returns Python values directly)
    select_attrs = select_element.attrs or {}
    selector_id = select_attrs.get('id')
    current_value = None
    if selector_id:
        try:
            current_value = await tab.evaluate(f'''
                (function() {{
                    const selectEl = document.getElementById('{selector_id}');
                    if (selectEl && selectEl.selectedIndex >= 0) {{
                        return selectEl.options[selectEl.selectedIndex].text;
                    }}
                    return null;
                }})();
            ''')
        except Exception:
            pass

    if current_value and current_value != "0" and current_value.isnumeric():
        current_count = int(current_value)
        target_count = int(ticket_number)
        acceptable = current_count == target_count or (
            allow_less_tickets and 0 < current_count < target_count
        )
        if acceptable:
            debug.log(
                f"[TICKETMASTER TICKET] Ticket number already set to: {current_value}"
            )
            try:
                auto_mode_button = await tab.query_selector('#autoMode')
                if auto_mode_button:
                    await auto_mode_button.click()
                    debug.log("[TICKETMASTER TICKET] Clicked #autoMode button")
            except Exception:
                pass
            return True
        debug.log(
            f"[TICKETMASTER TICKET] Existing ticket number {current_value} "
            f"does not satisfy target {ticket_number}; re-selecting"
        )

    try:
        # Get select element ID for JavaScript manipulation
        select_attrs = select_element.attrs or {}
        selector_id = select_attrs.get('id')
        if not selector_id:
            debug.log("[TICKETMASTER TICKET] Select element has no id attribute")
            return False

        # Dump all option texts for debugging
        option_texts = await tab.evaluate(f'''
            (function(elementId) {{
                const selectEl = document.getElementById(elementId);
                if (!selectEl) return [];
                return Array.from(selectEl.options).map(o => o.text + '|' + o.value);
            }})('{selector_id}');
        ''')
        debug.log(f"[TICKETMASTER TICKET] Available options: {option_texts}")

        # Exact count is mandatory unless the user explicitly enables
        # allow_less_tickets.  The fallback is always below the target; HunterX
        # must never silently buy more tickets than configured.
        result = await tab.evaluate(f'''
            (function(elementId, targetText, targetCount, allowLess) {{
                const selectEl = document.getElementById(elementId);
                if (!selectEl) {{
                    return {{ success: false, error: "Element not found" }};
                }}
                const options = selectEl.options;
                // Try exact text match first
                for (let i = 0; i < options.length; i++) {{
                    if (options[i].text === targetText) {{
                        selectEl.selectedIndex = i;
                        selectEl.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        return {{ success: true, value: options[i].value, selected: options[i].text }};
                    }}
                }}
                if (!allowLess) {{
                    return {{ success: false, error: "Exact ticket count unavailable" }};
                }}
                // Fallback: largest available count strictly below the target.
                let maxIdx = -1;
                let maxVal = 0;
                for (let i = 0; i < options.length; i++) {{
                    const v = parseInt(options[i].value);
                    if (!isNaN(v) && v > 0 && v < targetCount && v > maxVal) {{
                        maxVal = v;
                        maxIdx = i;
                    }}
                }}
                if (maxIdx >= 0) {{
                    selectEl.selectedIndex = maxIdx;
                    selectEl.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    return {{ success: true, value: options[maxIdx].value, selected: options[maxIdx].text, fallback: true }};
                }}
                return {{ success: false, error: "Option not found" }};
            }})({json.dumps(selector_id)}, {json.dumps(ticket_number)}, {int(ticket_number)}, {json.dumps(allow_less_tickets)});
        ''')

        if result and result.get('success'):
            selected = result.get('selected', ticket_number)
            if result.get('fallback'):
                debug.log(
                    f"[TICKETMASTER TICKET] Exact '{ticket_number}' not found; "
                    f"allow_less_tickets selected: {selected}"
                )
            else:
                debug.log(f"[TICKETMASTER TICKET] Set ticket number to: {selected}")

            # Click autoMode button
            await tab.sleep(0.1)
            try:
                auto_mode_button = await tab.query_selector('#autoMode')
                if auto_mode_button:
                    await auto_mode_button.click()
                    debug.log("[TICKETMASTER TICKET] Clicked #autoMode button")
            except Exception:
                pass
            return True
        else:
            debug.log(f"[TICKETMASTER TICKET] Failed to set ticket number: {result.get('error') if result else 'no result'}")
            return False

    except Exception as exc:
        debug.log(f"[TICKETMASTER TICKET] Exception setting ticket number: {exc}")
        return False

# ============================================
# User Story 4: Captcha Handling (T019)
# ============================================

async def nodriver_ticketmaster_captcha(tab, config_dict, ocr, captcha_browser):
    """
    Handle captcha on Ticketmaster check-captcha page.
    Returns: True if captcha was handled, False otherwise
    """
    debug = util.create_debug_logger(config_dict)

    # Check agree checkbox
    for _ in range(2):
        is_checked = await nodriver_check_checkbox(tab, '#TicketForm_agree')
        if is_checked:
            debug.log("[TICKETMASTER CAPTCHA] Checked TicketForm_agree")
            break

    # Alert state tracked by event handler
    alert_state = {"detected": False, "message": ""}

    async def on_captcha_alert(event: cdp.page.JavascriptDialogOpening):
        alert_state["detected"] = True
        alert_state["message"] = event.message
        debug.log(f"[TICKETMASTER CAPTCHA] Alert event: '{event.message[:60]}'")
        # Dismiss the alert immediately to prevent blocking
        try:
            await tab.send(cdp.page.handle_java_script_dialog(accept=True))
            debug.log("[TICKETMASTER CAPTCHA] Alert auto-dismissed by handler")
        except Exception:
            pass

    # Register handler for this captcha session
    tab.add_handler(cdp.page.JavascriptDialogOpening, on_captcha_alert)

    # Handle captcha
    if not config_dict.get("ocr_captcha", {}).get("enable", False):
        # OCR disabled - manual input
        await nodriver_tixcraft_keyin_captcha_code(tab, answer="", auto_submit=False, config_dict=config_dict)
        return False
    else:
        # OCR enabled - auto recognition
        previous_answer = None
        current_url = tab.target.url
        fail_count = 0
        total_fail_count = 0

        await asyncio.sleep(random.uniform(0.5, 1.0))

        for redo_ocr in range(99):
            try:
                alert_state["detected"] = False  # Reset before each attempt

                away_from_keyboard_enable = config_dict.get("ocr_captcha", {}).get("force_submit", False)
                ocr_captcha_image_source = config_dict.get("ocr_captcha", {}).get("image_source", "canvas")
                domain_name = tab.target.url.split('/')[2]

                # Call tixcraft_auto_ocr for captcha recognition
                is_need_redo_ocr, previous_answer, is_form_submitted = await nodriver_tixcraft_auto_ocr(
                    tab, config_dict, ocr, away_from_keyboard_enable, previous_answer,
                    captcha_browser, ocr_captcha_image_source, domain_name
                )

                if is_form_submitted:
                    debug.log("[TICKETMASTER CAPTCHA] Form submitted")

                    # Poll for alert event (max 2 seconds)
                    for wait_i in range(10):
                        await asyncio.sleep(0.2)
                        if alert_state["detected"]:
                            break

                    debug.log(f"[TICKETMASTER CAPTCHA] alert_state={alert_state}")

                    error_detected = alert_state["detected"]

                    # If alert was detected (already dismissed by handler), retry OCR
                    if error_detected:
                        debug.log("[TICKETMASTER CAPTCHA] Captcha error detected, retrying...")

                        await asyncio.sleep(0.3)
                        await nodriver_tixcraft_reload_captcha(tab, domain_name)
                        previous_answer = None
                        fail_count = 0
                        total_fail_count += 1

                        if total_fail_count >= 15:
                            print("[TICKETMASTER CAPTCHA] Failed 15 times. Manual input required.")
                            await nodriver_tixcraft_keyin_captcha_code(tab, config_dict=config_dict)
                            break

                        await asyncio.sleep(random.uniform(0.5, 1.0))
                        continue

                    # Check for Ticketmaster custom error modal (not native alert)
                    # The modal shows "The verification code that you entered is incorrect"
                    try:
                        # Check for modal overlay or dialog
                        modal_result = await tab.evaluate('''
                            (function() {
                                // Check for visible modal or alert dialog
                                const modals = document.querySelectorAll('.modal, .alert, [role="dialog"], [role="alertdialog"]');
                                for (const modal of modals) {
                                    if (modal.offsetParent !== null || getComputedStyle(modal).display !== 'none') {
                                        return {
                                            found: true,
                                            text: modal.innerText || modal.textContent
                                        };
                                    }
                                }
                                // Also check for any visible buttons that might be confirm/OK
                                const buttons = document.querySelectorAll('button');
                                for (const btn of buttons) {
                                    const text = btn.innerText || btn.textContent;
                                    if ((text.includes('確定') || text.includes('OK') || text.includes('Try again')) &&
                                        btn.offsetParent !== null) {
                                        return {
                                            found: true,
                                            buttonSelector: 'button'
                                        };
                                    }
                                }
                                return { found: false };
                            })();
                        ''')

                        # Handle CDP RemoteObject format (may return as list or dict)
                        modal_content = None
                        if modal_result:
                            if isinstance(modal_result, dict):
                                modal_content = modal_result
                            elif isinstance(modal_result, list) and len(modal_result) > 0:
                                # CDP sometimes returns [{'type': 'object', 'value': {...}}]
                                first_item = modal_result[0]
                                if isinstance(first_item, dict):
                                    if 'value' in first_item:
                                        modal_content = first_item.get('value', {})
                                    else:
                                        modal_content = first_item

                        if modal_content and isinstance(modal_content, dict) and modal_content.get('found'):
                            error_detected = True
                            debug.log(f"[TICKETMASTER CAPTCHA] Error modal detected")

                            # Try to click confirm/OK button to dismiss modal
                            dismiss_result = await tab.evaluate('''
                                (function() {
                                    // Find and click confirm button
                                    const buttons = document.querySelectorAll('button');
                                    for (const btn of buttons) {
                                        const text = btn.innerText || btn.textContent;
                                        if (text.includes('確定') || text.includes('OK') || text.includes('Try again')) {
                                            btn.click();
                                            return true;
                                        }
                                    }
                                    // Try to find any primary button
                                    const primaryBtn = document.querySelector('.btn-primary, [type="button"]');
                                    if (primaryBtn) {
                                        primaryBtn.click();
                                        return true;
                                    }
                                    return false;
                                })();
                            ''')

                            # Handle CDP RemoteObject format
                            dismiss_success = False
                            if dismiss_result is True:
                                dismiss_success = True
                            elif isinstance(dismiss_result, list) and len(dismiss_result) > 0:
                                dismiss_success = dismiss_result[0] is True or dismiss_result[0] == True

                            if debug.enabled:
                                if dismiss_success:
                                    debug.log("[TICKETMASTER CAPTCHA] Error modal dismissed, will retry OCR")
                                else:
                                    debug.log("[TICKETMASTER CAPTCHA] Could not dismiss modal")

                            # Reset state for retry
                            await asyncio.sleep(0.3)

                            # Reload captcha for new image
                            await nodriver_tixcraft_reload_captcha(tab, domain_name)
                            previous_answer = None
                            fail_count = 0
                            total_fail_count += 1

                            # Check retry limit
                            if total_fail_count >= 15:
                                print("[TICKETMASTER CAPTCHA] OCR failed 15 times after error modal. Please enter captcha manually.")
                                await nodriver_tixcraft_keyin_captcha_code(tab, config_dict=config_dict)
                                break

                            await asyncio.sleep(random.uniform(0.5, 1.0))
                            continue  # Retry OCR

                    except Exception as modal_exc:
                        debug.log(f"[TICKETMASTER CAPTCHA] Error checking modal: {modal_exc}")

                    # No error modal detected, form submitted successfully
                    if not error_detected:
                        break

                if not away_from_keyboard_enable:
                    break

                if not is_need_redo_ocr:
                    break

                # Track failures and handle retry limits
                fail_count += 1
                total_fail_count += 1

                debug.log(f"[TICKETMASTER CAPTCHA] Fail count: {fail_count}, Total fails: {total_fail_count}")

                # Check if total failures reached 15, switch to manual input mode
                if total_fail_count >= 15:
                    print("[TICKETMASTER CAPTCHA] OCR failed 15 times. Please enter captcha manually.")
                    await nodriver_tixcraft_keyin_captcha_code(tab, config_dict=config_dict)
                    break

                # Refresh captcha after 3 consecutive failures with same answer
                if fail_count >= 3:
                    debug.log("[TICKETMASTER CAPTCHA] 3 consecutive failures, reloading captcha...")
                    await nodriver_tixcraft_reload_captcha(tab, domain_name)
                    fail_count = 0
                    previous_answer = None  # Reset to allow fresh OCR
                    await asyncio.sleep(random.uniform(0.8, 1.2))  # Wait for new captcha to load
                else:
                    # Wait between retries to allow canvas to fully load
                    await asyncio.sleep(random.uniform(0.3, 0.5))

                # Check if URL changed
                new_url = tab.target.url
                if new_url != current_url:
                    debug.log("[TICKETMASTER CAPTCHA] URL changed, stopping OCR loop")
                    break

            except Exception as exc:
                debug.log(f"[TICKETMASTER CAPTCHA] OCR error: {exc}")
                break

        return True

async def nodriver_ticketmaster_promo(tab, config_dict, fail_list):
    question_selector = '#promoBox'
    return await nodriver_tixcraft_input_check_code(tab, config_dict, fail_list, question_selector)

async def nodriver_tixcraft_verify(tab, config_dict, fail_list):
    question_selector = '.zone-verify'
    return await nodriver_tixcraft_input_check_code(tab, config_dict, fail_list, question_selector)

async def nodriver_fill_verify_form(tab, config_dict, inferred_answer_string, fail_list, input_text_css, next_step_button_css, submit_by_enter, check_input_interval):
    """
    NoDriver version of fill_common_verify_form for TixCraft verification.

    Fills verification form input and submits the answer.

    Args:
        tab: NoDriver tab object
        config_dict: Configuration dictionary
        inferred_answer_string: Answer to fill in
        fail_list: List of failed answers
        input_text_css: CSS selector for input field
        next_step_button_css: CSS selector for submit button (optional)
        submit_by_enter: Whether to submit by pressing Enter
        check_input_interval: Interval to wait when no answer

    Returns:
        tuple[bool, list]: (is_answer_sent, updated fail_list)
    """
    debug = util.create_debug_logger(config_dict)

    is_answer_sent = False

    try:
        # Check if input field exists and get current value
        input_info = await tab.evaluate(f'''
            (function() {{
                var input = document.querySelector("{input_text_css}");
                if (input) {{
                    return {{
                        exists: true,
                        value: input.value || ""
                    }};
                }}
                return {{ exists: false, value: "" }};
            }})()
        ''')
        input_info = util.parse_nodriver_result(input_info)

        if not input_info or not input_info.get('exists', False):
            debug.log("[VERIFY FORM] Input field not found:", input_text_css)
            return is_answer_sent, fail_list

        inputed_value = input_info.get('value', '')

        debug.log(f"[VERIFY FORM] Current input value: '{inputed_value}'")
        debug.log(f"[VERIFY FORM] Answer to fill: '{inferred_answer_string}'")

        if len(inferred_answer_string) > 0:
            # Fill the answer if different from current value
            if inputed_value != inferred_answer_string:
                # Clear and fill using JavaScript
                await tab.evaluate(f'''
                    (function() {{
                        var input = document.querySelector("{input_text_css}");
                        if (input) {{
                            input.value = "";
                            input.value = {json.dumps(inferred_answer_string)};
                            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                    }})()
                ''')

                debug.log(f"[VERIFY FORM] Filled answer: {inferred_answer_string}")

            # Submit the form
            is_button_clicked = False

            if submit_by_enter:
                # Submit by pressing Enter
                await tab.evaluate(f'''
                    (function() {{
                        var input = document.querySelector("{input_text_css}");
                        if (input) {{
                            var event = new KeyboardEvent('keydown', {{
                                key: 'Enter',
                                code: 'Enter',
                                keyCode: 13,
                                which: 13,
                                bubbles: true
                            }});
                            input.dispatchEvent(event);

                            // Also try form submit
                            var form = input.closest('form');
                            if (form) {{
                                form.submit();
                            }}
                        }}
                    }})()
                ''')
                is_button_clicked = True
                debug.log("[VERIFY FORM] Submitted by Enter key")
            elif len(next_step_button_css) > 0:
                # Click the submit button
                try:
                    btn = await tab.query_selector(next_step_button_css)
                    if btn:
                        await btn.click()
                        is_button_clicked = True
                        debug.log(f"[VERIFY FORM] Clicked submit button: {next_step_button_css}")
                except Exception as btn_exc:
                    debug.log(f"[VERIFY FORM] Failed to click button: {btn_exc}")

            if is_button_clicked:
                is_answer_sent = True
                fail_list.append(inferred_answer_string)
                debug.log(f"[VERIFY FORM] Answer sent, attempt #{len(fail_list)}")

                # Wait and check for alert
                await asyncio.sleep(0.3)
        else:
            # No answer to fill, just focus the input
            if len(inputed_value) == 0:
                await tab.evaluate(f'''
                    (function() {{
                        var input = document.querySelector("{input_text_css}");
                        if (input && document.activeElement !== input) {{
                            input.focus();
                        }}
                    }})()
                ''')
                await asyncio.sleep(check_input_interval)
                debug.log("[VERIFY FORM] No answer, focused input field")

    except Exception as exc:
        debug.log(f"[VERIFY FORM] Error: {exc}")

    return is_answer_sent, fail_list

async def nodriver_tixcraft_input_check_code(tab, config_dict, fail_list, question_selector):
    debug = util.create_debug_logger(config_dict)

    answer_list = []

    question_text = await nodriver_get_text_by_selector(tab, question_selector, 'innerText')
    if len(question_text) > 0:
        write_question_to_file(question_text)

        answer_list = util.get_answer_list_from_user_guess_string(config_dict, CONST_MAXBOT_ANSWER_ONLINE_FILE)
        if len(answer_list)==0:
            if config_dict["advanced"]["auto_guess_options"]:
                # Note: guess_tixcraft_question() doesn't use the driver parameter
                answer_list = util.guess_tixcraft_question(None, question_text, config_dict)

        # Fallback: use discount_code as final answer when user_guess_string is empty
        # and auto_guess_options yielded no result. Covers serial-number style prompts
        # (e.g. Weverse Presale MY MEMBERSHIP) where users naturally fill the discount
        # code field rather than the answer dictionary.
        # Guard: only trigger for member/serial-number style prompts so the
        # discount_code is not wasted on unrelated questions (math, trivia, etc.).
        if len(answer_list)==0:
            discount_code_fallback = (config_dict["advanced"].get("discount_code") or "").strip()
            if discount_code_fallback and _is_serial_code_question(question_text):
                debug.log("[VERIFY] Using discount_code as serial-code fallback answer")
                answer_list = [discount_code_fallback]

        inferred_answer_string = ""
        for answer_item in answer_list:
            if not answer_item in fail_list:
                inferred_answer_string = answer_item
                break

        debug.log("inferred_answer_string:", inferred_answer_string)
        debug.log("answer_list:", answer_list)

        # PS: auto-focus() when empty inferred_answer_string with empty inputed text value.
        input_text_css = "input[name='checkCode']"
        next_step_button_css = "button.btn.btn-primary"
        submit_by_enter = False
        check_input_interval = 0.2
        is_answer_sent, fail_list = await nodriver_fill_verify_form(tab, config_dict, inferred_answer_string, fail_list, input_text_css, next_step_button_css, submit_by_enter, check_input_interval)

    return fail_list

async def nodriver_tixcraft_date_auto_select(tab, url, config_dict, domain_name):
    debug = util.create_debug_logger(config_dict)
    now_monotonic = time.monotonic()
    if _state.pop("date_navigation_retry_due", False):
        await _reload_page_when_due(
            tab,
            config_dict,
            "tixcraft_date_reload",
            "[DATE SELECT]",
        )
        return False
    pending_date = _state.get("pending_date_navigation")
    if isinstance(pending_date, TixCraftPendingNavigation):
        same_tab = not pending_date.tab_identity or pending_date.tab_identity == id(tab)
        same_route = _tixcraft_route_key(url) == _tixcraft_route_key(
            pending_date.source_url
        )
        if not same_tab or not same_route:
            _state.pop("pending_date_navigation", None)
        elif not _pending_navigation_expired(pending_date, now_monotonic):
            _runtime_log_rate_limited(
                "tixcraft_date_navigation_wait_log",
                "[DATE] click_waiting_navigation",
                config_dict,
                now=now_monotonic,
                identity=pending_date.source_url,
                current_url=url,
            )
            return True
        else:
            _state.pop("pending_date_navigation", None)
            _state["date_navigation_retry_due"] = False
            runtime_health.runtime_log(
                "[DATE] click_not_navigated",
                config_dict,
                current_url=url,
            )
            await _reload_page_when_due(
                tab,
                config_dict,
                "tixcraft_date_reload",
                "[DATE SELECT]",
            )
            return False

    # Issue #188: Check sold out cooldown before proceeding
    now_monotonic = time.monotonic()
    if _state and _state.get("sold_out_cooldown_until", 0) > now_monotonic:
        remaining = _state["sold_out_cooldown_until"] - now_monotonic
        cooldown_log_key = "sold_out_cooldown_last_log"
        now = now_monotonic
        if now - _state.get(cooldown_log_key, 0) >= 1:
            _state[cooldown_log_key] = now
            debug.log(f"[DATE SELECT] Sold out cooldown active for {remaining:.1f}s; purchase loop remains active")
        return False
    elif _state:
        _state["sold_out_cooldown_until"] = 0

    # T003: Check main switch (defensive programming)
    if not config_dict["date_auto_select"]["enable"]:
        debug.log("[DATE SELECT] Main switch is disabled, skipping date selection")
        return False

    # read config
    auto_select_mode = config_dict["date_auto_select"]["mode"]
    date_keyword = config_dict["date_auto_select"]["date_keyword"].strip()
    date_auto_fallback = config_dict.get('date_auto_fallback', False)  # T017: Safe access for new field (default: strict mode)
    pass_date_is_sold_out_enable = config_dict["tixcraft"]["pass_date_is_sold_out"]
    auto_reload_coming_soon_page_enable = config_dict["tixcraft"]["auto_reload_coming_soon_page"]

    sold_out_text_list = ["選購一空","已售完","No tickets available","Sold out","空席なし","完売した"]
    find_ticket_text_list = ['立即訂購','Find tickets', 'Start ordering','お申込みへ進む']

    game_name = ""
    if "/activity/game/" in url:
        url_split = url.split("/")
        if len(url_split) >= 6:
            game_name = url_split[5]

    check_game_detail = "/activity/game/%s" % (game_name,) in url

    area_list = None
    if check_game_detail:
        # 智慧等待：等待日期列表出現
        # 注意：從 /activity/detail/ redirect 過來時，redirect 函數已經等待過了
        # 這裡再等待一次是為了處理直接進入 /activity/game/ 頁面的情況
        try:
            await tab.wait_for('#gameList > table > tbody > tr', timeout=3)
        except Exception:
            pass  # timeout 沒關係，繼續嘗試讀取

        try:
            if is_leak_watch_mode(config_dict):
                area_list = await runtime_health.query_selector_all_with_timeout(
                    tab,
                    "#gameList > table > tbody > tr",
                    config_dict,
                    timeout_seconds=_TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
                    reason="DATE_QUERY_ROWS",
                    log_success=False,
                )
            else:
                area_list = await tab.query_selector_all(
                    "#gameList > table > tbody > tr"
                )
        except Exception:
            pass

    # Language detection for coming soon
    is_coming_soon = False
    coming_soon_conditions = {
        'en-US': [' day(s)', ' hrs.',' min',' sec',' till sale starts!','0',':','/'],
        'zh-TW': ['開賣','剩餘',' 天',' 小時',' 分鐘',' 秒','0',':','/','20'],
        'ja': ['発売開始', ' 日', ' 時間',' 分',' 秒','0',':','/','20']
    }

    if 'html_lang' not in _state:
        try:
            if is_leak_watch_mode(config_dict):
                _state['html_lang'] = (
                    await runtime_health.evaluate_with_timeout(
                        tab,
                        "document.documentElement.lang",
                        config_dict,
                        timeout_seconds=_TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
                        reason="DATE_HTML_LANG",
                        default="en-US",
                        log_success=False,
                    )
                    or "en-US"
                )
            else:
                _state['html_lang'] = (
                    await tab.evaluate("document.documentElement.lang")
                    or "en-US"
                )
        except Exception:
            _state['html_lang'] = 'en-US'
    html_lang = _state['html_lang']

    coming_soon_condictions_list = coming_soon_conditions.get(html_lang, coming_soon_conditions['en-US'])

    matched_blocks = None
    formated_area_list = None

    if area_list and len(area_list) > 0:
        formated_area_list = []
        formated_area_list_text = []
        # Batch fetch all row HTML in one CDP round-trip (~9-16x faster than sequential get_html())
        all_row_htmls = None
        try:
            row_cache_script = (
                "JSON.stringify(Array.from(document.querySelectorAll("
                "'#gameList > table > tbody > tr')).map(r => r.outerHTML))"
            )
            if is_leak_watch_mode(config_dict):
                all_row_htmls_raw = await runtime_health.evaluate_with_timeout(
                    tab,
                    row_cache_script,
                    config_dict,
                    timeout_seconds=_TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
                    reason="DATE_ROW_CACHE",
                    log_success=False,
                )
            else:
                all_row_htmls_raw = await tab.evaluate(row_cache_script)
            all_row_htmls = _parse_tixcraft_row_htmls(all_row_htmls_raw)
        except Exception:
            pass
        for i, row in enumerate(area_list):
            try:
                if all_row_htmls and i < len(all_row_htmls):
                    row_html = all_row_htmls[i]
                else:
                    if is_leak_watch_mode(config_dict):
                        row_html = await _run_bounded_tixcraft_operation(
                            row.get_html(),
                            _TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
                            "DATE_FALLBACK_ROW_HTML",
                            config_dict,
                        )
                    else:
                        row_html = await row.get_html()
                row_text = util.remove_html_tags(row_html)
            except Exception:
                break

            if row_text and not util.reset_row_text_if_match_keyword_exclude(config_dict, row_text):
                # Check coming soon
                if all(cond in row_text for cond in coming_soon_condictions_list):
                    is_coming_soon = True
                    debug.log(f"[DATE SELECT] Detected coming soon countdown")
                    if auto_reload_coming_soon_page_enable:
                        debug.log(f"[DATE SELECT] auto_reload_coming_soon_page=true, will reload and retry")
                        break
                    else:
                        # Skip this row (don't add to formated_area_list)
                        continue

                # Check if row has ticket text
                row_is_enabled = any(text in row_text for text in find_ticket_text_list)

                # Check sold out
                if row_is_enabled and pass_date_is_sold_out_enable:
                    for sold_out_item in sold_out_text_list:
                        if sold_out_item in row_text[-(len(sold_out_item)+5):]:
                            row_is_enabled = False
                            # 移除：售完訊息過度詳細
                            break

                if row_is_enabled:
                    formated_area_list.append(row)
                    formated_area_list_text.append(row_text)
                    # 移除：可用場次訊息過度詳細

        if debug.enabled:
            if formated_area_list_text:
                preview_rows = [
                    re.sub(r"\s+", " ", item).strip()[:120]
                    for item in formated_area_list_text[:8]
                ]
                debug.log(f"[DATE SELECT] Candidate date rows: {preview_rows}")
            else:
                debug.log("[DATE SELECT] No enabled date rows after filters")

        # T004-T008: NEW LOGIC - Early return pattern (Feature 003)
        # Keyword priority matching: first match wins and stops immediately
        if not date_keyword:
            matched_blocks = formated_area_list
            debug.log(f"[DATE KEYWORD] No keyword specified, using all {len(formated_area_list)} dates")
        else:
            # NEW: Early return pattern - iterate keywords in order
            matched_blocks = []
            target_row_found = False
            keyword_matched_index = -1

            try:
                keyword_array = util.parse_keyword_string_to_array(date_keyword)

                # T005: Start checking keywords log
                debug.log(f"[DATE KEYWORD] Start checking keywords in order: {keyword_array}")
                debug.log(f"[DATE KEYWORD] Total keyword groups: {len(keyword_array)}")
                debug.log(f"[DATE KEYWORD] Checking against {len(formated_area_list_text)} available dates...")

                # NEW: Iterate keywords in priority order (early return)
                for keyword_index, keyword_item_set in enumerate(keyword_array):
                    debug.log(f"[DATE KEYWORD] Checking keyword #{keyword_index + 1}: {keyword_item_set}")

                    # Check all rows for this keyword
                    for i, row_text in enumerate(formated_area_list_text):
                        is_match = False

                        if isinstance(keyword_item_set, str):
                            # OR logic: single keyword
                            is_match = _tixcraft_text_contains_keyword(row_text, keyword_item_set)
                        elif isinstance(keyword_item_set, list):
                            # AND logic: all keywords must match
                            match_results = [
                                _tixcraft_text_contains_keyword(row_text, kw)
                                for kw in keyword_item_set
                            ]
                            is_match = all(match_results)

                        if is_match:
                            # T006: Keyword matched log - IMMEDIATELY select and stop
                            matched_blocks = [formated_area_list[i]]
                            target_row_found = True
                            keyword_matched_index = keyword_index
                            debug.log(f"[DATE KEYWORD] Keyword #{keyword_index + 1} matched: '{keyword_item_set}'")
                            debug.log(f"[DATE SELECT] Selected date: {row_text[:80]} (keyword match)")
                            break

                    if target_row_found:
                        # EARLY RETURN: Stop checking further keywords
                        break

                # T007: All keywords failed log
                if not target_row_found:
                    debug.log(f"[DATE KEYWORD] All keywords failed to match")

            except Exception as e:
                debug.log(f"[DATE KEYWORD] Parsing error: {e}")
                # On error, use mode selection
                matched_blocks = []

    # T018-T020: NEW - Conditional fallback based on date_auto_fallback switch
    if matched_blocks is not None and len(matched_blocks) == 0 and date_keyword and formated_area_list is not None and len(formated_area_list) > 0:
        if date_auto_fallback:
            # T018: Fallback enabled - use auto_select_mode
            debug.log(f"[DATE FALLBACK] date_auto_fallback=true, triggering auto fallback")
            debug.log(f"[DATE FALLBACK] Selecting available date based on date_select_order='{auto_select_mode}'")
            matched_blocks = formated_area_list
        else:
            # T019: Fallback disabled - strict mode (no selection, but still reload)
            debug.log(f"[DATE FALLBACK] date_auto_fallback=false, fallback is disabled")
            debug.log(f"[DATE SELECT] No date selected, will reload page and retry")
            # Don't return - let reload logic execute below
            # matched_blocks remains None (no selection will be made)

    # T020: Handle case when formated_area_list is empty or None (all options excluded or sold out)
    if formated_area_list is None or len(formated_area_list) == 0:
        debug.log(f"[DATE FALLBACK] No available options after exclusion")
        debug.log(f"[DATE SELECT] Will reload page and retry")
        # Don't return - let reload logic execute at function end
        is_date_clicked = False
        target_area = None  # Skip selection when no options available
    elif matched_blocks is None or len(matched_blocks) == 0:
        # matched_blocks is None when fallback=false and keyword didn't match
        target_area = None
        is_date_clicked = False
    else:
        target_area = util.get_target_item_from_matched_list(matched_blocks, auto_select_mode)

    if debug.enabled:
        if target_area and matched_blocks:
            # Find which index was selected
            try:
                target_index = matched_blocks.index(target_area) if target_area in matched_blocks else -1
                debug.log(f"[DATE SELECT] Auto-select mode: {auto_select_mode}")
                debug.log(f"[DATE SELECT] Selected target: #{target_index + 1}/{len(matched_blocks)}")
            except Exception:
                debug.log(f"[DATE SELECT] Auto-select mode: {auto_select_mode}")
                debug.log(f"[DATE SELECT] Target selected from {len(matched_blocks)} matched dates")
        elif not matched_blocks or len(matched_blocks) == 0:
            debug.log(f"[DATE SELECT] No target selected (matched_blocks is empty)")

    is_date_clicked = False

    # 移除：內部選擇細節過度詳細

    navigation_observed = False
    if target_area:
        # Priority: button with data-href (tixcraft/indievox) > regular link > regular button
        # IMPORTANT: Search within target_area, not the whole page
        click_method_used = None
        navigation_confirmed = False
        data_href_attempted = False
        try:
            debug.log("[DATE SELECT] Trying button[data-href] method within target_area...")

            # Method 1: button[data-href] within target_area (tixcraft/indievox specific)
            # 使用 NoDriver Element API 取得 data-href
            button_with_href = await _run_bounded_tixcraft_operation(
                target_area.query_selector("button[data-href]"),
                _TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
                "DATE_QUERY_DATA_HREF",
                config_dict,
            )
            data_href = None
            if button_with_href:
                # 更新元素以確保屬性載入
                await _run_bounded_tixcraft_operation(
                    button_with_href.update(),
                    _TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
                    "DATE_UPDATE_DATA_HREF",
                    config_dict,
                )
                button_attrs = button_with_href.attrs or {}
                data_href = button_attrs.get('data-href')

                if debug.enabled:
                    if data_href:
                        debug.log(f"[DATE SELECT] button[data-href] found in target_area: {data_href}")
                    else:
                        debug.log("[DATE SELECT] button[data-href] found but no href value")

                if data_href:
                    data_href_attempted = True
                    debug.log("[DATE SELECT] Navigating via button[data-href]...")
                    try:
                        navigated = await _guarded_tixcraft_get(
                            tab,
                            data_href,
                            config_dict,
                            reason="DATE_DATA_HREF",
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as navigation_exc:
                        navigated = False
                        runtime_health.runtime_log(
                            "[DATE] guarded_navigation_error",
                            config_dict,
                            error_type=type(navigation_exc).__name__,
                            source_url=_tixcraft_route_key(url),
                        )
                    current_target_url = _get_cached_tab_url(tab)
                    navigation_observed = _is_confirmed_navigation(
                        url,
                        current_target_url,
                        classify_page(current_target_url),
                        allowed_pages=_TIXCRAFT_CONFIRMED_DATE_TARGET_PAGES,
                    )
                    navigation_confirmed = bool(navigated) and navigation_observed
                    if navigation_confirmed:
                        is_date_clicked = True
                        click_method_used = "button[data-href]"
                        debug.log(
                            "[DATE SELECT] Navigation confirmed via button[data-href]"
                        )
                    elif navigated or navigation_observed:
                        _set_pending_date_navigation(
                            tab,
                            url,
                            data_href,
                            config_dict,
                        )
                        # A cached route transition after guarded_get=False is a
                        # navigation race, not a confirmed success. Preserve a
                        # pending token so this freshly landed page is never
                        # reloaded in the same iteration.
                        is_date_clicked = bool(navigated)
                        click_method_used = (
                            "button[data-href] pending"
                            if navigated
                            else "button[data-href] route observed"
                        )
                        debug.log(
                            "[DATE SELECT] Navigation in flight; "
                            "waiting for route confirmation"
                        )
                    else:
                        runtime_health.runtime_log(
                            "[DATE] guarded_navigation_not_confirmed",
                            config_dict,
                            guarded_result=bool(navigated),
                            source_url=url,
                        )
            else:
                debug.log("[DATE SELECT] No button[data-href] in target_area, will try fallback methods")
        except Exception as e:
            debug.log(f"[DATE SELECT] button[data-href] method failed: {e}")

        # Method 2: regular link or button click
        if not is_date_clicked and not data_href_attempted:
            try:
                debug.log("[DATE SELECT] Trying link <a[href]> method within target_area...")

                # Try link first (ticketmaster, etc)
                link = await _run_bounded_tixcraft_operation(
                    target_area.query_selector("a[href]"),
                    _TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
                    "DATE_QUERY_LINK",
                    config_dict,
                )
                if link:
                    debug.log("[DATE SELECT] Link found in target_area, clicking...")
                    await _run_bounded_tixcraft_operation(
                        link.click(),
                        _TIXCRAFT_CLICK_DISPATCH_TIMEOUT_SECONDS,
                        "DATE_LINK_CLICK",
                        config_dict,
                    )
                    _set_pending_date_navigation(
                        tab,
                        url,
                        "",
                        config_dict,
                    )
                    is_date_clicked = True
                    click_method_used = "link <a[href]>"
                    debug.log("[DATE SELECT] Link click dispatched; waiting for navigation")
                else:
                    debug.log("[DATE SELECT] No link found, trying regular button within target_area...")

                    # Try regular button
                    button = await _run_bounded_tixcraft_operation(
                        target_area.query_selector("button"),
                        _TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
                        "DATE_QUERY_BUTTON",
                        config_dict,
                    )
                    if button:
                        debug.log("[DATE SELECT] Regular button found in target_area, clicking...")
                        await _run_bounded_tixcraft_operation(
                            button.click(),
                            _TIXCRAFT_CLICK_DISPATCH_TIMEOUT_SECONDS,
                            "DATE_BUTTON_CLICK",
                            config_dict,
                        )
                        _set_pending_date_navigation(
                            tab,
                            url,
                            "",
                            config_dict,
                        )
                        is_date_clicked = True
                        click_method_used = "regular button"
                        debug.log("[DATE SELECT] Button click dispatched; waiting for navigation")
                    else:
                        debug.log("[DATE SELECT] No clickable element found in target_area")
            except Exception as e:
                current_target_url = _get_cached_tab_url(tab)
                navigation_observed = _is_confirmed_navigation(
                    url,
                    current_target_url,
                    classify_page(current_target_url),
                    allowed_pages=_TIXCRAFT_CONFIRMED_DATE_TARGET_PAGES,
                )
                if navigation_observed:
                    _set_pending_date_navigation(
                        tab,
                        url,
                        current_target_url,
                        config_dict,
                    )
                    runtime_health.runtime_log(
                        "[DATE] click_navigation_observed",
                        config_dict,
                        error_type=type(e).__name__,
                        current_url=current_target_url,
                    )
                debug.log(f"[DATE SELECT] Click error: {type(e).__name__}")

        # Final summary
        if debug.enabled:
            if is_date_clicked and click_method_used:
                debug.log(f"[DATE SELECT] ========================================")
                debug.log(f"[DATE SELECT] Date selection completed successfully")
                debug.log(f"[DATE SELECT] Method used: {click_method_used}")
                debug.log(f"[DATE SELECT] ========================================")
            elif not is_date_clicked:
                debug.log(f"[DATE SELECT] ========================================")
                debug.log("[DATE SELECT] All click methods failed")
                debug.log(f"[DATE SELECT] ========================================")

    # Auto refresh if no date was selected (for strict mode or sold out scenarios)
    if not is_date_clicked and not navigation_observed:
        await _reload_page_when_due(tab, config_dict, "tixcraft_date_reload", "[DATE SELECT]")
    elif is_date_clicked and "pending_date_navigation" not in _state:
        _state["tixcraft_date_reload_next_at"] = 0

    return is_date_clicked


_TIXCRAFT_RECOVERY_SCAN_COMPLETED_OUTCOMES = {
    TixCraftAreaOutcome.ZONE_MISSING,
    TixCraftAreaOutcome.NO_AVAILABLE_AREA,
    TixCraftAreaOutcome.CLICK_DISPATCHED,
    TixCraftAreaOutcome.CLICK_NOT_NAVIGATED,
}


async def _finalize_tixcraft_area_iteration(
    tab,
    url,
    config_dict,
    outcome,
):
    """Apply recovery and reload policy for every area-loop outcome."""
    normalized_outcome = TixCraftAreaOutcome(outcome)
    now = time.monotonic()
    recovery_pending = bool(
        _state.get("soft_block_recovery_scan_pending", False)
    )
    if recovery_pending:
        deadline = float(
            _state.get("soft_block_recovery_scan_deadline", 0.0) or 0.0
        )
        if normalized_outcome in _TIXCRAFT_RECOVERY_SCAN_COMPLETED_OUTCOMES:
            _clear_tixcraft_recovery_scan_guard()
            runtime_health.runtime_log(
                "[EPS BLOCK] recovery_scan_completed_without_reload",
                config_dict,
                outcome=normalized_outcome.value,
                current_url=url,
            )
            return False
        if deadline > now:
            _runtime_log_rate_limited(
                "tixcraft_recovery_scan_wait_log",
                "[EPS BLOCK] recovery_scan_waiting",
                config_dict,
                now=now,
                identity=f"{normalized_outcome.value}:{_tixcraft_route_key(url)}",
                outcome=normalized_outcome.value,
                current_url=url,
            )
            return False
        _clear_tixcraft_recovery_scan_guard()
        runtime_health.runtime_log(
            "[EPS BLOCK] recovery_scan_deadline_expired",
            config_dict,
            outcome=normalized_outcome.value,
            current_url=url,
        )

    if normalized_outcome in {
        TixCraftAreaOutcome.CLICK_DISPATCHED,
        TixCraftAreaOutcome.CLICK_WAITING_NAVIGATION,
        TixCraftAreaOutcome.NAVIGATION_CONFIRMED,
    }:
        return False

    if (
        normalized_outcome == TixCraftAreaOutcome.NO_AVAILABLE_AREA
        and is_leak_watch_mode(config_dict)
    ):
        _get_leak_scheduler().mark_no_ticket_scan_complete()

    return await _reload_page_when_due(
        tab,
        config_dict,
        "tixcraft_area_reload",
        "[AREA SELECT]",
    )


def _release_tixcraft_area_dom_scan():
    scheduler = _state.get("leak_scheduler")
    if scheduler is None or not getattr(scheduler, "dom_scan_pending", False):
        return False
    scheduler.mark_dom_scan_end(now=time.monotonic())
    return True


def _has_tixcraft_area_navigation_transitioned(tab, source_url):
    current_url = _get_cached_tab_url(tab)
    return _is_confirmed_navigation(
        source_url,
        current_url,
        classify_page(current_url),
    )


async def _run_bounded_tixcraft_operation(
    awaitable,
    timeout_seconds,
    action,
    config_dict,
):
    # The bounded CDP policy belongs only to leak-watch liveness recovery.
    # On-sale mode keeps the v0.4.2 direct-await click/query semantics.
    if not is_leak_watch_mode(config_dict):
        return await awaitable
    result = await runtime_health.wait_for_operation(
        awaitable,
        timeout_seconds,
        action,
        config_dict,
        default=_TIXCRAFT_OPERATION_TIMEOUT,
        log_success=False,
    )
    if result is _TIXCRAFT_OPERATION_TIMEOUT:
        raise TimeoutError(f"{action} timed out")
    return result


async def _dispatch_tixcraft_area_click(
    target_area,
    tab,
    source_url,
    config_dict,
):
    """Dispatch a click, bounded only for leak-watch liveness recovery."""
    timeout_seconds = _TIXCRAFT_CLICK_DISPATCH_TIMEOUT_SECONDS
    try:
        await _run_bounded_tixcraft_operation(
            target_area.click(),
            timeout_seconds,
            "AREA_NATIVE_CLICK",
            config_dict,
        )
        return True
    except asyncio.CancelledError:
        raise
    except Exception as click_exc:
        if _has_tixcraft_area_navigation_transitioned(tab, source_url):
            runtime_health.runtime_log(
                "[AREA] native_click_navigation_observed",
                config_dict,
                error_type=type(click_exc).__name__,
                current_url=_get_cached_tab_url(tab),
            )
            return True
        runtime_health.runtime_log(
            "[AREA] native_click_failed",
            config_dict,
            error_type=type(click_exc).__name__,
            current_url=source_url,
        )

    try:
        await _run_bounded_tixcraft_operation(
            target_area.evaluate("el => el.click()"),
            timeout_seconds,
            "AREA_JAVASCRIPT_CLICK",
            config_dict,
        )
        return True
    except asyncio.CancelledError:
        raise
    except Exception as click_exc:
        if _has_tixcraft_area_navigation_transitioned(tab, source_url):
            runtime_health.runtime_log(
                "[AREA] javascript_click_navigation_observed",
                config_dict,
                error_type=type(click_exc).__name__,
                current_url=_get_cached_tab_url(tab),
            )
            return True
        runtime_health.runtime_log(
            "[AREA] click_failed",
            config_dict,
            error_type=type(click_exc).__name__,
            current_url=source_url,
        )
        return False


async def nodriver_tixcraft_area_auto_select(tab, url, config_dict):
    """Run one area iteration and always return through the reload finalizer."""
    perf_trace = performance.PerformanceTrace("tixcraft_area")
    try:
        return await _nodriver_tixcraft_area_auto_select_impl(
            tab,
            url,
            config_dict,
            perf_trace=perf_trace,
        )
    except asyncio.CancelledError:
        _release_tixcraft_area_dom_scan()
        raise
    except Exception as exc:
        _release_tixcraft_area_dom_scan()
        runtime_health.runtime_log(
            "[AREA] iteration_failed",
            config_dict,
            error_type=type(exc).__name__,
            current_url=url,
        )
        try:
            await _finalize_tixcraft_area_iteration(
                tab,
                url,
                config_dict,
                TixCraftAreaOutcome.DOM_QUERY_FAILED,
            )
        except asyncio.CancelledError:
            raise
        except Exception as finalize_exc:
            runtime_health.runtime_log(
                "[AREA] finalizer_failed",
                config_dict,
                error_type=type(finalize_exc).__name__,
                current_url=url,
            )
        return False
    finally:
        performance.log_trace(
            util.create_debug_logger(config_dict),
            perf_trace,
            "[TIXCRAFT AREA PERF]",
        )


async def _nodriver_tixcraft_area_auto_select_impl(
    tab,
    url,
    config_dict,
    *,
    perf_trace=None,
):
    route_started_ns = performance.perf_counter_ns()
    # 函數開始時檢查暫停
    if await check_and_handle_pause(config_dict):
        return False

    debug = util.create_debug_logger(config_dict)
    _sync_tixcraft_notification_flow(url)
    valid_area_url = _normalize_tixcraft_area_url(url)
    if valid_area_url:
        _state["last_valid_area_url"] = valid_area_url
    performance.record_elapsed(
        perf_trace,
        performance.ROUTE_OBSERVATION_STAGE,
        route_started_ns,
    )
    if _state.get("manual_intervention_required"):
        debug.log("[AREA SELECT] Manual intervention cleared after returning to area page")
        _reset_tixcraft_submit_state()
        _reset_tixcraft_area_retry_state()
    _record_action("entered_area_page", url)

    # T010: Check main switch (defensive programming)
    if not config_dict["area_auto_select"]["enable"]:
        debug.log("[AREA SELECT] Main switch is disabled, skipping area selection")
        return False

    leak_dom_guard = should_use_leak_watch(config_dict, url)
    leak_scheduler = _get_leak_scheduler() if leak_dom_guard else None
    now_monotonic = time.monotonic()
    if leak_dom_guard:
        expired_events = leak_scheduler.maintenance(
            config_dict,
            url,
            now=now_monotonic,
        )
        for expired_event in expired_events:
            runtime_health.runtime_log(
                "[LEAK] pending_watchdog_expired",
                config_dict,
                reason=expired_event,
                current_url=url,
            )

    pending_area = _state.get("pending_area_navigation")
    if isinstance(pending_area, TixCraftPendingNavigation):
        same_tab = not pending_area.tab_identity or pending_area.tab_identity == id(tab)
        same_route = _tixcraft_route_key(url) == _tixcraft_route_key(
            pending_area.source_url
        )
        if not same_tab or not same_route:
            _clear_pending_area_navigation("area_context_changed", config_dict)
        elif not _pending_navigation_expired(pending_area, now_monotonic):
            _runtime_log_rate_limited(
                "tixcraft_area_navigation_wait_log",
                "[AREA] click_waiting_navigation",
                config_dict,
                now=now_monotonic,
                identity=f"{pending_area.token}:{pending_area.source_url}",
                current_url=url,
            )
            return False
        else:
            _clear_pending_area_navigation("click_not_navigated", config_dict)
            _state["area_navigation_retry_due"] = False
            await _finalize_tixcraft_area_iteration(
                tab,
                url,
                config_dict,
                TixCraftAreaOutcome.CLICK_NOT_NAVIGATED,
            )
            return False

    if _state.pop("area_navigation_retry_due", False):
        await _finalize_tixcraft_area_iteration(
            tab,
            url,
            config_dict,
            TixCraftAreaOutcome.CLICK_NOT_NAVIGATED,
        )
        return False

    if leak_dom_guard:
        if not await runtime_health.wait_for_interactive_ready(
            tab,
            config_dict,
            log_success=False,
        ):
            _runtime_log_rate_limited(
                "tixcraft_area_page_loading_log",
                "[AREA] dom_read_skipped",
                config_dict,
                identity=_tixcraft_route_key(url),
                reason="page_loading",
                current_url=url,
            )
            await _finalize_tixcraft_area_iteration(
                tab,
                url,
                config_dict,
                TixCraftAreaOutcome.PAGE_NOT_READY,
            )
            return False
        if not leak_scheduler.should_scan_current_document():
            _runtime_log_rate_limited(
                "tixcraft_area_generation_scan_log",
                "[AREA] dom_read_skipped",
                config_dict,
                identity=(
                    f"generation_already_scanned:"
                    f"{leak_scheduler.document_generation}"
                ),
                reason="generation_already_scanned",
                current_url=url,
            )
            await _finalize_tixcraft_area_iteration(
                tab,
                url,
                config_dict,
                TixCraftAreaOutcome.NO_AVAILABLE_AREA,
            )
            return False
        if not leak_scheduler.mark_dom_scan_start(now=time.monotonic()):
            _runtime_log_rate_limited(
                "tixcraft_area_dom_pending_log",
                "[AREA] dom_read_skipped",
                config_dict,
                identity=_tixcraft_route_key(url),
                reason="dom_scan_pending",
                current_url=url,
            )
            await _finalize_tixcraft_area_iteration(
                tab,
                url,
                config_dict,
                TixCraftAreaOutcome.DOM_SCAN_BUSY,
            )
            return False
    import json

    area_keyword = config_dict["area_auto_select"]["area_keyword"].strip()
    auto_select_mode = config_dict["area_auto_select"]["mode"]
    area_auto_fallback = config_dict.get('area_auto_fallback', False)  # T021: Safe access for new field

    dom_started_ns = performance.perf_counter_ns()
    try:
        if leak_dom_guard:
            el = await runtime_health.query_selector_with_timeout(
                tab,
                ".zone",
                config_dict,
                reason="AREA_QUERY_ZONE",
                log_success=False,
            )
        else:
            el = await tab.query_selector('.zone')
    except asyncio.CancelledError:
        if leak_dom_guard:
            leak_scheduler.mark_dom_scan_end(now=time.monotonic())
        raise
    except Exception as exc:
        if leak_dom_guard:
            leak_scheduler.mark_dom_scan_end(now=time.monotonic())
        runtime_health.runtime_log(
            "[AREA] zone_query_failed",
            config_dict,
            error_type=type(exc).__name__,
            current_url=url,
        )
        await _finalize_tixcraft_area_iteration(
            tab,
            url,
            config_dict,
            TixCraftAreaOutcome.DOM_QUERY_FAILED,
        )
        return False

    if not el:
        if leak_dom_guard:
            leak_scheduler.mark_dom_scan_end(now=time.monotonic())
        await _finalize_tixcraft_area_iteration(
            tab,
            url,
            config_dict,
            TixCraftAreaOutcome.ZONE_MISSING,
        )
        return False

    # Batch pre-fetch: one JS call to get all area text and font data
    area_list_cache = None
    area_text_cache = None
    try:
        if leak_dom_guard:
            area_list_cache = await runtime_health.query_selector_all_with_timeout(
                el,
                "a",
                config_dict,
                reason="AREA_QUERY_LINKS",
                log_success=False,
            )
            area_text_cache_raw = await runtime_health.evaluate_with_timeout(
                tab,
                """
            JSON.stringify(Array.from(document.querySelectorAll('.zone a')).map(a => ({
                text: a.innerText.trim(),
                fontText: a.querySelector('font')?.textContent?.trim() ?? ''
            })))
        """,
                config_dict,
                reason="AREA_DOM_READ",
                log_success=False,
            )
        else:
            area_list_cache = await el.query_selector_all('a')
            area_text_cache_raw = await tab.evaluate("""
            JSON.stringify(Array.from(document.querySelectorAll('.zone a')).map(a => ({
                text: a.innerText.trim(),
                fontText: a.querySelector('font')?.textContent?.trim() ?? ''
            })))
        """)
        area_text_cache = _parse_tixcraft_area_text_cache(area_text_cache_raw)
        if area_list_cache and area_text_cache and len(area_list_cache) != len(area_text_cache):
            area_text_cache = None
        if area_text_cache:
            debug.log(f"[AREA KEYWORD] Batch pre-fetch: {len(area_text_cache)} areas cached")
            _record_action("area_dom_read", str(len(area_text_cache)))
            _runtime_log_rate_limited(
                "tixcraft_area_dom_success_log",
                "[AREA] dom_read_done",
                config_dict,
                identity=f"{_tixcraft_route_key(url)}:{len(area_text_cache)}",
                area_count=len(area_text_cache),
                current_url=_tixcraft_route_key(url),
            )
    except Exception:
        area_list_cache = None
        area_text_cache = None
    finally:
        if leak_dom_guard:
            leak_scheduler.mark_dom_scan_end(now=time.monotonic())
        performance.record_elapsed(
            perf_trace,
            performance.AREA_DOM_SNAPSHOT_STAGE,
            dom_started_ns,
        )

    is_need_refresh = False
    outcome = TixCraftAreaOutcome.NO_AVAILABLE_AREA
    matched_blocks = None

    selection_started_ns = performance.perf_counter_ns()
    if area_keyword:
        # Parse keywords using JSON to avoid splitting keywords containing commas (e.g., "5,600")
        # Format: "\"keyword1\",\"keyword2\"" → ['keyword1', 'keyword2']
        # Supports OR logic - iterates through keywords until match found
        area_keyword_array = util.parse_keyword_string_to_array(area_keyword)
        area_keyword_array = list(dict.fromkeys(area_keyword_array))

        # T012: Start checking keywords log
        debug.log(f"[AREA KEYWORD] Start checking keywords in order: {area_keyword_array}")
        debug.log(f"[AREA KEYWORD] Total keyword groups: {len(area_keyword_array)}")

        # T011: Early return pattern - iterate keywords in priority order
        keyword_matched = False
        for keyword_index, area_keyword_item in enumerate(area_keyword_array):
            debug.log(f"[AREA KEYWORD] Checking keyword #{keyword_index + 1}: {area_keyword_item}")

            is_need_refresh, matched_blocks = await nodriver_get_tixcraft_target_area(
                el, config_dict, area_keyword_item,
                area_list_cache=area_list_cache, area_text_cache=area_text_cache)

            if not is_need_refresh:
                # T013: Keyword matched log
                keyword_matched = True
                debug.log(f"[AREA KEYWORD] Keyword #{keyword_index + 1} matched: '{area_keyword_item}'")
                break

        # T014: All keywords failed log
        if not keyword_matched:
            debug.log(f"[AREA KEYWORD] All keywords failed to match")

        # T022-T024: NEW - Conditional fallback based on area_auto_fallback switch
        is_fallback_selection = False  # Track selection type for logging
        if is_need_refresh and matched_blocks is None:
            if area_auto_fallback:
                # T022: Fallback enabled - use auto_select_mode without keyword
                debug.log(f"[AREA FALLBACK] area_auto_fallback=true, triggering auto fallback")
                debug.log(f"[AREA FALLBACK] Selecting available area based on area_select_order='{auto_select_mode}'")
                is_need_refresh, matched_blocks = await nodriver_get_tixcraft_target_area(
                    el, config_dict, "",
                    area_list_cache=area_list_cache, area_text_cache=area_text_cache)
                is_fallback_selection = True  # Mark as fallback selection
            else:
                # T023: Fallback disabled - strict mode (no selection, but still reload)
                debug.log(f"[AREA FALLBACK] area_auto_fallback=false, fallback is disabled")
                debug.log(f"[AREA SELECT] No area selected, will reload page and retry")
                # Don't return - let reload logic execute below
                # matched_blocks remains None (no selection will be made)
                # is_need_refresh remains True (will trigger reload)
    else:
        is_need_refresh, matched_blocks = await nodriver_get_tixcraft_target_area(
            el, config_dict, "",
            area_list_cache=area_list_cache, area_text_cache=area_text_cache)
        # No keyword specified, treat as mode-based selection (similar to fallback)
        if not area_keyword:
            is_fallback_selection = True

    # T024: Handle case when matched_blocks is empty or None (all options excluded or sold out)
    if matched_blocks is None or len(matched_blocks) == 0:
        debug.log(f"[AREA FALLBACK] No available options after exclusion")
        debug.log(f"[AREA SELECT] Will reload page and retry")
        # Don't return - let reload logic execute below
        is_need_refresh = True  # Ensure reload will happen
        target_area = None  # Skip selection when no options available
    elif auto_select_mode == util.CONST_MOST_REMAINING:
        target_area = _get_tixcraft_most_remaining_area(matched_blocks, area_list_cache, area_text_cache)
        debug.log("[AREA SELECT] Auto-select mode: most remaining")
    else:
        target_area = util.get_target_item_from_matched_list(matched_blocks, auto_select_mode)
    performance.record_elapsed(
        perf_trace,
        performance.CANDIDATE_SELECTION_STAGE,
        selection_started_ns,
    )
    if target_area:
        area_text = await _read_selected_area_name(
            target_area,
            area_list_cache,
            area_text_cache,
            config_dict,
        )
        _state["selected_area_candidate"] = area_text
        if area_text:
            _record_action("area_candidate_selected", area_text)
            selection_type = "fallback" if is_fallback_selection else "keyword match"
            debug.log(f"[AREA SELECT] Selected area: {area_text} ({selection_type})")
            runtime_health.runtime_log("[AREA] candidate_selected", config_dict, seat_area=area_text, current_url=url)
        else:
            runtime_health.runtime_log(
                "[AREA] candidate_metadata_missing",
                config_dict,
                current_url=url,
            )

        click_started_ns = performance.perf_counter_ns()
        click_succeeded = await _dispatch_tixcraft_area_click(
            target_area,
            tab,
            valid_area_url or url,
            config_dict,
        )
        performance.record_elapsed(
            perf_trace,
            performance.CANDIDATE_CLICK_STAGE,
            click_started_ns,
        )

        if click_succeeded:
            click_now = time.monotonic()
            if leak_dom_guard:
                leak_scheduler.mark_area_click_pending(
                    valid_area_url or url,
                    now=click_now,
                )
            pending = _set_pending_area_navigation(
                tab,
                valid_area_url or url,
                area_text,
                config_dict,
                now=click_now,
            )
            outcome = TixCraftAreaOutcome.CLICK_DISPATCHED
            _record_action("area_click_dispatched", area_text)
            runtime_health.runtime_log(
                "[AREA] click_dispatched",
                config_dict,
                seat_area=area_text,
                click_token=pending.token,
                current_url=url,
            )
        else:
            is_need_refresh = True
            outcome = TixCraftAreaOutcome.CLICK_NOT_NAVIGATED

    if is_need_refresh and outcome == TixCraftAreaOutcome.CLICK_DISPATCHED:
        outcome = TixCraftAreaOutcome.NO_AVAILABLE_AREA
    await _finalize_tixcraft_area_iteration(
        tab,
        url,
        config_dict,
        outcome,
    )
    return outcome == TixCraftAreaOutcome.CLICK_DISPATCHED

async def nodriver_get_tixcraft_target_area(el, config_dict, area_keyword_item,
                                            area_list_cache=None, area_text_cache=None):
    area_auto_select_mode = config_dict["area_auto_select"]["mode"]
    debug = util.create_debug_logger(config_dict)
    is_need_refresh = False
    matched_blocks = None

    # Display keyword information
    if debug.enabled:
        debug.log(f"[AREA KEYWORD] ========================================")
        if area_keyword_item:
            keyword_parts = area_keyword_item.split(' ')
            debug.log(f"[AREA KEYWORD] Raw input: '{area_keyword_item}'")
            debug.log(f"[AREA KEYWORD] Parsed (AND logic): {keyword_parts}")
            debug.log(f"[AREA KEYWORD] Total sub-keywords: {len(keyword_parts)}")
            debug.log(f"[AREA KEYWORD] Auto-select mode: {area_auto_select_mode}")
        else:
            debug.log(f"[AREA KEYWORD] No keyword specified, matching all areas")
            debug.log(f"[AREA KEYWORD] Auto-select mode: {area_auto_select_mode}")

    if not el:
        debug.log(f"[AREA KEYWORD] Element is None, cannot select area")
        return True, None

    if area_list_cache is not None:
        area_list = area_list_cache
    else:
        try:
            if is_leak_watch_mode(config_dict):
                area_list = await runtime_health.wait_for_operation(
                    el.query_selector_all("a"),
                    _TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
                    "AREA_FALLBACK_QUERY_LINKS",
                    config_dict,
                    default=None,
                    log_success=False,
                )
            else:
                area_list = await el.query_selector_all('a')
        except asyncio.CancelledError:
            raise
        except Exception:
            debug.log(f"[AREA KEYWORD] Failed to query area list")
            return True, None

    if not area_list or len(area_list) == 0:
        debug.log(f"[AREA KEYWORD] No areas found")
        return True, None

    debug.log(f"[AREA KEYWORD] Found {len(area_list)} area(s) to check")
    debug.log(f"[AREA KEYWORD] ========================================")

    matched_blocks = []
    area_index = 0
    for row in area_list:
        area_index += 1

        if area_text_cache is not None:
            row_text = area_text_cache[area_index - 1].get('text', '')
        else:
            try:
                if is_leak_watch_mode(config_dict):
                    row_html = await runtime_health.wait_for_operation(
                        row.get_html(),
                        _TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
                        "AREA_FALLBACK_ROW_HTML",
                        config_dict,
                        default=None,
                        log_success=False,
                    )
                    if row_html is None:
                        debug.log(
                            f"[AREA KEYWORD] [{area_index}] Timed out reading row content"
                        )
                        break
                else:
                    row_html = await row.get_html()
                row_text = util.remove_html_tags(row_html)
            except asyncio.CancelledError:
                raise
            except Exception:
                debug.log(f"[AREA KEYWORD] [{area_index}] Failed to get row content")
                break

        if not row_text or util.reset_row_text_if_match_keyword_exclude(config_dict, row_text):
            debug.log(f"[AREA KEYWORD] [{area_index}] Excluded by keyword_exclude")
            continue

        debug.log(f"[AREA KEYWORD] [{area_index}/{len(area_list)}] Checking: {row_text[:80]}...")

        row_text = util.format_keyword_string(row_text)

        # Check keyword match
        if area_keyword_item:
            keyword_parts = area_keyword_item.split(' ')

            debug.log(f"[AREA KEYWORD]   Matching AND keywords: {keyword_parts}")

            # Check each keyword individually for detailed feedback
            match_results = {}
            for kw in keyword_parts:
                formatted_kw = util.format_keyword_string(kw)
                kw_match = formatted_kw in row_text
                match_results[kw] = kw_match

                if debug.enabled:
                    status = "PASS" if kw_match else "FAIL"
                    debug.log(f"[AREA KEYWORD]     {status} '{kw}': {kw_match}")

            is_match = all(match_results.values())

            if debug.enabled:
                if is_match:
                    debug.log(f"[AREA KEYWORD]   All AND keywords matched")
                else:
                    debug.log(f"[AREA KEYWORD]   AND logic failed")

            if not is_match:
                continue
        else:
            debug.log(f"[AREA KEYWORD]   No keyword filter, accepting this area")

        # Check seat availability for multiple tickets. The old 1-9 shortcut
        # rejected every single-digit remainder even when it satisfied the
        # requested count (for example 2 tickets with 9 remaining).
        allow_less_tickets = config_dict.get("tixcraft", {}).get("allow_less_tickets", False)
        if config_dict["ticket_number"] > 1 and not allow_less_tickets:
            try:
                if area_text_cache is not None:
                    font_text = area_text_cache[area_index - 1].get('fontText', '')
                else:
                    font_text = ''
                    if is_leak_watch_mode(config_dict):
                        font_el = await _run_bounded_tixcraft_operation(
                            row.query_selector("font"),
                            _TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
                            "AREA_FALLBACK_QUERY_FONT",
                            config_dict,
                        )
                    else:
                        font_el = await row.query_selector('font')
                    if font_el:
                        if is_leak_watch_mode(config_dict):
                            font_text = (
                                await _run_bounded_tixcraft_operation(
                                    font_el.evaluate("el => el.textContent"),
                                    _TIXCRAFT_EVALUATE_TIMEOUT_SECONDS,
                                    "AREA_FALLBACK_FONT_TEXT",
                                    config_dict,
                                )
                                or ""
                            )
                        else:
                            font_text = (
                                await font_el.evaluate("el => el.textContent")
                                or ""
                            )
                if font_text:
                    remaining_count = _extract_remaining_count(font_text)
                    debug.log(f"[AREA KEYWORD]   Checking seats: {font_text}")
                    if (
                        remaining_count is not None
                        and remaining_count < int(config_dict["ticket_number"])
                    ):
                        debug.log(
                            "[AREA KEYWORD]   Insufficient seats "
                            f"(need {config_dict['ticket_number']}, only {remaining_count} available)"
                        )
                        continue
                    debug.log(f"[AREA KEYWORD]   Sufficient seats available")
            except Exception:
                pass

        matched_blocks.append(row)

        debug.log(f"[AREA KEYWORD]   → Area added to matched list (total: {len(matched_blocks)})")

        if area_auto_select_mode == util.CONST_FROM_TOP_TO_BOTTOM:
            debug.log(f"[AREA KEYWORD]   Mode is '{area_auto_select_mode}', stopping at first match")
            break

    if not matched_blocks:
        is_need_refresh = True
        matched_blocks = None

    return is_need_refresh, matched_blocks

async def nodriver_ticket_number_select_fill(tab, select_obj, ticket_number, select_id=None, allow_less_tickets=False):
    """簡化版本：參考 Chrome 邏輯設定票券數量，並檢查 option 是否可用

    Args:
        tab: NoDriver tab object
        select_obj: The select element (for compatibility)
        ticket_number: Target ticket count to select
        select_id: The specific select element ID to use (fixes Issue #200/#201)
        allow_less_tickets: Allow selecting the largest available count below ticket_number
    """
    is_ticket_number_assigned = False

    if select_obj is None and select_id is None:
        return is_ticket_number_assigned

    # Build JavaScript selector - prefer specific ID over querySelector
    if select_id:
        js_selector = f"document.getElementById('{select_id}')"
    else:
        js_selector = "document.querySelector('.mobile-select') || document.querySelector('select[id*=\"TicketForm_ticketPrice_\"]')"

    try:
        # 嘗試透過 JavaScript 設定選擇器的值，並檢查 option 是否 disabled
        result = await tab.evaluate(f'''
            (function() {{
                const select = {js_selector};
                if (!select) return {{success: false, error: "Select not found"}};

                // 售完關鍵字列表
                const soldOutKeywords = ["選購一空", "已售完", "Sold out", "No tickets available", "空席なし", "完売した"];

                // 先嘗試設定目標數量（檢查是否 disabled 或售完）
                const targetOption = Array.from(select.options).find(opt =>
                    opt.value === "{ticket_number}" &&
                    !opt.disabled &&
                    !soldOutKeywords.includes(opt.value)
                );

                if (targetOption) {{
                    select.value = "{ticket_number}";
                    select.selectedIndex = targetOption.index;
                    select.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return {{success: true, selected: "{ticket_number}"}};
                }}

                if (!{str(bool(allow_less_tickets)).lower()}) {{
                    return {{success: false, error: "Target ticket count unavailable"}};
                }}

                // Fallback: select max available option below target ticket count.
                const validOptions = Array.from(select.options).filter(opt =>
                    !opt.disabled &&
                    !soldOutKeywords.includes(opt.value) &&
                    parseInt(opt.value) > 0 &&
                    parseInt(opt.value) < parseInt("{ticket_number}") &&
                    !isNaN(parseInt(opt.value))
                );

                if (validOptions.length > 0) {{
                    const maxOption = validOptions.reduce((max, opt) =>
                        parseInt(opt.value) > parseInt(max.value) ? opt : max
                    );
                    select.value = maxOption.value;
                    select.selectedIndex = maxOption.index;
                    select.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return {{success: true, selected: maxOption.value, fallback: true}};
                }}

                return {{success: false, error: "No valid options (all disabled or sold out)"}};
            }})();
        ''')

        # 解析結果
        result = util.parse_nodriver_result(result)
        if isinstance(result, dict):
            is_ticket_number_assigned = result.get('success', False)

    except Exception as exc:
        pass

    return is_ticket_number_assigned

async def nodriver_tixcraft_assign_ticket_number(tab, config_dict):
    """
    Enhanced ticket type selection with keyword matching support
    支援票種關鍵字選擇（indievox 類型 B 頁面：直接跳到 /ticket/ticket/）
    """
    # 函數開始時檢查暫停
    if await check_and_handle_pause(config_dict):
        return False

    debug = util.create_debug_logger(config_dict)
    is_ticket_number_assigned = False

    # 等待票券選擇器出現（智慧等待，取代固定 0.5 秒延遲）
    try:
        await tab.wait_for('.mobile-select, select[id*="TicketForm_ticketPrice_"]', timeout=2)
    except Exception:
        pass  # Continue even if timeout, will try to find selectors below

    # 查找票券選擇器
    form_select_list = []
    try:
        form_select_list = await tab.query_selector_all('.mobile-select')
    except Exception as exc:
        debug.log("Failed to find .mobile-select")

    # 如果沒找到 .mobile-select，嘗試其他選擇器
    if len(form_select_list) == 0:
        try:
            form_select_list = await tab.query_selector_all('select[id*="TicketForm_ticketPrice_"]')
        except Exception as exc:
            debug.log("Failed to find ticket selector")

    form_select_count = len(form_select_list)

    if form_select_count > 0:
        debug.log(f"[TICKET SELECT] Found {form_select_count} select element(s)")

    # Get area keyword configuration
    import json
    area_keyword = config_dict["area_auto_select"]["area_keyword"].strip()
    area_auto_fallback = config_dict.get('area_auto_fallback', False)
    auto_select_mode = config_dict["area_auto_select"]["mode"]

    # Parse keywords using JSON
    area_keyword_array = util.parse_keyword_string_to_array(area_keyword)
    if area_keyword_array:
        debug.log(f"[TICKET SELECT] Area keywords: {area_keyword_array}")

    # 過濾並收集票種資訊（包含票種名稱）
    valid_ticket_types = []
    sold_out_keywords = ["選購一空", "已售完", "Sold out", "No tickets available", "空席なし", "完売した"]

    # 使用 NoDriver Element API 檢查每個 select 元素
    for idx, select_element in enumerate(form_select_list):
        try:
            # 更新元素以確保屬性載入
            await select_element.update()

            # 檢查 select 是否 disabled
            select_attrs = select_element.attrs or {}
            select_id = select_attrs.get('id', f'select_{idx}')
            is_select_disabled = 'disabled' in select_attrs

            if is_select_disabled:
                debug.log(f"[TICKET SELECT] Skipping disabled select: {select_id}")
                continue

            # 檢查 option 元素
            option_elements = await select_element.query_selector_all('option')
            has_valid_option = False
            option_values = []

            for option_element in option_elements:
                try:
                    await option_element.update()
                    option_attrs = option_element.attrs or {}
                    option_value = option_attrs.get('value', '')
                    option_text = option_element.text or ''
                    option_disabled = 'disabled' in option_attrs

                    option_values.append(option_value)

                    # 檢查是否為有效選項
                    if (option_value != "0" and
                        not option_disabled and
                        option_value not in sold_out_keywords and
                        option_text not in sold_out_keywords):
                        has_valid_option = True

                except Exception as opt_exc:
                    debug.log(f"[TICKET SELECT] Error checking option: {opt_exc}")
                    continue

            if not has_valid_option:
                debug.log(f"[TICKET SELECT] Skipping select (all options sold out or disabled): {select_id}")
                continue

            # 嘗試獲取票種名稱（從父元素 <tr> 中的 <h4> 或 <td> 提取）
            ticket_type_name = ""
            try:
                # 查找父元素 <tr>
                parent_row = select_element
                for _ in range(5):  # 最多向上查找 5 層
                    parent_row = parent_row.parent
                    if parent_row and parent_row.tag.lower() == 'tr':
                        break

                if parent_row and parent_row.tag.lower() == 'tr':
                    # 嘗試找 <h4> 標籤
                    h4_element = await parent_row.query_selector('h4')
                    if h4_element:
                        ticket_type_name = h4_element.text or ""
                    else:
                        # 嘗試找 <td class="fcBlue">
                        td_element = await parent_row.query_selector('td.fcBlue')
                        if td_element:
                            ticket_type_name = td_element.text or ""

                    ticket_type_name = ticket_type_name.strip()

            except Exception as name_exc:
                debug.log(f"[TICKET SELECT] Failed to extract ticket type name: {name_exc}")

            # 加入 valid_ticket_types
            valid_ticket_types.append({
                'select': select_element,
                'id': select_id,
                'name': ticket_type_name,
                'index': idx
            })

            debug.log(f"[TICKET SELECT] Valid ticket type: {select_id} - '{ticket_type_name}'")

        except Exception as exc:
            debug.log(f"[TICKET SELECT] Error checking select element: {exc}")

    debug.log(f"[TICKET SELECT] Valid ticket types: {len(valid_ticket_types)}/{form_select_count}")

    if len(valid_ticket_types) == 0:
        debug.log("[TICKET SELECT] Warning: All ticket types are sold out or disabled")
        return False, None, None

    # Keyword matching logic (similar to area selection)
    matched_ticket = None
    is_keyword_matched = False

    if area_keyword_array:
        debug.log(f"[TICKET SELECT] Starting keyword matching with {len(area_keyword_array)} keyword(s)")

        for keyword_index, keyword_item in enumerate(area_keyword_array):
            debug.log(f"[TICKET SELECT] Checking keyword #{keyword_index + 1}: '{keyword_item}'")

            # Check each valid ticket type
            for ticket_info in valid_ticket_types:
                ticket_name = ticket_info['name']

                # Apply exclude keyword filter
                if util.reset_row_text_if_match_keyword_exclude(config_dict, ticket_name):
                    debug.log(f"[TICKET SELECT]   Excluded by keyword_exclude: {ticket_name}")
                    continue

                # Keyword matching (support space-separated AND logic)
                keyword_parts = keyword_item.split(' ')
                row_text = util.format_keyword_string(ticket_name)
                is_match = True

                for kw in keyword_parts:
                    formatted_kw = util.format_keyword_string(kw)
                    if formatted_kw not in row_text:
                        is_match = False
                        break

                if is_match:
                    matched_ticket = ticket_info
                    is_keyword_matched = True
                    debug.log(f"[TICKET SELECT]   [OK] Keyword matched: '{ticket_name}'")
                    break

            if matched_ticket:
                break  # Early return: first match wins

        if not matched_ticket:
            debug.log(f"[TICKET SELECT] All keywords failed to match")

    # Single option auto-select: when only one valid ticket type exists, select it directly
    # (unless excluded by keyword_exclude)
    if not matched_ticket and len(valid_ticket_types) == 1:
        single_ticket = valid_ticket_types[0]
        ticket_name = single_ticket['name']

        # Check if excluded by keyword_exclude
        if not util.reset_row_text_if_match_keyword_exclude(config_dict, ticket_name):
            matched_ticket = single_ticket
            debug.log(f"[TICKET SELECT] Single option auto-select: '{ticket_name}'")
        else:
            debug.log(f"[TICKET SELECT] Single option excluded by keyword_exclude: '{ticket_name}'")

    # Fallback logic (similar to area selection)
    if not matched_ticket:
        if area_keyword_array and not area_auto_fallback:
            # Strict mode: no keyword match and fallback disabled
            debug.log(f"[TICKET SELECT] area_auto_fallback=false, fallback is disabled")
            debug.log(f"[TICKET SELECT] No ticket type selected")
            return False, None, None
        else:
            # Fallback enabled or no keyword specified
            if area_keyword_array:
                debug.log(f"[TICKET SELECT] area_auto_fallback=true, using fallback selection")

            # Select based on auto_select_mode
            matched_ticket = util.get_target_item_from_matched_list(
                [t['select'] for t in valid_ticket_types],
                auto_select_mode
            )
            # Find the ticket_info for the matched select
            for ticket_info in valid_ticket_types:
                if ticket_info['select'] == matched_ticket:
                    matched_ticket = ticket_info
                    break

            if matched_ticket:
                selection_type = "fallback" if area_keyword_array else "mode-based"
                debug.log(f"[TICKET SELECT] Selected ticket type ({selection_type}): '{matched_ticket['name']}'")

    # Use the matched ticket select
    select_obj = matched_ticket['select'] if matched_ticket else None
    form_select_count = len(valid_ticket_types)

    # Get select ID for JavaScript operations
    select_id = matched_ticket['id'] if matched_ticket else None

    # 檢查是否已經選擇了符合設定的票券數量
    if select_id:
        try:
            # 使用 JavaScript 取得當前選中的值（使用正確的 select ID）
            current_value = await tab.evaluate(f'''
                (function() {{
                    const select = document.getElementById('{select_id}');
                    return select ? select.value : "0";
                }})();
            ''')

            # 解析結果
            current_value = util.parse_nodriver_result(current_value)

            if current_value and current_value != "0" and str(current_value).isnumeric():
                target_ticket_number = int(config_dict.get("ticket_number", 1))
                current_ticket_number = int(current_value)
                allow_less_tickets = config_dict.get("tixcraft", {}).get("allow_less_tickets", False)
                is_expected_count = current_ticket_number == target_ticket_number
                is_allowed_less_count = allow_less_tickets and 0 < current_ticket_number < target_ticket_number
                if is_expected_count or is_allowed_less_count:
                    is_ticket_number_assigned = True
                    debug.log(f"Ticket number already set to: {current_value}")
        except Exception as exc:
            debug.log(f"Failed to check current selected value: {exc}")

    # 回傳結果：select_obj 和 select_id 用於後續操作
    return is_ticket_number_assigned, select_obj, select_id

async def nodriver_tixcraft_ticket_main_agree(tab, config_dict):
    debug = util.create_debug_logger(config_dict)

    debug.log("Starting to check agreement checkbox")

    for i in range(3):
        is_finish_checkbox_click = await nodriver_check_checkbox_enhanced(tab, '#TicketForm_agree', config_dict)
        if is_finish_checkbox_click:
            debug.log("Agreement checkbox checked successfully")
            _record_action("agreement_checked")
            break
        else:
            debug.log(f"Failed to check agreement, retry {i+1}/3")

    if not is_finish_checkbox_click:
        debug.log("Warning: Failed to check agreement checkbox")


async def _dispatch_tixcraft_enter_submit(tab, current_url, submit_guard) -> bool:
    """Arm the attempt before Enter; a dispatch error is an ambiguous outcome."""

    # No await occurs between these writes and keyDown, so competing schedulers
    # cannot observe an unprotected submitted document.
    submit_guard.mark_submitted(
        current_url,
        pending_seconds=_TIXCRAFT_SUBMIT_CONTEXT_MAX_SECONDS,
    )
    _mark_tixcraft_submit_started(current_url, tab=tab)
    try:
        await tab.send(
            cdp.input_.dispatch_key_event(
                "keyDown",
                code="Enter",
                key="Enter",
                text="\r",
                windows_virtual_key_code=13,
            )
        )
    except asyncio.CancelledError:
        # Cancellation can race with a browser-side key event. Preserve the
        # protected state and propagate cancellation to the runtime owner.
        raise
    except Exception as exc:
        runtime_health.runtime_log(
            "[TIXCRAFT CAPTCHA] Enter keyDown outcome_inconclusive",
            None,
            error_type=type(exc).__name__,
            source_url=_tixcraft_route_key(current_url),
            attempt_id=getattr(_get_tixcraft_purchase_attempt(), "attempt_id", None),
            generation=int(_state.get("notification_flow_generation", 0) or 0),
            token=int(_state.get("submit_generation", 0) or 0),
        )
    _record_action("submit_clicked")
    try:
        await tab.send(
            cdp.input_.dispatch_key_event(
                "keyUp",
                code="Enter",
                key="Enter",
                text="\r",
                windows_virtual_key_code=13,
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # keyDown can submit and replace the document before keyUp is sent.
        runtime_health.runtime_log(
            "[TIXCRAFT CAPTCHA] Enter keyUp best-effort failed",
            None,
            error=str(exc),
        )
    return True


def _arm_tixcraft_manual_submit_pending(tab, current_url, config_dict=None) -> None:
    """Protect a valid ticket form while the user completes manual submit.

    Manual captcha mode has no Python-owned key event to observe. Arming when
    the visible captcha field is handed to the user closes the otherwise
    unavoidable race between a human Enter key and the next 50 ms main-loop
    dispatch. A confirmed captcha error clears this state for another try.
    """

    if _is_tixcraft_submit_in_flight(tab):
        return
    _ensure_runtime_helpers()
    _state["submit_guard"].mark_submitted(
        current_url,
        pending_seconds=_TIXCRAFT_SUBMIT_CONTEXT_MAX_SECONDS,
    )
    _mark_tixcraft_submit_started(current_url, tab=tab)
    _record_action("manual_submit_armed")
    context = _state.get("submit_in_flight")
    runtime_health.runtime_log(
        "[TIXCRAFT CAPTCHA] manual_submit_armed",
        config_dict,
        source_url=_tixcraft_route_key(current_url),
        target_url="",
        page_class=PageClass.TICKET.value,
        attempt_id=getattr(_get_tixcraft_purchase_attempt(), "attempt_id", None),
        generation=int(_state.get("notification_flow_generation", 0) or 0),
        token=getattr(context, "token", None),
    )


async def nodriver_tixcraft_ticket_main(tab, config_dict, ocr, Captcha_Browser, domain_name):
    # 函數開始時檢查暫停
    if await check_and_handle_pause(config_dict):
        return False
    debug = util.create_debug_logger(config_dict)
    ticket_trace = performance.PerformanceTrace("tixcraft_ticket")
    ticket_ready_started_ns = performance.perf_counter_ns()

    # 檢查是否已經設定過票券數量（方案 B：狀態標記）
    current_url, _ = await nodriver_current_url(tab)
    _record_action("entered_ticket_page", current_url)
    if _is_tixcraft_submit_in_flight(tab):
        runtime_health.runtime_log(
            "[TIXCRAFT] ticket_handler_blocked_submit_in_flight",
            config_dict,
            source_url=_tixcraft_route_key(current_url),
            target_url="",
            page_class=PageClass.TICKET.value,
            attempt_id=getattr(_get_tixcraft_purchase_attempt(), "attempt_id", None),
            generation=int(_state.get("notification_flow_generation", 0) or 0),
            token=int(_state.get("submit_generation", 0) or 0),
        )
        return
    attempt = _get_tixcraft_purchase_attempt()
    if attempt is None:
        attempt = _begin_tixcraft_purchase_attempt("ticket_page", current_url)
    ticket_number = str(config_dict["ticket_number"])
    allow_less_tickets = config_dict.get("tixcraft", {}).get("allow_less_tickets", False)
    ticket_state_key = (
        f"ticket_assigned_{attempt.attempt_id}_{current_url}_"
        f"{ticket_number}_{int(allow_less_tickets)}"
    )
    for stale_key in list(_state.keys()):
        if (
            str(stale_key).startswith("ticket_assigned_")
            and stale_key != ticket_state_key
        ):
            _state.pop(stale_key, None)

    # Skip this iteration while a captcha submit is in flight (awaiting navigation).
    if _state.get("captcha_submit_until", 0) > time.monotonic():
        debug.log("[TIXCRAFT OCR] Submit in progress, waiting for navigation")
        return

    if ticket_state_key in _state and _state[ticket_state_key]:
        if not await _is_tixcraft_ticket_count_ready(tab, config_dict):
            debug.log("[TICKET SELECT] Cached ticket state is stale or below requested count, resetting")
            _state[ticket_state_key] = False
            await _recover_to_last_valid_area(tab, config_dict, "ticket_count_cache_stale")
            return

        performance.record_elapsed(
            ticket_trace,
            performance.TICKET_READY_STAGE,
            ticket_ready_started_ns,
        )
        performance.log_trace(debug, ticket_trace, "[TIXCRAFT TICKET PERF]")

        debug.log(f"Ticket number already set ({ticket_number}), skipping")

        # Ensure agreement checkbox is checked (even if ticket number already set)
        await nodriver_tixcraft_ticket_main_agree(tab, config_dict)

        # Reset OCR state if captcha alert detected (wrong answer submitted)
        if _state.get("captcha_alert_detected", False):
            _state["ocr_completed_url"] = ""
            _state["ocr_completed_attempt_id"] = None
            _state["captcha_alert_detected"] = False

        # Skip OCR if already completed on this URL (non-force_submit mode only)
        is_force_submit = config_dict["ocr_captcha"]["force_submit"]
        ocr_completed_for_attempt = bool(
            _state.get("ocr_completed_url", "") == current_url
            and _state.get("ocr_completed_attempt_id") == attempt.attempt_id
        )
        if is_force_submit or not ocr_completed_for_attempt:
            await nodriver_tixcraft_ticket_main_ocr(tab, config_dict, ocr, Captcha_Browser, domain_name)
        return

    # Always check agreement checkbox in NoDriver mode
    await nodriver_tixcraft_ticket_main_agree(tab, config_dict)

    is_ticket_number_assigned = False

    # PS: some events on tixcraft have multi <select>.
    # Fix Issue #200/#201: Now returns select_id for correct element targeting
    is_ticket_number_assigned, select_obj, select_id = await nodriver_tixcraft_assign_ticket_number(tab, config_dict)

    if not is_ticket_number_assigned:
        debug.log(f"Setting ticket number: {ticket_number}")
        is_ticket_number_assigned = await nodriver_ticket_number_select_fill(
            tab,
            select_obj,
            ticket_number,
            select_id,
            allow_less_tickets=allow_less_tickets,
        )

    # Record state after successful setting
    if is_ticket_number_assigned:
        if not await _is_tixcraft_ticket_count_ready(tab, config_dict):
            debug.log("[TICKET SELECT] Ticket count validation failed, recovering to area instead of reloading")
            _state[ticket_state_key] = False
            await _recover_to_last_valid_area(tab, config_dict, "ticket_count_validation_failed")
            return

        performance.record_elapsed(
            ticket_trace,
            performance.TICKET_READY_STAGE,
            ticket_ready_started_ns,
        )
        performance.log_trace(debug, ticket_trace, "[TIXCRAFT TICKET PERF]")

        _state[ticket_state_key] = True
        _state["tixcraft_ticket_reload_next_at"] = 0
        _record_action("ticket_count_selected", await _read_tixcraft_ticket_count(tab, config_dict))
        debug.log("Ticket number set successfully, starting OCR captcha processing")
        await nodriver_tixcraft_ticket_main_ocr(tab, config_dict, ocr, Captcha_Browser, domain_name)
    else:
        # T026: Fix Issue #174 - reload page when ticket number cannot be set
        # This prevents infinite loop when desired ticket count is unavailable
        debug.log("[TICKET SELECT] Ticket count unavailable, recovering to area...")
        await _recover_to_last_valid_area(tab, config_dict, "ticket_count_unavailable")

async def nodriver_tixcraft_keyin_captcha_code(tab, answer="", auto_submit=False, config_dict=None, perf_trace=None):
    """輸入驗證碼到表單"""
    debug = util.create_debug_logger(config_dict) if config_dict else util.create_debug_logger(enabled=False)
    is_verifyCode_editing = False
    is_form_submitted = False

    # 找到驗證碼輸入框
    form_verifyCode = await tab.query_selector('#TicketForm_verifyCode')

    if form_verifyCode:
        is_visible = False
        try:
            # 檢查元素是否可見和可用
            is_visible = await tab.evaluate('''
                (function() {
                    const element = document.querySelector('#TicketForm_verifyCode');
                    return element && !element.disabled && element.offsetParent !== null;
                })();
            ''')
        except Exception as exc:
            pass

        if is_visible:
            if not auto_submit:
                current_url = getattr(getattr(tab, "target", None), "url", "") or ""
                _arm_tixcraft_manual_submit_pending(tab, current_url, config_dict)

            # 取得當前輸入值
            inputed_value = ""
            try:
                inputed_value = await form_verifyCode.apply('function (element) { return element.value; }') or ""
            except Exception as exc:
                pass

            is_text_clicked = False

            if not inputed_value and not answer:
                # 聚焦到輸入框等待手動輸入
                try:
                    await form_verifyCode.click()
                    is_text_clicked = True
                    is_verifyCode_editing = True
                except Exception as exc:
                    debug.log("[TIXCRAFT CAPTCHA] Failed to click captcha input, trying JavaScript")
                    try:
                        await tab.evaluate('''
                            document.getElementById("TicketForm_verifyCode").focus();
                        ''')
                        is_verifyCode_editing = True
                    except Exception as exc:
                        pass

            if answer:
                debug.log("[TIXCRAFT CAPTCHA] Starting to fill in captcha...")
                _record_action("captcha_input_attempted")
                try:
                    fill_started_ns = performance.perf_counter_ns()
                    if not is_text_clicked:
                        await form_verifyCode.click()

                    # 清空並輸入答案
                    await form_verifyCode.apply('function (element) { element.value = ""; }')
                    await form_verifyCode.send_keys(answer)
                    performance.record_elapsed(perf_trace, performance.FILL_STAGE, fill_started_ns)

                    if auto_submit:
                        submit_started_ns = performance.perf_counter_ns()
                        # 提交前確認票券數量是否已設定
                        ticket_number = str(config_dict.get("ticket_number", 2)) if config_dict else "2"
                        allow_less_tickets = config_dict.get("tixcraft", {}).get("allow_less_tickets", False) if config_dict else False
                        # Issue #200/#201: some tixcraft events render multiple ticket
                        # quantity selects; inspect every visible select, not just the first.
                        ticket_number_ok = await tab.evaluate(f'''
                            (function() {{
                                if (window.location.href.includes('ticketmaster')) return true;
                                const target = parseInt("{ticket_number}");
                                const allowLess = {str(bool(allow_less_tickets)).lower()};
                                const selects = Array.from(document.querySelectorAll(
                                    '.mobile-select, select[id*="TicketForm_ticketPrice_"]'
                                )).filter(s => s && !s.disabled);
                                return selects.some(s => {{
                                    if (s.value === "0" || s.value === "") return false;
                                    const current = parseInt(s.value);
                                    if (isNaN(current) || isNaN(target)) return false;
                                    return allowLess ? (current > 0 && current <= target) : (current === target);
                                }});
                            }})();
                        ''')
                        ticket_number_ok = util.parse_nodriver_result(ticket_number_ok)

                        if not ticket_number_ok and config_dict:
                            debug.log("[TIXCRAFT CAPTCHA] Warning: Ticket number not set, resetting...")
                            # Reset ticket number across all selects. Honor allow_less_tickets:
                            # when disabled only set the exact target option; otherwise fall back
                            # to the largest option within (0, target] (leave untouched if none,
                            # so Issue #174 reload retry can take over).
                            ticket_number_js = json.dumps(ticket_number)
                            await tab.evaluate(f'''
                                (function() {{
                                    const target = parseInt("{ticket_number}");
                                    const allowLess = {str(bool(allow_less_tickets)).lower()};
                                    const selects = Array.from(document.querySelectorAll(
                                        '.mobile-select, select[id*="TicketForm_ticketPrice_"]'
                                    )).filter(s => s && !s.disabled);
                                    const pick = (s) => {{
                                        let opt = Array.from(s.options).find(o =>
                                            o.value === {ticket_number_js} && !o.disabled);
                                        if (!opt && allowLess) {{
                                            const cands = Array.from(s.options).filter(o =>
                                                !o.disabled && parseInt(o.value) > 0 && parseInt(o.value) <= target);
                                            opt = cands.sort((a, b) => parseInt(b.value) - parseInt(a.value))[0];
                                        }}
                                        return opt;
                                    }};
                                    for (const s of selects) {{
                                        const opt = pick(s);
                                        if (opt) {{
                                            s.value = opt.value;
                                            s.dispatchEvent(new Event('change', {{bubbles: true}}));
                                            break;
                                        }}
                                    }}
                                }})();
                            ''')

                        # 勾選同意條款
                        await nodriver_check_checkbox_enhanced(tab, '#TicketForm_agree')

                        # 最終確認所有欄位都已填寫
                        form_ready = await tab.evaluate(f'''
                            (function() {{
                                const verify = document.querySelector('#TicketForm_verifyCode');
                                const agree = document.querySelector('#TicketForm_agree');

                                // Ticketmaster check-captcha page has no ticket selector
                                // Ticket number is already set on previous page
                                const isTicketmaster = window.location.href.includes('ticketmaster');
                                const target = parseInt("{ticket_number}");
                                const allowLess = {str(bool(allow_less_tickets)).lower()};
                                const selects = Array.from(document.querySelectorAll(
                                    '.mobile-select, select[id*="TicketForm_ticketPrice_"]'
                                )).filter(s => s && !s.disabled);
                                const matched = selects.find(s => {{
                                    if (s.value === "0" || s.value === "") return false;
                                    const current = parseInt(s.value);
                                    if (isNaN(current) || isNaN(target)) return false;
                                    return allowLess ? (current > 0 && current <= target) : (current === target);
                                }});
                                const ticketOk = isTicketmaster ? true : !!matched;

                                return {{
                                    ticket: ticketOk,
                                    ticket_select: matched ? (matched.id || matched.name || matched.className || "") : "",
                                    verify: verify && verify.value.length === 4,
                                    agree: agree && agree.checked,
                                    ready: ticketOk &&
                                           (verify && verify.value.length === 4) &&
                                           (agree && agree.checked)
                                }};
                            }})();
                        ''')
                        form_ready = util.parse_nodriver_result(form_ready)

                        if form_ready.get('ready', False):
                            _ensure_runtime_helpers()
                            current_url = getattr(getattr(tab, "target", None), "url", "") or ""
                            submit_guard = _state["submit_guard"]
                            if not submit_guard.can_submit(current_url):
                                debug.log("[TIXCRAFT CAPTCHA] SubmitGuard pending; skip duplicate submit")
                                return is_verifyCode_editing, False
                            # Enter keyDown can submit immediately. Persist the
                            # guard and attempt before the best-effort keyUp.
                            await _dispatch_tixcraft_enter_submit(
                                tab,
                                current_url,
                                submit_guard,
                            )
                            is_verifyCode_editing = False
                            is_form_submitted = True
                            # Short submit-in-progress guard: stop the main loop from
                            # re-clicking the captcha input while navigation is pending.
                            _state["captcha_submit_until"] = time.monotonic() + 1.5
                        else:
                            debug.log(f"[TIXCRAFT CAPTCHA] Form not ready - Ticket:{form_ready.get('ticket')} Select:{form_ready.get('ticket_select')} Captcha:{form_ready.get('verify')} Agreement:{form_ready.get('agree')}")
                        performance.record_elapsed(perf_trace, performance.SUBMIT_STAGE, submit_started_ns)
                    else:
                        # 選取輸入框內容並顯示提示
                        await tab.evaluate('''
                            document.getElementById("TicketForm_verifyCode").select();
                        ''')
                        # 顯示提示訊息
                        await nodriver_tixcraft_toast(tab, f"※ 按 Enter 如果答案是: {answer}")

                except Exception as exc:
                    debug.log(f"[TIXCRAFT CAPTCHA] Failed to input captcha: {exc}")

    return is_verifyCode_editing, is_form_submitted

async def nodriver_tixcraft_toast(tab, message):
    """顯示提示訊息"""
    try:
        await tab.evaluate(f'''
            (function() {{
                const toast = document.querySelector('p.remark-word');
                if (toast) {{
                    toast.innerHTML = '{message}';
                }}
            }})();
        ''')
    except Exception as exc:
        pass

async def nodriver_get_yii_captcha_hash(tab):
    """Read Yii2 captcha hash1 from body data (stored after refresh).
    Returns hash1 (int) or 0 if not yet available (first page load)."""
    try:
        result = await tab.evaluate('''
            (function() {
                if (typeof jQuery === "undefined") return 0;
                var data = jQuery("body").data("yiiCaptcha/ticket/captcha");
                return (data && data[0]) ? data[0] : 0;
            })()
        ''')
        return int(result) if result else 0
    except Exception:
        return 0


async def nodriver_tixcraft_reload_captcha(tab, domain_name, config_dict=None):
    """重新載入驗證碼（Yii2 jQuery refresh）並等待圖片更新。
    Yii2 refresh 後自動將 hash 存入 body data，供 nodriver_get_yii_captcha_hash 讀取。"""
    try:
        result = await tab.evaluate('''
            (async function() {
                if (typeof jQuery === "undefined") return false;
                var $img = jQuery("#TicketForm_verifyCode-image");
                if (!$img.length || typeof $img.yiiCaptcha !== "function") return false;
                var oldSrc = $img.attr("src") || "";
                $img.yiiCaptcha("refresh");
                // Wait up to 2s for src to change
                for (var i = 0; i < 20; i++) {
                    await new Promise(function(r) { setTimeout(r, 100); });
                    if (($img.attr("src") || "") !== oldSrc) break;
                }
                return true;
            })()
        ''', await_promise=True)
        return bool(result)
    except Exception as exc:
        debug = util.create_debug_logger(config_dict)
        debug.log(f"[TIXCRAFT OCR] reload_captcha failed: {exc}")
    return False

async def nodriver_tixcraft_get_ocr_answer(
    tab, ocr, ocr_captcha_image_source, Captcha_Browser, domain_name, perf_trace=None
):
    """取得驗證碼圖片並進行 OCR 識別"""
    debug = util.create_debug_logger(enabled=False)  # OCR: intentionally silent

    ocr_answer = None
    if not ocr is None:
        capture_started_ns = performance.perf_counter_ns()
        img_base64 = None

        if ocr_captcha_image_source == CONST_OCR_CAPTCH_IMAGE_SOURCE_NON_BROWSER:
            if not Captcha_Browser is None:
                captcha_payload = await run_cpu_bound(Captcha_Browser.request_captcha)
                img_base64 = base64.b64decode(captcha_payload)

        if ocr_captcha_image_source == CONST_OCR_CAPTCH_IMAGE_SOURCE_CANVAS:
            image_id = 'TicketForm_verifyCode-image'
            if 'indievox.com' in domain_name:
                image_id = 'TicketForm_verifyCode-image'

            try:
                # Stage 7: get captcha image via canvas
                # async IIFE waits for image load to avoid reading stale image after reload
                form_verifyCode_base64 = await tab.evaluate(f'''
                    (async function() {{
                        var img = document.getElementById('{image_id}');
                        if(!img || !img.src) return null;

                        if(img.naturalWidth === 0 || !img.complete) {{
                            await new Promise(function(resolve) {{
                                var timer = setTimeout(resolve, 3000);
                                img.onload = function() {{ clearTimeout(timer); resolve(); }};
                                img.onerror = function() {{ clearTimeout(timer); resolve(); }};
                            }});
                        }}

                        if(img.naturalWidth === 0 || img.naturalHeight === 0) return null;

                        var canvas = document.createElement('canvas');
                        var context = canvas.getContext('2d');
                        canvas.height = img.naturalHeight;
                        canvas.width = img.naturalWidth;
                        context.drawImage(img, 0, 0);
                        return canvas.toDataURL();
                    }})();
                ''', await_promise=True)

                if form_verifyCode_base64:
                    img_base64 = base64.b64decode(form_verifyCode_base64.split(',')[1])

                if img_base64 is None:
                    if not Captcha_Browser is None:
                        debug.log("[TIXCRAFT OCR] Failed to get image from canvas, using fallback: NonBrowser")
                        captcha_payload = await run_cpu_bound(Captcha_Browser.request_captcha)
                        img_base64 = base64.b64decode(captcha_payload)

            except Exception as exc:
                debug.log("[TIXCRAFT OCR] Canvas error:", str(exc))

        # OCR 識別
        performance.record_elapsed(perf_trace, performance.CAPTURE_STAGE, capture_started_ns)
        if not img_base64 is None:
            try:
                ocr_started_ns = performance.perf_counter_ns()
                ocr_answer = await run_cpu_bound(ocr.classification, img_base64)
                performance.record_elapsed(perf_trace, performance.OCR_STAGE, ocr_started_ns)
            except Exception as exc:
                debug.log("[TIXCRAFT OCR] Classification error:", str(exc))

    return ocr_answer


async def _wait_for_tixcraft_verify_input(
    tab,
    *,
    timeout=_TIXCRAFT_VERIFY_READY_TIMEOUT_SECONDS,
    interval=_TIXCRAFT_VERIFY_READY_INTERVAL_SECONDS,
):
    """Wait for a real verification element without confusing lookup with existence."""

    async def probe():
        current_url = _get_cached_tab_url(tab)
        if classify_page(current_url) is not PageClass.TICKET:
            return TixCraftTicketFormState.INVALID_PAGE
        try:
            element = await tab.query_selector("#TicketForm_verifyCode")
        except Exception as exc:
            if runtime_health.is_browser_connection_closed_error(exc):
                raise
            return None
        if element is None:
            return None
        return TixCraftTicketFormState.READY

    state = await bounded_poll(
        probe,
        timeout=timeout,
        interval=interval,
        description="TixCraft verification input",
    )
    if state is not None:
        return state
    if classify_page(_get_cached_tab_url(tab)) is not PageClass.TICKET:
        return TixCraftTicketFormState.INVALID_PAGE
    return TixCraftTicketFormState.UNAVAILABLE


async def nodriver_tixcraft_auto_ocr(tab, config_dict, ocr, away_from_keyboard_enable,
                                     previous_answer, Captcha_Browser,
                                     ocr_captcha_image_source, domain_name):
    """OCR 自動識別主邏輯"""
    debug = util.create_debug_logger(config_dict)

    is_need_redo_ocr = False
    is_form_submitted = False

    form_state = TixCraftTicketFormState.NOT_RENDERED_YET
    perf_trace = None
    if not ocr is None:
        perf_trace = performance.PerformanceTrace("tixcraft_captcha")
        ready_started_ns = performance.perf_counter_ns()
        form_state = await _wait_for_tixcraft_verify_input(tab)
        performance.record_elapsed(
            perf_trace,
            performance.CAPTCHA_READY_STAGE,
            ready_started_ns,
        )
    else:
        debug.log("[TIXCRAFT OCR] ddddocr component unavailable, you may be running on ARM")

    if form_state is TixCraftTicketFormState.READY:
        debug.log("[TIXCRAFT OCR] away_from_keyboard_enable:", away_from_keyboard_enable)
        debug.log("[TIXCRAFT OCR] previous_answer:", previous_answer)
        debug.log("[TIXCRAFT OCR] ocr_captcha_image_source:", ocr_captcha_image_source)

        ocr_start_time = time.monotonic()
        ocr_answer = await nodriver_tixcraft_get_ocr_answer(
            tab, ocr, ocr_captcha_image_source, Captcha_Browser, domain_name, perf_trace=perf_trace
        )
        ocr_done_time = time.monotonic()
        ocr_elapsed_time = ocr_done_time - ocr_start_time
        debug.log("[TIXCRAFT OCR] Processing time:", "{:.3f}".format(ocr_elapsed_time))

        if ocr_answer is None:
            if away_from_keyboard_enable:
                # 頁面尚未準備好，重試
                # PS: 通常發生在非同步腳本取得驗證碼圖片時
                is_need_redo_ocr = True
                await asyncio.sleep(0.1)
            else:
                await nodriver_tixcraft_keyin_captcha_code(tab, config_dict=config_dict)
        else:
            ocr_answer = ocr_answer.strip()
            debug.log("[TIXCRAFT OCR] Result:", ocr_answer)
            if len(ocr_answer) == 4:
                # Yii2 hash pre-validation (hash available after first reload, 0 on first load)
                if away_from_keyboard_enable:
                    hash1 = await nodriver_get_yii_captcha_hash(tab)
                    if hash1 > 0:
                        if not util.yii_captcha_verify(ocr_answer, hash1):
                            candidates = util.yii_captcha_edit1(ocr_answer, hash1)
                            if candidates:
                                ocr_answer = candidates[0]
                                debug.log(f"[TIXCRAFT OCR] Hash edit1 corrected to: {ocr_answer}")
                            else:
                                debug.log(f"[TIXCRAFT OCR] Hash mismatch, no edit1 solution, reloading")
                                is_need_redo_ocr = True
                                await nodriver_tixcraft_reload_captcha(tab, domain_name)
                                ocr_answer = None
                        else:
                            debug.log(f"[TIXCRAFT OCR] Hash verified ok: {ocr_answer}")
                if ocr_answer is not None:
                    who_care_var, is_form_submitted = await nodriver_tixcraft_keyin_captcha_code(
                        tab,
                        answer=ocr_answer,
                        auto_submit=away_from_keyboard_enable,
                        config_dict=config_dict,
                        perf_trace=perf_trace,
                    )
            else:
                if not away_from_keyboard_enable:
                    await nodriver_tixcraft_keyin_captcha_code(tab, config_dict=config_dict)
                else:
                    is_need_redo_ocr = True
                    if previous_answer != ocr_answer:
                        previous_answer = ocr_answer
                        debug.log("[TIXCRAFT OCR] Reloading captcha")

                        await nodriver_tixcraft_reload_captcha(tab, domain_name)

                        if ocr_captcha_image_source == CONST_OCR_CAPTCH_IMAGE_SOURCE_CANVAS:
                            await asyncio.sleep(0.3)
    else:
        debug.log(f"[TIXCRAFT OCR] Verification form state: {form_state.value}")

    if perf_trace is not None:
        performance.log_trace(debug, perf_trace, "[TIXCRAFT PERF]")

    return is_need_redo_ocr, previous_answer, is_form_submitted

async def nodriver_tixcraft_ticket_main_ocr(tab, config_dict, ocr, Captcha_Browser, domain_name):
    """票券頁面 OCR 處理主函數"""
    # 函數開始時檢查暫停
    if await check_and_handle_pause(config_dict):
        return False, "", False

    debug = util.create_debug_logger(config_dict)

    away_from_keyboard_enable = config_dict["ocr_captcha"]["force_submit"]
    if not config_dict["ocr_captcha"]["enable"]:
        away_from_keyboard_enable = False
    ocr_captcha_image_source = config_dict["ocr_captcha"]["image_source"]

    if not config_dict["ocr_captcha"]["enable"]:
        # 手動模式
        await nodriver_tixcraft_keyin_captcha_code(tab, config_dict=config_dict)
    else:
        # 自動 OCR 模式
        previous_answer = None
        current_url, _ = await nodriver_current_url(tab)
        fail_count = 0  # Track consecutive failures
        total_fail_count = 0  # Track total failures
        is_form_submitted = False

        for redo_ocr in range(5):
            is_need_redo_ocr, previous_answer, is_form_submitted = await nodriver_tixcraft_auto_ocr(
                tab, config_dict, ocr, away_from_keyboard_enable,
                previous_answer, Captcha_Browser, ocr_captcha_image_source, domain_name
            )

            if is_form_submitted:
                debug.log("[TIXCRAFT OCR] Form submitted")
                break

            if not away_from_keyboard_enable:
                break

            if not is_need_redo_ocr:
                break

            # Track failures and refresh captcha after 3 consecutive failures
            if is_need_redo_ocr:
                fail_count += 1
                total_fail_count += 1
                debug.log(f"[TIXCRAFT OCR] Fail count: {fail_count}, Total fails: {total_fail_count}")

                # Check if total failures reached 5, switch to manual input mode
                if total_fail_count >= 5:
                    print("[TIXCRAFT OCR] OCR failed 5 times. Please enter captcha manually.")
                    away_from_keyboard_enable = False
                    await nodriver_tixcraft_keyin_captcha_code(tab, config_dict=config_dict)
                    break

                if fail_count >= 3:
                    debug.log("[TIXCRAFT OCR] 3 consecutive failures reached")

                    # Try to dismiss any existing alert before continuing
                    try:
                        await tab.send(cdp.page.handle_java_script_dialog(accept=True))
                        debug.log("[TIXCRAFT OCR] Dismissed existing alert")
                    except Exception:
                        pass

                    # Wait for potential auto-refresh
                    await asyncio.sleep(2.5)
                    fail_count = 0  # Reset consecutive counter after handling

            # 檢查是否還在同一頁面
            new_url, _ = await nodriver_current_url(tab)
            if new_url != current_url:
                break

            debug.log(f"[TIXCRAFT OCR] Retry {redo_ocr + 1}/5")

        # Mark OCR completed for this URL only when form was actually submitted
        if is_form_submitted:
            _state["ocr_completed_url"] = current_url
            attempt = _get_tixcraft_purchase_attempt()
            _state["ocr_completed_attempt_id"] = (
                attempt.attempt_id if attempt is not None else None
            )

async def _nodriver_ticketmaster_check_ip_block_impl(tab, config_dict, current_url=""):
    """Detect PerimeterX EPS block page on tixcraft/ticketmaster domains.

    When blocked, waits 4-7 minutes (random) then navigates back to original URL.
    Returns True if blocked (caller should skip normal processing), False otherwise.
    """
    debug = util.create_debug_logger(config_dict)

    # Still within previous block wait period
    block_until = _state.get("ip_block_until", 0)
    now_monotonic = time.monotonic()
    recovery_retry_at = float(
        _state.get("soft_block_recovery_retry_at", 0.0) or 0.0
    )
    if recovery_retry_at > now_monotonic:
        return True
    if block_until > 0 and now_monotonic < block_until:
        remaining = max(0.0, block_until - now_monotonic)
        debug.log(
            f"[EPS BLOCK] Still waiting for block to expire, {remaining:.1f}s remaining"
        )
        await sleep_with_pause_check(
            tab,
            min(5.0, remaining),
            config_dict,
        )
        return True

    try:
        detection = await _detect_tixcraft_soft_block(tab, current_url, config_dict)
        if not detection.get("blocked", False):
            if detection.get("health_confirmed", False):
                _state["ip_block_count"] = 0
                _clear_tixcraft_soft_block_backoff(tab)
                return False
            if _state.get("soft_block_phase") in {"backoff", "recovering"}:
                _defer_tixcraft_soft_block_recovery(
                    config_dict,
                    current_url,
                    tab=tab,
                    now=now_monotonic,
                )
                return True
            return False
        _state["ip_block_count"] = int(_state.get("ip_block_count", 0)) + 1
        await _handle_tixcraft_soft_block(tab, config_dict, current_url, detection)
        return True

    except Exception as exc:
        debug.log(f"[EPS BLOCK] Error checking block status: {exc}")
        return False


async def nodriver_ticketmaster_check_ip_block(tab, config_dict, current_url=""):
    token = _state.bind(_dispatch_state_for_tab(tab))
    try:
        return await _nodriver_ticketmaster_check_ip_block_impl(
            tab,
            config_dict,
            current_url,
        )
    finally:
        _state.reset_binding(token)


async def _nodriver_tixcraft_main_impl(tab, url, config_dict, ocr, Captcha_Browser):
    # 函數開始時檢查暫停
    if await check_and_handle_pause(config_dict):
        return False

    debug = util.create_debug_logger(config_dict)

    # Global alert handler: only accept known retryable alerts. Unknown alerts
    # stay visible for manual intervention and the browser remains open.
    bound_state = _state.current()

    async def _handle_global_alert(event):
        # Skip alert handling when bot is paused (let user handle manually)
        if os.path.exists(util.get_instance_state_path(CONST_MAXBOT_INT28_FILE)):
            return
        # IMPORTANT: Use tab.target.url (cached) instead of nodriver_current_url (js_dumps)
        # When alert dialog is open, JavaScript execution is blocked, causing js_dumps to hang
        current_url = tab.target.url if hasattr(tab, 'target') and tab.target else ""
        if platform_key_for_url(current_url) != "tixcraft":
            return

        if '/ticket/checkout' in current_url:
            debug.log(f"[GLOBAL ALERT] Alert on checkout page, NOT auto-dismissing: '{event.message}'")
            return

        debug.log(f"[GLOBAL ALERT] Alert detected: '{event.message}'")

        captcha_error_keywords = [
            'verification code',
            '驗證碼',
            'incorrect',
            'try again',
            'captcha',
            'wrong code'
        ]
        alert_message_lower = event.message.lower()
        is_captcha_error = any(keyword in alert_message_lower for keyword in captcha_error_keywords)
        is_retryable_alert = _is_retryable_alert(event.message)

        if not is_captcha_error and not is_retryable_alert:
            _state["manual_intervention_required"] = True
            debug.log("[GLOBAL ALERT] Unknown alert; waiting for manual intervention")
            return

        if is_captcha_error:
            _state["captcha_alert_detected"] = True
            # Wrong answer submitted: clear the submit guard so retry is immediate.
            _reset_tixcraft_submit_state()
            debug.log("[GLOBAL ALERT] Captcha error detected, flagging for retry")

        dismiss_success = False
        for attempt in range(3):
            try:
                await tab.send(cdp.page.handle_java_script_dialog(accept=True))
                dismiss_success = True
                debug.log(f"[GLOBAL ALERT] Alert dismissed (attempt {attempt + 1})")
                break
            except Exception as dismiss_exc:
                error_msg = str(dismiss_exc)
                # CDP -32602 means no dialog is showing (already dismissed by another handler or user)
                if "No dialog is showing" in error_msg or "-32602" in error_msg:
                    dismiss_success = True  # Consider it handled
                    debug.log("[GLOBAL ALERT] Dialog already dismissed")
                    break  # No need to retry
                if attempt < 2:
                    await asyncio.sleep(0.1)  # Small delay before retry
                else:
                    debug.log(f"[GLOBAL ALERT] Failed to dismiss alert: {dismiss_exc}")

        if is_retryable_alert and dismiss_success:
            await _recover_to_last_valid_area(tab, config_dict, "retryable_alert")

    async def handle_global_alert(event):
        token = _state.bind(bound_state)
        try:
            return await _handle_global_alert(event)
        finally:
            _state.reset_binding(token)

    _ensure_tixcraft_state_defaults()

    _sync_tixcraft_notification_flow(url)

    # Register global alert handler (remains active throughout session)
    # Only register once to prevent infinite loop
    if not _state.get("alert_handler_registered", False):
        try:
            try:
                await tab.send(cdp.page.enable())
            except Exception:
                pass
            tab.add_handler(cdp.page.JavascriptDialogOpening, handle_global_alert)
            _state["alert_handler_registered"] = True
            debug.log(f"[GLOBAL ALERT] Global alert handler registered")
        except Exception as handler_exc:
            debug.log(f"[GLOBAL ALERT] Failed to register alert handler: {handler_exc}")

    # Queue-it virtual waiting room detection (TixCraft + Ticketmaster SG)
    # Pattern: ported from platforms/ibon.py — URL-based, customerId-agnostic.
    # Both family platforms route high-traffic users to *.queue-it.net before EPS evaluates.
    was_in_queue = _state.get("queue_it_enter_time") is not None
    should_pause, elapsed = _process_queue_it_state(url, _state, time.monotonic())
    if should_pause:
        if not was_in_queue:
            debug.log("[TIXCRAFT] Queue-IT entered, waiting...")
        return False
    if elapsed is not None:
        debug.log(f"[TIXCRAFT] Queue-IT passed (waited {elapsed:.1f}s)")

    await nodriver_tixcraft_home_close_window(tab)

    page_class = classify_page(url)
    submit_was_active = await _reconcile_tixcraft_submit_ownership(
        tab,
        page_class,
        url,
        config_dict,
    )
    protected_submit_transition = bool(
        submit_was_active
        and page_class in {PageClass.ORDER, PageClass.CHECKOUT, PageClass.PAYMENT}
    )
    if protected_submit_transition:
        # A confirmed protected route invalidates every stale ticket/area task.
        _track_tixcraft_attempt_page(page_class, url)
    elif submit_was_active:
        evidence_page = await _classify_recovery_page(tab, url, page_class)
        positive_failure_reasons = {
            PageClass.REJECTED_ERROR: "confirmed_rejected_error",
            PageClass.CANCELED_ORDER: "confirmed_canceled_order",
            PageClass.CONTINUE_SHOPPING: "confirmed_continue_shopping",
        }
        recovery_reason = positive_failure_reasons.get(evidence_page)
        if recovery_reason:
            await _recover_to_last_valid_area(
                tab,
                config_dict,
                recovery_reason,
            )
            return False
        order_processing = await _detect_tixcraft_order_pending(
            tab,
            url,
            force=True,
        )
        if order_processing:
            attempt = _get_tixcraft_purchase_attempt()
            if attempt is not None:
                attempt.phase = TixCraftAttemptPhase.ORDER_PENDING
            _record_action("order_pending", url)
            await _emit_tixcraft_attempt_notification(
                tab,
                config_dict,
                "order_pending",
                url,
            )
        runtime_health.runtime_log(
            "[TIXCRAFT] submit_navigation_protected",
            config_dict,
            reason="order_processing" if order_processing else "outcome_inconclusive",
            source_url=_tixcraft_route_key(url),
            target_url="",
            page_class=page_class.value,
            attempt_id=getattr(_get_tixcraft_purchase_attempt(), "attempt_id", None),
            generation=int(_state.get("notification_flow_generation", 0) or 0),
            token=int(_state.get("submit_generation", 0) or 0),
        )
        return False

    # EPS / soft-block detection must run before normal page dispatching so a
    # pause page that keeps an /area/ URL is not treated as a selectable area.
    if not protected_submit_transition and (
        _is_tixcraft_soft_block_scope(url) or 'ticketmaster' in url
    ):
        if await nodriver_ticketmaster_check_ip_block(tab, config_dict, current_url=url):
            return False

    observed_area_url = _normalize_tixcraft_area_url(url)
    if observed_area_url:
        _state["last_valid_area_url"] = observed_area_url
        _state["recent_area_route_url"] = observed_area_url

    page_class = classify_page(url)
    if should_use_leak_watch(config_dict):
        scheduler = _get_leak_scheduler()
        expired_events = scheduler.maintenance(
            config_dict,
            url,
            now=time.monotonic(),
        )
        for expired_event in expired_events:
            runtime_health.runtime_log(
                "[LEAK] pending_watchdog_expired",
                config_dict,
                reason=expired_event,
                current_url=url,
            )
    _reconcile_tixcraft_pending_navigation(
        tab,
        url,
        page_class,
        config_dict,
    )
    _track_tixcraft_attempt_page(page_class, url)
    _runtime_log_rate_limited(
        "tixcraft_dispatch_runtime_log",
        "[TIXCRAFT] dispatch",
        config_dict,
        identity=f"{page_class.value}:{_tixcraft_route_key(url)}",
        page_class=page_class.value,
        current_url=url,
    )
    if page_class in {PageClass.ACTIVITY, PageClass.DATE, PageClass.AREA, PageClass.TICKET}:
        await _remember_tixcraft_event_name(tab, url)
    if _state.get("last_valid_area_url"):
        page_class = await _classify_recovery_page(tab, url, page_class)
    positive_recovery_reasons = {
        PageClass.CANCELED_ORDER: "confirmed_canceled_order",
        PageClass.CONTINUE_SHOPPING: "confirmed_continue_shopping",
        PageClass.REJECTED_ERROR: "confirmed_rejected_error",
    }
    if _state.get("last_valid_area_url") and page_class in positive_recovery_reasons:
        recovery_reason = positive_recovery_reasons[page_class]
        recovered = await _recover_to_last_valid_area(tab, config_dict, recovery_reason)
        if recovered:
            return False

    # special case for same event re-open, redirect to user's homepage.
    # Add cooldown to prevent infinite redirect loop when area page is unavailable
    # Match homepage URLs: tixcraft.com, tixcraft.com/, tixcraft.com/activity
    is_tixcraft_home = url in ['https://tixcraft.com', 'https://tixcraft.com/', 'https://tixcraft.com/activity']
    if is_tixcraft_home:
        homepage = config_dict["homepage"]
        # Only redirect if homepage is not the platform root itself (avoid infinite loop)
        homepage_is_root = homepage.rstrip('/') in ['https://tixcraft.com', 'https://tixcraft.com/activity']
        if not homepage_is_root:
            current_time = time.monotonic()
            last_redirect_time = _state.get("last_homepage_redirect_time", 0)
            # Use active safe-page reload interval from settings, default to 3 seconds.
            redirect_interval = get_auto_reload_interval(config_dict, default=3)
            if redirect_interval <= 0:
                redirect_interval = 3  # Minimum 3 seconds to prevent rapid loop

            if current_time - last_redirect_time > redirect_interval:
                try:
                    _state["last_homepage_redirect_time"] = current_time
                    await _guarded_tixcraft_get(tab, homepage, config_dict, reason="HOMEPAGE_RECOVERY")
                except Exception:
                    pass

    # Ticketmaster renders #gameList on the detail route and does not use the
    # TixCraft detail -> game redirect.  Its date handler claims this page.
    if _should_redirect_tixcraft_detail(url):
        _state["start_time"] = time.monotonic()
        is_redirected = await nodriver_tixcraft_redirect(tab, url, config_dict)

    is_date_selected = False
    # Check if this is a Ticketmaster page before using TixCraft logic
    if "/activity/game/" in url and 'ticketmaster' not in url:
        _state["start_time"] = time.monotonic()
        if config_dict["date_auto_select"]["enable"]:
            domain_name = url.split('/')[2]
            is_date_selected = await nodriver_tixcraft_date_auto_select(tab, url, config_dict, domain_name)

    # T010: Ticketmaster date selection integration (User Story 1)
    # Support both URL formats:
    # - /artist/{artist_id} (artist listing page)
    # - /activity/game/{event_id} (event date listing page from /activity/detail redirect)
    is_ticketmaster_date_page = _is_ticketmaster_date_page(url)

    if is_ticketmaster_date_page:
        _state["start_time"] = time.monotonic()
        if config_dict["date_auto_select"]["enable"]:
            debug.log("[TICKETMASTER] Detected Ticketmaster date page, calling date auto select")
            domain_name = url.split('/')[2]
            # Call Ticketmaster date auto select
            is_date_selected = await nodriver_ticketmaster_date_auto_select(tab, config_dict)
            if debug.enabled:
                if is_date_selected:
                    debug.log("[TICKETMASTER] Date selection completed")
                else:
                    debug.log("[TICKETMASTER] Date selection failed or no match")

    # choose area
    if page_class is PageClass.AREA:
        domain_name = url.split('/')[2]
        if config_dict["area_auto_select"]["enable"]:
            if not 'ticketmaster' in domain_name:
                # for tixcraft
                await nodriver_tixcraft_area_auto_select(tab, url, config_dict)

                _state["area_retry_count"]+=1
                #print("count:", _state["area_retry_count"])
                if _state["area_retry_count"] >= (60 * 15):
                    # Cool-down
                    _state["area_retry_count"] = 0
                    await asyncio.sleep(5)
            else:
                # T013+T017: Ticketmaster area + ticket number (phase-based state machine)
                # Ticketmaster uses AJAX on same URL: area selection -> ticketPriceList loads -> set ticket number
                # Phase: area_select -> wait_ticket -> done (reset when URL leaves /ticket/area/)
                phase = _state.get("ticketmaster_phase", "area_select")

                if phase == "area_select":
                    zone_info = await nodriver_ticketmaster_parse_zone_info(tab, config_dict)
                    if zone_info:
                        await nodriver_ticketmaster_area_auto_select(tab, config_dict, zone_info)
                        _state["ticketmaster_phase"] = "wait_ticket"
                        _state["area_retry_count"] = 0
                    else:
                        _state["area_retry_count"] += 1

                elif phase == "wait_ticket":
                    is_assigned = await nodriver_ticketmaster_assign_ticket_number(tab, config_dict)
                    if is_assigned:
                        _state["fail_promo_list"] = await nodriver_ticketmaster_promo(tab, config_dict, _state["fail_promo_list"])
                        _state["ticketmaster_phase"] = "done"
                        _state["area_retry_count"] = 0
                    else:
                        _state["area_retry_count"] += 1
                        if _state["area_retry_count"] >= 30:
                            # A stale ticketPriceList or an unavailable exact
                            # count must not spin forever on the same document.
                            # The shared coordinator throttles this reload with
                            # the configured per-tab minimum interval.
                            debug.log(
                                "[TICKETMASTER] Ticket assignment failed after "
                                "30 retries, requesting throttled reload"
                            )
                            _state["ticketmaster_phase"] = "area_select"
                            _state["area_retry_count"] = 0
                            await guarded_reload(
                                tab,
                                reason="ticketmaster_ticket_count_stale",
                                config_dict=config_dict,
                            )

                # phase == "done": no-op, wait for POST navigation to /ticket/ticket/
    else:
        _state["fail_promo_list"] = []
        _state["area_retry_count"] = 0
        _state["ticketmaster_phase"] = "area_select"

    # T020: Ticketmaster captcha integration (User Story 4)
    # https://ticketmaster.sg/ticket/check-captcha/23_blackpink/954/5/75
    if '/ticket/check-captcha/' in url:
        domain_name = url.split('/')[2]
        if 'ticketmaster' in domain_name:
            # Check if we already processed this captcha page (avoid repeated execution)
            ticketmaster_captcha_processed = _state.get("ticketmaster_captcha_processed_url", "")
            if ticketmaster_captcha_processed != url:
                # Call Ticketmaster captcha handler
                await nodriver_ticketmaster_captcha(tab, config_dict, ocr, Captcha_Browser)
                # Mark this URL as processed
                _state["ticketmaster_captcha_processed_url"] = url
    else:
        # Reset captcha processed state when leaving captcha page
        _state["ticketmaster_captcha_processed_url"] = ""

    if '/ticket/verify/' in url:
        # Tixcraft verify handler (already implemented)
        _state["fail_list"] = await nodriver_tixcraft_verify(tab, config_dict, _state["fail_list"])
    else:
        _state["fail_list"] = []

    # main app, to select ticket number.
    if '/ticket/ticket/' in url:
        domain_name = url.split('/')[2]
        await nodriver_tixcraft_ticket_main(tab, config_dict, ocr, Captcha_Browser, domain_name)
        _state["done_time"] = time.monotonic()

        if not _state["played_sound_ticket"]:
            if config_dict["advanced"]["play_sound"]["ticket"]:
                play_sound_while_ordering(config_dict)
        _state["played_sound_ticket"] = True
    else:
        _state["played_sound_ticket"] = False

    order_pending_observed = '/ticket/order' in url
    if not order_pending_observed and page_class == PageClass.TICKET:
        order_pending_observed = await _detect_tixcraft_order_pending(tab, url)
    if order_pending_observed:
        _state["done_time"] = time.monotonic()
        _record_action("order_pending", url)
        await _emit_tixcraft_attempt_notification(tab, config_dict, "order_pending", url)

    is_quit_bot = False
    if '/ticket/checkout' in url:
        start_time = _state.get("start_time")
        done_time = _state.get("done_time")
        if start_time is not None:
            if done_time is not None:
                bot_elapsed_time = done_time - start_time
                if _state.get("elapsed_time") != bot_elapsed_time:
                    print("bot elapsed time:", "{:.3f}".format(bot_elapsed_time))
                _state["elapsed_time"] = bot_elapsed_time

        if not _state.get("is_popup_checkout", False):
            _state["is_popup_checkout"] = True
            _record_action("checkout_reached", url)

            # Issue #193: Move inside the block to execute only once on first checkout detection
            # Headless-specific behavior: open checkout URL in new browser window
            if config_dict["advanced"]["headless"]:
                domain_name = url.split('/')[2]
                checkout_url = "https://%s/ticket/checkout" % (domain_name)
                print("Checkout reached, please check order at: %s" % (checkout_url))
                webbrowser.open_new(checkout_url)

        if not _state["played_sound_order"] and config_dict["advanced"]["play_sound"]["order"]:
            play_sound_while_ordering(config_dict)
        attempt = _get_tixcraft_purchase_attempt()
        order_enqueued = bool(attempt and "order_pending" in attempt.discord_stages)
        if attempt is not None and not order_enqueued:
            await _emit_tixcraft_attempt_notification(
                tab,
                config_dict,
                "order_pending",
                url,
            )
        if attempt is not None:
            await _emit_tixcraft_attempt_notification(
                tab,
                config_dict,
                "checkout_reached",
                url,
            )
        _state["played_sound_order"] = True
    else:
        _state["is_popup_checkout"] = False
        _state["played_sound_order"] = False
        _state["printed_completed"] = False

    # Approach B: handle printed_completed internally
    if is_quit_bot:
        if not _state.get("printed_completed", False):
            print("TixCraft checkout reached")
            _state["printed_completed"] = True

    return is_quit_bot


async def nodriver_tixcraft_main(tab, url, config_dict, ocr, Captcha_Browser):
    token = _state.bind(_dispatch_state_for_tab(tab))
    try:
        return await _nodriver_tixcraft_main_impl(
            tab,
            url,
            config_dict,
            ocr,
            Captcha_Browser,
        )
    finally:
        _state.reset_binding(token)
