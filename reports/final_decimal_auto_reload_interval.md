# Final Decimal Auto Reload Interval

## Behavior

- `0` and `"0"` normalize to disabled.
- Positive decimal seconds are accepted by `get_auto_reload_interval()`, including `1.400` and `1.4`.
- Invalid, NaN, Infinity, empty, and malformed values fall back deterministically according to existing defaults.
- The settings UI warning for low intervals is preserved through `updateTixcraftRefreshWarning()`.

## Tests

- `tests/test_common_async.py::test_get_auto_reload_interval_normalizes_user_values` covers decimal and disabled values.
- `tests/test_config.py::test_migrate_config_normalizes_auto_reload_interval` covers migration normalization.
- Platform regression tests still cover zero disabled and positive interval behavior.
