# HunterX 0.2.0 Final Verification

## 1. Executive Verdict

Verification-only check completed against the current HunterX 0.2.0 source tree, tests, and existing output artifacts.

Evidence supports the requested 0.2.0 release state:

- Required version locations and output artifact names indicate 0.2.0.
- User-facing delay-calibrated refresh controls are absent or inactive.
- Deprecated delay-calibration config keys are tolerated but do not move the scheduled refresh time.
- `refresh_datetime` accepts second and exact millisecond formats.
- `auto_reload_page_interval` preserves decimal seconds and treats `0` / `"0"` as disabled.
- Requested Python, JS, build, zip, hash, replacement-character, import, and resource checks were run.

This is not a zero-bug claim. Remaining risks are listed at the end.

## 2. Exact Commands Executed

Primary requested commands:

| Command | Exit | Evidence |
| --- | ---: | --- |
| `python -m compileall src tests scripts` | 0 | Listed `src`, `src\platforms`, `src\www\dist`, `tests`, `scripts`. |
| `python -m pytest -q` | 0 | `102 passed in 14.62s`; coverage XML written by project config. |
| `python -m pytest --cov=src --cov-branch --cov-report=term-missing` | 0 | `102 passed in 14.89s`; total coverage table emitted. |
| `python -m ruff check src tests scripts` | 0 | `All checks passed!` |
| `python -m mypy` | 0 | `Success: no issues found in 4 source files` |
| `python -m bandit -c pyproject.toml -r src` | 1 | Low: 253, Medium: 0, High: 0, Files skipped: 0. |
| `python -m pip_audit -r requirement.txt` | 0 | `No known vulnerabilities found` |

Additional verification commands:

| Command | Exit | Evidence |
| --- | ---: | --- |
| `node --check src/www/settings.js` | 0 | No syntax errors. |
| `python -m PyInstaller build_scripts/nodriver_tixcraft.spec --clean --noconfirm` | 0 | `Build complete! The results are available in: ...\dist` |
| `python -m PyInstaller build_scripts/settings.spec --clean --noconfirm` | 0 | `Build complete! The results are available in: ...\dist` |
| Extract `outputs/hunterX_windows_0.2.0.zip` to `work/smoke_hunterX_windows_0.2.0` | 0 | Extracted; filesystem count after extraction: 270 entries. |
| `work/smoke_hunterX_windows_0.2.0/nodriver_tixcraft.exe --help` | 0 | Argparse help printed. |
| `work/smoke_hunterX_windows_0.2.0/settings.exe` bounded launch with port `16888` occupied | 0 | Process started and remained running until the 3.5s smoke timeout; no immediate crash detected. |
| Source and Windows ZIP `zipfile.testzip()` plus SHA256 sidecar check | 0 | Both `testzip_bad=None`; both sidecars exist and match. |
| Targeted source behavior, platform import, replacement/resource integrity verifier | 0 | Corrected verifier passed; values summarized below. |
| `auto_reload_page_interval` consumer verifier | 0 | Decimal and disabled behavior summarized below. |
| Source and Windows ZIP version scan | 0 | Required artifact files contain 0.2.0. |
| Precise user-facing delay-calibration removal check | 0 | All delay UI/AJAX absence checks returned `True`. |
| Complete `auto_reload_page_interval` reference scan | 0 | Consumers and tests discovered; key behavior summarized below. |

Note: an initial ad hoc targeted verifier failed because the verifier script used a non-existent dataclass field name (`effective_mode`). The corrected verifier used `effective_capability` and passed. This was a verifier-script error, not a HunterX runtime failure.

## 3. Exact Pass / Fail Results

- Pass: compileall.
- Pass: pytest normal run, `102 passed`.
- Pass: pytest coverage run, `102 passed`.
- Pass: Ruff.
- Pass: mypy.
- Fail by exit code: Bandit, with Low 253 / Medium 0 / High 0.
- Pass: pip-audit.
- Pass: JS syntax check.
- Pass: both PyInstaller specs.
- Pass: source ZIP integrity.
- Pass: Windows ZIP integrity.
- Pass: SHA256 sidecar existence and match.
- Pass: replacement-character scan, 0 files.
- Pass: platform import verification, 0 import failures.
- Pass: required resource path check, 0 missing paths.

## 4. Version Verification

Working tree required version locations:

| Location | Result |
| --- | --- |
| `src/hunter_metadata.py` | Contains 0.2.0; imported `APP_VERSION == "0.2.0"`. |
| `README.md` | Contains 0.2.0. |
| `CHANGELOG.md` | Contains 0.2.0. Also contains historical 0.1.6 entry. |
| `build_scripts/build_and_test.bat` | Contains 0.2.0. |
| `build_scripts/QUICK_START.md` | Contains 0.2.0. |
| `build_scripts/README_Release.txt` | Contains 0.2.0. |
| `src/www/settings.html` | Contains 0.2.0. |
| `src/www/settings.js` | Contains 0.2.0. |

Artifact version evidence:

- `outputs/hunterX_source_0.2.0.zip`: artifact name has 0.2.0; source files inside include 0.2.0.
- `outputs/hunterX_windows_0.2.0.zip`: artifact name has 0.2.0; `README.md`, `README_Release.txt`, `_internal/www/settings.html`, `_internal/www/settings.js`, `www/settings.html`, and `www/settings.js` contain 0.2.0.
- `CHANGELOG.md` inside artifacts contains historical 0.1.6 text as expected release history.

## 5. `refresh_datetime` Millisecond Verification

Direct parser results from `parse_refresh_datetime_value`:

| Input | Result |
| --- | --- |
| `2026/07/13 10:00:00` | `2026-07-13T10:00:00` |
| `2026/07/13 10:00:00.000` | `2026-07-13T10:00:00` |
| `2026/07/13 10:00:00.001` | `2026-07-13T10:00:00.001000` |
| `2026/07/13 10:00:00.999` | `2026-07-13T10:00:00.999000` |
| `2026/07/13 09:59:59.850` | `2026-07-13T09:59:59.850000` |

Missing milliseconds:

- `2026/07/13 10:00:00` parsed with `microsecond == 0`, equivalent to `.000`.

Invalid values failed safely:

| Input | Result |
| --- | --- |
| empty string | `None` |
| `None` | `None` |
| `2026-07-13 10:00:00` | `None` |
| `2026/07/13 10:00` | `None` |
| `2026/07/13 10:00:00.40` | `None` |
| `2026/07/13 10:00:00.1234` | `None` |
| `2026/02/30 10:00:00.000` | `None` |
| `bad` | `None` |
| `2026/07/13 10:00:00.abc` | `None` |

UI evidence:

- `src/www/settings.html` placeholder is `YYYY/MM/DD HH:MM:SS.SSS`.
- `src/www/settings.js` validation message says the accepted formats are `YYYY/MM/DD HH:MM:SS` or `YYYY/MM/DD HH:MM:SS.SSS`.

## 6. `auto_reload_page_interval` Decimal Verification

Config migration and shared consumer parsing results:

| Input | Parsed seconds | Disabled |
| --- | ---: | --- |
| `5` | `5.0` | false |
| `5.0` | `5.0` | false |
| `"5"` | `5.0` | false |
| `"5.0"` | `5.0` | false |
| `"1.400"` | `1.4` | false |
| `"1.4"` | `1.4` | false |
| `1.4` | `1.4` | false |
| `0` | `0.0` | true |
| `"0"` | `0.0` | true |

Decimal behavior evidence:

- `decimal_not_truncated`: `true` for `"1.400" -> 1.4`.
- `decimal_not_milliseconds`: `true` for `"1.4" -> 1.4`; it is not interpreted as `0.0014` or `1400`.
- `is_interval_due(101.399, 100.0, 1.4) is False`.
- `is_interval_due(101.4, 100.0, 1.4) is True`.

Zero/loop evidence:

- `get_auto_reload_interval(... 0 ...) == 0.0`.
- `get_auto_reload_interval(... "0" ...) == 0.0`.
- Platform regression tests include zero interval disabled behavior.
- `bounded_poll` rejects `interval_value <= 0`.
- `sleep_until_deadline` uses bounded sleeps and exits at or after deadline; no zero-delay sleep loop is used there.

## 7. Delay-Calibration Removal Verification

Precise user-facing removal checks:

| Check | Result |
| --- | --- |
| `id="refresh_calibration_enable"` absent from `settings.html` | true |
| `id="btn_ticket_latency_estimate"` absent from `settings.html` | true |
| `延遲校準刷新` heading absent from `settings.html` | true |
| `url: '/time_calibration'` absent from `settings.js` | true |
| `url: '/ticket_latency'` absent from `settings.js` | true |
| `delete settings.refresh_calibration` present in save path | true |

Source evidence:

- `settings.html` has no user-facing `refresh_calibration` section.
- `settings.js` deletes `settings.refresh_calibration` when saving.
- `settings.js` has no active `/time_calibration` or `/ticket_latency` AJAX path.

Residual compatibility evidence:

- `settings.js` still contains default compatibility keys such as `auto_calibrate_interval_seconds`, but there is no user-facing delay-calibration block or active ticket latency/time calibration AJAX path.

## 8. Deprecated Config Compatibility Verification

Input deprecated config included:

- `enable: true`
- `auto_calibrate: true`
- `advanced_delay_mode: "enabled"`
- `clock_offset_ms: -60000`
- `frontend_delay_ms: 5000`
- `network_uplink_ms: 5000`
- `estimated_one_way_delay_ms: 5000`
- `scheduler_jitter_ms: 5000`
- `safety_margin_ms: 5000`
- `freeze_before_seconds: 60`
- time source URL
- ticket site URL

Observed results:

| Field | Result |
| --- | --- |
| `get_refresh_calibration(...).enable` | `false` |
| `get_refresh_calibration(...).auto_calibrate` | `false` |
| effective calibration `enable` | `false` |
| platform ID | `tixcraft` |
| capability | `STANDARD_MILLISECOND_TARGET_REFRESH` |
| effective capability | `STANDARD_MILLISECOND_TARGET_REFRESH` |
| advanced supported | `false` |
| advanced active | `false` |
| warning | `advanced delay calibration is deprecated and ignored; using standard target refresh` |

Scheduling result:

- Target: `2026/07/13 10:00:00.123`.
- Computed trigger reference time: `2026/07/13 10:00:00.123`.
- Local trigger time: `2026/07/13 10:00:00.123`.
- `clock_offset_ms`: `0`.
- `ticket_network_budget_ms`: `0`.
- `frontend_budget_ms`: `0`.
- `scheduler_budget_ms`: `0`.
- `safety_margin_ms`: `0`.
- `total_advance_ms`: `0`.
- `calculate_refresh_trigger_datetime(target, deprecated_config) == target`: true.

Conclusion from evidence: old delay settings are tolerated but cannot move the scheduled refresh time.

## 9. REMAINING Verification

Direct results:

| Case | Result |
| --- | --- |
| Before trigger: `compute_remaining_ns(1_000_000_000, 999_000_000)` | `1_000_000` ns |
| At trigger: `compute_remaining_ns(1_000_000_000, 1_000_000_000)` | `0` |
| After trigger: `compute_remaining_ns(1_000_000_000, 1_001_000_000)` | `0` |
| Format after trigger | `0.000s` |
| Format negative input | `0.000s` |
| Never negative check over before/at/after samples | true |

## 10. Scheduler / UI Independence Verification

Evidence:

| Check | Result |
| --- | --- |
| UI preview timer: `setInterval(update_refresh_target_preview, 1000)` exists | true |
| `settings.js` mentions `sleep_until_deadline` | false |
| `refresh_timing.py` defines `sleep_until_deadline` | true |
| `nodriver_tixcraft.py` uses `sleep_until_deadline(...)` | true |
| `nodriver_tixcraft.py` uses `RefreshTriggerController` | true |

Conclusion from evidence:

- The one-second UI preview timer updates display text only.
- Trigger precision is controlled by `RefreshTriggerController`, monotonic deadline calculation, and `sleep_until_deadline`, not by the UI polling interval.

## 11. Platform File Inventory Result

Discovered `src/platforms/*.py` implementation files:

- `src/platforms/cityline.py`
- `src/platforms/common_async.py`
- `src/platforms/facebook.py`
- `src/platforms/famiticket.py`
- `src/platforms/fansigo.py`
- `src/platforms/funone.py`
- `src/platforms/hkticketing.py`
- `src/platforms/ibon.py`
- `src/platforms/kham.py`
- `src/platforms/kktix.py`
- `src/platforms/ticketplus.py`
- `src/platforms/tixcraft.py`

Import-verified modules:

- `nodriver_tixcraft`
- `platforms.cityline`
- `platforms.common_async`
- `platforms.facebook`
- `platforms.famiticket`
- `platforms.fansigo`
- `platforms.funone`
- `platforms.hkticketing`
- `platforms.ibon`
- `platforms.kham`
- `platforms.kktix`
- `platforms.ticketplus`
- `platforms.tixcraft`

Import failures: none.

Delay path dependency scan:

- `src/platforms/*.py`: no hits for `refresh_calibration`, `frontend_delay_ms`, `estimated_one_way_delay`, `clock_offset_ms`, `ticket_latency`, or `time_calibration`.
- `src/nodriver_tixcraft.py`: references `refresh_calibration` in the central gate path; targeted verification showed that this path ignores deprecated delay values and schedules at target time.

Existing platform refresh behavior evidence:

- Full pytest suite passed.
- Platform regression tests passed as part of the full suite.
- All platform modules imported successfully.
- No live ticketing site workflow was executed.

## 12. Windows Build / Smoke Result

Build evidence:

- `python -m PyInstaller build_scripts/nodriver_tixcraft.spec --clean --noconfirm`: exit 0.
- `python -m PyInstaller build_scripts/settings.spec --clean --noconfirm`: exit 0.

Packaged artifact smoke evidence:

- `outputs/hunterX_windows_0.2.0.zip` extracted to `work/smoke_hunterX_windows_0.2.0`.
- `nodriver_tixcraft.exe --help`: exit 0 and printed argparse help.
- `settings.exe` bounded launch with port `16888` occupied: process started and stayed running until the 3.5s smoke timeout; no immediate crash detected. This is startup smoke only, not a full UI workflow.

## 13. ZIP / SHA / Resource Integrity

Source ZIP:

- Path: `outputs/hunterX_source_0.2.0.zip`.
- Size: `10922063` bytes.
- Entries: `166`.
- `zipfile.testzip()`: `None`.
- SHA256: `90c8f36d903e2489f98fe163fc56a1e357a179d759717ad8add05876ba148f19`.
- Sidecar exists: `outputs/hunterX_source_0.2.0.zip.sha256`.
- Sidecar matches: true.
- Required source entries missing: none.
- Forbidden generated entries detected: none.

Windows ZIP:

- Path: `outputs/hunterX_windows_0.2.0.zip`.
- Size: `200851272` bytes.
- Entries: `207`.
- `zipfile.testzip()`: `None`.
- SHA256: `75a3e4613f91043b9dff0839575bf4b9ffb9ddb56b5b170236666baa733a6284`.
- Sidecar exists: `outputs/hunterX_windows_0.2.0.zip.sha256`.
- Sidecar matches: true.

Replacement-character scan:

- Scanned `src`, `tests`, `scripts`, `build_scripts`, `reports`, `README.md`, and `CHANGELOG.md`.
- Files containing U+FFFD replacement character: `0`.

Required resource paths:

- `src/hunter_metadata.py`: present.
- `src/www/settings.html`: present.
- `src/www/settings.js`: present.
- `src/www/help-content.js`: present.
- `src/www/dist/jquery.min.js`: present.
- `src/www/dist/bootstrap/bootstrap.min.css`: present.
- `src/www/dist/bootstrap/bootstrap.min.js`: present.
- `build_scripts/nodriver_tixcraft.spec`: present.
- `build_scripts/settings.spec`: present.

## 14. Remaining Risks

- Bandit still exits nonzero because 253 Low-severity findings remain in inherited code; Medium and High counts are 0.
- No live ticketing websites were hit.
- No real browser purchase flow was executed.
- `settings.exe` smoke was a bounded startup check only; it was not a full settings UI/browser workflow test.
- Some redirect/reload paths read the normalized config value directly; tests and imports passed, but those paths were not live-tested against real sites.
- This verification report was created after the already verified source ZIP artifact; the source ZIP integrity evidence applies to the existing `outputs/hunterX_source_0.2.0.zip` artifact.
