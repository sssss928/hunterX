from __future__ import annotations

import socket
import struct
import threading
from datetime import datetime, timedelta

import pytest

from refresh_timing import (
    NS_PER_MS,
    Clock,
    RefreshTriggerController,
    TimingModeCapability,
    TriggerPhase,
    build_ntp_request,
    calculate_refresh_trigger_datetime,
    calibrate_ntp_servers,
    compute_remaining_ns,
    compute_trigger_plan,
    format_datetime_ms,
    format_remaining_seconds,
    get_effective_refresh_calibration,
    get_platform_timing_capability,
    get_refresh_calibration,
    normalize_advanced_delay_mode,
    ntp_parts_to_unix_ns,
    parse_ntp_response,
    parse_refresh_datetime_value,
    query_sntp_server,
    robust_estimate,
    select_time_source,
    unix_ns_to_ntp_parts,
)


class FakeClock(Clock):
    def __init__(self, wall_ns: int, monotonic_ns: int) -> None:
        self.wall_ns = wall_ns
        self.mono_ns = monotonic_ns
        self.wall_sequence: list[int] = []

    def wall_time_ns(self) -> int:
        if self.wall_sequence:
            return self.wall_sequence.pop(0)
        return self.wall_ns

    def monotonic_ns(self) -> int:
        return self.mono_ns

    def advance_ms(self, value: int) -> None:
        delta = value * NS_PER_MS
        self.wall_ns += delta
        self.mono_ns += delta


def _write_ntp_timestamp(packet: bytearray, offset: int, unix_ns: int) -> None:
    seconds, fraction = unix_ns_to_ntp_parts(unix_ns)
    struct.pack_into("!II", packet, offset, seconds, fraction)


def _make_ntp_response(request: bytes, receive_ns: int, transmit_ns: int) -> bytes:
    response = bytearray(48)
    response[0] = (0 << 6) | (4 << 3) | 4
    response[1] = 2
    response[24:32] = request[40:48]
    _write_ntp_timestamp(response, 32, receive_ns)
    _write_ntp_timestamp(response, 40, transmit_ns)
    return bytes(response)


def _start_fake_ntp_server(offset_ms: int = 25, processing_ms: int = 5) -> tuple[str, int, threading.Thread]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()

    def serve_once() -> None:
        try:
            request, address = sock.recvfrom(512)
            t1 = ntp_parts_to_unix_ns(*struct.unpack("!II", request[40:48]))
            receive_ns = t1 + offset_ms * NS_PER_MS
            transmit_ns = receive_ns + processing_ms * NS_PER_MS
            sock.sendto(_make_ntp_response(request, receive_ns, transmit_ns), address)
        finally:
            sock.close()

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    return host, port, thread


def test_parse_refresh_datetime_accepts_millisecond_precision() -> None:
    assert parse_refresh_datetime_value("2026/07/13 10:00:00") == datetime(2026, 7, 13, 10, 0, 0)
    assert parse_refresh_datetime_value("2026/07/13 10:00:00.000") == datetime(2026, 7, 13, 10, 0, 0)
    assert parse_refresh_datetime_value("2026/07/13 09:59:59.850") == datetime(2026, 7, 13, 9, 59, 59, 850000)
    assert parse_refresh_datetime_value("2026/07/13 10:00:00.40") is None
    assert parse_refresh_datetime_value("2026-07-13 10:00:00.000") is None


def test_calculate_refresh_trigger_datetime_ignores_deprecated_advance_budget() -> None:
    target = datetime(2026, 7, 9, 12, 0, 0)
    calibration = {
        "enable": True,
        "clock_offset_ms": -2,
        "frontend_delay_ms": 45,
        "network_uplink_ms": 35,
        "scheduler_jitter_ms": 0,
        "safety_margin_ms": 30,
        "freeze_before_seconds": 10,
    }

    assert calculate_refresh_trigger_datetime(target, calibration) == target


def test_compute_trigger_plan_exposes_reference_and_local_trigger_times() -> None:
    target = datetime(2026, 7, 10, 12, 0, 0)
    plan = compute_trigger_plan(
        target,
        {
            "enable": True,
            "clock_offset_ms": 10,
            "clock_uncertainty_ms": 4,
            "frontend_delay_ms": 40,
            "network_uplink_ms": 60,
            "scheduler_jitter_ms": 7,
            "safety_margin_ms": 30,
            "timezone": "local",
            "confidence": "medium",
        },
    )

    assert plan.total_advance_ms == 0
    assert plan.computed_trigger_reference_time == target
    assert plan.local_trigger_time == target
    assert plan.computed_trigger_display == "2026/07/10 12:00:00.000"
    assert plan.confidence == "unavailable"
    assert "standard target refresh" in " ".join(plan.warnings)
    assert plan.to_dict()["public_sale_target_reference_time"] == "2026/07/10 12:00:00.000"
    assert plan.to_dict()["computed_early_trigger_reference_time"] == "2026/07/10 12:00:00.000"


def test_platform_timing_capability_matrix_defaults_to_safe_modes() -> None:
    cases = {
        "https://tixcraft.com/activity/detail/abc": (
            "tixcraft",
            TimingModeCapability.STANDARD_MILLISECOND_TARGET_REFRESH,
            False,
        ),
        "https://kktix.com/events/abc": ("kktix", TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH, False),
        "https://kham.com.tw/application/UTK02/abc": ("kham", TimingModeCapability.STANDARD_MILLISECOND_TARGET_REFRESH, False),
        "https://ticketplus.com.tw/activity/abc": ("ticketplus", TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH, False),
        "https://tickets.funone.io/events/abc": ("funone", TimingModeCapability.STANDARD_MILLISECOND_TARGET_REFRESH, False),
        "https://ticket.ibon.com.tw/ActivityInfo/Details/abc": ("ibon", TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH, False),
        "https://www.famiticket.com.tw/Home/Activity/Info/abc": ("famiticket", TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH, False),
        "https://www.cityline.com/Events.html": ("cityline", TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH, False),
        "https://hotshow.hkticketing.com/events/abc": ("hkticketing", TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH, False),
        "https://go.fansi.me/events/abc": ("fansigo", TimingModeCapability.STANDARD_MILLISECOND_TARGET_REFRESH, False),
        "https://example.com/events/abc": ("unknown", TimingModeCapability.STANDARD_MILLISECOND_TARGET_REFRESH, False),
    }
    config = {"refresh_calibration": {"enable": True, "advanced_delay_mode": "auto"}}

    for url, expected in cases.items():
        decision = get_platform_timing_capability(None, url, config)
        assert (decision.platform_id, decision.capability, decision.advanced_active) == expected


def test_advanced_delay_mode_normalizes_and_gates_platforms() -> None:
    assert normalize_advanced_delay_mode(None) == "auto"
    assert normalize_advanced_delay_mode("") == "auto"
    assert normalize_advanced_delay_mode("bad") == "auto"
    assert normalize_advanced_delay_mode("ENABLED") == "enabled"

    tixcraft_disabled = get_platform_timing_capability(
        None,
        "https://tixcraft.com/activity/detail/abc",
        {"refresh_calibration": {"enable": True, "advanced_delay_mode": "disabled"}},
    )
    assert tixcraft_disabled.advanced_active is False
    assert tixcraft_disabled.effective_capability == TimingModeCapability.STANDARD_MILLISECOND_TARGET_REFRESH

    forced_unsupported = get_platform_timing_capability(
        None,
        "https://ticketplus.com.tw/activity/abc",
        {"refresh_calibration": {"enable": True, "advanced_delay_mode": "enabled"}},
    )
    assert forced_unsupported.advanced_active is False
    assert forced_unsupported.effective_capability == TimingModeCapability.QUEUE_AWARE_STANDARD_REFRESH
    assert any("deprecated" in warning for warning in forced_unsupported.warnings)


def test_effective_refresh_calibration_disables_advance_on_unsupported_platforms() -> None:
    target = datetime(2026, 7, 10, 12, 0, 0)
    config = {
        "refresh_calibration": {
            "enable": True,
            "advanced_delay_mode": "auto",
            "clock_offset_ms": 10,
            "frontend_delay_ms": 40,
            "network_uplink_ms": 60,
            "scheduler_jitter_ms": 7,
            "safety_margin_ms": 30,
        }
    }

    calibration, decision = get_effective_refresh_calibration(config, "https://ticketplus.com.tw/activity/abc")
    plan = compute_trigger_plan(target, calibration)

    assert decision.advanced_active is False
    assert calibration["enable"] is False
    assert plan.local_trigger_time == target
    assert plan.computed_trigger_display == "2026/07/10 12:00:00.000"


def test_tixcraft_deprecated_delay_config_does_not_advance_trigger() -> None:
    target = datetime(2026, 7, 10, 12, 0, 0)
    config = {
        "refresh_calibration": {
            "enable": True,
            "advanced_delay_mode": "auto",
            "clock_offset_ms": 10,
            "frontend_delay_ms": 40,
            "network_uplink_ms": 60,
            "scheduler_jitter_ms": 7,
            "safety_margin_ms": 30,
        }
    }

    calibration, decision = get_effective_refresh_calibration(config, "https://tixcraft.com/activity/detail/abc")
    plan = compute_trigger_plan(target, calibration)

    assert decision.advanced_active is False
    assert plan.local_trigger_time == target
    assert plan.to_dict()["public_sale_target_reference_time"] == "2026/07/10 12:00:00.000"
    assert plan.to_dict()["computed_early_trigger_reference_time"] == "2026/07/10 12:00:00.000"
    assert plan.computed_trigger_display == "2026/07/10 12:00:00.000"


def test_computed_trigger_never_silently_lands_after_public_target() -> None:
    target = datetime(2026, 7, 10, 12, 0, 0)
    plan = compute_trigger_plan(
        target,
        {
            "enable": True,
            "advanced_delay_mode": "auto",
            "clock_offset_ms": -1000,
            "frontend_delay_ms": 0,
            "network_uplink_ms": 0,
            "scheduler_jitter_ms": 0,
            "safety_margin_ms": 0,
        },
    )

    assert plan.local_trigger_time == target
    assert plan.computed_trigger_display == "2026/07/10 12:00:00.000"
    assert any("standard target refresh" in warning for warning in plan.warnings)


def test_get_refresh_calibration_defaults_and_clamps_values() -> None:
    calibration = get_refresh_calibration(
        {
            "refresh_calibration": {
                "enable": True,
                "clock_offset_ms": "90000",
                "clock_uncertainty_ms": "bad",
                "frontend_delay_ms": "-1",
                "network_uplink_ms": "9999",
                "scheduler_jitter_ms": "9999",
                "safety_margin_ms": "bad",
                "freeze_before_seconds": "90",
                "confidence": "HIGH",
            }
        }
    )

    assert calibration["enable"] is False
    assert calibration["clock_offset_ms"] == 60000
    assert calibration["clock_uncertainty_ms"] == 0
    assert calibration["frontend_delay_ms"] == 0
    assert calibration["network_uplink_ms"] == 5000
    assert calibration["scheduler_jitter_ms"] == 5000
    assert calibration["safety_margin_ms"] == 30
    assert calibration["freeze_before_seconds"] == 60
    assert calibration["confidence"] == "high"


def test_get_refresh_calibration_parses_string_booleans_safely() -> None:
    disabled = get_refresh_calibration(
        {
            "refresh_calibration": {
                "enable": "false",
                "auto_calibrate": "0",
            }
        }
    )
    enabled = get_refresh_calibration(
        {
            "refresh_calibration": {
                "enable": "true",
                "auto_calibrate": "1",
            }
        }
    )

    assert disabled["enable"] is False
    assert disabled["auto_calibrate"] is False
    assert enabled["enable"] is False
    assert enabled["auto_calibrate"] is False


def test_remaining_is_monotonic_and_clamped() -> None:
    assert compute_remaining_ns(1_000, 999) == 1
    assert compute_remaining_ns(1_000, 1_000) == 0
    assert compute_remaining_ns(1_000, 2_000) == 0
    assert format_remaining_seconds(-1) == "0.000s"


def test_trigger_controller_freezes_plan_and_triggers_once() -> None:
    target = datetime(2026, 7, 10, 12, 0, 0)
    clock = FakeClock(int((target - timedelta(seconds=5)).timestamp() * 1_000_000_000), 10_000_000_000)
    controller = RefreshTriggerController(clock=clock)
    calibration = {
        "enable": True,
        "frontend_delay_ms": 10,
        "network_uplink_ms": 10,
        "safety_margin_ms": 10,
        "freeze_before_seconds": 10,
    }

    first_plan = controller.arm(target, calibration, "sale")
    assert controller.maybe_freeze(target, 10) is True
    assert controller.phase == TriggerPhase.FROZEN

    second_plan = controller.arm(
        target,
        {**calibration, "frontend_delay_ms": 500},
        "sale-changed-too-late",
    )
    assert second_plan == first_plan

    clock.advance_ms(5_000)
    assert controller.should_trigger_once() is True
    assert controller.should_trigger_once() is False
    assert controller.initial_trigger_count == 1


def test_robust_estimate_rejects_single_extreme_outlier() -> None:
    estimate = robust_estimate([99, 100, 100, 101, 10_000, "bad", float("nan"), float("inf")])

    assert estimate["value_ms"] == 100.0
    assert estimate["sample_count"] == 4
    assert estimate["rejected_count"] == 4
    assert estimate["confidence"] == "medium"


def test_ntp_timestamp_round_trip() -> None:
    unix_ns = 1_783_689_600_123_456_789
    assert abs(ntp_parts_to_unix_ns(*unix_ns_to_ntp_parts(unix_ns)) - unix_ns) <= 1


def test_parse_ntp_response_validates_packet_and_math() -> None:
    t1 = 1_783_689_600_000_000_000
    t2 = t1 + 20 * NS_PER_MS
    t3 = t2 + 5 * NS_PER_MS
    t4 = t1 + 60 * NS_PER_MS
    request = build_ntp_request(t1)
    response = _make_ntp_response(request, t2, t3)

    parsed = parse_ntp_response(response, t1, t4)

    assert parsed["offset_ms"] == -7.5
    assert parsed["delay_ms"] == 55.0


def test_parse_ntp_response_rejects_originate_mismatch() -> None:
    request = build_ntp_request(1_783_689_600_000_000_000)
    response = _make_ntp_response(request, 1_783_689_600_020_000_000, 1_783_689_600_025_000_000)

    with pytest.raises(ValueError, match="originate"):
        parse_ntp_response(response, 1_783_689_600_999_000_000, 1_783_689_600_060_000_000)


def test_query_sntp_server_works_against_local_fake_server() -> None:
    t1 = 1_783_689_600_000_000_000
    t4 = t1 + 60 * NS_PER_MS
    clock = FakeClock(t1, 0)
    clock.wall_sequence = [t1, t4]
    host, port, thread = _start_fake_ntp_server(offset_ms=20, processing_ms=5)

    result = query_sntp_server(host, port=port, timeout_ms=1000, clock=clock)
    thread.join(timeout=1)

    assert result["success"] is True
    assert result["offset_ms"] == -7.5
    assert result["delay_ms"] == 55.0


def test_calibrate_ntp_servers_uses_robust_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    samples = iter(
        [
            {"success": True, "offset_ms": 10.0, "delay_ms": 20.0},
            {"success": True, "offset_ms": 11.0, "delay_ms": 22.0},
            {"success": True, "offset_ms": 10_000.0, "delay_ms": 9000.0},
            {"success": True, "offset_ms": 9.0, "delay_ms": 18.0},
        ]
    )

    monkeypatch.setattr("refresh_timing.query_sntp_server", lambda *args, **kwargs: next(samples))

    result = calibrate_ntp_servers(["fake"], samples_per_server=4, min_valid_samples=3)

    assert result["success"] is True
    assert result["clock_offset_ms"] == 10
    assert result["clock_uncertainty_ms"] <= 2
    assert result["confidence"] == "medium"


def test_select_time_source_prefers_quality_over_source_type() -> None:
    selected = select_time_source(
        [
            {
                "success": True,
                "source": "ntp",
                "confidence": "low",
                "clock_uncertainty_ms": 200,
                "age_seconds": 1,
            },
            {
                "success": True,
                "source": "http",
                "confidence": "medium",
                "clock_uncertainty_ms": 50,
                "age_seconds": 5,
            },
        ]
    )

    assert selected["source"] == "http"


def test_format_datetime_ms_truncates_to_millisecond_display() -> None:
    assert format_datetime_ms(datetime(2026, 7, 10, 12, 0, 0, 987654)) == "2026/07/10 12:00:00.987"
