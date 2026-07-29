#!/usr/bin/env python3
#encoding=utf-8
import asyncio
import base64
import binascii
import concurrent.futures
import copy
import ipaddress
import json
import math
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections import deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse, urlunparse

import tornado
from tornado.ioloop import IOLoop
from tornado.web import Application
from tornado.web import StaticFileHandler

import requests
import util
from hunter_metadata import APP_DISPLAY_VERSION, APP_NAME, RELEASE_URL
from notification_context import make_notification_context
from refresh_timing import (
    DEFAULT_TIME_CALIBRATION,
    calibrate_ntp_servers,
    get_platform_timing_capability,
    normalize_advanced_delay_mode,
    robust_estimate,
    select_time_source,
)

from typing import (
    Dict,
    Any,
    Union,
    Optional,
    Awaitable,
    Tuple,
    List,
    Callable,
    Iterable,
    Generator,
    Type,
    TypeVar,
    cast,
    overload,
)

try:
    import ddddocr
except Exception as exc:
    print(f"[WARNING] ddddocr module not available: {exc}")
    print("[WARNING] OCR captcha auto-solve will be disabled.")

# Get script directory for resource paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CONST_APP_VERSION = APP_DISPLAY_VERSION

CONST_MAXBOT_ANSWER_ONLINE_FILE = "MAXBOT_ONLINE_ANSWER.txt"
CONST_MAXBOT_CONFIG_FILE = "settings.json"
CONST_MAXBOT_INT28_FILE = "MAXBOT_INT28_IDLE.txt"
CONST_MAXBOT_INT28_QUIT_FILE = "MAXBOT_INT28_QUIT.txt"
CONST_MAXBOT_AUTOMATION_STOP_FILE = "MAXBOT_AUTOMATION_STOP.txt"
CONST_MAXBOT_LAST_URL_FILE = "MAXBOT_LAST_URL.txt"
CONST_MAXBOT_QUESTION_FILE = "MAXBOT_QUESTION.txt"

CONST_SERVER_PORT = 16888
CONST_SERVER_ADDRESS = "127.0.0.1"
CONST_SERVER_STARTUP_TIMEOUT_SEC = 5.0
CONST_SENDKEY_TOKEN_RE = r"^[A-Za-z0-9_-]{1,64}$"
CONST_SECRET_MASK = "__HUNTERX_SECRET_MASKED__"
CONST_SECRET_SOURCE_PROFILE_KEY = "__hunterx_secret_source_profile"
CONST_CONTROL_MAX_BODY_BYTES = 4 * 1024 * 1024
CONST_CONTROL_MAX_BUFFER_BYTES = CONST_CONTROL_MAX_BODY_BYTES + (64 * 1024)
CONST_OCR_MAX_BASE64_BYTES = 2 * 1024 * 1024
CONST_OCR_MAX_DECODED_BYTES = 1536 * 1024
CONST_OCR_TIMEOUT_SECONDS = 5.0
CONST_OCR_RATE_LIMIT_REQUESTS = 20
CONST_OCR_RATE_LIMIT_WINDOW_SECONDS = 60.0
CONST_CONTROL_TASK_MAX_IN_FLIGHT = 4
CONST_CONTROL_TASK_TIMEOUT_SECONDS = 15.0
CONST_NTP_SERVER_LIMIT = 3
CONST_NTP_SAMPLE_LIMIT = 2
CONST_NTP_TIMEOUT_MS_LIMIT = 1500
CONST_HTTP_SAMPLE_LIMIT = 3
CONST_HTTP_REQUEST_TIMEOUT_SECONDS = 2.5
CONST_PUBLIC_RESPONSE_MAX_BYTES = 64 * 1024
CONST_TELEGRAM_CHAT_LIMIT = 5
CONST_RUN_STARTUP_GUARD_SECONDS = 15.0
GLOBAL_SERVER_SHUTDOWN = False

# Multi-instance dashboard: bot processes touch this file every few seconds;
# an instance counts as alive when its mtime is within the threshold.
CONST_HEARTBEAT_FILE = "heartbeat.txt"
CONST_HEARTBEAT_ALIVE_SEC = 30
CONST_INSTANCE_STOP_WAIT_SEC = 5.0
CALIBRATION_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="hunter-calibration",
)
OCR_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="hunter-ocr",
)
OCR_REQUEST_SLOTS = threading.BoundedSemaphore(1)
OCR_RATE_LIMIT_LOCK = threading.Lock()
OCR_REQUEST_TIMES = deque(maxlen=CONST_OCR_RATE_LIMIT_REQUESTS)
CONTROL_TASK_SLOTS = threading.BoundedSemaphore(
    CONST_CONTROL_TASK_MAX_IN_FLIGHT
)
RUN_INSTANCE_LOCK = threading.Lock()
RUN_INSTANCE_PENDING = {}
# Also used by settings-page outbound checks so slow network requests do not
# block Tornado's event loop.

# Multi-instance profiles: each profile is a full settings.json copy stored
# under profiles/<name>.json; "default" maps to settings.json.
CONST_PROFILES_DIR = "profiles"
CONST_DEFAULT_PROFILE = "default"
CONST_PROFILE_NAME_RE = r'^[A-Za-z0-9_-]{1,32}$'


def is_valid_profile_name(profile_name):
    return isinstance(profile_name, str) and bool(
        re.fullmatch(CONST_PROFILE_NAME_RE, profile_name)
    )


def get_profile_filepath(profile_name):
    app_root = util.get_app_root()
    if not profile_name or profile_name == CONST_DEFAULT_PROFILE:
        return os.path.join(app_root, CONST_MAXBOT_CONFIG_FILE)
    return os.path.join(app_root, CONST_PROFILES_DIR, profile_name + ".json")


def get_instance_state_filepath(profile_name, filename):
    app_root = util.get_app_root()
    if not profile_name or profile_name == CONST_DEFAULT_PROFILE:
        return os.path.join(app_root, filename)
    return os.path.join(app_root, "instances", profile_name, filename)


def get_instance_state_dir(profile_name):
    if not profile_name or profile_name == CONST_DEFAULT_PROFILE:
        return util.get_app_root()
    return os.path.join(util.get_app_root(), "instances", profile_name)


def get_sendkey_filepath(token):
    """Resolve an allowlisted sendkey token to a root-contained temporary file."""
    if not isinstance(token, str) or not re.fullmatch(CONST_SENDKEY_TOKEN_RE, token):
        raise ValueError("invalid token")

    app_root = os.path.realpath(util.get_app_root())
    candidate = os.path.realpath(os.path.join(app_root, f"{token}.tmp"))
    try:
        common_root = os.path.commonpath((app_root, candidate))
    except ValueError as exc:
        raise ValueError("invalid token path") from exc
    if os.path.normcase(common_root) != os.path.normcase(app_root):
        raise ValueError("invalid token path")
    return candidate


def is_sensitive_config_key(key):
    normalized = str(key or "").strip().lower()
    if normalized in {"ibonqware"}:
        return True
    key_parts = set(filter(None, re.split(r"[^a-z0-9]+", normalized)))
    sensitive_parts = {
        "password",
        "passwd",
        "token",
        "cookie",
        "cookies",
        "sid",
        "webhook",
        "secret",
        "authorization",
        "credential",
        "credentials",
    }
    return bool(key_parts & sensitive_parts) or "api_key" in normalized or "private_key" in normalized


def parse_json_object(raw_body):
    value = json.loads(raw_body or b"{}")
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def mask_config_secrets(value, key_name=""):
    if is_sensitive_config_key(key_name) and value not in (None, "", [], {}):
        return CONST_SECRET_MASK
    if isinstance(value, dict):
        return {key: mask_config_secrets(item, key) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_config_secrets(item) for item in value]
    return copy.deepcopy(value)


def restore_masked_config_secrets(incoming, existing, key_name=""):
    if is_sensitive_config_key(key_name) and incoming == CONST_SECRET_MASK:
        if existing is None:
            return ""
        return copy.deepcopy(existing)
    if isinstance(incoming, dict):
        existing_dict = existing if isinstance(existing, dict) else {}
        return {
            key: restore_masked_config_secrets(item, existing_dict.get(key), key)
            for key, item in incoming.items()
            if key != CONST_SECRET_SOURCE_PROFILE_KEY
        }
    if isinstance(incoming, list):
        existing_list = existing if isinstance(existing, list) else []
        return [
            restore_masked_config_secrets(
                item,
                existing_list[index] if index < len(existing_list) else None,
            )
            for index, item in enumerate(incoming)
        ]
    return copy.deepcopy(incoming)


def _parse_request_authority(authority, protocol):
    try:
        parsed = urlparse(f"{protocol}://{authority}")
        hostname = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except (TypeError, ValueError):
        return "", None
    if port is None:
        port = 443 if protocol == "https" else 80
    return hostname, port


def _is_loopback_hostname(hostname):
    normalized = str(hostname or "").lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def is_allowed_control_request(request):
    protocol = str(getattr(request, "protocol", "http") or "http").lower()
    request_host, request_port = _parse_request_authority(getattr(request, "host", ""), protocol)
    if not _is_loopback_hostname(request_host):
        return False

    headers = getattr(request, "headers", {}) or {}
    origin = headers.get("Origin")
    if not origin:
        origin = headers.get("Referer")
    if not origin:
        return True

    try:
        parsed_origin = urlparse(origin)
        origin_host = (parsed_origin.hostname or "").lower().rstrip(".")
        origin_protocol = parsed_origin.scheme.lower()
        origin_port = parsed_origin.port
    except (TypeError, ValueError):
        return False
    if origin_port is None:
        origin_port = 443 if origin_protocol == "https" else 80
    return (
        origin_protocol == protocol
        and origin_host == request_host
        and origin_port == request_port
        and _is_loopback_hostname(origin_host)
    )


def list_profile_names():
    profiles_dir = os.path.join(util.get_app_root(), CONST_PROFILES_DIR)
    names = []
    if os.path.isdir(profiles_dir):
        for filename in sorted(os.listdir(profiles_dir)):
            stem, ext = os.path.splitext(filename)
            if ext == ".json" and is_valid_profile_name(stem):
                names.append(stem)
    return [CONST_DEFAULT_PROFILE] + names

CONST_FROM_TOP_TO_BOTTOM = "from top to bottom"
CONST_FROM_BOTTOM_TO_TOP = "from bottom to top"
CONST_CENTER = "center"
CONST_RANDOM = "random"
CONST_MOST_REMAINING = "most remaining"
CONST_SELECT_ORDER_DEFAULT = CONST_RANDOM
CONST_EXCLUDE_DEFAULT = "\"輪椅\",\"身障\",\"身心\",\"障礙\",\"Restricted View\",\"燈柱遮蔽\",\"視線不完整\""
CONST_CAPTCHA_SOUND_FILENAME_DEFAULT = "assets/sounds/ding-dong.wav"
CONST_HOMEPAGE_DEFAULT = "about:blank"

CONST_OCR_CAPTCH_IMAGE_SOURCE_NON_BROWSER = "NonBrowser"
CONST_OCR_CAPTCH_IMAGE_SOURCE_CANVAS = "canvas"

CONST_WEBDRIVER_TYPE_NODRIVER = "nodriver"

CONST_SUPPORTED_SITES = ["https://kktix.com"
    ,"https://tixcraft.com (拓元)"
    ,"https://ticketmaster.sg"
    #,"https://ticketmaster.com"
    ,"https://teamear.tixcraft.com/ (添翼)"
    ,"https://www.indievox.com/ (獨立音樂)"
    ,"https://www.famiticket.com.tw (全網)"
    ,"https://ticket.ibon.com.tw/"
    ,"https://kham.com.tw/ (寬宏)"
    ,"https://ticket.com.tw/ (年代)"
    ,"https://tickets.udnfunlife.com/ (udn售票網)"
    ,"https://ticketplus.com.tw/ (遠大)"
    ,"===[香港或南半球的系統]==="
    ,"http://www.urbtix.hk/ (城市)"
    ,"https://www.cityline.com/ (買飛)"
    ,"https://hotshow.hkticketing.com/ (快達票)"
    ,"https://ticketing.galaxymacau.com/ (澳門銀河)"
    ,"http://premier.ticketek.com.au"
    ]

URL_DONATE = 'https://max-everyday.com/about/#donate'
URL_HELP = 'https://max-everyday.com/2018/03/tixcraft-bot/'
URL_RELEASE = RELEASE_URL
URL_CHROME_DRIVER = 'https://chromedriver.chromium.org/'
URL_FIREFOX_DRIVER = 'https://github.com/mozilla/geckodriver/releases'
URL_EDGE_DRIVER = 'https://developer.microsoft.com/zh-tw/microsoft-edge/tools/webdriver/'


def get_default_config():
    config_dict={}

    config_dict["homepage"] = CONST_HOMEPAGE_DEFAULT
    config_dict["browser"] = "chrome"
    config_dict["language"] = "English"
    config_dict["ticket_number"] = 2
    config_dict["refresh_datetime"] = ""
    config_dict["time_calibration"] = dict(DEFAULT_TIME_CALIBRATION)

    config_dict["ocr_captcha"] = {}
    config_dict["ocr_captcha"]["enable"] = True
    config_dict["ocr_captcha"]["beta"] = True
    config_dict["ocr_captcha"]["force_submit"] = True
    config_dict["ocr_captcha"]["image_source"] = CONST_OCR_CAPTCH_IMAGE_SOURCE_CANVAS
    config_dict["ocr_captcha"]["use_universal"] = True
    config_dict["ocr_captcha"]["path"] = "assets/model/universal"
    config_dict["webdriver_type"] = CONST_WEBDRIVER_TYPE_NODRIVER

    config_dict["date_auto_select"] = {}
    config_dict["date_auto_select"]["enable"] = True
    config_dict["date_auto_select"]["date_keyword"] = ""
    config_dict["date_auto_select"]["mode"] = CONST_SELECT_ORDER_DEFAULT

    config_dict["area_auto_select"] = {}
    config_dict["area_auto_select"]["enable"] = True
    config_dict["area_auto_select"]["mode"] = CONST_SELECT_ORDER_DEFAULT
    config_dict["area_auto_select"]["area_keyword"] = ""
    config_dict["keyword_exclude"] = CONST_EXCLUDE_DEFAULT

    config_dict['kktix']={}
    config_dict["kktix"]["auto_press_next_step_button"] = True
    config_dict["kktix"]["auto_fill_ticket_number"] = True
    config_dict["kktix"]["max_dwell_time"] = 90

    config_dict['cityline']={}

    config_dict['tixcraft']={}
    config_dict["tixcraft"]["pass_date_is_sold_out"] = True
    config_dict["tixcraft"]["auto_reload_coming_soon_page"] = True
    config_dict["tixcraft"]["allow_less_tickets"] = False


    # Contact information
    config_dict['contact']={}
    config_dict["contact"]["real_name"] = ""
    config_dict["contact"]["phone"] = ""
    config_dict["contact"]["credit_card_prefix"] = ""

    # Accounts section (cookies, accounts, passwords)
    config_dict['accounts']={}
    config_dict["accounts"]["tixcraft_sid"] = ""
    config_dict["accounts"]["ibonqware"] = ""
    config_dict["accounts"]["funone_session_cookie"] = ""
    config_dict["accounts"]["fansigo_cookie"] = ""
    config_dict["accounts"]["fansigo_account"] = ""
    config_dict["accounts"]["fansigo_password"] = ""
    config_dict["accounts"]["facebook_account"] = ""
    config_dict["accounts"]["kktix_account"] = ""
    config_dict["accounts"]["fami_account"] = ""
    config_dict["accounts"]["cityline_account"] = ""
    config_dict["accounts"]["urbtix_account"] = ""
    config_dict["accounts"]["hkticketing_account"] = ""
    config_dict["accounts"]["kham_account"] = ""
    config_dict["accounts"]["ticket_account"] = ""
    config_dict["accounts"]["udn_account"] = ""
    config_dict["accounts"]["ticketplus_account"] = ""

    config_dict["accounts"]["facebook_password"] = ""
    config_dict["accounts"]["kktix_password"] = ""
    config_dict["accounts"]["fami_password"] = ""
    config_dict["accounts"]["urbtix_password"] = ""
    config_dict["accounts"]["cityline_password"] = ""
    config_dict["accounts"]["hkticketing_password"] = ""
    config_dict["accounts"]["kham_password"] = ""
    config_dict["accounts"]["ticket_password"] = ""
    config_dict["accounts"]["udn_password"] = ""
    config_dict["accounts"]["ticketplus_password"] = ""

    # Advanced settings (non-credential settings only)
    config_dict['advanced']={}

    config_dict['advanced']['play_sound']={}
    config_dict["advanced"]["play_sound"]["ticket"] = True
    config_dict["advanced"]["play_sound"]["order"] = True
    config_dict["advanced"]["play_sound"]["filename"] = CONST_CAPTCHA_SOUND_FILENAME_DEFAULT

    config_dict["advanced"]["disable_adjacent_seat"] = False
    config_dict["advanced"]["hide_some_image"] = False
    config_dict["advanced"]["block_facebook_network"] = False

    config_dict["advanced"]["headless"] = False
    config_dict["advanced"]["browser_type"] = "chrome"
    config_dict["advanced"]["browser_private_mode"] = False
    config_dict["advanced"]["run_mode"] = "onsale"
    config_dict["advanced"]["verbose"] = False
    config_dict["advanced"]["show_timestamp"] = True
    config_dict["advanced"]["auto_guess_options"] = False
    config_dict["advanced"]["user_guess_string"] = ""
    config_dict["advanced"]["discount_code"] = ""

    # Server port for settings web interface (Issue #156)
    config_dict["advanced"]["server_port"] = CONST_SERVER_PORT
    # remote_url will be dynamically generated based on server_port
    config_dict["advanced"]["remote_url"] = ""

    config_dict["advanced"]["auto_reload_page_interval"] = 5
    config_dict["advanced"]["leak_refresh_interval_seconds"] = 3
    config_dict["advanced"]["tixcraft_soft_block_delay"] = ""
    config_dict["advanced"]["auto_reload_overheat_count"] = 4
    config_dict["advanced"]["auto_reload_overheat_cd"] = 1.0
    config_dict["advanced"]["reset_browser_interval"] = 0
    config_dict["advanced"]["proxy_server_port"] = ""
    config_dict["advanced"]["window_size"] = "600,1024"

    config_dict["advanced"]["idle_keyword"] = ""
    config_dict["advanced"]["resume_keyword"] = ""
    config_dict["advanced"]["idle_keyword_second"] = ""
    config_dict["advanced"]["resume_keyword_second"] = ""

    config_dict["advanced"]["discord_webhook_url"] = ""
    config_dict["advanced"]["discord_message"] = ""
    config_dict["advanced"]["telegram_bot_token"] = ""
    config_dict["advanced"]["telegram_chat_id"] = ""
    config_dict["advanced"]["telegram_message"] = ""

    # Keyword priority fallback (Feature 003)
    config_dict["date_auto_fallback"] = False  # default: strict mode (avoid unwanted purchases)
    config_dict["area_auto_fallback"] = False  # default: strict mode (avoid unwanted purchases)

    return config_dict

def read_last_url_from_file(profile_name=""):
    last_url_filepath = get_instance_state_filepath(profile_name, CONST_MAXBOT_LAST_URL_FILE)
    text = ""
    if os.path.exists(last_url_filepath):
        try:
            with open(last_url_filepath, "r", encoding="utf-8") as text_file:
                text = text_file.readline().strip()
        except Exception as e:
            print(f"[ERROR] Failed to read last_url from {last_url_filepath}: {e}")
    return text


def list_instance_ids():
    ids = list(list_profile_names())
    instances_dir = os.path.join(util.get_app_root(), "instances")
    if os.path.isdir(instances_dir):
        for item in sorted(os.listdir(instances_dir)):
            if os.path.isdir(os.path.join(instances_dir, item)) and item not in ids:
                ids.append(item)
    return ids


def is_instance_alive(profile_name):
    heartbeat_filepath = get_instance_state_filepath(profile_name, CONST_HEARTBEAT_FILE)
    if not os.path.exists(heartbeat_filepath):
        return False
    try:
        return (time.time() - os.path.getmtime(heartbeat_filepath)) < CONST_HEARTBEAT_ALIVE_SEC
    except Exception:
        return False


def get_related_instance_ids(profile_name):
    """Return the profile instance plus numbered duplicate launches."""
    related = [profile_name]
    duplicate_re = re.compile(r"^" + re.escape(profile_name) + r"-\d+$")
    for instance_id in list_instance_ids():
        if instance_id != profile_name and duplicate_re.match(instance_id):
            related.append(instance_id)
    return related


def wait_for_instances_to_stop(instance_ids, timeout_sec=CONST_INSTANCE_STOP_WAIT_SEC):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        alive_ids = [instance_id for instance_id in instance_ids if is_instance_alive(instance_id)]
        if not alive_ids:
            return []
        time.sleep(0.2)
    return [instance_id for instance_id in instance_ids if is_instance_alive(instance_id)]


def remove_instance_state_dir(profile_name):
    if not profile_name or profile_name == CONST_DEFAULT_PROFILE:
        return False
    instance_dir = get_instance_state_dir(profile_name)
    instances_root = os.path.join(util.get_app_root(), "instances")
    try:
        instance_dir_real = os.path.realpath(instance_dir)
        instances_root_real = os.path.realpath(instances_root)
        if not instance_dir_real.startswith(instances_root_real + os.sep):
            return False
        if os.path.isdir(instance_dir):
            shutil.rmtree(instance_dir)
            return True
    except Exception as exc:
        print(f"[WARNING] Failed to remove instance dir {profile_name}: {exc}")
    return False


def get_instance_status(profile_name):
    alive = is_instance_alive(profile_name)
    paused = os.path.exists(get_instance_state_filepath(profile_name, CONST_MAXBOT_INT28_FILE))
    return {
        "id": profile_name,
        "alive": alive,
        "paused": paused,
        "last_url": read_last_url_from_file(profile_name),
    }


def migrate_config(config_dict):
    """Migrate old config structure to new structure."""
    if config_dict is None:
        return config_dict

    # Migrate ocr_model_path from advanced to ocr_captcha.path
    if "advanced" in config_dict and "ocr_model_path" in config_dict["advanced"]:
        if "ocr_captcha" not in config_dict:
            config_dict["ocr_captcha"] = {}
        if "path" not in config_dict["ocr_captcha"]:
            config_dict["ocr_captcha"]["path"] = config_dict["advanced"]["ocr_model_path"]
        del config_dict["advanced"]["ocr_model_path"]

    # Ensure ocr_captcha.path exists
    if "ocr_captcha" in config_dict and "path" not in config_dict["ocr_captcha"]:
        config_dict["ocr_captcha"]["path"] = "assets/model/universal"

    # Migrate server_port: ensure old config has this field (Issue #156)
    if "advanced" in config_dict:
        if "server_port" not in config_dict["advanced"]:
            config_dict["advanced"]["server_port"] = CONST_SERVER_PORT

    # Migrate discount_code from accounts to advanced
    if "accounts" in config_dict and "discount_code" in config_dict["accounts"]:
        if "advanced" not in config_dict:
            config_dict["advanced"] = {}
        # Only migrate if advanced.discount_code doesn't exist or is empty
        if "discount_code" not in config_dict["advanced"] or not config_dict["advanced"]["discount_code"]:
            config_dict["advanced"]["discount_code"] = config_dict["accounts"]["discount_code"]
        del config_dict["accounts"]["discount_code"]

    # Ensure advanced.discount_code exists
    if "advanced" in config_dict and "discount_code" not in config_dict["advanced"]:
        config_dict["advanced"]["discount_code"] = ""

    # Ensure all default fields exist (fills missing keys from new versions)
    default = get_default_config()
    for section in ["advanced", "kktix", "tixcraft", "date_auto_select", "area_auto_select", "ocr_captcha", "contact", "accounts", "cityline", "time_calibration"]:
        if section in default:
            if section not in config_dict or not isinstance(config_dict[section], dict):
                config_dict[section] = dict(default[section])
            else:
                for key, value in default[section].items():
                    if key not in config_dict[section]:
                        config_dict[section][key] = value

    # Top-level scalar fields (auto-fill any missing non-section keys)
    dict_sections = {k for k, v in default.items() if isinstance(v, dict)}
    for key, value in default.items():
        if key not in dict_sections and key not in config_dict:
            config_dict[key] = value

    if "refresh_calibration" in config_dict:
        from refresh_timing import DEFAULT_REFRESH_CALIBRATION

        if not isinstance(config_dict["refresh_calibration"], dict):
            config_dict["refresh_calibration"] = {}
        migrated_calibration = dict(DEFAULT_REFRESH_CALIBRATION)
        migrated_calibration.update(config_dict["refresh_calibration"])
        migrated_calibration["enable"] = False
        migrated_calibration["auto_calibrate"] = False
        migrated_calibration["advanced_delay_mode"] = normalize_advanced_delay_mode(
            migrated_calibration.get("advanced_delay_mode")
        )
        config_dict["refresh_calibration"] = migrated_calibration

    config_dict["time_calibration"] = normalize_time_calibration_config(
        config_dict.get("time_calibration"),
        default["time_calibration"],
    )

    if "advanced" in config_dict:
        run_mode = str(config_dict["advanced"].get("run_mode", default["advanced"]["run_mode"])).strip().lower()
        if run_mode not in {"onsale", "leak_watch"}:
            run_mode = default["advanced"]["run_mode"]
        config_dict["advanced"]["run_mode"] = run_mode
        config_dict["advanced"]["auto_reload_page_interval"] = normalize_non_negative_float(
            config_dict["advanced"].get("auto_reload_page_interval"),
            default["advanced"]["auto_reload_page_interval"],
        )
        config_dict["advanced"]["leak_refresh_interval_seconds"] = normalize_non_negative_float(
            config_dict["advanced"].get("leak_refresh_interval_seconds"),
            default["advanced"]["leak_refresh_interval_seconds"],
        )

    return config_dict

def load_json(profile_name=""):
    config_filepath = get_profile_filepath(profile_name)
    config_dict = None
    if os.path.isfile(config_filepath):
        try:
            with open(config_filepath, encoding='utf-8') as json_data:
                config_dict = json.load(json_data)
        except Exception as e:
            print(f"[ERROR] Failed to load {config_filepath}: {e}")
            print("[ERROR] Settings file may be corrupted. Using default settings.")
            config_dict = get_default_config()
    else:
        config_dict = get_default_config()

    # Apply migrations for backward compatibility
    config_dict = migrate_config(config_dict)

    return config_filepath, config_dict

def reset_json():
    app_root = util.get_app_root()
    config_filepath = os.path.join(app_root, CONST_MAXBOT_CONFIG_FILE)
    config_dict = get_default_config()
    return config_filepath, config_dict


def normalize_non_negative_float_default(default_value):
    try:
        normalized_default = float(default_value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(normalized_default):
        return 0.0
    return max(0.0, normalized_default)


def normalize_non_negative_float(value, default_value):
    if value in (None, ""):
        return normalize_non_negative_float_default(default_value)
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return normalize_non_negative_float_default(default_value)
    if not math.isfinite(normalized):
        return normalize_non_negative_float_default(default_value)
    return max(0.0, normalized)


def normalize_positive_int(value, default_value, minimum=1, maximum=3600):
    try:
        normalized = int(float(value))
    except (TypeError, ValueError):
        normalized = int(default_value)
    return max(minimum, min(maximum, normalized))


def normalize_time_calibration_config(raw_config, default_config):
    if not isinstance(raw_config, dict):
        raw_config = {}
    config = dict(default_config)
    config.update(raw_config)
    mode = str(config.get("mode", "auto")).strip().lower()
    if mode not in ("auto", "ntp", "http", "system"):
        mode = "auto"
    config["mode"] = mode

    raw_servers = config.get("ntp_servers", default_config.get("ntp_servers", []))
    if isinstance(raw_servers, str):
        servers = [item.strip() for item in raw_servers.split(",") if item.strip()]
    elif isinstance(raw_servers, list):
        servers = [str(item).strip() for item in raw_servers if str(item).strip()]
    else:
        servers = list(default_config.get("ntp_servers", []))
    config["ntp_servers"] = servers[:8]
    config["ntp_timeout_ms"] = normalize_positive_int(
        config.get("ntp_timeout_ms"),
        default_config.get("ntp_timeout_ms", 1000),
        50,
        10000,
    )
    config["ntp_samples_per_server"] = normalize_positive_int(
        config.get("ntp_samples_per_server"),
        default_config.get("ntp_samples_per_server", 3),
        1,
        10,
    )
    config["ntp_min_valid_samples"] = normalize_positive_int(
        config.get("ntp_min_valid_samples"),
        default_config.get("ntp_min_valid_samples", 2),
        1,
        10,
    )
    config["background_refresh_seconds"] = normalize_positive_int(
        config.get("background_refresh_seconds"),
        default_config.get("background_refresh_seconds", 300),
        60,
        86400,
    )
    return config


def maxbot_idle(profile_name=""):
    idle_filepath = get_instance_state_filepath(profile_name, CONST_MAXBOT_INT28_FILE)
    try:
        os.makedirs(os.path.dirname(idle_filepath), exist_ok=True)
        with open(idle_filepath, "w") as text_file:
            text_file.write("")
    except Exception as e:
        print(f"[ERROR] Failed to create idle file: {e}")

def maxbot_resume(profile_name=""):
    idle_filepath = get_instance_state_filepath(profile_name, CONST_MAXBOT_INT28_FILE)
    stop_filepath = get_instance_state_filepath(profile_name, CONST_MAXBOT_AUTOMATION_STOP_FILE)
    for i in range(3):
         util.force_remove_file(idle_filepath)
         util.force_remove_file(stop_filepath)

def maxbot_stop(profile_name=""):
    maxbot_idle(profile_name)
    stop_filepath = get_instance_state_filepath(profile_name, CONST_MAXBOT_AUTOMATION_STOP_FILE)
    try:
        os.makedirs(os.path.dirname(stop_filepath), exist_ok=True)
        with open(stop_filepath, "w") as text_file:
            text_file.write("")
    except Exception as e:
        print(f"[ERROR] Failed to create automation stop file: {e}")


def maxbot_quit(profile_name=""):
    stop_filepath = get_instance_state_filepath(profile_name, CONST_MAXBOT_INT28_QUIT_FILE)
    try:
        os.makedirs(os.path.dirname(stop_filepath), exist_ok=True)
        with open(stop_filepath, "w") as text_file:
            text_file.write("")
    except Exception as e:
        print(f"[ERROR] Failed to create stop file: {e}")


def launch_maxbot(profile_name="", instance_override=""):
    global launch_counter
    if "launch_counter" in globals():
        launch_counter += 1
    else:
        launch_counter = 0

    effective_instance = instance_override if instance_override else profile_name
    util.force_remove_file(get_instance_state_filepath(effective_instance, CONST_MAXBOT_INT28_QUIT_FILE))
    util.force_remove_file(get_instance_state_filepath(effective_instance, CONST_MAXBOT_INT28_FILE))
    util.force_remove_file(get_instance_state_filepath(effective_instance, CONST_MAXBOT_AUTOMATION_STOP_FILE))

    config_filepath, config_dict = load_json(profile_name)

    script_name = "nodriver_tixcraft"

    input_filepath = ""
    if profile_name and profile_name != CONST_DEFAULT_PROFILE:
        input_filepath = config_filepath

    window_size = config_dict["advanced"]["window_size"]
    if len(window_size) > 0:
        if "," in window_size:
            size_array = window_size.split(",")
            if len(size_array) > 2:
                size_array = size_array[:2]
                window_size = ",".join(size_array)
            target_width = int(size_array[0])
            target_left = target_width * launch_counter
            #print("target_left:", target_left)
            if target_left >= 1440:
                launch_counter = 0
            window_size = window_size + "," + str(launch_counter)
            #print("window_size:", window_size)

    return util.launch_maxbot(
        script_name,
        input_filepath,
        "",
        "",
        "",
        window_size,
        instance=instance_override,
    )

def change_maxbot_status_by_keyword():
    system_clock_data = datetime.now()
    current_hms = system_clock_data.strftime('%H:%M:%S')
    current_sec = system_clock_data.strftime('%S')

    for profile_name in list_profile_names():
        _, config_dict = load_json(profile_name)
        advanced = config_dict.get("advanced", {})

        if len(advanced.get("idle_keyword", "")) > 0:
            if util.is_text_match_keyword(advanced["idle_keyword"], current_hms):
                maxbot_idle(profile_name)
        if len(advanced.get("resume_keyword", "")) > 0:
            if util.is_text_match_keyword(advanced["resume_keyword"], current_hms):
                maxbot_resume(profile_name)
        if len(advanced.get("idle_keyword_second", "")) > 0:
            if util.is_text_match_keyword(advanced["idle_keyword_second"], current_sec):
                maxbot_idle(profile_name)
        if len(advanced.get("resume_keyword_second", "")) > 0:
            if util.is_text_match_keyword(advanced["resume_keyword_second"], current_sec):
                maxbot_resume(profile_name)


def cleanup_orphan_instance_dirs():
    instances_dir = os.path.join(util.get_app_root(), "instances")
    removed = []
    preserved = []
    if not os.path.isdir(instances_dir):
        return removed, preserved

    with RUN_INSTANCE_LOCK:
        _prune_expired_run_instances_locked(time.monotonic())
        pending_instances = set(RUN_INSTANCE_PENDING)
        valid_names = set(list_profile_names())
        for item in sorted(os.listdir(instances_dir)):
            item_path = os.path.join(instances_dir, item)
            if not os.path.isdir(item_path) or item in valid_names:
                continue
            if item in pending_instances or is_instance_alive(item):
                preserved.append(item)
                print(f"[CLEANUP] Preserved active orphan instance dir: {item}")
                continue
            if remove_instance_state_dir(item):
                removed.append(item)
                print(f"[CLEANUP] Removed orphan instance dir: {item}")
    return removed, preserved


def clean_tmp_file():
    app_root = util.get_app_root()
    default_instance_alive = is_instance_alive(CONST_DEFAULT_PROFILE)
    if default_instance_alive:
        print("[CLEANUP] Preserved live default-instance runtime state.")
    else:
        remove_file_list = [
            CONST_MAXBOT_LAST_URL_FILE,
            CONST_MAXBOT_INT28_FILE,
            CONST_MAXBOT_INT28_QUIT_FILE,
            CONST_MAXBOT_AUTOMATION_STOP_FILE,
            CONST_MAXBOT_ANSWER_ONLINE_FILE,
            CONST_MAXBOT_QUESTION_FILE,
            CONST_HEARTBEAT_FILE,
        ]
        for filename in remove_file_list:
            filepath = os.path.join(app_root, filename)
            util.force_remove_file(filepath)

        for item in os.listdir(app_root):
            if item.endswith(".tmp"):
                try:
                    os.remove(os.path.join(app_root, item))
                except Exception as exc:
                    print(f"[WARNING] Failed to remove {item}: {exc}")

    cleanup_orphan_instance_dirs()


def _profile_exists_for_run(profile_name):
    base_profile = profile_name or CONST_DEFAULT_PROFILE
    if base_profile == CONST_DEFAULT_PROFILE:
        return True
    return os.path.isfile(get_profile_filepath(base_profile))


def _is_valid_instance_override(base_profile, instance_override):
    if not instance_override:
        return True
    return bool(
        re.fullmatch(
            rf"{re.escape(base_profile)}-(?:[2-9]|[1-9][0-9]+)",
            instance_override,
        )
    )


def _release_run_instance(instance_id):
    with RUN_INSTANCE_LOCK:
        RUN_INSTANCE_PENDING.pop(instance_id, None)


def _prune_expired_run_instances_locked(now):
    for pending_id, record in list(RUN_INSTANCE_PENDING.items()):
        if now >= float(record.get("expires_at", 0.0) or 0.0):
            RUN_INSTANCE_PENDING.pop(pending_id, None)


def _instance_belongs_to_profile(instance_id, profile_name):
    return instance_id == profile_name or bool(
        re.fullmatch(rf"{re.escape(profile_name)}-\d+", instance_id)
    )


def _pending_run_instances_for_profile_locked(profile_name):
    return sorted(
        instance_id
        for instance_id, record in RUN_INSTANCE_PENDING.items()
        if record.get("profile") == profile_name
        or (
            not record.get("profile")
            and _instance_belongs_to_profile(instance_id, profile_name)
        )
    )


def _claim_run_instance(instance_id, profile_name=None, now=None):
    current = time.monotonic() if now is None else float(now)
    with RUN_INSTANCE_LOCK:
        _prune_expired_run_instances_locked(current)
        if profile_name is not None and not _profile_exists_for_run(profile_name):
            return False
        if instance_id in RUN_INSTANCE_PENDING or is_instance_alive(instance_id):
            return False
        RUN_INSTANCE_PENDING[instance_id] = {
            "expires_at": current + CONST_RUN_STARTUP_GUARD_SECONDS,
            "process": None,
            "profile": profile_name,
        }
        return True


def _watch_run_instance_startup(instance_id, process):
    deadline = time.monotonic() + CONST_RUN_STARTUP_GUARD_SECONDS
    try:
        while time.monotonic() < deadline:
            poll = getattr(process, "poll", None)
            if callable(poll) and poll() is not None:
                return
            if is_instance_alive(instance_id):
                return
            time.sleep(0.1)
    finally:
        _release_run_instance(instance_id)


def _track_run_instance_startup(instance_id, process):
    with RUN_INSTANCE_LOCK:
        record = RUN_INSTANCE_PENDING.get(instance_id)
        if record is not None:
            record["process"] = process
    threading.Thread(
        target=_watch_run_instance_startup,
        args=(instance_id, process),
        daemon=True,
        name=f"hunter-run-guard-{instance_id}",
    ).start()

class NoCacheStaticFileHandler(StaticFileHandler):
    """Custom StaticFileHandler that prevents stale settings UI assets."""
    def set_extra_headers(self, path):
        # Keep settings UI assets uncached so help text and translations update immediately.
        if path in {'settings.html', 'help-content.js', 'settings.js'}:
            self.set_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.set_header('Pragma', 'no-cache')
            self.set_header('Expires', '0')


def normalize_time_source_url(raw_url):
    value = (raw_url or "").strip()
    if not value:
        value = "https://time.is/"
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("time source must be an http/https URL")
    if parsed.username or parsed.password:
        raise ValueError("time source URL must not include credentials")
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.params, parsed.query, ""))


def is_public_time_source_host(hostname):
    try:
        addr_infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    if not addr_infos:
        return False
    for info in addr_infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def ensure_public_calibration_url(raw_url, label):
    url = normalize_time_source_url(raw_url)
    parsed = urlparse(url)
    if not is_public_time_source_host(parsed.hostname):
        raise ValueError(f"{label} host must resolve to a public address")
    return url


def request_public_url(method, url, headers, timeout, stream=False, max_redirects=3):
    current_url = ensure_public_calibration_url(url, "redirect")
    for _ in range(max_redirects + 1):
        response = requests.request(
            method,
            current_url,
            headers=headers,
            timeout=timeout,
            stream=stream,
            allow_redirects=False,
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("redirect response missing Location header")
            current_url = ensure_public_calibration_url(urljoin(current_url, location), "redirect")
            continue
        return response
    raise ValueError("too many redirects")


def parse_server_time_from_json(data):
    if not isinstance(data, dict):
        return None
    for key in ("utc_datetime", "datetime", "dateTime", "currentDateTime"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            normalized = value.strip().replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normalized)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except ValueError:
                pass

    for key in ("unixtime", "unixTime", "timestamp"):
        value = data.get(key)
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            continue
        if timestamp > 100000000000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return None


def read_limited_response_body(response, limit=CONST_PUBLIC_RESPONSE_MAX_BYTES):
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > limit:
                raise ValueError("response body too large")
        except ValueError as exc:
            if str(exc) == "response body too large":
                raise

    chunks = []
    total = 0
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        for chunk in iterator(chunk_size=8192):
            if not chunk:
                continue
            total += len(chunk)
            if total > limit:
                raise ValueError("response body too large")
            chunks.append(chunk)
        return b"".join(chunks)

    content = getattr(response, "content", b"") or b""
    if len(content) > limit:
        raise ValueError("response body too large")
    return bytes(content)


def parse_response_server_time(response):
    content_type = response.headers.get("Content-Type", "")
    if "json" in content_type.lower():
        try:
            payload = read_limited_response_body(response)
            parsed = parse_server_time_from_json(json.loads(payload))
            if parsed is not None:
                return parsed, "json"
        except Exception:
            pass

    date_header = response.headers.get("Date")
    if not date_header:
        return None, ""
    parsed = parsedate_to_datetime(date_header)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc), "http-date"


def calibrate_time_source_url(raw_url, samples=5):
    url = ensure_public_calibration_url(raw_url, "time source")

    try:
        sample_count = max(1, min(CONST_HTTP_SAMPLE_LIMIT, int(samples)))
    except Exception:
        sample_count = 5

    headers = {
        "User-Agent": f"{APP_NAME}/{APP_DISPLAY_VERSION} time-calibration",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    sample_rows = []
    failures = []
    for _ in range(sample_count):
        started_wall = datetime.now(timezone.utc)
        started_mono = time.perf_counter()
        try:
            response = request_public_url(
                "GET",
                url,
                headers=headers,
                timeout=CONST_HTTP_REQUEST_TIMEOUT_SECONDS,
                stream=True,
            )
        except Exception as exc:
            failures.append(str(exc))
            continue
        ended_mono = time.perf_counter()
        ended_wall = datetime.now(timezone.utc)

        try:
            server_time, source_type = parse_response_server_time(response)
        except Exception as exc:
            failures.append(str(exc))
            response.close()
            continue

        rtt_ms = (ended_mono - started_mono) * 1000
        midpoint = started_wall + ((ended_wall - started_wall) / 2)
        offset_ms = None
        if server_time is not None:
            offset_ms = (server_time - midpoint).total_seconds() * 1000

        sample_rows.append({
            "rtt_ms": round(rtt_ms, 1),
            "offset_ms": None if offset_ms is None else round(offset_ms),
            "source": source_type,
            "final_url": response.url,
        })
        response.close()
        time.sleep(0.05)

    if not sample_rows:
        reason = failures[-1] if failures else "no usable response"
        raise ValueError(f"time calibration failed: {reason}")

    best_sample = min(sample_rows, key=lambda item: item["rtt_ms"])
    offsets = [row["offset_ms"] for row in sample_rows if row["offset_ms"] is not None]
    offset_estimate = robust_estimate(offsets)
    rtt_estimate = robust_estimate([row["rtt_ms"] for row in sample_rows])
    clock_offset_ms = round(offset_estimate["value_ms"]) if offsets else 0
    clock_uncertainty_ms = round(offset_estimate["uncertainty_ms"]) if offsets else 0
    estimated_one_way_delay_ms = max(1, round(best_sample["rtt_ms"] / 2))

    return {
        "success": True,
        "url": url,
        "final_url": best_sample["final_url"],
        "sample_count": len(sample_rows),
        "source": "http",
        "clock_offset_ms": clock_offset_ms,
        "clock_uncertainty_ms": clock_uncertainty_ms,
        "estimated_one_way_delay_ms": estimated_one_way_delay_ms,
        "network_uplink_ms": estimated_one_way_delay_ms,
        "best_rtt_ms": best_sample["rtt_ms"],
        "rtt_p50_ms": rtt_estimate["p50_ms"],
        "rtt_p95_ms": rtt_estimate["p95_ms"],
        "rtt_p99_ms": rtt_estimate["p99_ms"],
        "confidence": offset_estimate["confidence"] if offsets else "low",
        "time_source_type": best_sample["source"],
        "samples": sample_rows,
        "warning": "HTTP Date is usually second-precision. estimated_one_way_delay_ms is not exact uplink delay.",
    }


def estimate_ticket_site_latency(raw_url, samples=3):
    url = ensure_public_calibration_url(raw_url, "ticket site")

    try:
        sample_count = max(1, min(CONST_HTTP_SAMPLE_LIMIT, int(samples)))
    except Exception:
        sample_count = 3

    headers = {
        "User-Agent": f"{APP_NAME}/{APP_DISPLAY_VERSION} latency-estimate",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    sample_rows = []
    failures = []
    for _ in range(sample_count):
        started_mono = time.perf_counter()
        response = None
        method = "HEAD"
        try:
            response = request_public_url(
                "HEAD",
                url,
                headers=headers,
                timeout=CONST_HTTP_REQUEST_TIMEOUT_SECONDS,
                stream=True,
            )
            if response.status_code in (403, 405) or response.status_code >= 500:
                response.close()
                method = "GET"
                response = request_public_url(
                    "GET",
                    url,
                    headers=headers,
                    timeout=CONST_HTTP_REQUEST_TIMEOUT_SECONDS,
                    stream=True,
                )
            ended_mono = time.perf_counter()
        except Exception as exc:
            failures.append(str(exc))
            if response is not None:
                response.close()
            continue

        rtt_ms = (ended_mono - started_mono) * 1000
        sample_rows.append({
            "method": method,
            "status_code": response.status_code,
            "rtt_ms": round(rtt_ms, 1),
            "final_url": response.url,
        })
        response.close()
        time.sleep(0.08)

    if not sample_rows:
        reason = failures[-1] if failures else "no usable response"
        raise ValueError(f"ticket latency estimate failed: {reason}")

    best_sample = min(sample_rows, key=lambda item: item["rtt_ms"])
    rtt_estimate = robust_estimate([row["rtt_ms"] for row in sample_rows])
    estimated_one_way_delay_ms = max(1, round(best_sample["rtt_ms"] / 2))
    jitter_ms = round(rtt_estimate["p95_ms"] - rtt_estimate["p50_ms"], 1)

    return {
        "success": True,
        "source": "ticket-site",
        "url": url,
        "final_url": best_sample["final_url"],
        "sample_count": len(sample_rows),
        "network_uplink_ms": estimated_one_way_delay_ms,
        "estimated_one_way_delay_ms": estimated_one_way_delay_ms,
        "best_rtt_ms": best_sample["rtt_ms"],
        "rtt_p50_ms": rtt_estimate["p50_ms"],
        "rtt_p95_ms": rtt_estimate["p95_ms"],
        "rtt_p99_ms": rtt_estimate["p99_ms"],
        "uncertainty_ms": rtt_estimate["uncertainty_ms"],
        "confidence": rtt_estimate["confidence"],
        "jitter_ms": jitter_ms,
        "samples": sample_rows,
        "warning": "This estimates browser/network reachability only. It cannot predict queueing, captcha, ticket inventory, or exact one-way delay.",
    }


def calibrate_time_by_mode(body):
    mode = str(body.get("mode") or "http").strip().lower()
    if mode not in ("auto", "ntp", "http", "system"):
        mode = "http"

    if mode == "system":
        return {
            "success": True,
            "source": "system",
            "clock_offset_ms": 0,
            "clock_uncertainty_ms": 999999,
            "network_uplink_ms": 0,
            "estimated_one_way_delay_ms": 0,
            "sample_count": 0,
            "confidence": "low",
            "warning": "Using local system clock without external calibration.",
        }

    if mode == "ntp":
        servers = body.get("ntp_servers") or DEFAULT_TIME_CALIBRATION["ntp_servers"]
        if isinstance(servers, str):
            servers = [item.strip() for item in servers.split(",") if item.strip()]
        if not isinstance(servers, (list, tuple)):
            raise ValueError("ntp_servers must be a list")
        if len(servers) > CONST_NTP_SERVER_LIMIT:
            raise ValueError(
                f"at most {CONST_NTP_SERVER_LIMIT} NTP servers are allowed"
            )
        normalized_servers = []
        for server in servers:
            normalized = str(server or "").strip()
            if (
                not normalized
                or len(normalized) > 253
                or not re.fullmatch(r"[A-Za-z0-9.:\-\[\]]+", normalized)
            ):
                raise ValueError("invalid NTP server")
            if normalized not in normalized_servers:
                normalized_servers.append(normalized)
        if not normalized_servers:
            raise ValueError("at least one NTP server is required")
        try:
            timeout_ms = max(
                100,
                min(
                    CONST_NTP_TIMEOUT_MS_LIMIT,
                    int(
                        body.get(
                            "ntp_timeout_ms",
                            DEFAULT_TIME_CALIBRATION["ntp_timeout_ms"],
                        )
                    ),
                ),
            )
        except (TypeError, ValueError):
            timeout_ms = DEFAULT_TIME_CALIBRATION["ntp_timeout_ms"]
        try:
            samples_per_server = max(
                1,
                min(
                    CONST_NTP_SAMPLE_LIMIT,
                    int(
                        body.get(
                            "samples",
                            body.get(
                                "ntp_samples_per_server",
                                DEFAULT_TIME_CALIBRATION[
                                    "ntp_samples_per_server"
                                ],
                            ),
                        )
                    ),
                ),
            )
        except (TypeError, ValueError):
            samples_per_server = 1
        try:
            min_valid_samples = max(
                1,
                min(
                    len(normalized_servers) * samples_per_server,
                    int(
                        body.get(
                            "ntp_min_valid_samples",
                            DEFAULT_TIME_CALIBRATION["ntp_min_valid_samples"],
                        )
                    ),
                ),
            )
        except (TypeError, ValueError):
            min_valid_samples = min(
                len(normalized_servers) * samples_per_server,
                DEFAULT_TIME_CALIBRATION["ntp_min_valid_samples"],
            )
        return calibrate_ntp_servers(
            normalized_servers,
            timeout_ms=timeout_ms,
            samples_per_server=samples_per_server,
            min_valid_samples=min_valid_samples,
        )

    if mode == "auto":
        candidates = []
        try:
            auto_servers = (
                body.get("ntp_servers")
                or DEFAULT_TIME_CALIBRATION["ntp_servers"]
            )
            if isinstance(auto_servers, str):
                auto_servers = [
                    item.strip()
                    for item in auto_servers.split(",")
                    if item.strip()
                ]
            auto_servers = list(auto_servers)[:2]
            try:
                auto_ntp_timeout_ms = min(
                    1000,
                    int(
                        body.get(
                            "ntp_timeout_ms",
                            DEFAULT_TIME_CALIBRATION["ntp_timeout_ms"],
                        )
                    ),
                )
            except (TypeError, ValueError):
                auto_ntp_timeout_ms = 1000
            try:
                auto_ntp_samples = min(
                    2,
                    int(
                        body.get(
                            "ntp_samples_per_server",
                            body.get("samples", 2),
                        )
                    ),
                )
            except (TypeError, ValueError):
                auto_ntp_samples = 2
            auto_ntp_body = {
                **body,
                "mode": "ntp",
                "ntp_servers": auto_servers,
                "ntp_timeout_ms": auto_ntp_timeout_ms,
                "samples": auto_ntp_samples,
                "ntp_samples_per_server": auto_ntp_samples,
            }
            ntp_result = calibrate_time_by_mode(auto_ntp_body)
            ntp_result["age_seconds"] = 0
            candidates.append(ntp_result)
        except Exception as exc:
            candidates.append({"success": False, "source": "ntp", "error": str(exc)})
        try:
            http_result = calibrate_time_source_url(
                body.get("url", ""),
                min(2, int(body.get("samples", 2))),
            )
            http_result["age_seconds"] = 0
            candidates.append(http_result)
        except Exception as exc:
            candidates.append({"success": False, "source": "http", "error": str(exc)})
        selected = select_time_source(candidates)
        selected["candidates"] = candidates
        return selected

    return calibrate_time_source_url(body.get("url", ""), body.get("samples", 5))


class LocalControlHandler(tornado.web.RequestHandler):
    """Reject DNS-rebinding and cross-origin access to the local control API."""

    def prepare(self):
        if not is_allowed_control_request(self.request):
            raise tornado.web.HTTPError(403, reason="loopback same-origin request required")
        if len(self.request.body or b"") > CONST_CONTROL_MAX_BODY_BYTES:
            raise tornado.web.HTTPError(413, reason="request body too large")


class QuestionHandler(LocalControlHandler):
    def get(self):
        """Read the instance's MAXBOT_QUESTION.txt and return its content"""
        profile_name = self.get_query_argument("profile", "")
        if profile_name and not is_valid_profile_name(profile_name):
            self.set_status(400)
            self.write({"exists": False, "question": "", "error": "invalid profile name"})
            return
        question_text = ""
        question_file = get_instance_state_filepath(profile_name, CONST_MAXBOT_QUESTION_FILE)

        # Check if file exists
        if os.path.exists(question_file):
            try:
                with open(question_file, "r", encoding="utf-8") as f:
                    question_text = f.read().strip()
            except Exception as e:
                print(f"Error reading question file: {e}")

        # Return JSON response
        self.write({
            "exists": os.path.exists(question_file),
            "question": question_text
        })

class VersionHandler(LocalControlHandler):
    def get(self):
        self.write({"version":self.application.version})

class ShutdownHandler(LocalControlHandler):
    def post(self):
        global GLOBAL_SERVER_SHUTDOWN
        GLOBAL_SERVER_SHUTDOWN = True
        self.write({"shutdown": True})
        application = getattr(self, "application", None)
        shutdown_event = getattr(application, "shutdown_event", None)
        if shutdown_event is not None:
            IOLoop.current().add_callback(shutdown_event.set)

class StatusHandler(LocalControlHandler):
    def get(self):
        profile_name = self.get_query_argument("profile", "")
        if profile_name and not is_valid_profile_name(profile_name):
            self.set_status(400)
            self.write({"error": "invalid profile name"})
            return
        idle_filepath = get_instance_state_filepath(profile_name, CONST_MAXBOT_INT28_FILE)
        is_paused = os.path.exists(idle_filepath)
        url = read_last_url_from_file(profile_name)
        self.write({"status": not is_paused, "last_url": url})

class InstancesHandler(LocalControlHandler):
    def get(self):
        instances = [get_instance_status(name) for name in list_instance_ids()]
        self.write({"instances": instances})


class CleanupInstancesHandler(LocalControlHandler):
    def post(self):
        removed, preserved = cleanup_orphan_instance_dirs()
        self.write({"success": True, "removed": removed, "preserved": preserved})


class PauseHandler(LocalControlHandler):
    def post(self):
        profile_name = self.get_query_argument("profile", "")
        if profile_name and not is_valid_profile_name(profile_name):
            self.set_status(400)
            self.write({"pause": False, "error": "invalid profile name"})
            return
        maxbot_idle(profile_name)
        self.write({"pause": True})

class ResumeHandler(LocalControlHandler):
    def post(self):
        profile_name = self.get_query_argument("profile", "")
        if profile_name and not is_valid_profile_name(profile_name):
            self.set_status(400)
            self.write({"resume": False, "error": "invalid profile name"})
            return
        maxbot_resume(profile_name)
        self.write({"resume": True})

class StopHandler(LocalControlHandler):
    def post(self):
        profile_name = self.get_query_argument("profile", "")
        if profile_name and not is_valid_profile_name(profile_name):
            self.set_status(400)
            self.write({"stop": False, "error": "invalid profile name"})
            return
        maxbot_stop(profile_name)
        self.write({"stop": True})


class QuitHandler(LocalControlHandler):
    def post(self):
        profile_name = self.get_query_argument("profile", "")
        if profile_name and not is_valid_profile_name(profile_name):
            self.set_status(400)
            self.write({"quit": False, "error": "invalid profile name"})
            return
        maxbot_quit(profile_name)
        self.write({"quit": True})


class RunHandler(LocalControlHandler):
    def post(self):
        profile_name = self.get_query_argument("profile", "")
        if profile_name and not is_valid_profile_name(profile_name):
            self.set_status(400)
            self.write({"run": False, "error": "invalid profile name"})
            return
        instance_override = self.get_query_argument("instance", "")
        if instance_override and not is_valid_profile_name(instance_override):
            self.set_status(400)
            self.write({"run": False, "error": "invalid instance name"})
            return
        base_profile = profile_name or CONST_DEFAULT_PROFILE
        if not _profile_exists_for_run(base_profile):
            self.set_status(404)
            self.write({"run": False, "error": "profile not found"})
            return
        if not _is_valid_instance_override(base_profile, instance_override):
            self.set_status(400)
            self.write(
                {
                    "run": False,
                    "error": "instance must be a numbered child of the profile",
                }
            )
            return
        display_name = instance_override or base_profile
        if not _claim_run_instance(display_name, profile_name=base_profile):
            if not _profile_exists_for_run(base_profile):
                self.set_status(404)
                self.write({"run": False, "error": "profile not found"})
                return
            self.set_status(409)
            self.write({"run": False, "error": "instance is already starting or running"})
            return
        print('run button pressed. profile:', display_name)
        try:
            process = launch_maxbot(profile_name, instance_override)
        except Exception as exc:
            _release_run_instance(display_name)
            print("[ERROR] Bot launch failed:", util.redact_sensitive_text(str(exc)))
            self.set_status(500)
            self.write({"run": False, "error": "launch failed"})
            return
        pid = int(getattr(process, "pid", 0) or 0)
        poll = getattr(process, "poll", None)
        if process is None or pid <= 0 or (callable(poll) and poll() is not None):
            _release_run_instance(display_name)
            self.set_status(500)
            self.write({"run": False, "error": "process did not start"})
            return
        _track_run_instance_startup(display_name, process)
        self.write(
            {
                "run": True,
                "profile": display_name,
                "command_id": uuid.uuid4().hex,
                "pid": pid,
                "ack": "spawned",
            }
        )

class LoadJsonHandler(LocalControlHandler):
    def get(self):
        profile_name = self.get_query_argument("profile", "")
        if profile_name and not is_valid_profile_name(profile_name):
            self.set_status(400)
            self.write({"error": "invalid profile name"})
            return
        config_filepath, config_dict = load_json(profile_name)

        # Dynamically generate remote_url based on server_port (Issue #156)
        server_port = config_dict.get("advanced", {}).get("server_port", CONST_SERVER_PORT)
        if not isinstance(server_port, int) or server_port < 1024 or server_port > 65535:
            server_port = CONST_SERVER_PORT
        config_dict["advanced"]["remote_url"] = f'"http://127.0.0.1:{server_port}/"'

        public_config = mask_config_secrets(config_dict)
        public_config[CONST_SECRET_SOURCE_PROFILE_KEY] = profile_name or CONST_DEFAULT_PROFILE
        self.write(public_config)

class ResetJsonHandler(LocalControlHandler):
    def post(self):
        profile_name = self.get_query_argument("profile", "")
        if profile_name and profile_name != CONST_DEFAULT_PROFILE:
            self.set_status(400)
            self.write({"error": "reset only supports the default profile"})
            return
        config_filepath, config_dict = reset_json()
        try:
            util.save_json(config_dict, config_filepath)
        except Exception as exc:
            print("[ERROR] Failed to reset settings:", util.redact_sensitive_text(str(exc)))
            self.set_status(500)
            self.write({"error": "failed to save settings"})
            return
        self.write(config_dict)

class SaveJsonHandler(LocalControlHandler):
    def post(self):
        _body = None
        is_pass_check = True
        error_message = ""
        error_code = 0

        if is_pass_check:
            is_pass_check = False
            try :
                _body = json.loads(self.request.body)
                if isinstance(_body, dict):
                    is_pass_check = True
                else:
                    error_message = "wrong json format"
                    error_code = 1002
            except Exception:
                error_message = "wrong json format"
                error_code = 1002
                pass

        profile_name = self.get_query_argument("profile", "")
        if is_pass_check and profile_name and not is_valid_profile_name(profile_name):
            is_pass_check = False
            error_message = "invalid profile name"
            error_code = 1003

        if is_pass_check:
            config_filepath = get_profile_filepath(profile_name)
            os.makedirs(os.path.dirname(config_filepath), exist_ok=True)
            _, existing_config = load_json(profile_name)
            config_dict = restore_masked_config_secrets(_body, existing_config)

            if config_dict["kktix"]["max_dwell_time"] > 0:
                if config_dict["kktix"]["max_dwell_time"] < 15:
                    # min value is 15 seconds.
                    config_dict["kktix"]["max_dwell_time"] = 15

            if config_dict["advanced"]["reset_browser_interval"] > 0:
                if config_dict["advanced"]["reset_browser_interval"] < 20:
                    # min value is 20 seconds.
                    config_dict["advanced"]["reset_browser_interval"] = 20

            # due to cloudflare.
            if ".cityline.com" in config_dict["homepage"]:
                config_dict["webdriver_type"] = CONST_WEBDRIVER_TYPE_NODRIVER

            try:
                util.save_json(config_dict, config_filepath)
            except Exception as exc:
                print("[ERROR] Failed to save settings:", util.redact_sensitive_text(str(exc)))
                self.set_status(500)
                self.write(dict(error=dict(message="failed to save settings", code=1004)))
                return

        if not is_pass_check:
            self.set_status(401)
            self.write(dict(error=dict(message=error_message,code=error_code)))

        self.finish()


class ProfilesHandler(LocalControlHandler):
    """Manage instance profiles (full settings.json copies under profiles/)."""

    def get(self):
        details = []
        for name in list_profile_names():
            homepage = ""
            try:
                with open(get_profile_filepath(name), encoding='utf-8') as json_data:
                    homepage = json.load(json_data).get("homepage", "")
            except Exception:
                pass
            details.append({"name": name, "homepage": homepage})
        self.write({"profiles": [d["name"] for d in details], "details": details})

    def post(self):
        try:
            _body = parse_json_object(self.request.body)
        except (TypeError, ValueError, json.JSONDecodeError):
            self.set_status(400)
            self.write({"success": False, "error": "wrong json format"})
            return

        profile_name = _body.get("name", "")
        if not is_valid_profile_name(profile_name) or profile_name == CONST_DEFAULT_PROFILE:
            self.set_status(400)
            self.write({"success": False, "error": "invalid profile name (allowed: A-Z a-z 0-9 _ -, max 32 chars)"})
            return

        config_filepath = get_profile_filepath(profile_name)
        if os.path.exists(config_filepath):
            self.set_status(409)
            self.write({"success": False, "error": "profile already exists"})
            return

        config_dict = _body.get("config")
        if not isinstance(config_dict, dict):
            _, config_dict = load_json()
        else:
            source_profile = config_dict.get(
                CONST_SECRET_SOURCE_PROFILE_KEY,
                CONST_DEFAULT_PROFILE,
            )
            if not isinstance(source_profile, str) or not is_valid_profile_name(source_profile):
                source_profile = CONST_DEFAULT_PROFILE
            _, source_config = load_json(source_profile)
            config_dict = restore_masked_config_secrets(config_dict, source_config)
        config_dict = migrate_config(config_dict)

        os.makedirs(os.path.dirname(config_filepath), exist_ok=True)
        try:
            util.save_json(config_dict, config_filepath)
        except Exception as exc:
            print("[ERROR] Failed to create profile:", util.redact_sensitive_text(str(exc)))
            self.set_status(500)
            self.write({"success": False, "error": "failed to save profile"})
            return
        self.write({"success": True, "profile": profile_name})

    def delete(self):
        profile_name = self.get_query_argument("profile", "")
        if not is_valid_profile_name(profile_name) or profile_name == CONST_DEFAULT_PROFILE:
            self.set_status(400)
            self.write({"success": False, "error": "invalid profile name"})
            return
        config_filepath = get_profile_filepath(profile_name)
        try:
            with RUN_INSTANCE_LOCK:
                _prune_expired_run_instances_locked(time.monotonic())
                if not os.path.exists(config_filepath):
                    self.set_status(404)
                    self.write({"success": False, "error": "profile not found"})
                    return

                starting_instances = _pending_run_instances_for_profile_locked(
                    profile_name
                )
                related_instances = sorted(
                    set(get_related_instance_ids(profile_name) + starting_instances)
                )
                alive_instances = [
                    instance_id
                    for instance_id in related_instances
                    if is_instance_alive(instance_id)
                ]
                if starting_instances or alive_instances:
                    self.set_status(409)
                    self.write(
                        {
                            "success": False,
                            "error": "profile has starting or running instances",
                            "starting_instances": starting_instances,
                            "running_instances": alive_instances,
                        }
                    )
                    return

                os.remove(config_filepath)
                removed_dirs = [
                    instance_id
                    for instance_id in related_instances
                    if remove_instance_state_dir(instance_id)
                ]
        except Exception as exc:
            self.set_status(500)
            self.write({"success": False, "error": str(exc)})
            return
        self.write(
            {
                "success": True,
                "stopped_instances": [],
                "removed_instance_dirs": removed_dirs,
            }
        )


class SendkeyHandler(LocalControlHandler):
    def post(self):
        try:
            body = parse_json_object(self.request.body)
        except (TypeError, ValueError, json.JSONDecodeError):
            self.set_status(400)
            self.write({"return": False, "error": "wrong json format"})
            return
        try:
            config_filepath = get_sendkey_filepath(body.get("token"))
        except ValueError:
            self.set_status(400)
            self.write({"return": False, "error": "invalid token"})
            return

        try:
            util.save_json(body, config_filepath)
        except Exception as exc:
            print("[ERROR] Failed to persist sendkey payload:", util.redact_sensitive_text(str(exc)))
            self.set_status(500)
            self.write({"return": False, "error": "failed to persist payload"})
            return
        self.write({"return": True})


def send_discord_webhook_test(webhook_url, payload):
    return requests.post(webhook_url, json=payload, timeout=5.0)


def send_telegram_test_messages(url, chat_ids, text, bot_token):
    errors = []
    ok_count = 0
    for cid in chat_ids:
        try:
            payload = {"chat_id": cid, "text": text}
            response = requests.post(url, json=payload, timeout=5.0)
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Telegram response is not a JSON object")
            if response.status_code == 200 and result.get("ok", False):
                ok_count += 1
            else:
                desc = result.get("description", "HTTP %d" % response.status_code)
                errors.append(f"{cid}: {desc}")
        except (requests.RequestException, ValueError) as exc:
            safe_msg = util.redact_sensitive_text(str(exc), [bot_token, url])
            errors.append(f"{cid}: {safe_msg}")
    return ok_count, errors


class TestDiscordWebhookHandler(LocalControlHandler):
    ALLOWED_HOSTS = ("discord.com", "discordapp.com")

    async def post(self):
        try:
            body = parse_json_object(self.request.body)
        except (TypeError, ValueError, json.JSONDecodeError):
            self.set_status(400)
            self.write({"success": False, "message": "wrong json format"})
            return

        profile_name = self.get_query_argument("profile", "")
        if profile_name and not is_valid_profile_name(profile_name):
            self.set_status(400)
            self.write({"success": False, "message": "invalid profile name"})
            return

        webhook_value = body.get("webhook_url", "")
        custom_message_value = body.get("custom_message", "")
        if not isinstance(webhook_value, str) or not isinstance(
            custom_message_value, str
        ):
            self.set_status(400)
            self.write({"success": False, "message": "invalid field type"})
            return
        webhook_url = webhook_value.strip()
        _, config_dict = load_json(profile_name)
        if webhook_url == CONST_SECRET_MASK:
            webhook_url = config_dict.get("advanced", {}).get("discord_webhook_url", "").strip()
        if not webhook_url:
            self.write({"success": False, "message": "webhook URL is empty"})
            return

        from urllib.parse import urlparse
        try:
            parsed = urlparse(webhook_url)
        except Exception:
            self.write({"success": False, "message": "invalid URL format"})
            return

        if parsed.scheme != "https":
            self.write({"success": False, "message": "only HTTPS URLs are allowed"})
            return

        if not any(parsed.netloc == host or parsed.netloc.endswith("." + host) for host in self.ALLOWED_HOSTS):
            self.write({"success": False, "message": "only Discord webhook URLs are allowed"})
            return

        if not parsed.path.startswith("/api/webhooks/"):
            self.write({"success": False, "message": "invalid Discord webhook URL format"})
            return

        debug = util.create_debug_logger(config_dict)

        custom_message = body.get("custom_message", "").strip()
        content = custom_message if custom_message else f"[Test] {APP_NAME} webhook test successful!"
        payload = {
            "content": content,
            "username": APP_NAME
        }
        try:
            response = await run_bounded_control_task(
                send_discord_webhook_test,
                webhook_url,
                payload,
            )
            if response.status_code in (200, 204):
                debug.log("[Discord Webhook] Test OK")
                self.write({"success": True, "message": "ok"})
            else:
                debug.log("[Discord Webhook] Test failed: HTTP %d" % response.status_code)
                self.write({"success": False, "message": "HTTP %d" % response.status_code})
        except ControlTaskBusyError:
            self.set_status(429)
            self.write({"success": False, "message": "control task queue is busy"})
        except asyncio.TimeoutError:
            self.set_status(504)
            self.write({"success": False, "message": "request timed out"})
        except Exception as exc:
            safe_msg = util.redact_sensitive_text(str(exc), [webhook_url])
            debug.log("[Discord Webhook] Test failed: %s" % safe_msg)
            self.write({"success": False, "message": safe_msg})

class TestTelegramHandler(LocalControlHandler):
    async def post(self):
        try:
            body = parse_json_object(self.request.body)
        except (TypeError, ValueError, json.JSONDecodeError):
            self.set_status(400)
            self.write({"success": False, "message": "wrong json format"})
            return

        profile_name = self.get_query_argument("profile", "")
        if profile_name and not is_valid_profile_name(profile_name):
            self.set_status(400)
            self.write({"success": False, "message": "invalid profile name"})
            return

        bot_token_value = body.get("bot_token", "")
        chat_id_value = body.get("chat_id", "")
        custom_message_value = body.get("custom_message", "")
        if not all(
            isinstance(value, str)
            for value in (bot_token_value, chat_id_value, custom_message_value)
        ):
            self.set_status(400)
            self.write({"success": False, "message": "invalid field type"})
            return
        bot_token = bot_token_value.strip()
        chat_id = chat_id_value.strip()
        _, config_dict = load_json(profile_name)
        if bot_token == CONST_SECRET_MASK:
            bot_token = config_dict.get("advanced", {}).get("telegram_bot_token", "").strip()

        if not bot_token:
            self.write({"success": False, "message": "Bot Token is empty"})
            return

        import re
        if not re.match(r'^\d+:[A-Za-z0-9_-]+$', bot_token):
            self.write({"success": False, "message": "Bot Token format invalid"})
            return

        if not chat_id:
            self.write({"success": False, "message": "Chat ID is empty"})
            return

        chat_ids = [cid.strip() for cid in chat_id.split(",") if cid.strip()]
        if not chat_ids:
            self.write({"success": False, "message": "Chat ID is empty"})
            return

        invalid_ids = [cid for cid in chat_ids if not re.match(r'^-?\d+$', cid)]
        if invalid_ids:
            self.write({"success": False, "message": "Chat ID format invalid: %s" % ", ".join(invalid_ids)})
            return

        debug = util.create_debug_logger(config_dict)

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        custom_message = custom_message_value.strip()
        text = custom_message if custom_message else f"[Test] {APP_NAME} Telegram test successful!"
        if len(chat_ids) > CONST_TELEGRAM_CHAT_LIMIT:
            self.set_status(400)
            self.write(
                {
                    "success": False,
                    "message": (
                        f"At most {CONST_TELEGRAM_CHAT_LIMIT} Chat IDs are allowed"
                    ),
                }
            )
            return

        try:
            ok_count, errors = await run_bounded_control_task(
                send_telegram_test_messages,
                url,
                chat_ids,
                text,
                bot_token,
            )
        except ControlTaskBusyError:
            self.set_status(429)
            self.write(
                {"success": False, "message": "control task queue is busy"}
            )
            return
        except asyncio.TimeoutError:
            self.set_status(504)
            self.write({"success": False, "message": "request timed out"})
            return
        except Exception:
            self.set_status(502)
            self.write({"success": False, "message": "Telegram test failed"})
            return

        if ok_count == len(chat_ids):
            debug.log("[Telegram] Test OK (%d chat(s))" % ok_count)
            self.write({"success": True, "message": "ok"})
        elif ok_count > 0:
            debug.log("[Telegram] Test partial: %d/%d OK" % (ok_count, len(chat_ids)))
            self.write({"success": True, "message": "%d/%d OK, errors: %s" % (ok_count, len(chat_ids), "; ".join(errors))})
        else:
            msg = "; ".join(errors)
            debug.log("[Telegram] Test failed: %s" % msg)
            self.write({"success": False, "message": msg})


class TimeCalibrationHandler(LocalControlHandler):
    async def post(self):
        try:
            self.set_header("Content-Type", "application/json; charset=utf-8")
        except Exception:
            pass

        try:
            body = parse_json_object(self.request.body)
        except (TypeError, ValueError, json.JSONDecodeError):
            self.set_status(400)
            self.write({"success": False, "error": "wrong json format"})
            return

        try:
            result = await run_bounded_control_task(
                calibrate_time_by_mode,
                body,
            )
        except ControlTaskBusyError:
            self.set_status(429)
            self.write({"success": False, "error": "control task queue is busy"})
            return
        except asyncio.TimeoutError:
            self.set_status(504)
            self.write({"success": False, "error": "time calibration timed out"})
            return
        except Exception as exc:
            self.set_status(400)
            self.write({"success": False, "error": str(exc)})
            return

        self.write(result)


class TicketLatencyHandler(LocalControlHandler):
    async def post(self):
        try:
            self.set_header("Content-Type", "application/json; charset=utf-8")
        except Exception:
            pass

        try:
            body = parse_json_object(self.request.body)
        except (TypeError, ValueError, json.JSONDecodeError):
            self.set_status(400)
            self.write({"success": False, "error": "wrong json format"})
            return

        try:
            result = await run_bounded_control_task(
                estimate_ticket_site_latency,
                body.get("url", ""),
                body.get("samples", 3),
            )
        except ControlTaskBusyError:
            self.set_status(429)
            self.write({"success": False, "error": "control task queue is busy"})
            return
        except asyncio.TimeoutError:
            self.set_status(504)
            self.write({"success": False, "error": "latency check timed out"})
            return
        except Exception as exc:
            self.set_status(400)
            self.write({"success": False, "error": str(exc)})
            return

        self.write(result)


class PlatformTimingCapabilityHandler(LocalControlHandler):
    def post(self):
        try:
            body = parse_json_object(self.request.body)
        except (TypeError, ValueError, json.JSONDecodeError):
            self.set_status(400)
            self.write({"success": False, "error": "wrong json format"})
            return

        config_dict = {"refresh_calibration": body.get("refresh_calibration", {})}
        if "homepage" in body:
            config_dict["homepage"] = body.get("homepage", "")
        decision = get_platform_timing_capability(
            body.get("platform_id"),
            body.get("url") or body.get("homepage"),
            config_dict,
        )
        self.write({"success": True, **decision.to_dict()})


class ControlTaskBusyError(RuntimeError):
    pass


def _run_control_task_with_slot(function, args):
    try:
        return function(*args)
    finally:
        CONTROL_TASK_SLOTS.release()


def _consume_control_task_completion(future):
    """Retrieve late failures after an HTTP timeout to avoid orphan warnings."""
    if future.cancelled():
        return
    try:
        future.exception()
    except (asyncio.CancelledError, Exception):
        pass


async def run_bounded_control_task(function, *args):
    """Run a blocking control-plane task without an unbounded executor queue."""

    if not CONTROL_TASK_SLOTS.acquire(blocking=False):
        raise ControlTaskBusyError("control task queue is busy")
    try:
        future = IOLoop.current().run_in_executor(
            CALIBRATION_EXECUTOR,
            _run_control_task_with_slot,
            function,
            args,
        )
    except Exception:
        CONTROL_TASK_SLOTS.release()
        raise
    future.add_done_callback(_consume_control_task_completion)
    # Keep the executor future alive after a client timeout/cancellation so the
    # worker wrapper always releases its slot. The blocking functions have
    # their own finite network/socket timeouts.
    return await asyncio.wait_for(
        asyncio.shield(future),
        timeout=CONST_CONTROL_TASK_TIMEOUT_SECONDS,
    )


def _allow_ocr_request(now=None):
    current = time.monotonic() if now is None else float(now)
    cutoff = current - CONST_OCR_RATE_LIMIT_WINDOW_SECONDS
    with OCR_RATE_LIMIT_LOCK:
        while OCR_REQUEST_TIMES and OCR_REQUEST_TIMES[0] <= cutoff:
            OCR_REQUEST_TIMES.popleft()
        if len(OCR_REQUEST_TIMES) >= CONST_OCR_RATE_LIMIT_REQUESTS:
            return False
        OCR_REQUEST_TIMES.append(current)
        return True


def _classify_ocr_with_slot(ocr, image_bytes):
    try:
        return ocr.classification(image_bytes)
    finally:
        OCR_REQUEST_SLOTS.release()


class OcrHandler(LocalControlHandler):
    def get(self):
        self.write({"answer": "1234"})

    async def post(self):
        raw_body = self.request.body or b""
        if len(raw_body) > CONST_CONTROL_MAX_BODY_BYTES:
            self.set_status(413)
            self.write({"answer": "", "error": "request body too large"})
            return
        try:
            body = json.loads(raw_body)
        except (TypeError, ValueError):
            self.set_status(400)
            self.write({"answer": "", "error": "wrong json format"})
            return
        image_data = body.get("image_data") if isinstance(body, dict) else None
        if not isinstance(image_data, str) or not image_data:
            self.set_status(400)
            self.write({"answer": "", "error": "image_data not exist"})
            return
        if len(image_data) > CONST_OCR_MAX_BASE64_BYTES:
            self.set_status(413)
            self.write({"answer": "", "error": "image payload too large"})
            return
        try:
            image_bytes = base64.b64decode(image_data, validate=True)
        except (binascii.Error, ValueError):
            self.set_status(400)
            self.write({"answer": "", "error": "invalid image_data"})
            return
        if not image_bytes or len(image_bytes) > CONST_OCR_MAX_DECODED_BYTES:
            self.set_status(413)
            self.write({"answer": "", "error": "image payload too large"})
            return
        application = getattr(self, "application", None)
        ocr = getattr(application, "ocr", None)
        if ocr is None:
            self.set_status(503)
            self.write({"answer": "", "error": "OCR is initializing or unavailable"})
            return
        if not _allow_ocr_request():
            self.set_status(429)
            self.write({"answer": "", "error": "OCR rate limit exceeded"})
            return
        if not OCR_REQUEST_SLOTS.acquire(blocking=False):
            self.set_status(429)
            self.write({"answer": "", "error": "OCR is busy"})
            return

        try:
            future = IOLoop.current().run_in_executor(
                OCR_EXECUTOR,
                _classify_ocr_with_slot,
                ocr,
                image_bytes,
            )
        except Exception:
            OCR_REQUEST_SLOTS.release()
            self.set_status(500)
            self.write({"answer": "", "error": "OCR unavailable"})
            return

        future.add_done_callback(_consume_control_task_completion)
        try:
            answer = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=CONST_OCR_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            self.set_status(504)
            self.write({"answer": "", "error": "OCR timeout"})
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            self.set_status(500)
            self.write({"answer": "", "error": "OCR failed"})
            return

        self.write({"answer": str(answer or "")})

class QueryHandler(LocalControlHandler):
    def format_config_keyword_for_json(self, user_input):
        if len(user_input) > 0:
            # Remove any existing quotes first
            user_input = user_input.replace('"', '').replace("'", '')

            # Add quotes to each keyword
            # Use semicolon as the ONLY delimiter (Issue #23)
            if util.CONST_KEYWORD_DELIMITER in user_input:
                items = user_input.split(util.CONST_KEYWORD_DELIMITER)
                user_input = ','.join([f'"{item.strip()}"' for item in items if item.strip()])
            else:
                user_input = f'"{user_input.strip()}"'
        return user_input

    def compose_as_json(self, user_input):
        user_input = self.format_config_keyword_for_json(user_input)
        return "{\"data\":[%s]}" % user_input

    def get(self):
        global txt_answer_value
        answer_text = ""
        try:
            answer_text = txt_answer_value.get().strip()
        except Exception as exc:
            pass
        answer_text_output = self.compose_as_json(answer_text)
        #print("answer_text_output:", answer_text_output)
        self.write(answer_text_output)

def _signal_server_startup(startup_event, startup_result, started, error=""):
    if startup_result is not None and "started" not in startup_result:
        startup_result["started"] = bool(started)
        startup_result["error"] = str(error)
    if startup_event is not None:
        startup_event.set()


def _create_ocr_engine():
    ocr_module = globals().get("ddddocr")
    if ocr_module is None:
        return None
    return ocr_module.DdddOcr(show_ad=False, beta=True)


async def _initialize_ocr_application(app):
    try:
        ocr = await IOLoop.current().run_in_executor(
            OCR_EXECUTOR,
            _create_ocr_engine,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(
            "[WARNING] OCR initialization failed:",
            util.redact_sensitive_text(str(exc)),
        )
        return
    shutdown_event = getattr(app, "shutdown_event", None)
    is_shutting_down = bool(
        shutdown_event is not None
        and getattr(shutdown_event, "is_set", lambda: False)()
    )
    if not is_shutting_down:
        app.ocr = ocr


async def main_server(startup_event=None, startup_result=None):
    global GLOBAL_SERVER_SHUTDOWN
    GLOBAL_SERVER_SHUTDOWN = False
    app = Application([
        ("/version", VersionHandler),
        ("/shutdown", ShutdownHandler),
        ("/sendkey", SendkeyHandler),

        # status api
        ("/status", StatusHandler),
        ("/instances", InstancesHandler),
        ("/cleanup_instances", CleanupInstancesHandler),
        ("/pause", PauseHandler),
        ("/resume", ResumeHandler),
        ("/stop", StopHandler),
        ("/quit", QuitHandler),
        ("/run", RunHandler),
        
        # json api
        ("/load", LoadJsonHandler),
        ("/save", SaveJsonHandler),
        ("/reset", ResetJsonHandler),
        ("/profiles", ProfilesHandler),

        ("/test_discord_webhook", TestDiscordWebhookHandler),
        ("/test_telegram", TestTelegramHandler),
        ("/time_calibration", TimeCalibrationHandler),
        ("/ticket_latency", TicketLatencyHandler),
        ("/platform_timing_capability", PlatformTimingCapabilityHandler),
        ("/ocr", OcrHandler),
        ("/query", QueryHandler),
        ("/question", QuestionHandler),
        ('/(.*)', NoCacheStaticFileHandler, {"path": os.path.join(SCRIPT_DIR, 'www')}),
    ])
    app.ocr = None
    app.version = CONST_APP_VERSION
    app.shutdown_event = asyncio.Event()

    # Get server_port from config, fallback to default (Issue #156)
    _, config_dict = load_json()
    server_port = config_dict.get("advanced", {}).get("server_port", CONST_SERVER_PORT)

    # Validate port range
    if not isinstance(server_port, int) or server_port < 1024 or server_port > 65535:
        print(f"[WARNING] Invalid server_port: {server_port}, using default: {CONST_SERVER_PORT}")
        server_port = CONST_SERVER_PORT

    app.http_server = app.listen(
        server_port,
        address=CONST_SERVER_ADDRESS,
        max_body_size=CONST_CONTROL_MAX_BODY_BYTES,
        max_buffer_size=CONST_CONTROL_MAX_BUFFER_BYTES,
    )
    app.ocr_initialization_task = asyncio.create_task(
        _initialize_ocr_application(app)
    )
    # Let the event loop schedule asynchronous initialization before reporting
    # readiness. OCR itself remains optional and never blocks the listener.
    await asyncio.sleep(0)
    _signal_server_startup(startup_event, startup_result, True)
    print("server running on port:", server_port)

    url = f"http://{CONST_SERVER_ADDRESS}:{server_port}/settings.html"
    print("goto url:", url)
    try:
        webbrowser.open_new(url)
    except Exception as exc:
        print("[WARNING] Failed to open settings page:", util.redact_sensitive_text(str(exc)))
    try:
        await app.shutdown_event.wait()
    finally:
        http_server = getattr(app, "http_server", None)
        if http_server is not None:
            http_server.stop()
            close_all = getattr(http_server, "close_all_connections", None)
            if callable(close_all):
                await close_all()
        ocr_task = getattr(app, "ocr_initialization_task", None)
        if ocr_task is not None and not ocr_task.done():
            ocr_task.cancel()
        if ocr_task is not None:
            await asyncio.gather(ocr_task, return_exceptions=True)

def get_server_port():
    """Get server port from config file, fallback to default."""
    _, config_dict = load_json()
    server_port = config_dict.get("advanced", {}).get("server_port", CONST_SERVER_PORT)
    if not isinstance(server_port, int) or server_port < 1024 or server_port > 65535:
        server_port = CONST_SERVER_PORT
    return server_port

def web_server(startup_event=None, startup_result=None):
    server_port = get_server_port()
    is_port_binded = util.is_connectable(server_port, host=CONST_SERVER_ADDRESS)
    if is_port_binded:
        existing_url = f"http://{CONST_SERVER_ADDRESS}:{server_port}"
        is_current_hunterx = False
        try:
            response = requests.get(f"{existing_url}/version", timeout=0.75)
            payload = response.json()
            is_current_hunterx = bool(
                response.status_code == 200
                and isinstance(payload, dict)
                and payload.get("version") == CONST_APP_VERSION
            )
        except (requests.RequestException, ValueError):
            pass
        if is_current_hunterx:
            print("HunterX settings server is already running on port:", server_port)
            try:
                webbrowser.open_new(f"{existing_url}/settings.html")
            except Exception as exc:
                print(
                    "[WARNING] Failed to open existing settings page:",
                    util.redact_sensitive_text(str(exc)),
                )
            error = "HunterX settings server already running"
        else:
            print(
                "[ERROR] Port",
                server_port,
                "is occupied by another or incompatible service.",
            )
            error = "port occupied by incompatible service"
        _signal_server_startup(startup_event, startup_result, False, error)
        return False

    try:
        asyncio.run(main_server(startup_event, startup_result))
    except Exception as exc:
        _signal_server_startup(startup_event, startup_result, False, exc)
        print("[ERROR] Settings server failed to start:", util.redact_sensitive_text(str(exc)))
        return False
    return True


def start_web_server_background(startup_timeout=CONST_SERVER_STARTUP_TIMEOUT_SEC):
    startup_event = threading.Event()
    startup_result = {}
    server_thread = threading.Thread(
        target=web_server,
        args=(startup_event, startup_result),
        daemon=True,
        name="hunter-settings-server",
    )
    server_thread.start()
    if not startup_event.wait(timeout=startup_timeout):
        print("[ERROR] Settings server startup timed out.")
        return None
    if not startup_result.get("started", False):
        return None
    return server_thread


def initialize_settings_server(startup_timeout=CONST_SERVER_STARTUP_TIMEOUT_SEC):
    """Start and own the settings listener before cleaning shared state files."""
    server_thread = start_web_server_background(startup_timeout=startup_timeout)
    if server_thread is None:
        return None
    clean_tmp_file()
    return server_thread

def handle_cli_command(argv):
    """Handle short smoke-test commands without starting the long-running UI server."""
    normalized = [str(arg).strip().lower() for arg in argv]
    if not normalized:
        return False

    if "/version" in normalized or "--version" in normalized:
        print(APP_DISPLAY_VERSION)
        return True

    if "/shutdown" in normalized or "--shutdown" in normalized:
        server_port = get_server_port()
        shutdown_url = f"http://127.0.0.1:{server_port}/shutdown"
        try:
            response = requests.post(shutdown_url, timeout=1.5)
            print(f"shutdown requested: HTTP {response.status_code}")
        except requests.RequestException:
            print("shutdown requested: no running settings server")
        return True

    if len(normalized) >= 4 and normalized[0] == "http" and "/run" in normalized and "smoke" in normalized and "test" in normalized:
        config_filepath, _ = load_json()
        www_root = os.path.join(SCRIPT_DIR, "www")
        required_assets = [
            os.path.join(www_root, "settings.html"),
            os.path.join(www_root, "settings.js"),
            os.path.join(www_root, "help-content.js"),
            os.path.join(www_root, "css", "settings.css"),
            os.path.join(www_root, "dist", "jquery.min.js"),
            os.path.join(www_root, "dist", "bootstrap", "bootstrap.min.css"),
            os.path.join(www_root, "dist", "bootstrap", "bootstrap.min.js"),
        ]
        missing_assets = [asset for asset in required_assets if not os.path.exists(asset)]
        if missing_assets:
            print("smoke test failed: missing assets:", ", ".join(missing_assets))
            return True
        if hasattr(sys, "frozen"):
            bot_executable, _ = util.resolve_frozen_executable("nodriver_tixcraft")
            if not os.path.isfile(bot_executable):
                print("smoke test failed: missing bot executable:", bot_executable)
                return True
        formatter_sample = make_notification_context(
            platform="TixCraft",
            stage="checkout_reached",
            event_name="2026 PLAVE World Tour [KEEP IT MANIC] in Taipei",
            ticket_count=10,
            seat_area="2F Y1B-1區5300",
            seat_rows=[f"4排{seat_number}號" for seat_number in range(1, 11)],
        ).format_message()
        formatter_markers = (
            "活動：2026 PLAVE World Tour [KEEP IT MANIC] in Taipei",
            "區域： 2F Y1B-1區5300",
            "1️⃣ 4排1號",
            "🔟 4排10號",
            "\n\n狀態：已進入結帳畫面，請立即付款！",
        )
        if not all(marker in formatter_sample for marker in formatter_markers):
            print("smoke test failed: notification formatter")
            return True
        print(f"smoke test ok: {APP_DISPLAY_VERSION}")
        print("notification formatter: ok (1-10)")
        print(f"config: {config_filepath}")
        return True

    return False

def settgins_gui_timer():
    while True:
        change_maxbot_status_by_keyword()
        time.sleep(0.4)
        if GLOBAL_SERVER_SHUTDOWN:
            break

if __name__ == "__main__":
    GLOBAL_SERVER_SHUTDOWN = False

    if not handle_cli_command(sys.argv[1:]):
        active_server_thread = initialize_settings_server()
        if active_server_thread is not None:
            threading.Thread(target=settgins_gui_timer, daemon=True).start()
            print("To exit web server press Ctrl + C.")
            while active_server_thread.is_alive() and not GLOBAL_SERVER_SHUTDOWN:
                time.sleep(0.4)
            if not active_server_thread.is_alive() and not GLOBAL_SERVER_SHUTDOWN:
                print("[ERROR] Settings server stopped unexpectedly.")
            print("Bye bye, see you next time.")
