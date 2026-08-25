# HunterX v0.5.2 FINAL Test Report

Release mode: **USER_WAIVED_8H_GATES**. The two eight-hour actual-browser
tests are not verified and are not included in any PASS total.

## Final delivery verification

| Gate | Result |
|---|---|
| Release/waiver focused suite | 75/75 passed |
| Dictionary + terminal lifecycle + production iteration focused | 112/112 passed |
| Four critical tests in 20 fresh processes | 80/80 passed |
| Full suite, fresh process run 1 | 1193/1193 passed in 158.19 s |
| Full suite, fresh process run 2 | 1193/1193 passed in 154.66 s |
| Coverage | 39%, configured 30% gate satisfied |
| Python compileall | PASS |
| Ruff | PASS |
| Configured strict mypy | PASS, 27 source files |
| pip-audit | PASS, no known vulnerabilities |
| Bandit high-severity gate | PASS, 0 high; 1 medium and 138 low reported |

No skip or xfail was added or used to bypass a failure.

## User dictionary acceptance

The focused and repeated tests cover settings-to-runtime delivery,
`advanced.user_guess_string`, hot reload without restart, online multiline
input, comma/quote/backslash/newline/half-width and full-width semicolon
preservation, TixCraft, KKTIX safe JSON encoding, TicketPlus multi-field use,
and all registered text-question consumers. Tests also enforce that the shared
dictionary is not consumed by CAPTCHA, login, Queue-it, challenge,
risk-control, checkout, or payment paths.

## Terminal browser lifecycle

Production-boundary tests exercise `_run_main`, not just a helper. Terminal
browser failures reach the authoritative owner and select bounded reacquire,
transport rebind, safe restart, or fail-closed behavior according to page and
attempt state. A submit-unknown disconnect remains zero-retry fail-closed, and
manual browser closure does not reopen pages.

## Local actual-browser production integration

- Single Edge process: 61.422 seconds, 69 cycles, 0 errors, 0 CDP errors,
  0 duplicate claims, max one tab, asyncio tasks 8→8.
- Three isolated Edge processes: 62.484/63.265/63.281 seconds and 69/70/70
  cycles, all workers exit 0, 0 errors, 0 CDP errors, 0 duplicate claims,
  max one tab per process, asyncio tasks 8→8 per process.
- These tests used the local synthetic ticket SPA and the same authoritative
  production iteration. They did not contact third-party ticket sites.

The first smoke invocation was rejected before browser startup because the
test `run-id` made the bounded instance identity longer than 32 characters.
It was corrected to a valid short ID and both requested smoke modes then
passed; this runner-input failure is not counted as a product PASS.

## Qualification limitation

**BOTH 8H ACTUAL-BROWSER GATES USER WAIVED / NOT VERIFIED.**

The stopped partial qualification ran 2,052.408 seconds (about 34 minutes 12
seconds) and had empty captured stderr, but it is not an eight-hour PASS. The
authoritative record is `FINAL_8H_SOAK_WAIVER.json`.
