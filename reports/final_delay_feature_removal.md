# Final Delay Feature Removal

## Removed UI Elements

- Removed the active `延遲校準刷新` settings block from `src/www/settings.html`.
- Removed active controls for `advanced_delay_mode`, time source URL/mode, ticket latency estimate, frontend delay estimate, full delay estimate, and computed trigger display.
- Replaced the advanced block with `refresh_target_time_preview`, a standard target-time preview under `刷新在指定時間`.

## Deprecated Config Keys

- `refresh_calibration`
- `advanced_delay_mode`
- `enable`
- `auto_calibrate`
- `clock_offset_ms`
- `clock_uncertainty_ms`
- `frontend_delay_ms`
- `network_uplink_ms`
- `scheduler_jitter_ms`
- `safety_margin_ms`
- `freeze_before_seconds`
- `auto_calibrate_interval_seconds`
- `time_source_mode`
- `time_source_url`
- `ticket_site_url`

## Runtime Behavior

- `compute_trigger_plan()` now sets `local_trigger_time == target_dt`.
- `calculate_refresh_trigger_datetime()` returns the user-entered target time.
- `get_platform_timing_capability()` never activates `ADVANCED_DELAY_CALIBRATED_REFRESH`; TixCraft now uses standard millisecond target refresh.
- `settings.migrate_config()` loads old `refresh_calibration` safely but forces `enable=False` and `auto_calibrate=False`.
- New saves from the settings UI delete `settings.refresh_calibration`.

## Tests

- Updated `tests/test_refresh_timing.py`, `tests/test_config.py`, `tests/test_platform_timing_gate.py`, and `tests/test_time_calibration.py`.
- Focused tests and full tests passed after the change.
