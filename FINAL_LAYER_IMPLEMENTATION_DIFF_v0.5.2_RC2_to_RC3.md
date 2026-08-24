# HunterX v0.5.2 RC2 to RC3 Implementation Diff

## Product changes

### `src/nodriver_tixcraft.py`

- Added terminal-failure attempt-state lookup.
- Added a strict safe-restart eligibility predicate.
- Added the authoritative terminal-iteration lifecycle handler.
- Wrapped the single production `run_runtime_iteration()` call in `_run_main()` and delegated only terminal browser failures to that handler.

No platform selector, date/area/ticket quantity logic, submission click path, refresh cadence, OCR/CAPTCHA policy, or challenge/checkout/payment bypass was changed.

### `src/platforms/ticketplus.py`

- Removed user-dictionary injection from the TicketPlus confirmation/checkout handler.
- Preserved user-dictionary injection in the actual TicketPlus order custom-question multi-field flow.

This change narrows the dictionary boundary; it does not alter ticket selection or submission ownership.

## Test-isolation corrections

Three lifecycle test modules now clear their task-local `PlatformEngine` binding before and after each test. The same ordering defect reproduces on immutable RC2; it was a test-runner isolation problem, not a product behavior change. Assertions and production fixtures were not weakened.

## Release engineering

The committed-snapshot pipeline now supports the explicit `rc3` profile while retaining the existing RC2 profile. RC3 is locked to `hunterX_windows_0.5.2_rc2.zip` SHA-256 `47747a962cf5c4ae49654aec574ca64ac52c27032fc5b1ec1f70d83c3d09da48`, requires all Final-Layer reports, writes `RC3_BUILD_PROVENANCE.json`, forbids FINAL claims, runs fresh-extract packaged smoke, and verifies source/Windows runtime byte parity.

## Files intentionally unchanged

Core date, area, ticket, quantity, `allow_less_tickets`, attempt generation, duplicate-submit fences, `SUBMIT_OUTCOME_UNKNOWN`, TicketPlus lifecycle, TixCraft central/inner bridge, `ReloadGuard`, `RefreshCoordinator`, `LeakWatchScheduler`, named-instance isolation, OCR/manual CAPTCHA, notifications, profiles, and challenge/payment boundaries were not redesigned.

