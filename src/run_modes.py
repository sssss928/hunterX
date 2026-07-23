from __future__ import annotations

import math
from enum import Enum
from typing import Any


class RunMode(str, Enum):
    ONSALE = "onsale"
    LEAK_WATCH = "leak_watch"


DEFAULT_LEAK_REFRESH_INTERVAL_SECONDS = 3.0


def normalize_run_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == RunMode.LEAK_WATCH.value:
        return RunMode.LEAK_WATCH.value
    return RunMode.ONSALE.value


def get_run_mode(config_dict: dict[str, Any] | None) -> str:
    advanced = config_dict.get("advanced", {}) if isinstance(config_dict, dict) else {}
    return normalize_run_mode(advanced.get("run_mode"))


def is_leak_watch_mode(config_dict: dict[str, Any] | None) -> bool:
    return get_run_mode(config_dict) == RunMode.LEAK_WATCH.value


def _non_negative_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    if not math.isfinite(number):
        number = 0.0
    return max(0.0, number)


def get_onsale_reload_interval(config_dict: dict[str, Any] | None, default: float = 0.0) -> float:
    advanced = config_dict.get("advanced", {}) if isinstance(config_dict, dict) else {}
    raw_value = advanced.get("auto_reload_page_interval", default)
    if raw_value in (None, ""):
        raw_value = default
    return _non_negative_float(raw_value, default)


def get_leak_refresh_interval(config_dict: dict[str, Any] | None, default: float = DEFAULT_LEAK_REFRESH_INTERVAL_SECONDS) -> float:
    advanced = config_dict.get("advanced", {}) if isinstance(config_dict, dict) else {}
    raw_value = advanced.get("leak_refresh_interval_seconds", default)
    if raw_value in (None, ""):
        raw_value = default
    return _non_negative_float(raw_value, default)


def get_effective_reload_interval(config_dict: dict[str, Any] | None, default: float = 0.0) -> float:
    if is_leak_watch_mode(config_dict):
        return get_leak_refresh_interval(config_dict, DEFAULT_LEAK_REFRESH_INTERVAL_SECONDS)
    return get_onsale_reload_interval(config_dict, default)
