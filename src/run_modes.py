from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any


class RunMode(str, Enum):
    ONSALE = "onsale"
    LEAK_WATCH = "leak_watch"


DEFAULT_LEAK_REFRESH_INTERVAL_SECONDS = 3.0


@dataclass(frozen=True)
class ModePolicy:
    name: RunMode
    refresh_interval_key: str
    default_refresh_interval_seconds: float
    scheduled_one_shot: bool
    max_no_ticket_scans_per_generation: int | None


ONSALE_POLICY = ModePolicy(
    name=RunMode.ONSALE,
    refresh_interval_key="auto_reload_page_interval",
    default_refresh_interval_seconds=0.0,
    scheduled_one_shot=True,
    max_no_ticket_scans_per_generation=None,
)
LEAK_WATCH_POLICY = ModePolicy(
    name=RunMode.LEAK_WATCH,
    refresh_interval_key="leak_refresh_interval_seconds",
    default_refresh_interval_seconds=DEFAULT_LEAK_REFRESH_INTERVAL_SECONDS,
    scheduled_one_shot=True,
    max_no_ticket_scans_per_generation=1,
)


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


def get_mode_policy(config_dict: dict[str, Any] | None) -> ModePolicy:
    if is_leak_watch_mode(config_dict):
        return LEAK_WATCH_POLICY
    return ONSALE_POLICY


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
    advanced = config_dict.get("advanced", {}) if isinstance(config_dict, dict) else {}
    if normalize_run_mode(advanced.get("run_mode")) == RunMode.LEAK_WATCH.value:
        key = LEAK_WATCH_POLICY.refresh_interval_key
        fallback = LEAK_WATCH_POLICY.default_refresh_interval_seconds
    else:
        key = ONSALE_POLICY.refresh_interval_key
        fallback = default
    raw_value = advanced.get(key, fallback)
    if raw_value in (None, ""):
        raw_value = fallback
    return _non_negative_float(raw_value, fallback)
