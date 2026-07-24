from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import settings


def test_delay_calibration_ui_block_is_removed() -> None:
    settings_html = Path("src/www/settings.html").read_text(encoding="utf-8")
    settings_js = Path("src/www/settings.js").read_text(encoding="utf-8")
    help_js = Path("src/www/help-content.js").read_text(encoding="utf-8")

    combined = "\n".join((settings_html, settings_js, help_js))
    assert 'id="refresh_calibration_enable"' not in settings_html
    assert 'id="advanced_delay_mode"' not in settings_html
    assert 'id="btn_ticket_latency_estimate"' not in settings_html
    assert "延遲校準刷新" not in settings_html
    assert "估計單向延遲" not in combined
    assert "estimated one-way" not in combined


def test_timing_ui_exposes_standard_target_preview() -> None:
    settings_html = Path("src/www/settings.html").read_text(encoding="utf-8")
    settings_js = Path("src/www/settings.js").read_text(encoding="utf-8")

    assert 'id="refresh_target_time_preview"' in settings_html
    assert "Scheduled target" in settings_js
    assert "update_refresh_target_preview" in settings_js
    assert "calculate_refresh_trigger_date" in settings_js


def test_ui_countdown_timer_does_not_probe_every_second() -> None:
    settings_js = Path("src/www/settings.js").read_text(encoding="utf-8")

    assert "setInterval(update_refresh_target_preview, 1000)" in settings_js
    assert "calibrate_refresh_timing" not in settings_js
    assert "url: '/time_calibration'" not in settings_js
    assert "url: '/ticket_latency'" not in settings_js


def test_normalize_time_source_url_adds_https_and_path() -> None:
    assert settings.normalize_time_source_url("time.is") == "https://time.is/"
    assert settings.normalize_time_source_url("https://time.is/Taipei") == "https://time.is/Taipei"


def test_normalize_time_source_url_rejects_credentials() -> None:
    try:
        settings.normalize_time_source_url("https://user:pass@example.com/")
    except ValueError as exc:
        assert "credentials" in str(exc)
    else:
        raise AssertionError("expected credentials URL to be rejected")


def test_parse_server_time_from_json_accepts_common_shapes() -> None:
    expected = datetime(2026, 7, 9, 0, 0, tzinfo=UTC)
    assert settings.parse_server_time_from_json({"unixtime": expected.timestamp()}) == expected
    assert settings.parse_server_time_from_json({"utc_datetime": "2026-07-09T00:00:00.123456+00:00"}) == datetime(
        2026,
        7,
        9,
        0,
        0,
        0,
        123456,
        tzinfo=UTC,
    )


def test_private_time_source_hosts_are_rejected() -> None:
    assert settings.is_public_time_source_host("127.0.0.1") is False
    assert settings.is_public_time_source_host("localhost") is False


def test_estimate_ticket_site_latency_uses_best_rtt(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, status_code: int, url: str) -> None:
            self.status_code = status_code
            self.url = url
            self.headers: dict[str, str] = {}
            self.is_redirect = False
            self.is_permanent_redirect = False

        def close(self) -> None:
            pass

    values = iter([0.0, 0.12, 1.0, 1.08, 2.0, 2.2])

    monkeypatch.setattr(settings, "is_public_time_source_host", lambda hostname: True)
    monkeypatch.setattr(settings.time, "perf_counter", lambda: next(values))
    monkeypatch.setattr(settings.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        settings.requests,
        "request",
        lambda method, url, headers, timeout, stream, allow_redirects: FakeResponse(200, "https://tixcraft.com/activity/detail/test"),
    )

    result = settings.estimate_ticket_site_latency("https://tixcraft.com/activity/detail/test", samples=3)

    assert result["success"] is True
    assert result["best_rtt_ms"] == 80.0
    assert result["network_uplink_ms"] == 40
    assert result["estimated_one_way_delay_ms"] == 40
    assert result["rtt_p50_ms"] == 120.0
    assert result["jitter_ms"] == 72.0
    assert "exact one-way delay" in result["warning"]


def test_calibrate_time_by_mode_supports_system_fallback() -> None:
    result = settings.calibrate_time_by_mode({"mode": "system"})

    assert result["success"] is True
    assert result["source"] == "system"
    assert result["clock_offset_ms"] == 0
    assert result["confidence"] == "low"


def test_calibrate_time_by_mode_dispatches_ntp(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "calibrate_ntp_servers",
        lambda servers, timeout_ms, samples_per_server, min_valid_samples: {
            "success": True,
            "source": "ntp",
            "servers": servers,
            "timeout_ms": timeout_ms,
            "samples_per_server": samples_per_server,
            "min_valid_samples": min_valid_samples,
            "clock_offset_ms": 3,
            "clock_uncertainty_ms": 1,
            "confidence": "high",
        },
    )

    result = settings.calibrate_time_by_mode(
        {
            "mode": "ntp",
            "ntp_servers": "127.0.0.1",
            "ntp_timeout_ms": 50,
            "ntp_samples_per_server": 1,
            "ntp_min_valid_samples": 1,
        }
    )

    assert result["source"] == "ntp"
    assert result["servers"] == ["127.0.0.1"]
    assert result["clock_offset_ms"] == 3


def test_calibration_handlers_are_async_to_avoid_blocking_settings_server() -> None:
    assert inspect.iscoroutinefunction(settings.TimeCalibrationHandler.post)
    assert inspect.iscoroutinefunction(settings.TicketLatencyHandler.post)
    assert inspect.iscoroutinefunction(settings.TestDiscordWebhookHandler.post)
    assert inspect.iscoroutinefunction(settings.TestTelegramHandler.post)


def test_request_public_url_rejects_redirect_to_private_host(monkeypatch) -> None:
    class RedirectResponse:
        status_code = 302
        url = "https://example.com/"
        headers: ClassVar[dict[str, str]] = {"Location": "http://127.0.0.1/"}
        is_redirect = True
        is_permanent_redirect = False

        def close(self) -> None:
            pass

    monkeypatch.setattr(settings, "is_public_time_source_host", lambda hostname: hostname != "127.0.0.1")
    monkeypatch.setattr(
        settings.requests,
        "request",
        lambda method, url, headers, timeout, stream, allow_redirects: RedirectResponse(),
    )

    try:
        settings.request_public_url("GET", "https://example.com/", headers={}, timeout=1)
    except ValueError as exc:
        assert "public address" in str(exc)
    else:
        raise AssertionError("expected private redirect to be rejected")
