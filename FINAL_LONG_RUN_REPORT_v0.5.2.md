# HunterX v0.5.2 FINAL Long-Run Report

## Final delivery local actual-browser integration

- Single named instance: 32.844 seconds, 63 cycles, 0 errors, 0 CDP errors,
  0 duplicate submits, maximum one tab, asyncio tasks 8 → 8.
- Three named instances in three OS processes: 62.515/63.343/63.500 seconds,
  69/70/70 cycles, each worker exit code 0, 0 errors, 0 CDP errors,
  0 duplicate submits, maximum one tab each, asyncio tasks 8 → 8 each.
- The synthetic route rotation covered TicketPlus, TixCraft, and KKTIX-shaped
  onsale/login-like/fallback transitions through the authoritative production
  iteration used by `_run_main`.

These short runs did not reach target-replacement or reload-injection
thresholds. Those behaviors remain covered by deterministic tests and earlier
documented RC evidence, not claimed from these two 60-second runs. No live
third-party ticket service, CAPTCHA, queue, challenge, checkout, payment, or
risk-control bypass was exercised.

The pre-fix three-process run reproduced a fixture-startup race as
`TypeError: undefined.push`. The synthetic harness was invoking its local page
API before page-script initialization completed. The fix waits for the exact
`push`, `replace`, and `rerender` API with a five-second limit after initial
load, target replacement, and reload; a missing API raises a terminal timeout.
The post-fix evidence above is from fresh browser profiles and OS processes.

## Eight-hour gate status

**BOTH 8H ACTUAL-BROWSER GATES USER WAIVED / NOT VERIFIED.**

- The qualification run was stopped at the user's request after 2,052.408
  seconds (about 34 minutes 12 seconds); both captured stderr files were empty.
- Eight-hour single named instance: not completed and not claimed as passed.
- Eight-hour three named instances: not completed and not claimed as passed.
- Remaining exact soak processes after shutdown: 0.

The user expressly requested FINAL artifacts while waiving these two duration
gates. `FINAL_8H_SOAK_WAIVER.json` is the authoritative machine-readable
record. The partial run and waiver are not test successes.
