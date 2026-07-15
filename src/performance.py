from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter_ns
from typing import Any


CAPTURE_STAGE = "capture"
OCR_STAGE = "ocr"
FILL_STAGE = "fill"
SUBMIT_STAGE = "submit"
CORE_STAGE_ORDER = (CAPTURE_STAGE, OCR_STAGE, FILL_STAGE, SUBMIT_STAGE)


def _stage_key(stage: str) -> str:
    return stage if stage.endswith("_ms") else f"{stage}_ms"


@dataclass
class PerformanceTrace:
    """Small per-attempt timing collector for browser workflow diagnostics."""

    label: str
    started_ns: int = field(default_factory=perf_counter_ns)
    _values_ms: dict[str, float] = field(default_factory=dict)

    def record_ms(self, stage: str, elapsed_ms: float) -> None:
        key = _stage_key(stage)
        elapsed_ms = max(0.0, float(elapsed_ms))
        self._values_ms[key] = self._values_ms.get(key, 0.0) + elapsed_ms

    def record_elapsed(self, stage: str, started_ns: int, ended_ns: int | None = None) -> None:
        if ended_ns is None:
            ended_ns = perf_counter_ns()
        self.record_ms(stage, (ended_ns - started_ns) / 1_000_000)

    def snapshot(self) -> dict[str, float | None]:
        result: dict[str, float | None] = {}
        for stage in CORE_STAGE_ORDER:
            result[_stage_key(stage)] = self._values_ms.get(_stage_key(stage))
        for key in sorted(self._values_ms):
            result.setdefault(key, self._values_ms[key])
        result["total_ms"] = max(0.0, (perf_counter_ns() - self.started_ns) / 1_000_000)
        return result

    def format_for_log(self) -> str:
        fields = [f"label={self.label}"]
        for key, value in self.snapshot().items():
            if value is None:
                fields.append(f"{key}=n/a")
            else:
                fields.append(f"{key}={value:.3f}")
        return " ".join(fields)


def record_elapsed(trace: PerformanceTrace | None, stage: str, started_ns: int) -> None:
    if trace is not None:
        trace.record_elapsed(stage, started_ns)


def log_trace(debug: Any, trace: PerformanceTrace | None, prefix: str) -> None:
    if trace is not None and debug is not None:
        debug.log(prefix, trace.format_for_log())
