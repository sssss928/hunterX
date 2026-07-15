# HunterX 0.2.0 Performance Update

Date: 2026-07-13

## Scope

This update keeps the runtime behavior conservative. It adds measurement and local benchmarking support, and removes repeated iBon OCR initialization. It does not add captcha bypass, anti-bot evasion, queue manipulation, proxy behavior, or new purchasing behavior.

## Changes

- Added `src/performance.py` with `PerformanceTrace` for per-attempt timing fields:
  - `capture_ms`
  - `ocr_ms`
  - `fill_ms`
  - `submit_ms`
  - `total_ms`
- Added profiling hooks to existing OCR boundaries:
  - TixCraft captcha capture/OCR/fill/submit
  - iBon captcha capture/OCR/fill/submit
  - KHAM captcha capture/OCR/fill/submit
- Added iBon OCR instance caching through `_get_ibon_ocr_instance()` so the platform does not repeatedly create `DdddOcr(...)` while OCR settings are unchanged.
- Added `scripts/ocr_latency_benchmark.py` for offline local image benchmarking. It preloads OCR once, then reports per-image OCR latency statistics.
- Added `tests/test_performance_trace.py` and import-smoke coverage for `performance`.
- Updated `requirement.txt` from `Pillow==12.2.0` to `Pillow==12.3.0` after `pip-audit` reported known Pillow 12.2.0 vulnerabilities with 12.3.0 as the fixed version.
- Added `performance` to the PyInstaller hidden imports for `nodriver_tixcraft`.

## Validation Summary

- `python -m compileall src tests scripts`: pass
- `python -m pytest -q`: pass, 105 passed
- `python -m pytest --cov=src --cov-branch --cov-report=term-missing`: pass, 105 passed
- `python -m ruff check src tests scripts`: pass
- `python -m mypy`: pass
- `python -m pip_audit -r requirement.txt`: pass after Pillow 12.3.0 update
- JS syntax check with bundled Node: pass
- `python -m bandit -c pyproject.toml -r src`: exits 1 with existing Low findings only; Medium 0, High 0

## Remaining Risks

- Profiling hooks expose where time is spent, but they do not prove faster real-world outcomes without platform-specific runtime samples.
- Existing browser automation coverage remains low because most platform flows require live browser/page state.
- iBon OCR cache is keyed by OCR settings; if future runtime code mutates an OCR instance directly, cache invalidation may need to be revisited.
- KHAM and TixCraft still contain fixed waits and full browser interactions; this update measures them but does not broadly refactor them.
