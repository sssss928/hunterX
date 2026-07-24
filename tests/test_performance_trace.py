from __future__ import annotations

import performance


class CapturingDebug:
    def __init__(self) -> None:
        self.messages: list[tuple[object, ...]] = []

    def log(self, *parts: object) -> None:
        self.messages.append(parts)


def test_performance_trace_formats_core_stage_fields() -> None:
    trace = performance.PerformanceTrace("unit")

    trace.record_ms(performance.CAPTURE_STAGE, 1.25)
    trace.record_ms(performance.OCR_STAGE, 2.5)
    trace.record_ms(performance.FILL_STAGE, 3.75)
    trace.record_ms(performance.SUBMIT_STAGE, 4.0)

    line = trace.format_for_log()

    assert "label=unit" in line
    assert "capture_ms=1.250" in line
    assert "ocr_ms=2.500" in line
    assert "fill_ms=3.750" in line
    assert "submit_ms=4.000" in line
    assert "total_ms=" in line


def test_performance_trace_clamps_negative_elapsed_values() -> None:
    trace = performance.PerformanceTrace("unit")

    trace.record_ms(performance.CAPTURE_STAGE, -10)

    assert trace.snapshot()["capture_ms"] == 0.0


def test_log_trace_uses_debug_logger_contract() -> None:
    debug = CapturingDebug()
    trace = performance.PerformanceTrace("unit")

    performance.log_trace(debug, trace, "[PERF]")

    assert len(debug.messages) == 1
    assert debug.messages[0][0] == "[PERF]"
    assert "capture_ms=n/a" in debug.messages[0][1]
