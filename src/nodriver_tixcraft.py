#!/usr/bin/env python3
#encoding=utf-8
import argparse
import base64
import json
import logging
import asyncio
import math
import os
import pathlib
import platform
import random
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime

# 強制使用 UTF-8 編碼輸出（解決 Windows CP950 編碼問題）
# 適用於所有輸出環境（終端、IDE、重定向、管道）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import zendriver as uc
from zendriver import cdp
import urllib.parse

import util
import settings
import runtime_health
from refresh_timing import (
    RUNTIME_NTP_CRITICAL_WINDOW_SECONDS,
    RefreshTriggerController,
    RuntimeNtpCalibrationCoordinator,
    TriggerPhase,
    compute_remaining_ns,
    describe_refresh_calibration,
    format_remaining_seconds,
    get_effective_refresh_calibration,
    parse_refresh_datetime_value,
    sleep_until_deadline,
    wall_datetime_to_monotonic_deadline_ns,
)
from NonBrowser import NonBrowser
from page_classifier import PageClass, classify_page
from platforms.common_async import is_interval_due
from reload_guard import guarded_reload
from trigger_arbiter import TriggerReloadArbiter, TriggerReloadDecision

try:
    import ddddocr
except Exception as exc:
    print(exc)
    pass

from nodriver_common import *
from platforms.facebook import *
from platforms.fansigo import *
from platforms.cityline import *
from platforms.famiticket import *
from platforms.ticketplus import *
from platforms.funone import *
from platforms.kktix import *
from platforms.tixcraft import *
from platforms.ibon import *
from platforms.kham import *
from platforms.hkticketing import *
import platforms.tixcraft as tixcraft_platform

CONST_CITYLINE_SIGN_IN_URL = "https://www.cityline.com/Login.html?targetUrl=https%3A%2F%2Fwww.cityline.com%2FEvents.html"
CONST_CITYLINE_HK_SIGN_IN_URL = "https://www.cityline.com.hk/Login.html?targetUrl=%s"
CONST_FAMI_SIGN_IN_URL = "https://www.famiticket.com.tw/Home/User/SignIn"
CONST_HKTICKETING_SIGN_IN_URL = "https://premier.hkticketing.com/Secure/ShowLogin.aspx"
CONST_HKTICKETING_TYPE02_SIGN_IN_URL = "https://hkt.hkticketing.com/hant/#/login"
CONST_KHAM_SIGN_IN_URL = "https://kham.com.tw/application/UTK13/UTK1306_.aspx"
CONST_KKTIX_SIGN_IN_URL = "https://kktix.com/users/sign_in?back_to=%s"
CONST_TICKET_SIGN_IN_URL = "https://ticket.com.tw/application/utk13/utk1306_.aspx"
CONST_UDN_SIGN_IN_URL = "https://tickets.udnfunlife.com/application/UTK01/UTK0101_.aspx"
CONST_URBTIX_SIGN_IN_URL = "https://www.urbtix.hk/member-login"

logging.basicConfig()
logger = logging.getLogger('logger')

CONFIG_RELOAD_CHECK_INTERVAL_SEC = 0.5
REFRESH_TRIGGER_RETRY_DELAY_NS = 200_000_000
REFRESH_TRIGGER_RETRY_WINDOW_NS = 2_000_000_000
REFRESH_TRIGGER_MAX_ATTEMPTS = 2
REFRESH_TRIGGER_MAX_LATENESS_NS = 60_000_000_000
REFRESH_STALE_RESPONSE_RETRY_DELAY_NS = 500_000_000
REFRESH_CACHED_URL_FAST_PATH_WINDOW_NS = 10_000_000_000
REFRESH_GATE_HEALTH_CACHE_SECONDS = 3.0
REFRESH_GATE_HEALTH_NEAR_BOUNDARY_SECONDS = 2.5
REFRESH_PREFLIGHT_RECOVERY_RETRY_DELAY_NS = 500_000_000
REFRESH_TRIGGER_RETRYABLE_REASONS = frozenset(
    {
        "empty_url",
        "reload_exception",
        "reload_failed",
        "reload_in_flight",
    }
)
BROWSER_CONNECTION_EMPTY_URL_GRACE_SECONDS = 10.0
BROWSER_CONNECTION_RESTART_WINDOW_SECONDS = 120.0
BROWSER_CONNECTION_MAX_RESTARTS = 3
BROWSER_CONNECTION_RESTART_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
BROWSER_CONNECTION_SAFE_RESTART_PAGES = frozenset(
    {
        PageClass.HOME,
        PageClass.ACTIVITY,
        PageClass.DATE,
        PageClass.AREA,
        PageClass.SOLD_OUT,
        PageClass.REJECTED_ERROR,
        PageClass.CANCELED_ORDER,
        PageClass.CONTINUE_SHOPPING,
    }
)


async def nodriver_goto_homepage(driver, config_dict):
    debug = util.create_debug_logger(config_dict)
    tab = None
    homepage = config_dict["homepage"]
    if 'kktix.c' in homepage:
        # for like human.
        try:
            tab = await driver.get(homepage)
            await asyncio.sleep(random.uniform(1.0, 2.5))
        except Exception as e:
            print(f"[ERROR] Failed to navigate to kktix homepage: {e}")

        if len(config_dict["accounts"]["kktix_account"])>0:
            if not 'https://kktix.com/users/sign_in?' in homepage:
                homepage = CONST_KKTIX_SIGN_IN_URL % (homepage)

    if 'famiticket.com' in homepage:
        if len(config_dict["accounts"]["fami_account"])>0:
            homepage = CONST_FAMI_SIGN_IN_URL

    if 'kham.com' in homepage:
        if len(config_dict["accounts"]["kham_account"])>0:
            homepage = CONST_KHAM_SIGN_IN_URL

    if 'ticket.com.tw' in homepage:
        if len(config_dict["accounts"]["ticket_account"])>0:
            homepage = CONST_TICKET_SIGN_IN_URL

    if 'udnfunlife.com' in homepage:
        if len(config_dict["accounts"]["udn_account"])>0:
            homepage = CONST_UDN_SIGN_IN_URL

    if 'urbtix.hk' in homepage:
        if len(config_dict["accounts"]["urbtix_account"])>0:
            homepage = CONST_URBTIX_SIGN_IN_URL

    if 'cityline.com' in homepage:
        if len(config_dict["accounts"]["cityline_account"])>0:
            if 'cityline.com.hk' in homepage:
                homepage = CONST_CITYLINE_HK_SIGN_IN_URL % urllib.parse.quote(config_dict["homepage"], safe='')
            else:
                homepage = CONST_CITYLINE_SIGN_IN_URL

    if 'hkticketing.com' in homepage:
        if len(config_dict["accounts"]["hkticketing_account"])>0:
            if 'hkt.hkticketing.com' in homepage:
                # Type02: hkt.hkticketing.com SPA
                homepage = CONST_HKTICKETING_TYPE02_SIGN_IN_URL
            else:
                # Type01: premier.hkticketing.com
                homepage = CONST_HKTICKETING_SIGN_IN_URL

    # https://ticketplus.com.tw/*
    if 'ticketplus.com.tw' in homepage:
        if len(config_dict["accounts"]["ticketplus_account"]) > 1:
            homepage = "https://ticketplus.com.tw/"

    try:
        tab = await driver.get(homepage)
        await asyncio.sleep(random.uniform(1.0, 2.5))
    except Exception as e:
        print(f"[ERROR] Failed to navigate to homepage: {e}")

    tixcraft_family = False
    if 'tixcraft.com' in homepage:
        tixcraft_family = True

    if 'indievox.com' in homepage:
        tixcraft_family = True

    if 'ticketmaster.' in homepage:
        tixcraft_family = True

    if tixcraft_family:
        # Determine correct cookie domain and name based on homepage
        # Each site uses different session cookie names (Issue #207)
        if 'ticketmaster.sg' in homepage:
            cookie_domain = "ticketmaster.sg"
            cookie_name = "TIXPUISID"
        elif 'ticketmaster.com' in homepage:
            cookie_domain = ".ticketmaster.com"
            cookie_name = "TIXUISID"
        elif 'indievox.com' in homepage:
            cookie_domain = "www.indievox.com"
            cookie_name = "IVUISID"
        else:
            cookie_domain = ".tixcraft.com"
            cookie_name = "TIXUISID"

        tixcraft_sid = config_dict["accounts"]["tixcraft_sid"]
        if len(tixcraft_sid) > 1:
            debug.log(f"[TIXCRAFT] Setting {cookie_name} cookie, length: {len(tixcraft_sid)}")

            try:
                # Step 1: Delete existing cookies (both legacy SID and session cookie)
                try:
                    await tab.send(cdp.network.delete_cookies(
                        name="SID",
                        domain=cookie_domain
                    ))
                    await tab.send(cdp.network.delete_cookies(
                        name=cookie_name,
                        domain=cookie_domain
                    ))
                    # Delete cookies from alternate domain to avoid conflicts
                    if 'indievox.com' in homepage:
                        await tab.send(cdp.network.delete_cookies(
                            name=cookie_name,
                            domain=".indievox.com"
                        ))
                    if 'ticketmaster.sg' in homepage:
                        await tab.send(cdp.network.delete_cookies(
                            name=cookie_name,
                            domain=".ticketmaster.sg"
                        ))
                    debug.log(f"[TIXCRAFT] Deleted existing SID and {cookie_name} cookies for domain: {cookie_domain}")
                except Exception as del_e:
                    debug.log(f"[TIXCRAFT] Note: Could not delete existing cookies: {del_e}")

                # Step 2: Set new session cookie using CDP
                cookie_result = await tab.send(cdp.network.set_cookie(
                    name=cookie_name,
                    value=tixcraft_sid,
                    domain=cookie_domain,
                    path="/",
                    secure=True,
                    http_only=True
                ))

                debug.log(f"[TIXCRAFT] CDP setCookie result: {cookie_result}")
                debug.log(f"[TIXCRAFT] {cookie_name} cookie set successfully")

                # Verify cookie was set
                updated_cookies = await driver.cookies.get_all()
                sid_cookies = [c for c in updated_cookies if c.name == cookie_name]
                if not sid_cookies:
                    debug.log(f"[TIXCRAFT] Warning: {cookie_name} cookie not found after setting")
                else:
                    debug.log(f"[TIXCRAFT] Verified {cookie_name} cookie: domain={sid_cookies[0].domain}, value length={len(sid_cookies[0].value)}")

            except Exception as e:
                debug.log(f"[TIXCRAFT] Error setting {cookie_name} cookie: {str(e)}")
                import traceback
                traceback.print_exc()
                debug.log("[TIXCRAFT] Falling back to old method...")

                # Fallback to old method if CDP fails
                cookies = await driver.cookies.get_all()
                # Filter out all existing SID and session cookies to avoid conflicts
                cookies_filtered = [c for c in cookies if c.name not in ('SID', cookie_name)]
                # Create new session cookie with correct attributes (Issue #144, #207)
                new_cookie = cdp.network.CookieParam(
                    cookie_name,
                    tixcraft_sid,
                    domain=cookie_domain,
                    path="/",
                    http_only=True,
                    secure=True
                )
                cookies_filtered.append(new_cookie)
                await driver.cookies.set_all(cookies_filtered)

                debug.log(f"[TIXCRAFT] {cookie_name} cookie set successfully (fallback method)")

    if 'ibon.com' in homepage:
        # ibon fires a window.alert() on homepage load ("會員登入方式調整").
        # Register the global handler now so any future alert is auto-dismissed,
        # then clear the dialog that already opened during the driver.get() above
        # (whose JavascriptDialogOpening event has already passed and was missed).
        await register_ibon_alert_handler(tab, config_dict)
        await dismiss_pending_ibon_dialog(tab, config_dict)

        # Special handling for tour.ibon.com.tw:
        # Need to visit ticket.ibon.com.tw first to complete OAuth and get _at_e token
        is_tour_ibon = 'tour.ibon.com.tw' in homepage
        original_homepage = homepage

        if is_tour_ibon:
            debug.log("[TOUR IBON] Detected tour.ibon.com.tw homepage")
            debug.log("[TOUR IBON] Step 1: Visiting ticket.ibon.com.tw first for OAuth...")

            # Step 1: Visit ticket.ibon.com.tw homepage first
            try:
                tab = await driver.get("https://ticket.ibon.com.tw/")
                await asyncio.sleep(random.uniform(1.5, 2.5))
                # ticket.ibon homepage re-fires the same onload notice
                await dismiss_pending_ibon_dialog(tab, config_dict)
            except Exception as e:
                debug.log(f"[TOUR IBON] Error visiting ticket.ibon.com.tw: {e}")

        # Step 2: Set ibon cookie
        login_result = await nodriver_ibon_login(tab, config_dict, driver)

        if login_result['success']:
            debug.log("[IBON] login process completed successfully")
            # Reload so the server receives the cookie and establishes the session.
            # Without this, the first page load was anonymous; buying tickets would
            # trigger a login redirect even though the cookie is already set.
            if not is_tour_ibon:
                try:
                    await guarded_reload(tab, reason="ibon_cookie_session_apply", recovery=True)
                    await asyncio.sleep(random.uniform(1.0, 1.5))
                    await dismiss_pending_ibon_dialog(tab, config_dict)
                    debug.log("[IBON] Page reloaded to apply cookie session")
                except Exception as reload_exc:
                    debug.log(f"[IBON] Reload after cookie set failed: {reload_exc}")
        else:
            debug.log(f"[IBON] login process failed: {login_result.get('reason', 'unknown')}")
            if 'error' in login_result:
                debug.log(f"[IBON] Error details: {login_result['error']}")

        # Step 3: For tour.ibon.com.tw, navigate to original homepage after OAuth
        if is_tour_ibon:
            debug.log(f"[TOUR IBON] Step 2: Navigating to tour.ibon homepage: {original_homepage}")

            try:
                tab = await driver.get(original_homepage)
                await asyncio.sleep(random.uniform(1.5, 2.5))
                # Clear any onload dialog from tour.ibon homepage (no-op if none)
                await dismiss_pending_ibon_dialog(tab, config_dict)
            except Exception as e:
                debug.log(f"[TOUR IBON] Error navigating to tour.ibon homepage: {e}")

        # 不管成功與否，都繼續後續處理，讓使用者手動處理登入問題
        # 這樣可以避免完全中斷搶票流程

    # FunOne cookie injection (same pattern as TixCraft/iBon)
    if 'tickets.funone.io' in homepage:
        funone_cookie = config_dict["accounts"].get("funone_session_cookie", "").strip()
        if len(funone_cookie) > 1:
            debug.log(f"[FUNONE] Setting ticket_session cookie, length: {len(funone_cookie)}")

            try:
                # Set ticket_session cookie using CDP
                await tab.send(cdp.network.set_cookie(
                    name="ticket_session",
                    value=funone_cookie,
                    domain="tickets.funone.io",
                    path="/",
                    secure=False,
                    http_only=True
                ))

                debug.log("[FUNONE] ticket_session cookie set successfully")

                # Verify cookie was set
                updated_cookies = await driver.cookies.get_all()
                funone_cookies = [c for c in updated_cookies if c.name == 'ticket_session']
                if funone_cookies:
                    debug.log(f"[FUNONE] Verified cookie: domain={funone_cookies[0].domain}, value length={len(funone_cookies[0].value)}")

                    # Reload page to apply cookie
                    debug.log("[FUNONE] Reloading page to apply cookie...")
                    await guarded_reload(tab, reason="funone_cookie_session_apply", recovery=True)
                    await asyncio.sleep(1.5)
                else:
                    debug.log("[FUNONE] Warning: ticket_session cookie not found after setting")

            except Exception as e:
                debug.log(f"[FUNONE] Error setting cookie: {str(e)}")

    return tab

async def nodrver_block_urls(tab, config_dict):
    """
    Block unnecessary network requests for performance and privacy

    Strategy for Cityline:
    - Allow: others.min.js (required for buy button and _global_citylineWebBase)
    - Block: Analytics/tracking requests initiated by others.min.js

    Strategy for TicketPlus:
    - Allow: analytics beacons (Clarity, GA, GTM) so the server sees normal user behavior
    - Blocking these signals causes server-side detection (empty DOM / honeypot data)
    """
    # Determine target platform from homepage URL
    homepage = config_dict.get("homepage", "")
    is_ticketplus = 'ticketplus.com.tw' in homepage

    NETWORK_BLOCKED_URLS = [
        # General tracking and analytics
        # Note: TicketPlus-specific trackers (clarity.ms, google-analytics, googletagmanager)
        # are excluded below to avoid server-side bot detection via missing beacons
        '*.appier.net/*',
        '*.c.appier.net/*',
        # Appier-owned QGraph/AIQUA marketing automation (push, in-web campaigns, behavior tracking)
        # quantumgraph.com / qgr.ph = QGraph (acquired by Appier 2018, renamed AIQUA); aiqua.io = AIQUA web SDK
        '*api.quantumgraph.com/*',
        '*cdn.qgr.ph/*',
        '*.aiqua.io/*',
        '*.cloudfront.com/*',
        '*.doubleclick.net/*',  # Covers securepubads.g.doubleclick.net
        '*.lndata.com/*',
        '*.rollbar.com/*',
        '*.smartlook.com/*',
        '*anymind360.com/*',  # Block Anymind360 tracking (loaded by cityline others.min.js)
        '*cdn.cookielaw.org/*',
        '*e2elog.fetnet.net*',
        '*fundingchoicesmessages.google.com/*',

        # Facebook Pixel tracking (does not affect FB login)
        '*connect.facebook.net/*/fbevents.js',
        '*connect.facebook.net/signals/*',

        # Google tracking (broad patterns cover specific URLs)
        '*google-analytics.*',  # Covers www.google-analytics.com/analytics.js & collect
        '*googlesyndication.*',  # Covers pagead2.googlesyndication.com
        '*googletagmanager.*',  # Covers www.googletagmanager.com/gtag/js
        '*googletagservices.*',
        '*googleadservices.com/*',  # Ad conversion tracking
        '*adtrafficquality.google/*',  # Ad quality detection

        # GA4
        '*analytics.google.com/*',

        # Social media and video
        '*.twitter.com/i/*',
        '*platform.twitter.com/*',
        '*syndication.twitter.com/*',
        '*youtube.com/*',
        '*player.youku.*',
        '*play.google.com/*',

        # Ad scripts
        '*/adblock.js',
        '*/google_ad_block.js',
        '*img.uniicreative.com/*',

        # Cityline: Allow others.min.js (required for buy button), block tracking only
        # '*cityline.com/js/others.min.js',  # DISABLED: Required for buy button rendering

        # Ticketmaster ad scripts
        '*ticketmaster.sg/js/adblock*',
        '*ticketmaster.sg/js/ads.js*',
        #'*ticketmaster.sg/epsf/asset/eps.js*',
        #'*ticketmaster.sg/epsf/asset/eps-gec.js',
        #'*ticketmaster.sg/epsf/asset/eps-mgr',
        '*ticketmaster.com/js/ads.js*',
        '*ticketmaster.com/epsf/asset/eps.js*',

        # Cloudflare analytics (KKTIX)
        '*static.cloudflareinsights.com/*',

        # Chat widgets (not needed for ticket purchase)
        '*chat.botbonnie.com/*',
        '*asset.botbonnie.com/*',
        '*web-chat-service.project.imbee.io/*',
        '*web-chat-assets.imbee.io/*',

        # Cookie consent geolocation
        '*geolocation.onetrust.com/*',
    ]

    # Block session-recording trackers for non-TicketPlus platforms only.
    # These tools (Clarity, Hotjar) record mouse trails / clicks / scroll heatmaps,
    # which can fingerprint automated cursor behavior. TicketPlus is excluded because
    # its server-side detection requires real Clarity beacons (blocking yields honeypot data).
    # KKTIX loads both Clarity and Hotjar (see anti-detection-audit T8).
    if not is_ticketplus:
        NETWORK_BLOCKED_URLS.append('*.clarity.ms/*')
        NETWORK_BLOCKED_URLS.append('*.hotjar.com/*')
        NETWORK_BLOCKED_URLS.append('*.hotjar.io/*')

    if config_dict["advanced"]["hide_some_image"]:
        NETWORK_BLOCKED_URLS.append('*.woff')
        NETWORK_BLOCKED_URLS.append('*.woff2')
        NETWORK_BLOCKED_URLS.append('*.ttf')
        NETWORK_BLOCKED_URLS.append('*.otf')
        NETWORK_BLOCKED_URLS.append('*fonts.googleapis.com/earlyaccess/*')
        NETWORK_BLOCKED_URLS.append('*/ajax/libs/font-awesome/*')
        NETWORK_BLOCKED_URLS.append('*.ico')
        NETWORK_BLOCKED_URLS.append('*ticketimg2.azureedge.net/image/ActivityImage/*')
        NETWORK_BLOCKED_URLS.append('*static.tixcraft.com/images/activity/*')
        NETWORK_BLOCKED_URLS.append('*static.ticketmaster.sg/images/activity/*')
        NETWORK_BLOCKED_URLS.append('*static.ticketmaster.com/images/activity/*')
        NETWORK_BLOCKED_URLS.append('*ticketimg2.azureedge.net/image/ActivityImage/ActivityImage_*')
        NETWORK_BLOCKED_URLS.append('*.azureedge.net/QWARE_TICKET//images/*')
        # Removed: '*static.ticketplus.com.tw/event/*' was blocking Vue JS/API resources needed for SPA rendering

        #NETWORK_BLOCKED_URLS.append('https://kktix.cc/change_locale?locale=*')
        NETWORK_BLOCKED_URLS.append('https://t.kfs.io/assets/logo_*.png')
        NETWORK_BLOCKED_URLS.append('https://t.kfs.io/assets/icon-*.png')
        NETWORK_BLOCKED_URLS.append('https://t.kfs.io/upload_images/*.jpg')

    if config_dict["advanced"]["block_facebook_network"]:
        NETWORK_BLOCKED_URLS.append('*facebook.com/*')
        NETWORK_BLOCKED_URLS.append('*.fbcdn.net/*')

    try:
        await tab.send(cdp.network.enable())
        # Block unnecessary network requests for performance optimization
        await tab.send(cdp.network.set_blocked_ur_ls(urls=NETWORK_BLOCKED_URLS))
    except Exception as exc:
        print(f"Warning: Failed to enable network blocking: {exc}")
        # Continue without network blocking if it fails

    # TicketPlus: clarity.ms is unblocked above; stub injection no longer needed
    # if is_ticketplus:
    #     await _inject_clarity_stub_for_ticketplus(tab)

    return tab


async def _inject_clarity_stub_for_ticketplus(tab):
    """
    Simulate Microsoft Clarity presence for TicketPlus without loading the real script.
    Injects a window.clarity queue stub so the Clarity tag script proceeds normally.
    Do NOT set .v or .t: tag script checks 'if (clarity.v || clarity.t) return early',
    so setting .v would prevent clarity.js from loading and no beacon would be sent.
    """
    # window.clarity stub: queue function only, lets tag script load clarity.js and send beacons
    clarity_stub_js = """(function() {
        if (typeof window.clarity === 'undefined') {
            var _cq = [];
            window.clarity = function() { _cq.push(arguments); };
            window.clarity.q = _cq;
        }
    })();"""
    try:
        await tab.send(cdp.page.add_script_to_evaluate_on_new_document(source=clarity_stub_js))
    except Exception as exc:
        print(f"[TicketPlus] Warning: Failed to inject Clarity stub: {exc}")



def parse_refresh_datetime(target_str):
    # Parse refresh_datetime setting to a datetime object.
    # Supports: "YYYY/MM/DD HH:MM:SS(.SSS)" | "HH:MM:SS(.SSS)" (today) | "" (disabled)
    # Returns: datetime or None
    if not target_str or not target_str.strip():
        return None
    target_str = target_str.strip()
    target_dt = parse_refresh_datetime_value(target_str)
    if target_dt is not None:
        return target_dt
    try:
        # Stage 0: legacy HH:MM:SS format, treat as today.
        today = datetime.now().date()
        for fmt in ('%H:%M:%S.%f', '%H:%M:%S'):
            try:
                t = datetime.strptime(target_str, fmt).time()
                return datetime.combine(today, t)
            except ValueError:
                pass
    except ValueError:
        pass
    return None


def _should_suppress_target_boundary_action(current_url: str) -> bool:
    url = (current_url or "").lower()
    confirmed_path_markers = (
        "/confirm",
        "/checkout",
        "/payment",
        "/order",
        "/cart",
        "/ticket/verify",
        "/booking",
    )
    return any(marker in url for marker in confirmed_path_markers)


def _is_tixcraft_family_url(current_url) -> bool:
    try:
        host = (urllib.parse.urlsplit(str(current_url or "")).hostname or "").lower()
    except ValueError:
        return False
    labels = host.split(".")
    return (
        host == "tixcraft.com"
        or host.endswith(".tixcraft.com")
        or host == "indievox.com"
        or host.endswith(".indievox.com")
        or "ticketmaster" in labels
    )


def _is_tixcraft_dom_url(current_url) -> bool:
    try:
        host = (urllib.parse.urlsplit(str(current_url or "")).hostname or "").lower()
    except ValueError:
        return False
    return (
        host == "tixcraft.com"
        or host.endswith(".tixcraft.com")
        or host == "indievox.com"
        or host.endswith(".indievox.com")
    )


def _get_trigger_runtime_state(current_url: str):
    """Expose only the live TixCraft state needed by the refresh arbiter."""

    if not _is_tixcraft_family_url(current_url):
        return {}
    state = getattr(tixcraft_platform, "_state", None)
    return state if isinstance(state, dict) else {}


def _get_runtime_ntp_coordinator(
    state,
    controller,
) -> RuntimeNtpCalibrationCoordinator:
    coordinator = state.get("runtime_ntp_coordinator")
    if not isinstance(coordinator, RuntimeNtpCalibrationCoordinator):
        coordinator = RuntimeNtpCalibrationCoordinator(clock=controller.clock)
        state["runtime_ntp_coordinator"] = coordinator
    return coordinator


def _close_runtime_ntp_coordinator(state) -> None:
    coordinator = state.get("runtime_ntp_coordinator")
    if isinstance(coordinator, RuntimeNtpCalibrationCoordinator):
        coordinator.close()


def _get_runtime_refresh_calibration_config(
    config_dict,
    state,
    controller,
    current_str,
    target_dt,
):
    """Poll runtime NTP state and return an ephemeral timing config."""

    now_wall = datetime.fromtimestamp(
        controller.clock.wall_time_ns() / 1_000_000_000
    )
    remaining_seconds = (
        (target_dt - now_wall).total_seconds()
        if target_dt is not None
        else float("inf")
    )
    raw_time_config = config_dict.get("time_calibration")
    if not isinstance(raw_time_config, dict):
        # Production settings migration always supplies this section. Treat a
        # missing section as an explicit local-clock fallback so tests, legacy
        # hand-written configs, and partial profiles never start hidden I/O.
        raw_time_config = {"mode": "system"}
    coordinator = _get_runtime_ntp_coordinator(state, controller)
    runtime_calibration = coordinator.tick(
        raw_time_config,
        target_identity=current_str if target_dt is not None else "",
        target_remaining_seconds=remaining_seconds,
    )
    state["runtime_clock_calibration"] = runtime_calibration
    state["runtime_clock_calibration_status"] = coordinator.status
    state["runtime_clock_calibration_generation"] = coordinator.generation
    if runtime_calibration is None:
        return config_dict

    timing_config = dict(config_dict)
    raw_refresh_calibration = config_dict.get("refresh_calibration")
    merged_calibration = (
        dict(raw_refresh_calibration)
        if isinstance(raw_refresh_calibration, dict)
        else {}
    )
    # Runtime NTP replaces only reference-clock fields. It never persists and
    # never contributes HTTP/renderer/RTT delay to the trigger.
    merged_calibration.update(runtime_calibration)
    timing_config["refresh_calibration"] = merged_calibration
    return timing_config


def _clear_runtime_recovery_scan_guard(runtime_state) -> None:
    runtime_state["soft_block_recovery_scan_pending"] = False
    runtime_state["soft_block_recovery_landing_url"] = ""
    runtime_state["soft_block_recovery_scan_deadline"] = 0.0


def _maintain_tixcraft_refresh_runtime(
    tab,
    current_url,
    config_dict,
    *,
    now_monotonic=None,
):
    """Expire stale coordination guards while refresh_datetime owns dispatch.

    This function performs state-only maintenance. It never scans inventory,
    clicks a date/area, submits a form, navigates, or reloads.
    """

    if not _is_tixcraft_family_url(current_url):
        return ()
    runtime_state = _get_trigger_runtime_state(current_url)
    if not runtime_state:
        return ()
    try:
        now = (
            float(now_monotonic)
            if now_monotonic is not None
            else time.monotonic()
        )
    except (TypeError, ValueError):
        now = time.monotonic()
    if not math.isfinite(now):
        now = time.monotonic()

    events = []
    scheduler = runtime_state.get("leak_scheduler")
    if scheduler is not None:
        try:
            events.extend(
                scheduler.maintenance(
                    config_dict,
                    current_url,
                    now=now,
                )
            )
        except Exception as exc:
            runtime_health.runtime_log(
                "[REFRESH] scheduler_maintenance_error",
                config_dict,
                error_type=type(exc).__name__,
            )

    pending_type = getattr(
        tixcraft_platform,
        "TixCraftPendingNavigation",
        (),
    )
    current_route = _refresh_route_key(current_url)
    current_page = classify_page(current_url)
    current_event_id = str(runtime_state.get("current_event_id", "") or "")
    current_game_id = str(runtime_state.get("current_game_id", "") or "")
    current_flow_generation = int(
        runtime_state.get("notification_flow_generation", 0) or 0
    )

    for kind in ("date", "area"):
        key = f"pending_{kind}_navigation"
        pending = runtime_state.get(key)
        if pending is None:
            continue
        is_valid_pending = bool(pending_type) and isinstance(
            pending,
            pending_type,
        )
        if not is_valid_pending:
            runtime_state.pop(key, None)
            events.append(f"pending_{kind}_invalid")
            if kind == "area" and scheduler is not None:
                scheduler.clear_area_click_pending()
            continue

        source_route = _refresh_route_key(
            getattr(pending, "source_url", "")
        )
        pending_event_id = str(getattr(pending, "event_id", "") or "")
        pending_game_id = str(getattr(pending, "game_id", "") or "")
        pending_flow_generation = int(
            getattr(pending, "flow_generation", 0) or 0
        )
        tab_identity = int(getattr(pending, "tab_identity", 0) or 0)
        deadline = float(getattr(pending, "deadline", 0.0) or 0.0)
        identity_changed = (
            (tab_identity and tab_identity != id(tab))
            or (
                pending_event_id
                and current_event_id
                and pending_event_id != current_event_id
            )
            or (
                pending_game_id
                and current_game_id
                and pending_game_id != current_game_id
            )
            or (
                pending_flow_generation
                and current_flow_generation
                and pending_flow_generation != current_flow_generation
            )
        )
        route_changed = bool(
            source_route and current_route != source_route
        )
        if kind == "date":
            allowed_pages = getattr(
                tixcraft_platform,
                "_TIXCRAFT_CONFIRMED_DATE_TARGET_PAGES",
                {
                    PageClass.AREA,
                    PageClass.TICKET,
                    PageClass.ORDER,
                    PageClass.CHECKOUT,
                    PageClass.PAYMENT,
                },
            )
        else:
            allowed_pages = getattr(
                tixcraft_platform,
                "_TIXCRAFT_CONFIRMED_PURCHASE_PAGES",
                {
                    PageClass.TICKET,
                    PageClass.ORDER,
                    PageClass.CHECKOUT,
                    PageClass.PAYMENT,
                },
            )
        confirmed_transition = (
            route_changed
            and not identity_changed
            and current_page in allowed_pages
        )
        context_changed = identity_changed or (
            route_changed and not confirmed_transition
        )
        expired = (
            not confirmed_transition
            and (
                not math.isfinite(deadline)
                or deadline <= 0
                or now >= deadline
            )
        )
        if not context_changed and not expired:
            continue

        runtime_state.pop(key, None)
        event_suffix = "context_changed" if context_changed else "expired"
        events.append(f"pending_{kind}_{event_suffix}")
        if kind == "area" and scheduler is not None:
            scheduler.clear_area_click_pending()

    if runtime_state.get("soft_block_recovery_scan_pending", False):
        try:
            recovery_deadline = float(
                runtime_state.get(
                    "soft_block_recovery_scan_deadline",
                    0.0,
                )
                or 0.0
            )
        except (TypeError, ValueError):
            recovery_deadline = 0.0
        recovery_route = _refresh_route_key(
            runtime_state.get("soft_block_recovery_landing_url", "")
        )
        recovery_context_changed = bool(
            recovery_route and recovery_route != current_route
        )
        recovery_expired = (
            not math.isfinite(recovery_deadline)
            or recovery_deadline <= 0
            or now >= recovery_deadline
        )
        if recovery_context_changed or recovery_expired:
            _clear_runtime_recovery_scan_guard(runtime_state)
            events.append(
                "recovery_scan_context_changed"
                if recovery_context_changed
                else "recovery_scan_expired"
            )

    for event in events:
        runtime_health.runtime_log(
            "[REFRESH] stale_runtime_guard_pruned",
            config_dict,
            reason=event,
            current_url=current_url,
        )
    return tuple(events)


def _mark_tixcraft_scheduled_reload_landed(
    current_url,
    config_dict,
    *,
    now_monotonic=None,
) -> None:
    """Advance leak/area cooldown after the refresh coordinator reloads."""

    runtime_state = _get_trigger_runtime_state(current_url)
    if not runtime_state:
        return
    try:
        landed_at = (
            float(now_monotonic)
            if now_monotonic is not None
            else time.monotonic()
        )
    except (TypeError, ValueError):
        landed_at = time.monotonic()
    if not math.isfinite(landed_at):
        landed_at = time.monotonic()

    # A reload creates a new document. Any fast-negative health cache belongs
    # to the old document and could otherwise hide EPS/white-screen state for
    # the first 250 ms after the scheduled reload.
    runtime_state["soft_block_known_good_url"] = ""
    runtime_state["soft_block_known_good_at"] = 0.0
    runtime_state["soft_block_blank_since"] = 0.0
    runtime_state["soft_block_blank_url"] = ""
    runtime_state["soft_block_probe_failure_since"] = 0.0
    runtime_state["soft_block_probe_failure_url"] = ""
    runtime_state["soft_block_probe_failure_count"] = 0

    scheduler = runtime_state.get("leak_scheduler")
    if scheduler is not None:
        try:
            scheduler.mark_recovery_landed(
                config_dict,
                now=landed_at,
            )
        except Exception as exc:
            runtime_health.runtime_log(
                "[REFRESH] scheduler_cooldown_error",
                config_dict,
                error_type=type(exc).__name__,
            )
    runtime_state["tixcraft_area_reload_url"] = current_url
    if scheduler is not None:
        runtime_state["tixcraft_area_reload_next_at"] = getattr(
            scheduler,
            "next_cycle_at",
            landed_at,
        )


def _refresh_soft_block_probe_token(state, current_url, boundary_name):
    controller = state["controller"]
    return (
        state.get("state_key", ""),
        controller.generation,
        _refresh_route_key(current_url),
        boundary_name,
    )


def _invalidate_refresh_gate_health_evidence(state) -> None:
    """Discard page-health evidence whenever a reload replaces the document."""

    state["refresh_soft_block_preflight_token"] = None
    state["refresh_soft_block_preflight_reason"] = ""
    state["refresh_gate_health_next_probe_at"] = 0.0
    state["refresh_gate_health_ready_route"] = ""
    state["refresh_gate_health_ready_at"] = 0.0


async def _preflight_tixcraft_refresh_boundary(
    tab,
    current_url,
    config_dict,
    state,
    boundary_name,
) -> TriggerReloadDecision | None:
    """Run one bounded page-health read before a TixCraft boundary reload."""

    if (
        not _is_tixcraft_family_url(current_url)
        or classify_page(current_url)
        not in {PageClass.ACTIVITY, PageClass.DATE, PageClass.AREA}
    ):
        return None
    token = _refresh_soft_block_probe_token(
        state,
        current_url,
        boundary_name,
    )
    cached_health_route = state.get("refresh_gate_health_ready_route", "")
    cached_health_at = float(
        state.get("refresh_gate_health_ready_at", 0.0) or 0.0
    )
    if (
        cached_health_route == _refresh_route_key(current_url)
        and time.monotonic() - cached_health_at
        <= REFRESH_GATE_HEALTH_CACHE_SECONDS
    ):
        state["refresh_soft_block_preflight_token"] = token
        state["refresh_soft_block_preflight_reason"] = "ready"
        return None
    if token == state.get("refresh_soft_block_preflight_token"):
        cached_reason = state.get("refresh_soft_block_preflight_reason", "")
        if cached_reason == "order_processing_detected":
            return TriggerReloadDecision(
                attempted=False,
                reloaded=False,
                reason=cached_reason,
                page_class=classify_page(current_url),
            )
        # Ready evidence has a short TTL and inconclusive/blocked evidence must
        # be re-read after recovery; neither may be cached for the whole trigger.

    state["refresh_soft_block_preflight_token"] = token
    reason = "ready"
    try:
        snapshot = await tixcraft_platform._read_tixcraft_page_health(
            tab,
            config_dict,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        snapshot = {"probeFailed": True}

    if not isinstance(snapshot, dict) or snapshot.get("probeFailed", False):
        reason = "health_probe_unavailable"
    elif snapshot.get("blocked", False):
        reason = "soft_block_detected"
    elif snapshot.get("knownOrderProcessing", False):
        reason = "order_processing_detected"
    elif snapshot.get("whiteOverlay", False):
        reason = "soft_block_detected"
    else:
        page_text = (
            f"{snapshot.get('title', '')}\n"
            f"{snapshot.get('bodyText', '')}"
        )
        if tixcraft_platform._is_tixcraft_soft_block_text(page_text):
            reason = "soft_block_detected"
        elif tixcraft_platform._is_tixcraft_blank_page_snapshot(snapshot):
            reason = "soft_block_detected"

    state["refresh_soft_block_preflight_reason"] = reason
    state["refresh_soft_block_preflight_snapshot_kind"] = str(
        snapshot.get("kind", "") if isinstance(snapshot, dict) else ""
    )
    if reason == "ready":
        return None
    return TriggerReloadDecision(
        attempted=False,
        reloaded=False,
        reason=reason,
        page_class=classify_page(current_url),
    )


async def _run_refresh_gate_health_watchdog(
    tab,
    current_url,
    config_dict,
    state,
    remaining_seconds,
) -> bool:
    """Keep TixCraft soft-block recovery alive while normal dispatch is gated."""

    if (
        not _is_tixcraft_family_url(current_url)
        or classify_page(current_url)
        not in {PageClass.ACTIVITY, PageClass.DATE, PageClass.AREA}
    ):
        state["refresh_gate_health_ready_route"] = ""
        state["refresh_gate_health_ready_at"] = 0.0
        return False

    now = time.monotonic()
    if remaining_seconds <= REFRESH_GATE_HEALTH_NEAR_BOUNDARY_SECONDS:
        interval = 0.0
    elif remaining_seconds <= 60.0:
        interval = 1.0
    else:
        interval = 5.0
    next_probe_at = float(
        state.get("refresh_gate_health_next_probe_at", 0.0) or 0.0
    )
    if interval > 0 and now < next_probe_at:
        return False

    state["refresh_gate_health_next_probe_at"] = now + max(0.5, interval)
    detection = await tixcraft_platform._detect_tixcraft_soft_block(
        tab,
        current_url,
        config_dict,
    )
    blocked = bool(detection.get("blocked", False))
    if blocked:
        state["refresh_recovery_dispatch_required"] = True
        state["last_refresh_reload_decision"] = "soft_block_detected"
    platform_state = getattr(tixcraft_platform, "_state", {})
    route_key = _refresh_route_key(current_url)
    health_confirmed = bool(
        not blocked
        and platform_state.get("soft_block_known_good_url") == route_key
        and float(platform_state.get("soft_block_known_good_at", 0.0) or 0.0)
        > 0
    )
    if health_confirmed:
        state["refresh_gate_health_ready_route"] = route_key
        state["refresh_gate_health_ready_at"] = time.monotonic()
    else:
        state["refresh_gate_health_ready_route"] = ""
        state["refresh_gate_health_ready_at"] = 0.0
    return bool(blocked)


async def _defer_refresh_trigger_for_page_recovery(
    tab,
    current_url,
    config_dict,
    state,
    controller,
    decision,
) -> None:
    """Preserve the one-shot trigger until health recovery can be confirmed."""

    now_ns = controller.clock.monotonic_ns()
    state["refresh_recovery_dispatch_required"] = True
    state["refresh_soft_block_preflight_token"] = None
    state["refresh_soft_block_preflight_reason"] = ""
    state["refresh_gate_health_ready_route"] = ""
    state["refresh_gate_health_ready_at"] = 0.0
    state["refresh_retry_pending"] = True
    state["refresh_retry_not_before_monotonic_ns"] = (
        now_ns + REFRESH_PREFLIGHT_RECOVERY_RETRY_DELAY_NS
    )
    # The soft-block handler may legitimately spend the configured wait time
    # recovering. Start a fresh bounded reload window after it returns.
    state["refresh_retry_deadline_monotonic_ns"] = (
        now_ns + REFRESH_TRIGGER_RETRY_WINDOW_NS
    )
    state["last_refresh_reload_decision"] = decision.reason


def _get_trigger_arbiter(state) -> TriggerReloadArbiter:
    arbiter = state.get("trigger_arbiter")
    if not isinstance(arbiter, TriggerReloadArbiter):
        arbiter = TriggerReloadArbiter()
        state["trigger_arbiter"] = arbiter
    return arbiter


def _should_prefer_cached_refresh_url(config_dict, state) -> bool:
    if not str(config_dict.get("refresh_datetime", "") or "").strip():
        return False
    if state.get("post_boundary_retry_pending", False):
        return True
    if state.get("reached", False):
        return False
    controller = state.get("controller")
    if (
        isinstance(controller, RefreshTriggerController)
        and controller.trigger_deadline_monotonic_ns is not None
    ):
        return controller.remaining_ns() <= REFRESH_CACHED_URL_FAST_PATH_WINDOW_NS
    target_dt = parse_refresh_datetime(config_dict.get("refresh_datetime", ""))
    if target_dt is None:
        return False
    remaining_ns = int(
        (target_dt.timestamp() * 1_000_000_000) - time.time_ns()
    )
    return remaining_ns <= REFRESH_CACHED_URL_FAST_PATH_WINDOW_NS


def _reset_refresh_trigger_retry(state) -> None:
    state["refresh_retry_pending"] = False
    state["refresh_reload_attempts"] = 0
    state["refresh_retry_deadline_monotonic_ns"] = None
    state["refresh_retry_not_before_monotonic_ns"] = 0
    state["post_boundary_retry_pending"] = False
    state["post_boundary_retry_route"] = ""
    state["post_boundary_retry_not_before_monotonic_ns"] = 0
    state["post_boundary_retry_deadline_monotonic_ns"] = 0


def _refresh_route_key(url) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not host:
        return ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    path = (parsed.path or "/").rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{host}{port}{path}"


def _arm_stale_response_retry(tab, state, controller, current_url) -> None:
    cached_url = ""
    try:
        cached_url = getattr(getattr(tab, "target", None), "url", "") or ""
    except Exception:
        cached_url = ""
    effective_url = cached_url or current_url
    if not _is_tixcraft_dom_url(effective_url):
        return
    if classify_page(effective_url) not in {
        PageClass.ACTIVITY,
        PageClass.DATE,
        PageClass.AREA,
    }:
        return
    route = _refresh_route_key(effective_url)
    if not route:
        return
    now_ns = controller.clock.monotonic_ns()
    boundary_ns = controller.trigger_deadline_monotonic_ns or now_ns
    state["post_boundary_retry_pending"] = True
    state["post_boundary_retry_route"] = route
    state["post_boundary_retry_not_before_monotonic_ns"] = (
        now_ns + REFRESH_STALE_RESPONSE_RETRY_DELAY_NS
    )
    state["post_boundary_retry_deadline_monotonic_ns"] = (
        boundary_ns + REFRESH_TRIGGER_RETRY_WINDOW_NS
    )


async def _read_tixcraft_sale_dom_ready(tab, current_url, config_dict):
    """Return True/False only when a bounded DOM probe is conclusive.

    ``None`` is fail-safe: an unavailable JS context must not be interpreted as
    a stale response and trigger another request.
    """

    if not _is_tixcraft_dom_url(current_url):
        return None
    page_class = classify_page(current_url)
    selectors = {
        PageClass.ACTIVITY: 'a[href*="/activity/game/"]',
        PageClass.DATE: (
            '#gameList button[data-href], '
            '#gameList a[href*="/ticket/area/"]'
        ),
        PageClass.AREA: ".zone",
    }
    selector = selectors.get(page_class)
    if not selector:
        return None
    script = (
        "(() => {"
        "const ready = document.readyState === 'interactive' "
        "|| document.readyState === 'complete';"
        "if (!ready) return null;"
        "const body = document.body;"
        "if (!body) return null;"
        "const knownShell = body.querySelector("
        "'.activity-info, .game-title, #gameList, .zone, #TicketForm'"
        ");"
        "const bodyText = (body.innerText || '').replace(/\\s+/g, '');"
        "const elementCount = body.querySelectorAll('*').length;"
        "if (!knownShell && bodyText.length <= 8 && elementCount <= 25) "
        "return null;"
        f"return document.querySelector({json.dumps(selector)}) !== null;"
        "})()"
    )
    try:
        result = await runtime_health.evaluate_with_timeout(
            tab,
            script,
            config_dict,
            timeout_seconds=0.35,
            reason="REFRESH_STALE_DOM_EVIDENCE",
            default=None,
            log_success=False,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        return None
    try:
        parsed = util.parse_nodriver_result(result)
    except Exception:
        parsed = result
    return parsed if isinstance(parsed, bool) else None


async def _request_refresh_datetime_reload(
    tab,
    config_dict,
    state,
    current_url,
    reason,
    expected_route="",
) -> TriggerReloadDecision:
    controller = state["controller"]
    decision = await _get_trigger_arbiter(state).request_reload(
        tab,
        current_url=current_url,
        runtime_state=_get_trigger_runtime_state(current_url),
        reason=reason,
        reload_callable=guarded_reload,
        config_dict=config_dict,
        now_monotonic=controller.clock.monotonic_ns() / 1_000_000_000,
        expected_route=expected_route,
    )
    if decision.reloaded:
        _invalidate_refresh_gate_health_evidence(state)
        _mark_tixcraft_scheduled_reload_landed(
            current_url,
            config_dict,
            now_monotonic=time.monotonic(),
        )
    return decision


def _finish_refresh_trigger_without_reload(state, controller, decision) -> None:
    state["reached"] = True
    state["refresh_retry_pending"] = False
    state["last_refresh_reload_decision"] = decision.reason
    controller.mark_trigger_failed()


async def _execute_refresh_trigger_reload(
    tab,
    config_dict,
    state,
    current_url,
    controller,
    reason,
) -> TriggerReloadDecision:
    preflight = await _preflight_tixcraft_refresh_boundary(
        tab,
        current_url,
        config_dict,
        state,
        "initial_trigger",
    )
    if preflight is not None:
        if preflight.reason in {
            "soft_block_detected",
            "health_probe_unavailable",
        }:
            await _defer_refresh_trigger_for_page_recovery(
                tab,
                current_url,
                config_dict,
                state,
                controller,
                preflight,
            )
            return preflight
        _finish_refresh_trigger_without_reload(
            state,
            controller,
            preflight,
        )
        return preflight

    now_ns = controller.clock.monotonic_ns()
    retry_deadline_ns = state.get("refresh_retry_deadline_monotonic_ns")
    if retry_deadline_ns is None:
        boundary_ns = controller.trigger_deadline_monotonic_ns or now_ns
        retry_deadline_ns = boundary_ns + REFRESH_TRIGGER_RETRY_WINDOW_NS
        state["refresh_retry_deadline_monotonic_ns"] = retry_deadline_ns

    decision = await _request_refresh_datetime_reload(
        tab,
        config_dict,
        state,
        current_url,
        reason,
    )
    state["last_refresh_reload_decision"] = decision.reason
    if decision.attempted:
        state["refresh_reload_attempts"] = int(
            state.get("refresh_reload_attempts", 0) or 0
        ) + 1

    if decision.reloaded:
        state["reached"] = True
        state["refresh_retry_pending"] = False
        controller.mark_post_trigger_reload()
        if reason == "refresh_datetime_trigger":
            _arm_stale_response_retry(
                tab,
                state,
                controller,
                current_url,
            )
        return decision

    decision_time_ns = controller.clock.monotonic_ns()
    attempts = int(state.get("refresh_reload_attempts", 0) or 0)
    still_in_window = decision_time_ns < int(retry_deadline_ns)
    can_retry = (
        decision.reason in REFRESH_TRIGGER_RETRYABLE_REASONS
        and attempts < REFRESH_TRIGGER_MAX_ATTEMPTS
        and still_in_window
    )
    if can_retry:
        state["refresh_retry_pending"] = True
        state["refresh_retry_not_before_monotonic_ns"] = (
            decision_time_ns + REFRESH_TRIGGER_RETRY_DELAY_NS
        )
    else:
        _finish_refresh_trigger_without_reload(state, controller, decision)
    return decision


async def check_refresh_datetime_gate(tab, config_dict, state, current_url=""):
    # Gate platform dispatching until refresh_datetime is reached.
    # Returns True if gate is active (caller should continue),
    # False if gate is cleared (proceed with dispatching).
    current_str = config_dict.get("refresh_datetime", "")
    controller = state.get("controller")
    if controller is None:
        controller = RefreshTriggerController()
        state["controller"] = controller
    target_dt = parse_refresh_datetime(current_str)
    timing_config = _get_runtime_refresh_calibration_config(
        config_dict,
        state,
        controller,
        current_str,
        target_dt,
    )
    calibration, timing_decision = get_effective_refresh_calibration(
        timing_config,
        current_url,
    )
    state_key = (
        current_str
        + "|"
        + json.dumps(calibration, sort_keys=True)
        + "|"
        + json.dumps(
            timing_decision.to_dict(),
            sort_keys=True,
        )
    )

    _maintain_tixcraft_refresh_runtime(
        tab,
        current_url,
        config_dict,
        now_monotonic=time.monotonic(),
    )

    # Detect config change: reset state if user changed the value or delay-calibration offsets.
    if state_key != state.get("state_key", "") and controller.phase not in (
        TriggerPhase.FROZEN,
        TriggerPhase.TRIGGERED,
    ):
        state["state_key"] = state_key
        state["target_str"] = current_str
        state["reached"] = False
        state["last_countdown_print"] = 0
        state["reported_plan"] = False
        state["reported_platform_timing"] = False
        state["target_boundary_done"] = False
        state["target_boundary_deadline_monotonic_ns"] = None
        state["refresh_soft_block_preflight_token"] = None
        state["refresh_soft_block_preflight_reason"] = ""
        state["refresh_gate_health_next_probe_at"] = 0.0
        state["refresh_gate_health_ready_route"] = ""
        state["refresh_gate_health_ready_at"] = 0.0
        _reset_refresh_trigger_retry(state)

    if not state.get("reported_platform_timing", False):
        for warning in timing_decision.warnings:
            print(f"[REFRESH] {warning}")
        state["reported_platform_timing"] = True

    post_boundary_retry_reloaded = False
    if state.get("post_boundary_retry_pending", False):
        now_ns = controller.clock.monotonic_ns()
        retry_deadline_ns = int(
            state.get("post_boundary_retry_deadline_monotonic_ns", 0) or 0
        )
        current_route = _refresh_route_key(current_url)
        expected_route = state.get("post_boundary_retry_route", "")
        if now_ns >= retry_deadline_ns or current_route != expected_route:
            state["post_boundary_retry_pending"] = False
        else:
            retry_not_before_ns = int(
                state.get(
                    "post_boundary_retry_not_before_monotonic_ns",
                    0,
                )
                or 0
            )
            if now_ns >= retry_not_before_ns:
                # Clear before awaiting so cancellation/re-entry cannot create
                # a third scheduled request.
                state["post_boundary_retry_pending"] = False
                arbiter = _get_trigger_arbiter(state)
                runtime_state = _get_trigger_runtime_state(current_url)
                preflight = arbiter.can_reload(
                    tab,
                    current_url=current_url,
                    runtime_state=runtime_state,
                    now_monotonic=now_ns / 1_000_000_000,
                )
                if preflight.reason != "ready":
                    print(
                        "[REFRESH] Bounded stale-response retry canceled:",
                        preflight.reason,
                    )
                else:
                    dom_ready = await _read_tixcraft_sale_dom_ready(
                        tab,
                        current_url,
                        config_dict,
                    )
                    if dom_ready is False:
                        try:
                            latest_url = (
                                getattr(
                                    getattr(tab, "target", None),
                                    "url",
                                    "",
                                )
                                or ""
                            )
                        except Exception:
                            latest_url = ""
                        if _refresh_route_key(latest_url) != expected_route:
                            print(
                                "[REFRESH] Bounded stale-response retry canceled: "
                                "route advanced during DOM probe"
                            )
                        else:
                            decision = await _preflight_tixcraft_refresh_boundary(
                                tab,
                                latest_url,
                                config_dict,
                                state,
                                "stale_response_retry",
                            )
                            if decision is None:
                                decision = await _request_refresh_datetime_reload(
                                    tab,
                                    config_dict,
                                    state,
                                    latest_url,
                                    "refresh_datetime_stale_response_retry",
                                    expected_route=expected_route,
                                )
                            state["last_refresh_reload_decision"] = decision.reason
                            if decision.reloaded:
                                post_boundary_retry_reloaded = True
                                print("[REFRESH] Bounded stale-response retry completed.")
                            else:
                                print(
                                    "[REFRESH] Bounded stale-response retry skipped:",
                                    decision.reason,
                                )
                    elif dom_ready is True:
                        print(
                            "[REFRESH] Bounded stale-response retry canceled: "
                            "sale DOM is ready"
                        )
                    else:
                        print(
                            "[REFRESH] Bounded stale-response retry canceled: "
                            "DOM evidence unavailable"
                        )

    if post_boundary_retry_reloaded:
        return True

    # If already reached, no gating.
    if state.get("reached", False):
        return False

    if target_dt is None:
        # Empty or invalid = no waiting
        state["reached"] = True
        return False

    now = datetime.fromtimestamp(controller.clock.wall_time_ns() / 1_000_000_000)
    was_armed = (
        controller.state_key == state_key
        and controller.plan is not None
        and controller.trigger_deadline_monotonic_ns is not None
    )
    plan = controller.arm(target_dt, calibration, state_key)

    trusted_clock_plan = plan.confidence in {"high", "medium"}
    if calibration.get("enable", False) or trusted_clock_plan:
        freeze_seconds = calibration.get(
            "freeze_before_seconds",
            RUNTIME_NTP_CRITICAL_WINDOW_SECONDS,
        )
        if trusted_clock_plan:
            freeze_seconds = max(
                RUNTIME_NTP_CRITICAL_WINDOW_SECONDS,
                float(freeze_seconds),
            )
        if controller.maybe_freeze(target_dt, freeze_seconds):
            print(
                "[REFRESH-B] Freeze trigger:",
                plan.computed_trigger_display,
                "| target:",
                target_dt.strftime('%Y/%m/%d %H:%M:%S.%f')[:-3],
                "|",
                describe_refresh_calibration(calibration),
            )
        if controller.plan is not None:
            plan = controller.plan

    if controller.phase in {TriggerPhase.ARMED, TriggerPhase.FROZEN}:
        watchdog_remaining_seconds = max(
            0.0,
            controller.remaining_ns() / 1_000_000_000,
        )
        if await _run_refresh_gate_health_watchdog(
            tab,
            current_url,
            config_dict,
            state,
            watchdog_remaining_seconds,
        ):
            return True

    target_boundary_required = calibration.get("enable", False) and plan.local_trigger_time < target_dt
    if target_boundary_required and state.get("target_boundary_deadline_monotonic_ns") is None:
        state["target_boundary_deadline_monotonic_ns"] = wall_datetime_to_monotonic_deadline_ns(target_dt, controller.clock)

    if (
        controller.phase == TriggerPhase.POST_TRIGGER_RELOAD
        and target_boundary_required
        and not state.get("target_boundary_done", False)
    ):
        boundary_deadline_ns = state.get("target_boundary_deadline_monotonic_ns")
        if boundary_deadline_ns is not None and compute_remaining_ns(boundary_deadline_ns, controller.clock.monotonic_ns()) <= 0:
            state["target_boundary_done"] = True
            state["reached"] = True
            if _should_suppress_target_boundary_action(current_url):
                print(f"[REFRESH] Public-sale target boundary suppressed; workflow appears advanced: {current_url}")
            else:
                decision = await _preflight_tixcraft_refresh_boundary(
                    tab,
                    current_url,
                    config_dict,
                    state,
                    "public_sale_target",
                )
                if (
                    decision is not None
                    and decision.reason
                    in {"soft_block_detected", "health_probe_unavailable"}
                ):
                    state["target_boundary_done"] = False
                    state["reached"] = False
                    state["refresh_recovery_dispatch_required"] = True
                    state["refresh_soft_block_preflight_token"] = None
                    state["refresh_soft_block_preflight_reason"] = ""
                    return True
                if decision is None:
                    decision = await _request_refresh_datetime_reload(
                        tab,
                        config_dict,
                        state,
                        current_url,
                        "refresh_datetime_target_boundary",
                    )
                state["last_refresh_reload_decision"] = decision.reason
                if decision.reloaded:
                    print(
                        "[REFRESH] Public-sale target boundary reached:",
                        target_dt.strftime('%Y/%m/%d %H:%M:%S.%f')[:-3],
                    )
                    return True
                else:
                    print(
                        "[REFRESH] Target-boundary reload skipped:",
                        decision.reason,
                    )
        return False

    if controller.phase == TriggerPhase.POST_TRIGGER_RELOAD:
        return False

    if controller.phase == TriggerPhase.TRIGGERED:
        if not state.get("refresh_retry_pending", False):
            controller.mark_trigger_failed()
            state["reached"] = True
            return False
        now_ns = controller.clock.monotonic_ns()
        retry_deadline_ns = int(
            state.get("refresh_retry_deadline_monotonic_ns", 0) or 0
        )
        attempts = int(state.get("refresh_reload_attempts", 0) or 0)
        if (
            now_ns >= retry_deadline_ns
            or attempts >= REFRESH_TRIGGER_MAX_ATTEMPTS
        ):
            state["refresh_retry_pending"] = False
            state["reached"] = True
            controller.mark_trigger_failed()
            return False
        retry_not_before_ns = int(
            state.get("refresh_retry_not_before_monotonic_ns", 0) or 0
        )
        if now_ns < retry_not_before_ns:
            return True
        decision = await _execute_refresh_trigger_reload(
            tab,
            config_dict,
            state,
            current_url,
            controller,
            "refresh_datetime_trigger_retry",
        )
        if decision.reloaded:
            print(
                "[REFRESH] Trigger retry succeeded:",
                plan.computed_trigger_display,
            )
            return True
        elif state.get("refresh_retry_pending", False):
            print("[REFRESH] Trigger retry deferred:", decision.reason)
        else:
            print("[REFRESH] Trigger retry stopped:", decision.reason)
        return bool(state.get("refresh_retry_pending", False))

    deadline_ns = controller.trigger_deadline_monotonic_ns
    if (
        deadline_ns is not None
        and controller.clock.monotonic_ns() - deadline_ns
        > REFRESH_TRIGGER_MAX_LATENESS_NS
    ):
        # A machine can resume long after the sale boundary. Replaying an old
        # one-shot schedule then would be both surprising and an unnecessary
        # request burst, so expire it instead of treating it as on-time.
        state["reached"] = True
        _reset_refresh_trigger_retry(state)
        controller.phase = TriggerPhase.STOPPED
        return False

    # Already far past target: do not reload stale schedules repeatedly.
    if not was_armed and (now - target_dt).total_seconds() > 60:
        state["reached"] = True
        controller.phase = TriggerPhase.STOPPED
        return False

    # Reached the monotonic deadline derived from the reference wall target.
    if controller.should_trigger_once():
        decision = await _execute_refresh_trigger_reload(
            tab,
            config_dict,
            state,
            current_url,
            controller,
            "refresh_datetime_trigger",
        )
        if decision.reloaded:
            print(
                "[REFRESH] Trigger reached:",
                plan.computed_trigger_display,
                "| target:",
                target_dt.strftime('%Y/%m/%d %H:%M:%S.%f')[:-3],
                "|",
                describe_refresh_calibration(calibration),
            )
            return True
        elif state.get("refresh_retry_pending", False):
            print("[REFRESH] Trigger reload deferred for one safe retry:", decision.reason)
        else:
            print("[REFRESH] Trigger reload skipped:", decision.reason)
        return bool(state.get("refresh_retry_pending", False))

    # Before target time: gate is active
    remaining_ns = controller.remaining_ns()
    remaining = remaining_ns / 1_000_000_000
    target_remaining = max(0.0, (target_dt - now).total_seconds())

    # Countdown display
    now_mono = controller.clock.monotonic_ns() / 1_000_000_000
    should_print = False
    if remaining <= 60:
        if now_mono - state["last_countdown_print"] >= 1.0:
            should_print = True
    else:
        if now_mono - state["last_countdown_print"] >= 10.0:
            should_print = True

    if should_print:
        if remaining > 3600:
            hrs = int(remaining // 3600)
            mins = int((remaining % 3600) // 60)
            print(f"[WAIT] Target: {current_str} | Trigger remaining: {hrs}h {mins}m")
        elif remaining > 60:
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            print(f"[WAIT] Target: {current_str} | Trigger remaining: {mins}m {secs}s")
        else:
            print(
                f"[WAIT] Target: {current_str} | Trigger: {plan.computed_trigger_display} | "
                f"Trigger remaining: {format_remaining_seconds(remaining_ns)} | Target remaining: {target_remaining:.1f}s"
            )
        state["last_countdown_print"] = now_mono

    # Final 2-second approach: bounded monotonic scheduling until the computed trigger.
    if remaining <= 2.0:
        if controller.trigger_deadline_monotonic_ns is not None:
            await sleep_until_deadline(controller.trigger_deadline_monotonic_ns, controller.clock)
        if controller.should_trigger_once():
            decision = await _execute_refresh_trigger_reload(
                tab,
                config_dict,
                state,
                current_url,
                controller,
                "refresh_datetime_trigger",
            )
            if decision.reloaded:
                print(
                    "[REFRESH] Trigger reached:",
                    plan.computed_trigger_display,
                    "| target:",
                    target_dt.strftime('%Y/%m/%d %H:%M:%S.%f')[:-3],
                    "|",
                    describe_refresh_calibration(calibration),
                )
                return True
            elif state.get("refresh_retry_pending", False):
                print("[REFRESH] Trigger reload deferred for one safe retry:", decision.reason)
            else:
                print("[REFRESH] Trigger reload skipped:", decision.reason)
        return bool(state.get("refresh_retry_pending", False))

    return True

async def reload_config(config_dict, last_mtime, config_filepath):
    if not os.path.exists(config_filepath):
        return config_dict, last_mtime

    try:
        current_mtime = os.path.getmtime(config_filepath)
        if current_mtime > last_mtime:
            await asyncio.sleep(0.1)
            with open(config_filepath, 'r', encoding='utf-8') as json_data:
                new_config = json.load(json_data)
                new_config = settings.migrate_config(new_config)

                # Update fields
                fields = [
                    "ticket_number", "date_auto_select", "area_auto_select", "keyword_exclude",
                    "ocr_captcha", "tixcraft", "kktix", "cityline",
                    "refresh_datetime", "refresh_calibration", "time_calibration", "contact",
                    "date_auto_fallback", "area_auto_fallback"
                ]
                for field in fields:
                    if field in new_config:
                        config_dict[field] = new_config[field]

                if "advanced" in new_config:
                    if "advanced" not in config_dict:
                        config_dict["advanced"] = {}
                    adv_fields = [
                        "play_sound", "disable_adjacent_seat", "hide_some_image",
                        "auto_guess_options", "user_guess_string", "auto_reload_page_interval",
                        "run_mode", "leak_refresh_interval_seconds", "verbose",
                        "tixcraft_soft_block_delay",
                        "auto_reload_overheat_count", "auto_reload_overheat_cd",
                        "idle_keyword", "resume_keyword", "idle_keyword_second", "resume_keyword_second",
                        "discord_webhook_url", "telegram_bot_token", "telegram_chat_id",
                        "discount_code"
                    ]
                    for field in adv_fields:
                        if field in new_config["advanced"]:
                            config_dict["advanced"][field] = new_config["advanced"][field]

                print("Configuration reloaded from " + os.path.basename(config_filepath))
                return config_dict, current_mtime
    except Exception as e:
        print(f"[WARNING] Failed to reload config: {e}")

    return config_dict, last_mtime


def _is_safe_browser_recovery_url(url, configured_homepage=""):
    value = str(url or "").strip()
    if classify_page(value) not in BROWSER_CONNECTION_SAFE_RESTART_PAGES:
        return False
    try:
        target = urllib.parse.urlsplit(value)
        configured = urllib.parse.urlsplit(str(configured_homepage or "").strip())
    except ValueError:
        return False
    if target.scheme not in {"http", "https"} or not target.hostname:
        return False
    if configured.hostname and target.hostname != configured.hostname:
        return False
    return True


def _has_empty_url_watchdog_expired(
    last_url,
    empty_since_monotonic,
    now_monotonic,
):
    if not str(last_url or "").strip() or empty_since_monotonic <= 0:
        return False
    return (
        now_monotonic - empty_since_monotonic
        >= BROWSER_CONNECTION_EMPTY_URL_GRACE_SECONDS
    )


async def _navigate_browser_start(driver, config_dict, recovery_url=""):
    recovery_url = str(recovery_url or "").strip()
    if recovery_url:
        try:
            return await driver.get(recovery_url)
        except Exception as exc:
            runtime_health.raise_if_browser_connection_lost(
                exc,
                "recovery_navigation",
            )
            runtime_health.runtime_log(
                "[BROWSER] recovery_navigation_failed",
                config_dict,
                error_type=type(exc).__name__,
                current_url=recovery_url,
            )
    return await nodriver_goto_homepage(driver, config_dict)


async def _run_main(args, resources):
    instance_id = ""
    if args and getattr(args, "instance", None):
        instance_id = args.instance
    elif args and getattr(args, "input", None):
        instance_id = os.path.splitext(os.path.basename(args.input))[0]
        if instance_id == "settings":
            instance_id = ""
    if instance_id:
        if util.set_instance_id(instance_id):
            print(f"[INSTANCE] Running as instance: {util.get_instance_id()}")
        else:
            print(f"[INSTANCE] Invalid instance id '{instance_id}' (allowed: [A-Za-z0-9_-], max 32 chars), fallback to default")

    heartbeat_filename = "heartbeat.txt"
    resources["heartbeat_path"] = util.get_instance_state_path(
        heartbeat_filename
    )
    config_dict = get_config_dict(args)
    resources["config_dict"] = config_dict
    if config_dict is not None:
        configured_homepage = str(config_dict.get("homepage", "") or "")
        resources["configured_homepage"] = configured_homepage
        resume_url = str(resources.get("resume_url", "") or "")
        if _is_safe_browser_recovery_url(resume_url, configured_homepage):
            resources["launch_url"] = resume_url
            runtime_health.runtime_log(
                "[BROWSER] recovery_resume_url",
                config_dict,
                current_url=resume_url,
            )
        if not resources.get("last_url") and _is_safe_browser_recovery_url(
            configured_homepage,
            configured_homepage,
        ):
            resources["last_url"] = configured_homepage

    loop = asyncio.get_running_loop()
    browser_failure_event = asyncio.Event()
    previous_loop_exception_handler = loop.get_exception_handler()
    loop.set_exception_handler(
        runtime_health.create_browser_loop_exception_handler(
            config_dict,
            browser_failure_event,
            previous_loop_exception_handler,
        )
    )
    resources["event_loop"] = loop
    resources["previous_loop_exception_handler"] = (
        previous_loop_exception_handler
    )
    resources["browser_failure_event"] = browser_failure_event

    # Prefix logs with timestamp (optional) and instance id (always) so mixed
    # multi-instance stdout remains attributable.
    _show_timestamp = bool(config_dict and config_dict.get("advanced", {}).get("show_timestamp", False))
    _instance_tag = f"[{util.get_instance_id()}]"
    import builtins
    _original_print = builtins.print
    def _prefixed_print(*args_p, **kwargs_p):
        prefix_parts = []
        if _show_timestamp:
            prefix_parts.append(datetime.now().strftime("[%H:%M:%S]"))
        prefix_parts.append(_instance_tag)
        _original_print(*prefix_parts, *args_p, **kwargs_p)
    resources["original_print"] = _original_print
    builtins.print = _prefixed_print

    refresh_datetime_state = {
        "target_str": "",
        "reached": False,
        "last_countdown_print": 0,
    }
    resources["refresh_datetime_state"] = refresh_datetime_state
    driver = None
    tab = None
    session_manager = None
    if not config_dict is None:
        sandbox = False
        session_manager = create_browser_session_manager(config_dict, args)
        resources["session_manager"] = session_manager
        conf = get_extension_config(config_dict, args, session_manager=session_manager)
        nodriver_overwrite_prefs(conf)
        # PS: nodrirver run twice always cause error:
        # Failed to connect to browser
        # One of the causes could be when you are running as root.
        # In that case you need to pass no_sandbox=True
        #driver = await uc.start(conf, sandbox=sandbox, headless=config_dict["advanced"]["headless"])
        driver = await uc.start(conf)
        session_manager.attach(driver)
        if not driver is None:
            # Output actual CDP port for MCP connection (when mcp_debug is requested)
            mcp_debug_requested = (args and hasattr(args, 'mcp_debug') and args.mcp_debug) or \
                                  config_dict["advanced"].get("mcp_debug_port", 0) > 0
            if mcp_debug_requested:
                actual_port = driver.config.port
                print(f"[MCP DEBUG] Browser started on actual port: {actual_port}")
                print(f"[MCP DEBUG] Update .mcp.json with: --browserUrl http://127.0.0.1:{actual_port}")
                # Write port to file for /mcpstart command auto-update
                try:
                    if util.get_instance_id() == "default":
                        port_file = os.path.join(os.path.dirname(__file__), '..', '.temp', 'mcp_port.txt')
                        os.makedirs(os.path.dirname(port_file), exist_ok=True)
                    else:
                        port_file = util.get_instance_state_path('mcp_port.txt')
                    with open(port_file, 'w') as f:
                        f.write(str(actual_port))
                    print(f"[MCP DEBUG] Port saved to {port_file}")
                except Exception as e:
                    print(f"[MCP DEBUG] Warning: Could not save port to file: {e}")
            initial_tab = driver.main_tab
            if initial_tab:
                await nodrver_block_urls(initial_tab, config_dict)
            tab = await _navigate_browser_start(
                driver,
                config_dict,
                resources.get("launch_url", ""),
            )
            if tab is None:
                print("[ERROR] Homepage navigation failed. Cannot continue.")
                return
            if not initial_tab:
                tab = await nodrver_block_urls(tab, config_dict)
            if not config_dict["advanced"]["headless"]:
                await nodriver_resize_window(tab, config_dict)
        else:
            print("無法使用nodriver，程式無法繼續工作")
            return
    else:
        print("Load config error!")
        return

    url = ""
    last_url = ""
    cloudflare_checked = False
    cloudflare_fail_count = 0
    last_paused_state = False  # Track pause state changes

    ocr = None
    Captcha_Browser = None
    try:
        if config_dict["ocr_captcha"]["enable"]:
            debug = util.create_debug_logger(config_dict)
            ocr = create_ocr_for_platform(config_dict, debug=debug, fallback_ranges=1)
            Captcha_Browser = NonBrowser()
            if len(config_dict["accounts"]["tixcraft_sid"]) > 1:
                #set_non_browser_cookies(driver, config_dict["homepage"], Captcha_Browser)
                pass
    except Exception as exc:
        debug = util.create_debug_logger(config_dict)
        debug.log(f"[OCR INIT] Failed to initialize OCR: {exc}")

    maxbot_last_reset_time = time.time()
    heartbeat_interval_sec = 5
    last_heartbeat_time = 0.0
    last_runtime_alive_log = 0.0
    last_empty_url_log = 0.0
    empty_url_since_monotonic = 0.0
    is_quit_bot = False
    ticketplus_purchase_done = False  # Guard: stop polling after purchase completed

    # Initialize config mtime. Hot reload watches the file this instance was
    # launched with, so named profiles do not get overwritten by settings.json.
    app_root = util.get_app_root()
    config_filepath = os.path.join(app_root, CONST_MAXBOT_CONFIG_FILE)
    if args and getattr(args, "input", None):
        config_filepath = args.input
    config_mtime = 0
    if os.path.exists(config_filepath):
        config_mtime = os.path.getmtime(config_filepath)
    last_config_reload_check = 0.0

    while True:
        await asyncio.sleep(0.05)

        loop_mono = time.monotonic()
        if is_interval_due(loop_mono, last_config_reload_check, CONFIG_RELOAD_CHECK_INTERVAL_SEC):
            last_config_reload_check = loop_mono
            config_dict, config_mtime = await reload_config(config_dict, config_mtime, config_filepath)

        heartbeat_now = time.time()
        if heartbeat_now - last_heartbeat_time >= heartbeat_interval_sec:
            last_heartbeat_time = heartbeat_now
            runtime_health.touch_heartbeat(heartbeat_filename)
            if heartbeat_now - last_runtime_alive_log >= 30:
                last_runtime_alive_log = heartbeat_now
                runtime_health.runtime_log("[LOOP] alive", config_dict)

        if browser_failure_event.is_set():
            raise runtime_health.BrowserConnectionLost(
                "zendriver_listener",
                "InvalidStateError",
            )

        # pass if driver not loaded.
        if driver is None:
            _close_runtime_ntp_coordinator(refresh_datetime_state)
            print("nodriver not accessible!")
            break

        if not is_quit_bot and await check_and_handle_quit(config_dict):
            is_quit_bot = True

        if not is_quit_bot:
            prefer_cached_url = _should_prefer_cached_refresh_url(
                config_dict,
                refresh_datetime_state,
            )
            url, is_quit_bot = await nodriver_current_url(
                tab,
                config_dict,
                prefer_cached=prefer_cached_url,
            )
            #print("url:", url)

        if is_quit_bot:
            util.force_remove_file(util.get_instance_state_path(CONST_MAXBOT_INT28_QUIT_FILE))
            util.force_remove_file(util.get_instance_state_path(CONST_MAXBOT_AUTOMATION_STOP_FILE))
            break

        if not url:
            now_mono = time.monotonic()
            if empty_url_since_monotonic <= 0:
                empty_url_since_monotonic = now_mono
            empty_elapsed = now_mono - empty_url_since_monotonic
            if now_mono - last_empty_url_log >= 2.0:
                last_empty_url_log = now_mono
                util.create_debug_logger(config_dict).log(
                    "[URL DIAG] empty url, skipping dispatch; "
                    f"{format_cached_target_url_diagnostic(tab)}"
                )
            if _has_empty_url_watchdog_expired(
                resources.get("last_url", ""),
                empty_url_since_monotonic,
                now_mono,
            ):
                runtime_health.runtime_log(
                    "[BROWSER] empty_url_watchdog",
                    config_dict,
                    elapsed_ms=int(empty_elapsed * 1000),
                    current_url=resources.get("last_url", ""),
                )
                raise runtime_health.BrowserConnectionLost(
                    "empty_url_watchdog",
                    "EmptyBrowserUrl",
                )
            continue
        empty_url_since_monotonic = 0.0
        resources["last_url"] = url

        is_maxbot_paused = await check_and_handle_pause(config_dict)

        # Detect pause state change and show message immediately
        if is_maxbot_paused and not last_paused_state:
            instance_suffix = "" if util.get_instance_id() == "default" else f" [{util.get_instance_id()}]"
            print("BOT Paused." + instance_suffix)
        last_paused_state = is_maxbot_paused

        if len(url) > 0 :
            if url != last_url:
                print(url)
                write_last_url_to_file(url)
                cloudflare_checked = False
                cloudflare_fail_count = 0
            last_url = url

        if is_maxbot_paused:
            if 'kktix.c' in url:
                await nodriver_kktix_paused_main(tab, url, config_dict)
            # sleep more when paused.
            await asyncio.sleep(0.1)
            continue

        # Gate: block platform dispatching until refresh_datetime target time
        if await check_refresh_datetime_gate(tab, config_dict, refresh_datetime_state, url):
            if refresh_datetime_state.pop(
                "refresh_recovery_dispatch_required",
                False,
            ):
                await tixcraft_platform.nodriver_ticketmaster_check_ip_block(
                    tab,
                    config_dict,
                    current_url=url,
                )
            await asyncio.sleep(0.1)
            continue

        # Cloudflare challenge detection (only on URL change to avoid performance hit)
        # After 3 consecutive failures on same URL, stop retrying to avoid infinite loop
        # Skip Cityline Login page: Turnstile there is part of the login form, not a block
        if is_cityline_login_page(url):
            cloudflare_checked = True
        if not cloudflare_checked and cloudflare_fail_count < 3:
            is_cloudflare = await detect_cloudflare_challenge(tab, show_debug=config_dict.get("advanced", {}).get("verbose", False))
            cloudflare_checked = True
            if is_cloudflare:
                print("[CLOUDFLARE] Challenge page detected, attempting to solve...")
                cf_result = await handle_cloudflare_challenge(tab, config_dict)
                if cf_result:
                    cloudflare_checked = False  # Re-check after successful handling
                    cloudflare_fail_count = 0
                    continue
                else:
                    cloudflare_fail_count += 1
                    cloudflare_checked = False  # Allow retry on next loop iteration
                    if cloudflare_fail_count >= 3:
                        print("[CLOUDFLARE] Max failures reached, waiting for URL change to retry")

        # for kktix.cc and kktix.com
        if 'kktix.c' in url:
            is_quit_bot = await nodriver_kktix_main(tab, url, config_dict)
            if is_quit_bot:
                # 不自動暫停：讓多開實例可獨立運作
                # 保留 is_quit_bot = False 以防止程式結束，但不建立暫停檔案
                is_quit_bot = False

        tixcraft_family = False
        if 'tixcraft.com' in url:
            tixcraft_family = True

        if 'indievox.com' in url:
            tixcraft_family = True

        if 'ticketmaster.' in url:
            tixcraft_family = True

        if tixcraft_family:
            try:
                is_quit_bot = await nodriver_tixcraft_main(
                    tab,
                    url,
                    config_dict,
                    ocr,
                    Captcha_Browser,
                )
            except Exception as exc:
                if not runtime_health.is_browser_connection_closed_error(exc):
                    raise
                runtime_health.runtime_log(
                    "[BROWSER] connection_closed",
                    config_dict,
                    error_type=type(exc).__name__,
                )
                print("[BROWSER] Browser connection closed; stopping this instance cleanly.")
                is_quit_bot = True
                continue
            if is_quit_bot:
                # 不自動暫停：讓多開實例可獨立運作
                # 保留 is_quit_bot = False 以防止程式結束，但不建立暫停檔案
                is_quit_bot = False

        if 'famiticket.com' in url:
            await nodriver_famiticket_main(tab, url, config_dict)

        if 'ibon.com' in url:
            await nodriver_ibon_main(tab, url, config_dict, ocr, Captcha_Browser)

        kham_family = False
        if 'kham.com.tw' in url:
            kham_family = True

        if 'ticket.com.tw' in url:
            kham_family = True

        if 'tickets.udnfunlife.com' in url:
            kham_family = True

        if kham_family:
            tab = await nodriver_kham_main(tab, url, config_dict, ocr)

        # https://ticketplus.com.tw/*
        if 'ticketplus.com' in url and not ticketplus_purchase_done:
            tp_status = await nodriver_ticketplus_main(tab, url, config_dict, ocr, Captcha_Browser)

            if isinstance(tp_status, dict):
                if tp_status.get("purchase_completed", False):
                    if not ticketplus_purchase_done:
                        print("[SUCCESS] TicketPlus purchase completed")
                        ticketplus_purchase_done = True
                elif tp_status.get("is_ticket_assigned", False) and '/confirm/' in url.lower():
                    if not ticketplus_purchase_done:
                        print("[SUCCESS] TicketPlus on confirmation page, booking successful")
                        ticketplus_purchase_done = True

        if 'urbtix.hk' in url:
            #urbtix_main(driver, url, config_dict)
            pass

        if 'cityline.com' in url:
            tab = await nodriver_cityline_main(tab, url, config_dict)

        softix_family = False
        if 'hkticketing.com' in url:
            softix_family = True
        if 'galaxymacau.com' in url:
            softix_family = True
        if 'ticketek.com' in url:
            softix_family = True
        if softix_family:
            tab = await nodriver_hkticketing_main(tab, url, config_dict)

        # FunOne Tickets
        if 'tickets.funone.io' in url:
            tab = await nodriver_funone_main(tab, url, config_dict)

        # FANSI GO
        if 'go.fansi.me' in url:
            tab = await nodriver_fansigo_main(tab, url, config_dict)

        # FANSI GO Cognito login
        if FANSIGO_COGNITO_DOMAIN in url:
            await nodriver_fansigo_signin(tab, url, config_dict)

        # for facebook
        facebook_login_url = 'https://www.facebook.com/login.php?'
        if url[:len(facebook_login_url)]==facebook_login_url:
            await nodriver_facebook_main(tab, config_dict)


async def _cleanup_main_resources(resources):
    """Release runtime resources without masking an in-flight main error."""

    if resources.get("cleanup_complete", False):
        return None

    cleanup_errors = []
    refresh_datetime_state = resources.get("refresh_datetime_state")
    if isinstance(refresh_datetime_state, dict):
        try:
            _close_runtime_ntp_coordinator(refresh_datetime_state)
        except BaseException as exc:
            cleanup_errors.append(exc)

    session_manager = resources.get("session_manager")
    if session_manager is not None:
        stop_task = asyncio.create_task(session_manager.stop_browser())
        try:
            await asyncio.shield(stop_task)
        except asyncio.CancelledError as exc:
            # A cancellation arriving during finalization must still allow the
            # session owner to finish closing the browser. Re-raise it only
            # after the remaining synchronous cleanup has completed.
            cleanup_errors.append(exc)
            if not stop_task.done():
                try:
                    await stop_task
                except BaseException as stop_exc:
                    cleanup_errors.append(stop_exc)
        except BaseException as exc:
            cleanup_errors.append(exc)

    event_loop = resources.get("event_loop")
    if event_loop is not None:
        try:
            if not event_loop.is_closed():
                event_loop.set_exception_handler(
                    resources.get("previous_loop_exception_handler")
                )
        except BaseException as exc:
            cleanup_errors.append(exc)

    heartbeat_path = resources.get("heartbeat_path")
    if heartbeat_path:
        try:
            util.force_remove_file(heartbeat_path)
        except BaseException as exc:
            cleanup_errors.append(exc)

    original_print = resources.get("original_print")
    if original_print is not None:
        try:
            import builtins

            builtins.print = original_print
        except BaseException as exc:
            cleanup_errors.append(exc)

    resources["cleanup_complete"] = True
    return cleanup_errors[0] if cleanup_errors else None


def _new_main_resources(resume_url=""):
    return {
        "session_manager": None,
        "refresh_datetime_state": None,
        "heartbeat_path": "",
        "original_print": None,
        "config_dict": None,
        "configured_homepage": "",
        "last_url": str(resume_url or ""),
        "launch_url": "",
        "resume_url": str(resume_url or ""),
        "event_loop": None,
        "previous_loop_exception_handler": None,
        "browser_failure_event": None,
        "cleanup_complete": False,
    }


async def _run_main_once(args, resources):
    try:
        return await _run_main(args, resources)
    finally:
        primary_exception = sys.exc_info()[1]
        cleanup_exception = await _cleanup_main_resources(resources)
        if primary_exception is None and cleanup_exception is not None:
            raise cleanup_exception


async def main(args):
    restart_times = []
    resume_url = ""
    while True:
        resources = _new_main_resources(resume_url)
        try:
            return await _run_main_once(args, resources)
        except runtime_health.BrowserConnectionLost as exc:
            last_url = str(resources.get("last_url", "") or "")
            page_class = classify_page(last_url)
            config_dict = resources.get("config_dict")
            configured_homepage = str(
                resources.get("configured_homepage", "")
                or (
                    config_dict.get("homepage", "")
                    if isinstance(config_dict, dict)
                    else ""
                )
                or ""
            )
            if not _is_safe_browser_recovery_url(
                last_url,
                configured_homepage,
            ):
                runtime_health.runtime_log(
                    "[BROWSER] recovery_blocked",
                    config_dict,
                    reason="protected_or_unknown_page",
                    page_class=page_class.value,
                    current_url=last_url,
                    operation=exc.operation,
                )
                raise

            now = time.monotonic()
            restart_times = [
                timestamp
                for timestamp in restart_times
                if now - timestamp
                <= BROWSER_CONNECTION_RESTART_WINDOW_SECONDS
            ]
            if len(restart_times) >= BROWSER_CONNECTION_MAX_RESTARTS:
                runtime_health.runtime_log(
                    "[BROWSER] recovery_exhausted",
                    config_dict,
                    attempts=len(restart_times),
                    current_url=last_url,
                    operation=exc.operation,
                )
                raise

            restart_times.append(now)
            attempt = len(restart_times)
            delay = BROWSER_CONNECTION_RESTART_BACKOFF_SECONDS[
                min(
                    attempt - 1,
                    len(BROWSER_CONNECTION_RESTART_BACKOFF_SECONDS) - 1,
                )
            ]
            resume_url = last_url
            runtime_health.runtime_log(
                "[BROWSER] recovery_scheduled",
                config_dict,
                attempt=attempt,
                delay_seconds=delay,
                current_url=last_url,
                operation=exc.operation,
                error_type=exc.error_type,
            )
            print(
                "[BROWSER] Browser control connection lost; "
                f"restarting safe session ({attempt}/"
                f"{BROWSER_CONNECTION_MAX_RESTARTS})..."
            )
            await asyncio.sleep(delay)


def cli():
    parser = argparse.ArgumentParser(
            description="MaxBot Aggument Parser")

    parser.add_argument("--input",
        help="config file path",
        type=str)

    parser.add_argument("--instance",
        help="instance name for multi-instance isolation (default: derived from --input filename)",
        type=str)

    parser.add_argument("--homepage",
        help="overwrite homepage setting",
        type=str)

    parser.add_argument("--ticket_number",
        help="overwrite ticket_number setting",
        type=int)

    #default="False",
    parser.add_argument("--headless",
        help="headless mode",
        type=str)

    parser.add_argument("--browser",
        help="overwrite browser setting",
        default='',
        choices=['chrome','edge'],
        type=str)

    parser.add_argument("--browser_private_mode",
        help="launch browser in private mode (Chrome incognito / Edge InPrivate)",
        nargs="?",
        const="true",
        default=None,
        type=str)

    parser.add_argument("--run_mode",
        help="run mode: onsale keeps the v0.4.0 hot path, leak_watch uses leak-watch safe-page scanning",
        choices=["onsale", "leak_watch"],
        type=str)

    parser.add_argument("--leak_refresh_interval_seconds",
        help="safe-page refresh interval in leak_watch mode",
        type=float)

    parser.add_argument("--window_size",
        help="Window size",
        type=str)

    parser.add_argument("--proxy_server",
        help="overwrite proxy server, format: ip:port",
        type=str)

    parser.add_argument("--date_auto_select_mode",
        help="overwrite date_auto_select mode",
        choices=['random', 'center', 'from top to bottom', 'from bottom to top'],
        type=str)

    parser.add_argument("--date_keyword",
        help="overwrite date_auto_select date_keyword",
        type=str)

    parser.add_argument("--area_auto_select_mode",
        help="overwrite area_auto_select mode",
        choices=['random', 'center', 'from top to bottom', 'from bottom to top', 'most remaining'],
        type=str)

    parser.add_argument("--area_keyword",
        help="overwrite area_auto_select area_keyword",
        type=str)

    parser.add_argument("--mcp_debug",
        help="Enable MCP debug mode with fixed CDP port (default: 9222)",
        nargs='?',
        const=9222,
        type=int)

    parser.add_argument("--mcp_connect",
        help="Connect to existing Chrome on specified port (e.g., --mcp_connect 9222)",
        type=int,
        metavar="PORT")

    args = parser.parse_args()
    asyncio.run(main(args))

if __name__ == "__main__":
    cli()
