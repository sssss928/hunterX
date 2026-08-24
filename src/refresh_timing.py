#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import math
import re
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from enum import StrEnum
from statistics import median
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from task_registry import hunterx_tasks


NTP_UNIX_EPOCH_DELTA_SECONDS = 2_208_988_800
NTP_PACKET_SIZE = 48
NS_PER_SECOND = 1_000_000_000
NS_PER_MS = 1_000_000
MAX_TRUSTED_CLOCK_UNCERTAINTY_MS = 100
TRUSTED_CLOCK_SOURCES = frozenset({"ntp", "sntp"})
RUNTIME_NTP_CRITICAL_WINDOW_SECONDS = 10.0
RUNTIME_NTP_MIN_START_LEAD_SECONDS = 30.0
RUNTIME_NTP_SOCKET_BUDGET_MS = 6_000
RUNTIME_NTP_OUTER_TIMEOUT_MARGIN_SECONDS = 1.0
RUNTIME_NTP_MAX_SOCKET_TIMEOUT_MS = 2_000
RUNTIME_NTP_MAX_SERVERS = 4
RUNTIME_NTP_MAX_SAMPLES_PER_SERVER = 3

_RUNTIME_NTP_WORKER_LOCK = threading.Lock()


DEFAULT_REFRESH_CALIBRATION: dict[str, Any] = {
    "enable": False,
    "auto_calibrate": False,
    "advanced_delay_mode": "disabled",
    "time_source_mode": "http",
    "time_source_url": "https://time.is/",
    "ticket_site_url": "",
    "clock_offset_ms": 0,
    "clock_uncertainty_ms": 0,
    "frontend_delay_ms": 45,
    "network_uplink_ms": 35,
    "scheduler_jitter_ms": 0,
    "safety_margin_ms": 30,
    "freeze_before_seconds": 10,
    "auto_calibrate_interval_seconds": 300,
    "timezone": "Asia/Taipei",
    "confidence": "low",
}


DEFAULT_TIME_CALIBRATION: dict[str, Any] = {
    "mode": "auto",
    "ntp_servers": ["time.google.com", "pool.ntp.org"],
    "ntp_timeout_ms": 1000,
    "ntp_samples_per_server": 3,
    "ntp_min_valid_samples": 2,
    "background_refresh_seconds": 300,
}


class TriggerPhase(StrEnum):
    CALIBRATING = "CALIBRATING"
    ARMED = "ARMED"
    FROZEN = "FROZEN"
    TRIGGERED = "TRIGGERED"
    POST_TRIGGER_RELOAD = "POST_TRIGGER_RELOAD"
    SUCCESS = "SUCCESS"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class TimingModeCapability(StrEnum):
    STANDARD_MILLISECOND_TARGET_REFRESH = "STANDARD_MILLISECOND_TARGET_REFRESH"
    ADVANCED_DELAY_CALIBRATED_REFRESH = "ADVANCED_DELAY_CALIBRATED_REFRESH"
    QUEUE_AWARE_STANDARD_REFRESH = "QUEUE_AWARE_STANDARD_REFRESH"


ADVANCED_DELAY_MODE_VALUES = {"auto", "enabled", "disabled"}

PLATFORM_TIMING_CAPABILITY_TABLE: dict[str, TimingModeCapability] = {
    "tixcraft": TimingModeCapability.STANDARD_MILLISECOND_TARGET_REFRESH,
    "kktix": TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH,
    "kham": TimingModeCapability.STANDARD_MILLISECOND_TARGET_REFRESH,
    "ticketplus": TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH,
    "funone": TimingModeCapability.STANDARD_MILLISECOND_TARGET_REFRESH,
    "ibon": TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH,
    "famiticket": TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH,
    "cityline": TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH,
    "hkticketing": TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH,
    "fansigo": TimingModeCapability.STANDARD_MILLISECOND_TARGET_REFRESH,
    "indievox": TimingModeCapability.STANDARD_MILLISECOND_TARGET_REFRESH,
    "ticketmaster": TimingModeCapability.STANDARD_MILLISECOND_TARGET_REFRESH,
    "unknown": TimingModeCapability.STANDARD_MILLISECOND_TARGET_REFRESH,
}


class Clock:
    def wall_time_ns(self) -> int:
        raise NotImplementedError

    def monotonic_ns(self) -> int:
        raise NotImplementedError


class RealClock(Clock):
    def wall_time_ns(self) -> int:
        return time.time_ns()

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


@dataclass(frozen=True)
class TriggerPlan:
    target_reference_time: datetime
    computed_trigger_reference_time: datetime
    local_trigger_time: datetime
    computed_trigger_display: str
    clock_offset_ms: int
    clock_uncertainty_ms: int
    ticket_network_budget_ms: int
    frontend_budget_ms: int
    scheduler_budget_ms: int
    safety_margin_ms: int
    total_advance_ms: int
    confidence: str
    timezone: str
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "public_sale_target_reference_time": format_datetime_ms(self.target_reference_time),
            "computed_early_trigger_reference_time": format_datetime_ms(self.computed_trigger_reference_time),
            "target_reference_time": format_datetime_ms(self.target_reference_time),
            "computed_trigger_reference_time": format_datetime_ms(self.computed_trigger_reference_time),
            "local_trigger_time": format_datetime_ms(self.local_trigger_time),
            "computed_trigger_display": self.computed_trigger_display,
            "clock_offset_ms": self.clock_offset_ms,
            "clock_uncertainty_ms": self.clock_uncertainty_ms,
            "ticket_network_budget_ms": self.ticket_network_budget_ms,
            "frontend_budget_ms": self.frontend_budget_ms,
            "scheduler_budget_ms": self.scheduler_budget_ms,
            "safety_margin_ms": self.safety_margin_ms,
            "total_advance_ms": self.total_advance_ms,
            "confidence": self.confidence,
            "timezone": self.timezone,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class PlatformTimingDecision:
    platform_id: str
    capability: TimingModeCapability
    effective_capability: TimingModeCapability
    advanced_delay_mode: str
    refresh_calibration_enabled: bool
    advanced_supported: bool
    advanced_active: bool
    queue_aware: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform_id": self.platform_id,
            "capability": self.capability.value,
            "effective_capability": self.effective_capability.value,
            "advanced_delay_mode": self.advanced_delay_mode,
            "refresh_calibration_enabled": self.refresh_calibration_enabled,
            "advanced_supported": self.advanced_supported,
            "advanced_active": self.advanced_active,
            "queue_aware": self.queue_aware,
            "warnings": list(self.warnings),
        }


@dataclass
class RefreshTriggerController:
    clock: Clock = field(default_factory=RealClock)
    phase: TriggerPhase = TriggerPhase.STOPPED
    generation: int = 0
    state_key: str = ""
    plan: TriggerPlan | None = None
    trigger_deadline_monotonic_ns: int | None = None
    frozen_generation: int | None = None
    initial_trigger_count: int = 0
    last_trigger_error_ns: int | None = None

    def arm(self, target_dt: datetime, calibration: dict[str, Any], state_key: str) -> TriggerPlan:
        if self.phase in (TriggerPhase.FROZEN, TriggerPhase.TRIGGERED):
            return self.plan  # type: ignore[return-value]
        if (
            state_key == self.state_key
            and self.plan is not None
            and self.trigger_deadline_monotonic_ns is not None
        ):
            # Convert the wall-clock target exactly once. Re-anchoring on every
            # automation-loop iteration would make an already-armed deadline
            # jump when NTP or the user adjusts the system wall clock.
            return self.plan
        if state_key != self.state_key:
            self.generation += 1
            self.state_key = state_key
            self.phase = TriggerPhase.ARMED
            self.initial_trigger_count = 0
        plan = compute_trigger_plan(target_dt, calibration)
        self.plan = plan
        self.trigger_deadline_monotonic_ns = wall_datetime_to_monotonic_deadline_ns(
            plan.local_trigger_time,
            self.clock,
        )
        return plan

    def maybe_freeze(self, target_dt: datetime, freeze_before_seconds: int) -> bool:
        if self.plan is None or self.phase != TriggerPhase.ARMED:
            return False
        now_wall = datetime.fromtimestamp(
            self.clock.wall_time_ns() / NS_PER_SECOND,
            tz=target_dt.tzinfo,
        )
        if (target_dt - now_wall).total_seconds() <= freeze_before_seconds:
            self.phase = TriggerPhase.FROZEN
            self.frozen_generation = self.generation
            return True
        return False

    def remaining_ns(self) -> int:
        if self.trigger_deadline_monotonic_ns is None:
            return 0
        return compute_remaining_ns(self.trigger_deadline_monotonic_ns, self.clock.monotonic_ns())

    def should_trigger_once(self) -> bool:
        if self.phase in (TriggerPhase.TRIGGERED, TriggerPhase.POST_TRIGGER_RELOAD, TriggerPhase.SUCCESS):
            return False
        if self.trigger_deadline_monotonic_ns is None:
            return False
        if self.remaining_ns() > 0:
            return False
        self.phase = TriggerPhase.TRIGGERED
        self.initial_trigger_count += 1
        self.last_trigger_error_ns = self.clock.monotonic_ns() - self.trigger_deadline_monotonic_ns
        return self.initial_trigger_count == 1

    def mark_post_trigger_reload(self) -> None:
        if self.phase == TriggerPhase.TRIGGERED:
            self.phase = TriggerPhase.POST_TRIGGER_RELOAD

    def mark_trigger_failed(self) -> None:
        if self.phase == TriggerPhase.TRIGGERED:
            self.phase = TriggerPhase.ERROR


def resolve_refresh_timezone(value: Any = "Asia/Taipei") -> tzinfo:
    """Resolve the configured wall-clock timezone without requiring tzdata on Windows.

    Taiwan has used UTC+08:00 without daylight-saving transitions since 1979,
    so the explicit fixed-offset fallback is correct for current sale dates.
    Other IANA names use :mod:`zoneinfo` when the host provides its database.
    """

    name = str(value or "Asia/Taipei").strip() or "Asia/Taipei"
    if name.casefold() == "local":
        return datetime.now().astimezone().tzinfo or timezone.utc
    if name.casefold() in {"utc", "etc/utc", "z"}:
        return timezone.utc
    if name.casefold() in {"asia/taipei", "taipei"}:
        return timezone(timedelta(hours=8), name="Asia/Taipei")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone(timedelta(hours=8), name="Asia/Taipei")


def parse_refresh_datetime_value(
    raw_value: Any,
    timezone_name: Any | None = None,
) -> datetime | None:
    target_text = str(raw_value or "").strip()
    if not target_text:
        return None
    if not re.fullmatch(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{3})?", target_text):
        return None
    fmt = "%Y/%m/%d %H:%M:%S.%f" if "." in target_text else "%Y/%m/%d %H:%M:%S"
    try:
        parsed = datetime.strptime(target_text, fmt)
        if timezone_name is not None:
            parsed = parsed.replace(tzinfo=resolve_refresh_timezone(timezone_name))
        return parsed
    except ValueError:
        return None


def format_datetime_ms(value: datetime) -> str:
    return value.strftime("%Y/%m/%d %H:%M:%S.") + f"{value.microsecond // 1000:03d}"


def _finite_float(value: Any, default_value: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default_value)
    if not math.isfinite(parsed):
        parsed = float(default_value)
    return max(minimum, min(maximum, parsed))


def _calibration_int(config_dict: dict[str, Any], key: str, default_value: int, minimum: int = -60000, maximum: int = 60000) -> int:
    return round(_finite_float(config_dict.get(key, default_value), default_value, minimum, maximum))


def _calibration_bool(value: Any, default_value: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default_value
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enable", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disable", "disabled", ""}:
        return False
    return default_value


def normalize_advanced_delay_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in ADVANCED_DELAY_MODE_VALUES:
        return text
    return "auto"


def detect_platform_id_from_url(url: str | None) -> str:
    raw_url = str(url or "").strip()
    if not raw_url:
        return "unknown"
    parse_target = raw_url if "://" in raw_url else "https://" + raw_url
    try:
        parsed = urlparse(parse_target)
    except ValueError:
        parsed = urlparse("")
    host = (parsed.hostname or "").lower()
    full_text = raw_url.lower()

    if host == "tixcraft.com" or host.endswith(".tixcraft.com"):
        return "tixcraft"
    if host == "kktix.com" or host.endswith(".kktix.com") or host == "kktix.cc" or host.endswith(".kktix.cc"):
        return "kktix"
    if host == "kham.com.tw" or host.endswith(".kham.com.tw") or host == "ticket.com.tw" or host.endswith(".ticket.com.tw"):
        return "kham"
    if host == "tickets.udnfunlife.com":
        return "kham"
    if "ticketplus.com" in host:
        return "ticketplus"
    if host == "tickets.funone.io":
        return "funone"
    if host == "ibon.com" or host.endswith(".ibon.com") or host == "ibon.com.tw" or host.endswith(".ibon.com.tw"):
        return "ibon"
    if host == "famiticket.com.tw" or host.endswith(".famiticket.com.tw"):
        return "famiticket"
    if host == "cityline.com" or host.endswith(".cityline.com") or host == "cityline.com.hk" or host.endswith(".cityline.com.hk"):
        return "cityline"
    if "hkticketing.com" in host or "galaxymacau.com" in host or "ticketek.com" in host:
        return "hkticketing"
    if host == "go.fansi.me" or "fansi" in host:
        return "fansigo"
    if "indievox.com" in host:
        return "indievox"
    if "ticketmaster." in full_text:
        return "ticketmaster"
    return "unknown"


def get_platform_timing_capability(
    platform_id: str | None = None,
    url: str | None = None,
    config_dict: dict[str, Any] | None = None,
) -> PlatformTimingDecision:
    calibration = get_refresh_calibration(config_dict or {})
    resolved_platform = (platform_id or "").strip().lower() or detect_platform_id_from_url(
        url or calibration.get("ticket_site_url") or (config_dict or {}).get("homepage")
    )
    if resolved_platform not in PLATFORM_TIMING_CAPABILITY_TABLE:
        resolved_platform = "unknown"

    capability = PLATFORM_TIMING_CAPABILITY_TABLE[resolved_platform]
    advanced_supported = False
    mode = normalize_advanced_delay_mode(calibration.get("advanced_delay_mode"))
    refresh_enabled = bool(calibration.get("enable", False))
    warnings: list[str] = []

    advanced_active = False
    if refresh_enabled or mode != "disabled":
        warnings.append("advanced delay calibration is deprecated and ignored; using standard target refresh")

    if capability == TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH:
        effective_capability = TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH
    else:
        effective_capability = TimingModeCapability.STANDARD_MILLISECOND_TARGET_REFRESH

    return PlatformTimingDecision(
        platform_id=resolved_platform,
        capability=capability,
        effective_capability=effective_capability,
        advanced_delay_mode=mode,
        refresh_calibration_enabled=refresh_enabled,
        advanced_supported=advanced_supported,
        advanced_active=advanced_active,
        queue_aware=effective_capability == TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH,
        warnings=tuple(warnings),
    )


def get_effective_refresh_calibration(
    config_dict: dict[str, Any],
    url: str | None = None,
    platform_id: str | None = None,
) -> tuple[dict[str, Any], PlatformTimingDecision]:
    raw_calibration = config_dict.get("refresh_calibration", {})
    if not isinstance(raw_calibration, dict):
        raw_calibration = {}
    calibration = get_refresh_calibration(config_dict)
    calibration["_clock_source_explicit"] = "source" in raw_calibration
    calibration["_clock_offset_explicit"] = "clock_offset_ms" in raw_calibration
    calibration["_clock_uncertainty_explicit"] = (
        "clock_uncertainty_ms" in raw_calibration
    )
    calibration["_clock_sample_count_explicit"] = "sample_count" in raw_calibration
    decision = get_platform_timing_capability(platform_id, url, config_dict)
    if not decision.advanced_active:
        calibration = dict(calibration)
        calibration["enable"] = False
    return calibration, decision


def _normalize_confidence(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"high", "medium", "low", "unavailable"}:
        return text
    return "low"


def get_refresh_calibration(config_dict: dict[str, Any]) -> dict[str, Any]:
    calibration = config_dict.get("refresh_calibration", {})
    if not isinstance(calibration, dict):
        calibration = {}
    merged = dict(DEFAULT_REFRESH_CALIBRATION)
    merged.update(calibration)
    merged["enable"] = False
    merged["auto_calibrate"] = False
    merged["advanced_delay_mode"] = normalize_advanced_delay_mode(merged.get("advanced_delay_mode"))
    time_source_mode = str(merged.get("time_source_mode") or "http").strip().lower()
    if time_source_mode not in {"auto", "ntp", "http", "system"}:
        time_source_mode = "http"
    merged["time_source_mode"] = time_source_mode
    merged["time_source_url"] = str(merged.get("time_source_url") or DEFAULT_REFRESH_CALIBRATION["time_source_url"]).strip()
    merged["ticket_site_url"] = str(merged.get("ticket_site_url") or "").strip()
    merged["clock_offset_ms"] = _calibration_int(merged, "clock_offset_ms", DEFAULT_REFRESH_CALIBRATION["clock_offset_ms"])
    merged["clock_uncertainty_ms"] = _calibration_int(merged, "clock_uncertainty_ms", 0, 0, 60000)
    merged["frontend_delay_ms"] = _calibration_int(merged, "frontend_delay_ms", DEFAULT_REFRESH_CALIBRATION["frontend_delay_ms"], 0, 5000)
    merged["network_uplink_ms"] = _calibration_int(merged, "network_uplink_ms", DEFAULT_REFRESH_CALIBRATION["network_uplink_ms"], 0, 5000)
    merged["scheduler_jitter_ms"] = _calibration_int(merged, "scheduler_jitter_ms", DEFAULT_REFRESH_CALIBRATION["scheduler_jitter_ms"], 0, 5000)
    merged["safety_margin_ms"] = _calibration_int(merged, "safety_margin_ms", DEFAULT_REFRESH_CALIBRATION["safety_margin_ms"], 0, 5000)
    merged["freeze_before_seconds"] = _calibration_int(merged, "freeze_before_seconds", DEFAULT_REFRESH_CALIBRATION["freeze_before_seconds"], 0, 60)
    merged["auto_calibrate_interval_seconds"] = _calibration_int(merged, "auto_calibrate_interval_seconds", 300, 60, 86400)
    merged["timezone"] = (
        str(merged.get("timezone") or "Asia/Taipei").strip() or "Asia/Taipei"
    )
    merged["confidence"] = _normalize_confidence(merged.get("confidence"))
    return merged


def _trusted_clock_offset(calibration: dict[str, Any]) -> tuple[int, int, str, bool]:
    """Return a reference-clock correction only when its provenance is trusted.

    Offset correction is deliberately separate from network/renderer latency.
    RTT cannot establish one-way request latency and must never be used as a
    blind early-refresh budget.
    """

    source = str(calibration.get("source") or "").strip().lower()
    confidence = _normalize_confidence(calibration.get("confidence"))
    offset_ms = _calibration_int(calibration, "clock_offset_ms", 0)
    uncertainty_ms = _calibration_int(
        calibration,
        "clock_uncertainty_ms",
        MAX_TRUSTED_CLOCK_UNCERTAINTY_MS + 1,
        0,
        60000,
    )
    sample_count = int(
        _finite_float(
            calibration.get("sample_count", 0),
            0,
            0,
            1000,
        )
    )
    source_explicit = bool(
        calibration.get("_clock_source_explicit", "source" in calibration)
    )
    offset_explicit = bool(
        calibration.get(
            "_clock_offset_explicit",
            "clock_offset_ms" in calibration,
        )
    )
    uncertainty_explicit = bool(
        calibration.get(
            "_clock_uncertainty_explicit",
            "clock_uncertainty_ms" in calibration,
        )
    )
    sample_count_explicit = bool(
        calibration.get(
            "_clock_sample_count_explicit",
            "sample_count" in calibration,
        )
    )
    if source not in TRUSTED_CLOCK_SOURCES:
        return 0, 0, "unavailable", False
    if (
        not source_explicit
        or not offset_explicit
        or not uncertainty_explicit
        or not sample_count_explicit
        or sample_count < 2
    ):
        return 0, 0, confidence, False
    if confidence not in {"high", "medium"}:
        return 0, 0, confidence, False
    if uncertainty_ms > MAX_TRUSTED_CLOCK_UNCERTAINTY_MS:
        return 0, 0, confidence, False
    return offset_ms, uncertainty_ms, confidence, True


def compute_trigger_plan(target_dt: datetime, calibration: dict[str, Any] | None = None) -> TriggerPlan:
    raw_calibration = calibration if isinstance(calibration, dict) else {}
    normalized = get_refresh_calibration({"refresh_calibration": raw_calibration})
    frontend_budget_ms = 0
    ticket_network_budget_ms = 0
    scheduler_budget_ms = 0
    safety_margin_ms = 0
    (
        clock_offset_ms,
        clock_uncertainty_ms,
        confidence,
        clock_offset_trusted,
    ) = _trusted_clock_offset(raw_calibration)
    total_advance_ms = 0

    computed_reference = target_dt
    local_trigger_time = target_dt - timedelta(milliseconds=clock_offset_ms)
    if clock_offset_trusted:
        warnings = [
            "standard target refresh with trusted NTP clock correction; "
            "RTT and browser/network latency are not used to advance the request"
        ]
    else:
        warnings = [
            "standard target refresh; untrusted clock offsets and RTT delay calibration are ignored"
        ]

    return TriggerPlan(
        target_reference_time=target_dt,
        computed_trigger_reference_time=computed_reference,
        local_trigger_time=local_trigger_time,
        computed_trigger_display=format_datetime_ms(local_trigger_time),
        clock_offset_ms=clock_offset_ms,
        clock_uncertainty_ms=clock_uncertainty_ms,
        ticket_network_budget_ms=ticket_network_budget_ms,
        frontend_budget_ms=frontend_budget_ms,
        scheduler_budget_ms=scheduler_budget_ms,
        safety_margin_ms=safety_margin_ms,
        total_advance_ms=total_advance_ms,
        confidence=confidence if clock_offset_trusted else "unavailable",
        timezone=normalized["timezone"],
        warnings=tuple(warnings),
    )


def calculate_refresh_trigger_datetime(target_dt: datetime, calibration: dict[str, Any]) -> datetime:
    return compute_trigger_plan(target_dt, calibration).local_trigger_time


def describe_refresh_calibration(calibration: dict[str, Any]) -> str:
    offset_ms, uncertainty_ms, _confidence, is_trusted = _trusted_clock_offset(
        calibration
    )
    if is_trusted:
        return f"trusted NTP clock correction {offset_ms:+d}ms (uncertainty {uncertainty_ms}ms)"
    return "standard target refresh"


def wall_datetime_to_monotonic_deadline_ns(trigger_wall_dt: datetime, clock: Clock | None = None) -> int:
    active_clock = clock or RealClock()
    anchor_wall_ns = active_clock.wall_time_ns()
    anchor_mono_ns = active_clock.monotonic_ns()
    trigger_wall_seconds = int(trigger_wall_dt.replace(microsecond=0).timestamp())
    trigger_wall_ns = trigger_wall_seconds * NS_PER_SECOND + trigger_wall_dt.microsecond * 1_000
    return anchor_mono_ns + (trigger_wall_ns - anchor_wall_ns)


def compute_remaining_ns(trigger_deadline_monotonic_ns: int, current_monotonic_ns: int) -> int:
    return max(0, int(trigger_deadline_monotonic_ns) - int(current_monotonic_ns))


def format_remaining_seconds(remaining_ns: int) -> str:
    remaining = max(0, int(remaining_ns))
    seconds, ns_remainder = divmod(remaining, NS_PER_SECOND)
    millis = ns_remainder // NS_PER_MS
    return f"{seconds}.{millis:03d}s"


async def sleep_until_deadline(deadline_monotonic_ns: int, clock: Clock | None = None) -> int:
    active_clock = clock or RealClock()
    while True:
        remaining_ns = compute_remaining_ns(deadline_monotonic_ns, active_clock.monotonic_ns())
        if remaining_ns <= 0:
            return active_clock.monotonic_ns() - deadline_monotonic_ns
        remaining_seconds = remaining_ns / NS_PER_SECOND
        if remaining_seconds > 2.0:
            await asyncio.sleep(min(1.0, remaining_seconds - 1.0))
        elif remaining_seconds > 0.25:
            await asyncio.sleep(max(0.001, remaining_seconds - 0.1))
        elif remaining_seconds > 0.025:
            await asyncio.sleep(max(0.001, remaining_seconds - 0.01))
        elif remaining_seconds > 0.003:
            await asyncio.sleep(max(0.0005, remaining_seconds - 0.002))
        else:
            # Keep the final window cooperative.  This is intentionally short;
            # it avoids a long busy-spin while not asking the Windows timer for
            # a millisecond sleep that can overshoot by an entire scheduler tick.
            await asyncio.sleep(0)


def robust_estimate(samples: list[Any] | tuple[Any, ...]) -> dict[str, Any]:
    valid = []
    rejected_initial = 0
    for value in samples:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            rejected_initial += 1
            continue
        if math.isfinite(parsed):
            valid.append(parsed)
        else:
            rejected_initial += 1
    valid = sorted(valid)
    if not valid:
        return {
            "value_ms": 0.0,
            "sample_count": 0,
            "rejected_count": rejected_initial,
            "median_ms": 0.0,
            "mad_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "uncertainty_ms": 0.0,
            "confidence": "unavailable",
        }
    med = float(median(valid))
    deviations = [abs(value - med) for value in valid]
    mad = float(median(deviations))
    threshold = max(1.0, mad * 6.0)
    filtered = [value for value in valid if abs(value - med) <= threshold]
    rejected = rejected_initial + len(valid) - len(filtered)
    if not filtered:
        filtered = valid
    filtered = sorted(filtered)
    p50 = percentile(filtered, 50)
    p95 = percentile(filtered, 95)
    p99 = percentile(filtered, 99)
    uncertainty = max(float(median([abs(value - p50) for value in filtered])), p95 - p50, 0.0)
    if len(filtered) >= 5 and uncertainty <= 10:
        confidence = "high"
    elif len(filtered) >= 3:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "value_ms": round(p50, 3),
        "sample_count": len(filtered),
        "rejected_count": rejected,
        "median_ms": round(p50, 3),
        "mad_ms": round(mad, 3),
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
        "p99_ms": round(p99, 3),
        "uncertainty_ms": round(uncertainty, 3),
        "confidence": confidence,
    }


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * (percentile_value / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return values[int(rank)]
    return values[lower] * (upper - rank) + values[upper] * (rank - lower)


def unix_ns_to_ntp_parts(unix_ns: int) -> tuple[int, int]:
    unix_seconds, ns_remainder = divmod(int(unix_ns), NS_PER_SECOND)
    ntp_seconds = unix_seconds + NTP_UNIX_EPOCH_DELTA_SECONDS
    fraction = int((ns_remainder * (1 << 32)) // NS_PER_SECOND)
    return ntp_seconds & 0xFFFFFFFF, fraction & 0xFFFFFFFF


def ntp_parts_to_unix_ns(seconds: int, fraction: int) -> int:
    unix_seconds = int(seconds) - NTP_UNIX_EPOCH_DELTA_SECONDS
    ns_remainder = (int(fraction) * NS_PER_SECOND) // (1 << 32)
    return unix_seconds * NS_PER_SECOND + ns_remainder


def _read_ntp_timestamp(packet: bytes, offset: int) -> int:
    seconds, fraction = struct.unpack("!II", packet[offset : offset + 8])
    return ntp_parts_to_unix_ns(seconds, fraction)


def _write_ntp_timestamp(packet: bytearray, offset: int, unix_ns: int) -> None:
    seconds, fraction = unix_ns_to_ntp_parts(unix_ns)
    struct.pack_into("!II", packet, offset, seconds, fraction)


def build_ntp_request(transmit_time_ns: int | None = None) -> bytes:
    tx_ns = int(transmit_time_ns if transmit_time_ns is not None else time.time_ns())
    packet = bytearray(NTP_PACKET_SIZE)
    packet[0] = (0 << 6) | (4 << 3) | 3
    _write_ntp_timestamp(packet, 40, tx_ns)
    return bytes(packet)


def parse_ntp_response(packet: bytes, expected_originate_ns: int, client_receive_ns: int) -> dict[str, Any]:
    if len(packet) < NTP_PACKET_SIZE:
        raise ValueError("NTP packet too short")
    li = (packet[0] >> 6) & 0b11
    version = (packet[0] >> 3) & 0b111
    mode = packet[0] & 0b111
    stratum = packet[1]
    if li == 3:
        raise ValueError("NTP server reports unsynchronized clock")
    if version not in (3, 4):
        raise ValueError("unsupported NTP version")
    if mode != 4:
        raise ValueError("NTP response mode is not server")
    if not 1 <= stratum <= 15:
        raise ValueError("NTP response stratum is not usable")

    originate_ns = _read_ntp_timestamp(packet, 24)
    receive_ns = _read_ntp_timestamp(packet, 32)
    transmit_ns = _read_ntp_timestamp(packet, 40)
    if originate_ns == 0 or receive_ns == 0 or transmit_ns == 0:
        raise ValueError("NTP response contains zero timestamps")
    if abs(originate_ns - int(expected_originate_ns)) > NS_PER_MS:
        raise ValueError("NTP originate timestamp mismatch")

    t1 = int(expected_originate_ns)
    t2 = receive_ns
    t3 = transmit_ns
    t4 = int(client_receive_ns)
    delay_ns = (t4 - t1) - (t3 - t2)
    offset_ns = ((t2 - t1) + (t3 - t4)) // 2
    if delay_ns < 0:
        raise ValueError("NTP response produced negative network delay")
    return {
        "success": True,
        "offset_ns": offset_ns,
        "offset_ms": round(offset_ns / NS_PER_MS, 3),
        "delay_ns": delay_ns,
        "delay_ms": round(delay_ns / NS_PER_MS, 3),
        "stratum": stratum,
        "version": version,
        "mode": mode,
    }


def query_sntp_server(server: str, port: int = 123, timeout_ms: int = 1000, clock: Clock | None = None) -> dict[str, Any]:
    active_clock = clock or RealClock()
    timeout_seconds = _finite_float(timeout_ms, 1000, 50, 10000) / 1000
    transmit_ns = active_clock.wall_time_ns()
    packet = build_ntp_request(transmit_ns)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout_seconds)
        sock.sendto(packet, (server, int(port)))
        response, address = sock.recvfrom(512)
    received_ns = active_clock.wall_time_ns()
    parsed = parse_ntp_response(response, transmit_ns, received_ns)
    parsed["server"] = server
    parsed["port"] = int(port)
    parsed["address"] = address[0]
    return parsed


def calibrate_ntp_servers(
    servers: list[str] | tuple[str, ...],
    timeout_ms: int = 1000,
    samples_per_server: int = 3,
    min_valid_samples: int = 2,
    port: int = 123,
    clock: Clock | None = None,
) -> dict[str, Any]:
    active_servers = [str(server).strip() for server in servers if str(server).strip()]
    if not active_servers:
        raise ValueError("no NTP servers configured")
    sample_limit = int(_finite_float(samples_per_server, 3, 1, 10))
    min_valid = int(_finite_float(min_valid_samples, 2, 1, 10))
    samples: list[dict[str, Any]] = []
    failures: list[str] = []
    for server in active_servers:
        for _ in range(sample_limit):
            try:
                samples.append(query_sntp_server(server, port=port, timeout_ms=timeout_ms, clock=clock))
            except (OSError, ValueError) as exc:
                failures.append(f"{server}: {exc}")
    if len(samples) < min_valid:
        reason = failures[-1] if failures else "no usable NTP response"
        raise ValueError(f"NTP calibration failed: {reason}")
    offset_estimate = robust_estimate([sample["offset_ms"] for sample in samples])
    delay_estimate = robust_estimate([sample["delay_ms"] for sample in samples])
    return {
        "success": True,
        "source": "ntp",
        "sample_count": len(samples),
        "clock_offset_ms": round(offset_estimate["value_ms"]),
        "clock_uncertainty_ms": round(offset_estimate["uncertainty_ms"]),
        "network_delay_ms": delay_estimate["value_ms"],
        "confidence": offset_estimate["confidence"],
        "samples": samples,
        "failures": failures,
        "warning": "NTP delay is clock-calibration uncertainty, not ticket-site latency.",
    }


def _normalize_runtime_ntp_config(raw_config: Any) -> dict[str, Any]:
    """Return a bounded, NTP-only runtime calibration specification.

    The settings endpoint supports other display/diagnostic time sources, but
    only NTP/SNTP can provide a clock offset suitable for the sale trigger.
    Runtime work is deliberately smaller than the settings endpoint's maximum
    so a failed DNS/server path cannot consume the automation loop's critical
    window.
    """

    raw = raw_config if isinstance(raw_config, dict) else {}
    merged = dict(DEFAULT_TIME_CALIBRATION)
    merged.update(raw)
    mode = str(merged.get("mode") or "auto").strip().lower()
    if mode not in {"auto", "ntp", "http", "system"}:
        mode = "auto"

    raw_servers = merged.get("ntp_servers")
    server_values: list[Any] | tuple[Any, ...]
    if isinstance(raw_servers, str):
        server_values = raw_servers.split(",")
    elif isinstance(raw_servers, (list, tuple)):
        server_values = raw_servers
    else:
        server_values = DEFAULT_TIME_CALIBRATION["ntp_servers"]
    servers: list[str] = []
    seen_servers: set[str] = set()
    for raw_server in server_values:
        server = str(raw_server or "").strip()
        server_key = server.lower()
        if not server or server_key in seen_servers:
            continue
        seen_servers.add(server_key)
        servers.append(server)

    timeout_ms = round(
        _finite_float(
            merged.get("ntp_timeout_ms"),
            DEFAULT_TIME_CALIBRATION["ntp_timeout_ms"],
            50,
            RUNTIME_NTP_MAX_SOCKET_TIMEOUT_MS,
        )
    )
    samples_per_server = round(
        _finite_float(
            merged.get("ntp_samples_per_server"),
            DEFAULT_TIME_CALIBRATION["ntp_samples_per_server"],
            2,
            RUNTIME_NTP_MAX_SAMPLES_PER_SERVER,
        )
    )
    maximum_attempts = max(2, RUNTIME_NTP_SOCKET_BUDGET_MS // timeout_ms)
    maximum_servers = max(1, maximum_attempts // samples_per_server)
    servers = servers[: min(RUNTIME_NTP_MAX_SERVERS, maximum_servers)]
    total_attempts = len(servers) * samples_per_server
    requested_minimum = round(
        _finite_float(
            merged.get("ntp_min_valid_samples"),
            DEFAULT_TIME_CALIBRATION["ntp_min_valid_samples"],
            2,
            10,
        )
    )
    min_valid_samples = min(total_attempts, requested_minimum)
    background_refresh_seconds = round(
        _finite_float(
            merged.get("background_refresh_seconds"),
            DEFAULT_TIME_CALIBRATION["background_refresh_seconds"],
            60,
            86_400,
        )
    )
    outer_timeout_seconds = (
        (total_attempts * timeout_ms) / 1000
        + RUNTIME_NTP_OUTER_TIMEOUT_MARGIN_SECONDS
    )
    eligible = (
        mode in {"auto", "ntp"}
        and bool(servers)
        and total_attempts >= 2
        and min_valid_samples >= 2
    )
    identity = (
        mode,
        tuple(server.lower() for server in servers),
        timeout_ms,
        samples_per_server,
        min_valid_samples,
        background_refresh_seconds,
    )
    return {
        "eligible": eligible,
        "mode": mode,
        "servers": tuple(servers),
        "timeout_ms": timeout_ms,
        "samples_per_server": samples_per_server,
        "min_valid_samples": min_valid_samples,
        "background_refresh_seconds": background_refresh_seconds,
        "max_age_seconds": max(120, background_refresh_seconds * 2),
        "outer_timeout_seconds": max(
            1.0,
            outer_timeout_seconds,
        ),
        "identity": identity,
    }


def sanitize_runtime_ntp_calibration(raw_result: Any) -> dict[str, Any] | None:
    """Keep only trusted clock scalars from a runtime NTP result."""

    if not isinstance(raw_result, dict) or not raw_result.get("success", False):
        return None
    source = str(raw_result.get("source") or "").strip().lower()
    confidence = _normalize_confidence(raw_result.get("confidence"))
    offset_raw = raw_result.get("clock_offset_ms")
    uncertainty_raw = raw_result.get("clock_uncertainty_ms")
    sample_raw = raw_result.get("sample_count")
    if offset_raw is None or uncertainty_raw is None or sample_raw is None:
        return None
    try:
        offset_value = float(offset_raw)
        uncertainty_value = float(uncertainty_raw)
        sample_value = float(sample_raw)
    except (TypeError, ValueError):
        return None
    if not all(
        math.isfinite(value)
        for value in (offset_value, uncertainty_value, sample_value)
    ):
        return None
    if (
        source not in TRUSTED_CLOCK_SOURCES
        or abs(offset_value) > 60_000
        or uncertainty_value < 0
        or sample_value < 2
    ):
        return None

    candidate = {
        "source": source,
        "clock_offset_ms": round(offset_value),
        "clock_uncertainty_ms": round(uncertainty_value),
        "sample_count": int(sample_value),
        "confidence": confidence,
    }
    _offset, _uncertainty, _confidence, trusted = _trusted_clock_offset(
        candidate
    )
    return candidate if trusted else None


def _run_runtime_ntp_worker(spec: dict[str, Any]) -> dict[str, Any]:
    """Serialize runtime NTP work even if an asyncio timeout cancels its Future."""

    if not _RUNTIME_NTP_WORKER_LOCK.acquire(blocking=False):
        raise RuntimeError("runtime NTP worker is already active")
    try:
        return calibrate_ntp_servers(
            spec["servers"],
            timeout_ms=spec["timeout_ms"],
            samples_per_server=spec["samples_per_server"],
            min_valid_samples=spec["min_valid_samples"],
        )
    finally:
        _RUNTIME_NTP_WORKER_LOCK.release()


@dataclass
class RuntimeNtpCalibrationCoordinator:
    """Memory-only, non-blocking runtime NTP calibration coordinator."""

    clock: Clock = field(default_factory=RealClock)
    critical_window_seconds: float = RUNTIME_NTP_CRITICAL_WINDOW_SECONDS
    minimum_start_lead_seconds: float = RUNTIME_NTP_MIN_START_LEAD_SECONDS
    task: asyncio.Task[Any] | None = field(default=None, init=False, repr=False)
    status: str = field(default="idle", init=False)
    generation: int = field(default=0, init=False)
    revision: int = field(default=0, init=False)
    _identity: tuple[Any, ...] | None = field(default=None, init=False, repr=False)
    _task_generation: int = field(default=0, init=False, repr=False)
    _task_identity: tuple[Any, ...] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _last_attempt_monotonic_ns: int | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _result: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _result_identity: tuple[Any, ...] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _result_monotonic_ns: int | None = field(
        default=None,
        init=False,
        repr=False,
    )

    async def _calibrate(
        self,
        spec: dict[str, Any],
        generation: int,
        identity: tuple[Any, ...],
    ) -> tuple[int, tuple[Any, ...], Any, str]:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_run_runtime_ntp_worker, spec),
                timeout=float(spec["outer_timeout_seconds"]),
            )
            return generation, identity, result, "completed"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return (
                generation,
                identity,
                None,
                f"error:{type(exc).__name__}",
            )

    @staticmethod
    def _consume_cancelled_task(task: asyncio.Task[Any]) -> None:
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    def _consume_finished_task(
        self,
        *,
        now_ns: int,
        critical_window: bool,
    ) -> None:
        task = self.task
        if task is None or not task.done():
            return
        self.task = None
        try:
            generation, identity, raw_result, outcome = task.result()
        except asyncio.CancelledError:
            self.status = "cancelled"
            return
        except Exception as exc:
            self.status = f"error:{type(exc).__name__}"
            return

        if generation != self.generation or identity != self._identity:
            self.status = "discarded_generation"
            return
        if outcome != "completed":
            self.status = outcome
            return
        if critical_window:
            self.status = "discarded_critical_window"
            return
        trusted = sanitize_runtime_ntp_calibration(raw_result)
        if trusted is None:
            self.status = "discarded_untrusted"
            return

        self.revision += 1
        trusted["calibrated_at_monotonic_ns"] = now_ns
        trusted["runtime_generation"] = generation
        trusted["runtime_revision"] = self.revision
        self._result = trusted
        self._result_identity = identity
        self._result_monotonic_ns = now_ns
        self.status = "applied"

    def tick(
        self,
        time_calibration_config: Any,
        *,
        target_identity: str,
        target_remaining_seconds: float,
    ) -> dict[str, Any] | None:
        """Poll/apply/start work without awaiting network I/O."""

        spec = _normalize_runtime_ntp_config(time_calibration_config)
        identity = (str(target_identity or ""), spec["identity"])
        now_ns = self.clock.monotonic_ns()
        try:
            remaining_seconds = float(target_remaining_seconds)
        except (TypeError, ValueError):
            remaining_seconds = math.inf
        if not math.isfinite(remaining_seconds):
            remaining_seconds = math.inf
        critical_window = remaining_seconds <= max(
            0.0,
            float(self.critical_window_seconds),
        )

        if identity != self._identity:
            self.generation += 1
            self._identity = identity
            self._last_attempt_monotonic_ns = None
            self._result = None
            self._result_identity = None
            self._result_monotonic_ns = None
            self.status = (
                "generation_changed"
                if self.task is not None
                else "idle"
            )

        self._consume_finished_task(
            now_ns=now_ns,
            critical_window=critical_window,
        )

        if (
            self._result is not None
            and self._result_identity == identity
            and self._result_monotonic_ns is not None
            and not critical_window
        ):
            age_ns = now_ns - self._result_monotonic_ns
            max_age_ns = int(spec["max_age_seconds"] * NS_PER_SECOND)
            if age_ns < 0 or age_ns > max_age_ns:
                self._result = None
                self._result_identity = None
                self._result_monotonic_ns = None
                self.status = "expired"

        result = dict(self._result) if self._result is not None else None
        if (
            self.task is not None
            or not spec["eligible"]
            or not target_identity
            or critical_window
        ):
            return result

        minimum_lead = max(
            float(self.minimum_start_lead_seconds),
            float(spec["outer_timeout_seconds"]) + 5.0,
        )
        if remaining_seconds <= minimum_lead:
            return result

        refresh_interval_ns = int(
            spec["background_refresh_seconds"] * NS_PER_SECOND
        )
        if (
            self._last_attempt_monotonic_ns is not None
            and now_ns - self._last_attempt_monotonic_ns < refresh_interval_ns
        ):
            return result

        self._last_attempt_monotonic_ns = now_ns
        self._task_generation = self.generation
        self._task_identity = identity
        self.task = hunterx_tasks.create(
            self._calibrate(
                spec,
                self._task_generation,
                identity,
            ),
            owner="runtime_ntp",
            purpose="background_calibration",
            generation=self.generation,
            name=f"runtime-ntp-calibration-{self.generation}",
        )
        self.status = "running"
        return result

    def close(self) -> None:
        self.generation += 1
        task = self.task
        self.task = None
        if task is not None:
            task.cancel()
            task.add_done_callback(self._consume_cancelled_task)
        self._result = None
        self._result_identity = None
        self._result_monotonic_ns = None
        self.status = "cancelled"


def select_time_source(candidates: list[dict[str, Any]], max_age_seconds: int = 3600) -> dict[str, Any]:
    healthy: list[dict[str, Any]] = []
    confidence_rank = {"high": 0, "medium": 1, "low": 2, "unavailable": 3}
    for candidate in candidates:
        if not candidate or not candidate.get("success", False):
            continue
        age = _finite_float(candidate.get("age_seconds", 0), 0, 0, 10_000_000)
        if age > max_age_seconds:
            continue
        confidence = _normalize_confidence(candidate.get("confidence", "low"))
        uncertainty = _finite_float(candidate.get("clock_uncertainty_ms", candidate.get("uncertainty_ms", 999999)), 999999, 0, 999999)
        row = dict(candidate)
        row["_sort_key"] = (confidence_rank[confidence], uncertainty, age)
        healthy.append(row)
    if not healthy:
        return {
            "success": True,
            "source": "system",
            "clock_offset_ms": 0,
            "clock_uncertainty_ms": 999999,
            "confidence": "low",
            "warning": "no healthy calibration source; using local system clock",
        }
    selected = sorted(healthy, key=lambda item: item["_sort_key"])[0]
    selected.pop("_sort_key", None)
    return selected
