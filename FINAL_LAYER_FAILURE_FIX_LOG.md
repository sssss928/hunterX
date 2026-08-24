# Final-Layer Failure and Fix Log

## P0-1: terminal browser exception terminated Windows EXE

- RC2 negative control: failed at `_run_main()` with uncaught `ConnectionClosedError`.
- Root cause: missing authoritative lifecycle boundary.
- Fix: minimal `_run_main()` terminal escalation owner using existing supervisor/session manager.
- First focused failure: an already fail-closed supervisor was overwritten by a new URL-failure record.
- Correction: return the existing fail-closed decision before recording another failure.
- Result: single reproducer, focused, 20 fresh processes, adjacent, and cross-platform suites passed.

## P1-1: dictionary crossed into TicketPlus checkout

- RC2 negative control: confirmation handler invoked the custom dictionary.
- Fix: removed only the confirmation-page call; retained order-page multi-field answers.
- Result: negative control converted to pass; TicketPlus and dictionary suites passed.

## Test-order isolation failure

- Symptom: cross-platform aggregate tests read a previous synthetic task-local platform-state binding.
- Negative control: identical test order failed on immutable RC2.
- Fix: clear task-local bindings in three test modules before/after tests.
- Production code: unchanged.
- Result: original failing order and aggregate cross-platform suite passed.

## Performance investigation

- Initial low-density measurements exceeded investigation thresholds in two noisy metrics.
- No assertion was relaxed. Balanced focused and high-density repeats were run.
- The increase did not reproduce; no production performance change was made.

## Release-profile compatibility failures

- First RC3 release focused run: 7 failures (2 RC2 message-contract regressions and 5 stale RC2 workflow assertions).
- Fix: restored the exact RC2 errors and replaced stale assertions with strict RC3 base/hash/tag/qualifier/provenance checks.
- Result: 119 release-focused tests passed.

## Recorded limitations

- system `python` initially resolved to a Windows Store alias; all authoritative runs used the pinned workspace CPython 3.11 runtime;
- direct whole-file legacy mypy on `src/nodriver_tixcraft.py` reports the same 126 pre-existing errors in RC2 and RC3; the configured strict 28-file gate passes;
- all-level Bandit reports one existing medium `marshal.loads` finding in the trusted PyInstaller repacker plus low findings; the configured high-severity release gate reports zero;
- 8-hour actual-browser gates were not executed.

