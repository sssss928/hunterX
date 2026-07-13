# Final Millisecond Refresh Datetime

## Accepted Formats

- `YYYY/MM/DD HH:MM:SS`
- `YYYY/MM/DD HH:MM:SS.SSS`

## Rejected Inputs

- Invalid dates/times rejected through strict parse plus `datetime.strptime`.
- Fractional seconds must be exactly 3 digits.
- Hyphen date form is no longer accepted by the shared parser for the settings field.

## Scheduler Semantics

- Wall-clock target is converted to a monotonic deadline through `wall_datetime_to_monotonic_deadline_ns()`.
- Remaining display is clamped at `0.000s` through `compute_remaining_ns()` and `format_remaining_seconds()`.
- UI preview cadence is independent from final bounded scheduler wait in `sleep_until_deadline()`.

## Evidence

- `tests/test_refresh_timing.py::test_parse_refresh_datetime_accepts_millisecond_precision` covers accepted and rejected formats.
- 20 repeated focused timing test runs passed.
