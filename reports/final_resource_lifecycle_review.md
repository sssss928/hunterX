# Final Resource Lifecycle Review

## Reviewed Areas

- Settings UI no longer starts background delay-calibration timers or ticket-latency AJAX flows.
- Deprecated delay settings no longer arm freeze/early-trigger paths.
- PyInstaller artifacts were built and packaged manually because PowerShell execution in this tool channel was unreliable.
- Downloaded Chrome permissions were tightened from `0o755` to `0o700`, fixing the only Bandit Medium finding.

## Remaining Risks

- Large inherited platform modules still contain broad exception handling and fixed sleeps.
- `settings.exe` smoke was limited to package/existence/integrity because launching it starts a local server/browser workflow.
