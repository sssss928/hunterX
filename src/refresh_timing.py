#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import math
import re
import socket
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from statistics import median
from typing import Any
from urllib.parse import urlparse


NTP_UNIX_EPOCH_DELTA_SECONDS = 2_208_988_800
NTP_PACKET_SIZE = 48
NS_PER_SECOND = 1_000_000_000
NS_PER_MS = 1_000_000


DEFAULT_REFRESH_CALIBRATION = {
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
    "timezone": "local",
    "confidence": "low",
}


DEFAULT_TIME_CALIBRATION = {
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
        if self.phase in (TriggerPhase.FROZEN, TriggerPhase.TRIGGERED, TriggerPhase.POST_TRIGGER_RELOAD):
            return self.plan  # type: ignore[return-value]
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
        now_wall = datetime.fromtimestamp(self.clock.wall_time_ns() / NS_PER_SECOND)
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


def parse_refresh_datetime_value(raw_value: Any) -> datetime | None:
    target_text = str(raw_value or "").strip()
    if not target_text:
        return None
    if not re.fullmatch(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{3})?", target_text):
        return None
    fmt = "%Y/%m/%d %H:%M:%S.%f" if "." in target_text else "%Y/%m/%d %H:%M:%S"
    try:
        return datetime.strptime(target_text, fmt)
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
    calibration = get_refresh_calibration(config_dict)
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
    merged["timezone"] = str(merged.get("timezone") or "local").strip() or "local"
    merged["confidence"] = _normalize_confidence(merged.get("confidence"))
    return merged


def compute_trigger_plan(target_dt: datetime, calibration: dict[str, Any] | None = None) -> TriggerPlan:
    normalized = get_refresh_calibration({"refresh_calibration": calibration or {}})
    frontend_budget_ms = 0
    ticket_network_budget_ms = 0
    scheduler_budget_ms = 0
    safety_margin_ms = 0
    clock_offset_ms = 0
    clock_uncertainty_ms = 0
    total_advance_ms = 0

    computed_reference = target_dt
    local_trigger_time = target_dt
    warnings = ["standard target refresh; delay calibration is ignored"]

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
        confidence="unavailable",
        timezone=normalized["timezone"],
        warnings=tuple(warnings),
    )


def calculate_refresh_trigger_datetime(target_dt: datetime, calibration: dict[str, Any]) -> datetime:
    return compute_trigger_plan(target_dt, calibration).local_trigger_time


def describe_refresh_calibration(calibration: dict[str, Any]) -> str:
    return "standard target refresh"


def wall_datetime_to_monotonic_deadline_ns(trigger_wall_dt: datetime, clock: Clock | None = None) -> int:
    active_clock = clock or RealClock()
    anchor_wall_ns = active_clock.wall_time_ns()
    anchor_mono_ns = active_clock.monotonic_ns()
    trigger_wall_ns = int(trigger_wall_dt.timestamp() * NS_PER_SECOND)
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
            await asyncio.sleep(min(1.0, remaining_seconds / 2))
        elif remaining_seconds > 0.05:
            await asyncio.sleep(min(0.05, remaining_seconds / 2))
        else:
            await asyncio.sleep(max(0.001, min(0.01, remaining_seconds)))


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
