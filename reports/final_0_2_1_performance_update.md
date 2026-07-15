# HunterX 0.2.1 Performance Update

Date: 2026-07-13

## Scope

This release turns the 0.2.0 timing work into a conservative shared OCR performance layer. It is designed to reduce OCR initialization stalls without changing high-risk captcha input and submit behavior.

This update does not add captcha bypass, anti-bot evasion, queue manipulation, proxy behavior, or new purchasing behavior.

## Runtime Changes

- Added `src/ocr_cache.py` for shared OCR profile resolution, cache reuse, and preload-style initialization.
- Updated the main NoDriver startup OCR path to use the shared cache instead of ad hoc OCR construction.
- Replaced the iBon private OCR cache with the shared OCR cache while keeping iBon's fallback range `0`.
- Added FunOne to the shared OCR cache while forcing standard OCR with `set_ranges(5)` so its A-Z/0-9 captcha behavior is preserved.
- Kept TixCraft and KHAM captcha entry behavior conservative:
  - TixCraft still requires 4-character OCR output and keeps Yii2 hash validation when available.
  - KHAM remains manual-safe because its captcha loop disables away-from-keyboard auto-submit.
  - Existing fill/submit guards and fallbacks remain in place.
- Restored the settings UI vendor assets required by existing metadata tests.
- Updated visible version strings and release instructions to `0.2.1`.

## Validation Summary

- `python -m compileall src tests scripts`: pass
- `python -m pytest -q`: pass, 109 passed
- `python -m ruff check src tests scripts`: pass
- `python -m mypy`: pass
- `python -m pip_audit -r requirement.txt`: pass, no known vulnerabilities found
- `python -m bandit -c pyproject.toml -r src`: exits 1 with existing Low findings only; Medium 0, High 0
- `python tests/benchmarks/audit_performance.py --output work/v0_2_1_performance_audit.json --markdown work/v0_2_1_performance_audit.md --samples 5 --iterations 1000`: pass

## Performance Benchmark

Generated: 2026-07-13T16:31:14

| Benchmark | p50 wall ms | p95 wall ms | p99 wall ms | mean wall ms | max peak bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| default_config | 29.461300 | 29.950600 | 29.950600 | 29.399400 | 3192 |
| config_migration | 144.766700 | 147.317500 | 147.317500 | 144.889760 | 12557 |
| interval_parsing | 3.924900 | 4.198200 | 4.198200 | 3.893940 | 617 |
| interval_due | 2.270500 | 2.509900 | 2.509900 | 2.314720 | 601 |
| bounded_poll_immediate | 7.611600 | 8.091900 | 8.091900 | 7.619660 | 11245 |
| keyword_matching | 25.704000 | 26.493500 | 26.493500 | 25.842200 | 1706 |
| refresh_timing | 241.169400 | 245.099100 | 245.099100 | 240.816280 | 2546 |
| trigger_planner | 55.065800 | 55.316600 | 55.316600 | 54.989020 | 2581 |
| robust_estimate | 64.659100 | 67.534600 | 67.534600 | 65.192840 | 728 |
| remaining_calculation | 3.090600 | 3.183800 | 3.183800 | 3.088040 | 212 |
| source_selection | 26.086200 | 26.159700 | 26.159700 | 26.073720 | 1783 |
| platform_interval_helpers | 2.926200 | 3.115500 | 3.115500 | 2.925420 | 160 |
| url_classification | 17.391300 | 17.953000 | 17.953000 | 17.481240 | 1374 |

## Expected Impact

- iBon, FunOne, and the startup OCR path no longer pay repeated OCR model initialization cost for matching OCR settings.
- TixCraft and KHAM keep the original conservative captcha input/submit behavior while using the shared startup OCR instance.
- The update should reduce OCR-related stalls, especially when the same OCR profile is reused across retries.

## Remaining Risks

- This report does not claim better OCR recognition accuracy.
- This report does not claim faster live checkout submission, because live platform flows depend on page state, network latency, queue state, and platform-side validation.
- The correct next measurement is real runtime logs from `[TIXCRAFT PERF]`, `[IBON PERF]`, `[KHAM PERF]`, and FunOne OCR logs during actual monitored sessions.
