# HunterX v0.5.2 RC2 — Round 2 Test Report

Release status: **RC2 — not FINAL**

> **8H SOAK NOT VERIFIED**

## Baseline and anti-fake controls

- Development started from the immutable v0.5.2 Round-1 RC, not v0.5.1.
- Round-1 source SHA-256:
  `ca4c28ca0df8c3054507233e3f6e20777bc0a0ead1b9bb0cec308cba2b4f2b04`.
- The Round-1 baseline suite passed 842 tests.
- Round-2 P0/P1 negative controls were executed against the immutable base.
- No failing candidate test was skipped, xfailed, deleted or converted to pass
  by weakening an assertion or avoiding a production branch.

## Final candidate results

| Gate | Result |
|---|---|
| Cross-platform user-dictionary focused suite | 26 passed |
| User-dictionary fresh-process repetition | 20 × 26 = 520/520, repeated again after fast-path optimization = 520/520 |
| User-dictionary adjacent platform/config/lifecycle suite | 441 passed, rerun after optimization = 441 passed |
| Production iteration focused | 25 passed |
| Production iteration adjacent | 122 passed |
| Route/state matrix | 106 passed; 10 fresh processes = 1,060/1,060 |
| Terminal browser handler audit | 466/466 classified; 0 manual-review handlers |
| TixCraft rejection/bridge/recovery focused | 56 passed; stale callback fresh-process race 20/20 |
| Final fresh-process full suite run 1 | 1150 passed in 132.69 s |
| Final fresh-process full suite run 2 | 1150 passed in 146.03 s |
| Python compileall | PASS |
| Ruff | PASS |
| Strict configured mypy | PASS — 28 source files |
| Node syntax (`src/www/settings.js`) | PASS |
| pip-audit (`requirement.txt`) | PASS — no known vulnerabilities found |
| Bandit high-severity gate | PASS — 0 high-severity findings |
| `git diff --check` | PASS; line-ending conversion notices only |

## User dictionary root cause and coverage

The failure was not one selector bug. The shared parser accepted only a narrow
JSON-fragment representation, the settings frontend discarded quoting and
escaped content, the online dictionary reader consumed only one line, and
several platform handlers either split raw storage text or referenced the wrong
configuration key. KKTIX also interpolated an unescaped answer into JavaScript.
Two known hosts, TeamEar and Ticketek Australia, were absent from registry
dispatch coverage.

The candidate now has one lossless parser/serializer contract supporting the
legacy JSON fragment, a JSON array, newline, ASCII/full-width semicolon, Python
lists, embedded comma, quote and backslash content. It trims empty items,
deduplicates stably, reads the complete UTF-8/UTF-8-BOM online dictionary and
merges local answers first. Migration, saving and runtime hot reload normalize
through the same boundary.

Actual production-path tests cover:

- TixCraft family: TixCraft, TeamEar, IndieVox and Ticketmaster;
- KKTIX;
- FamiTicket;
- iBon;
- KHAM, ticket.com.tw and UDN;
- TicketPlus;
- HKTicketing, Galaxy Macau and Ticketek Australia.

Cityline, FunOne and FANSI GO do not currently expose a text-question dictionary
field in their production handlers. The dictionary was deliberately not wired
into CAPTCHA, login, queue, risk-control or payment behavior. This prevents a
false “all platforms” claim while keeping every text-question-capable registered
family on the shared parser.

## Browser and long-run evidence

The final post-dictionary local Edge runs both passed:

- one named instance, requested 180 s: 183.187 s, 153 cycles, 0 duplicate
  submit, 0 error, 0 CDP error, max 1 tab, asyncio tasks 8→8;
- three named instances, requested 60 s each: 69/70/70 cycles, 0 duplicate
  submit, 0 error, 0 CDP error, max 1 tab per instance, bounded task/mapper
  counts.

These runs use the real Edge/Zendriver/browser-session path and the same
`run_runtime_iteration` as production, but navigate only a local loopback
synthetic application. They are not evidence of live third-party purchase,
payment, CAPTCHA, Queue-it, challenge or risk-control behavior.

## Security-runner clarification

An exploratory audit of the entire temporary Python runner reported advisories
for the runner's own old `pip`, `setuptools` and an unrelated installed
`cryptography`. That command is not the repository release gate. The workflow's
authoritative product audit is `pip-audit -r requirement.txt`; it resolved the
locked production dependencies, including `cryptography==50.0.0`, and reported
`No known vulnerabilities found`. The exploratory red result is recorded, not
used as a product pass or silently omitted.

## Release boundary

No live ticket purchase was executed and no bypass was implemented. Because an
eight-hour actual-browser soak was unavailable, only `_rc2` artifacts may be
produced; FINAL naming is prohibited.
