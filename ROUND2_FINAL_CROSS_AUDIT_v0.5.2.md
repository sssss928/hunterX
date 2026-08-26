# HunterX v0.5.2 RC2 — Round 2 Final Cross Audit

## Decision

The source candidate has passed the executed Round-2 P0/P1, cross-platform
dictionary, double full-suite, static/type/security, balanced performance and
local actual-browser gates. It is eligible only for an exact-commit RC2 build.

> **8H SOAK NOT VERIFIED — this is RC2, not FINAL.**

Artifact acceptance remains fail-closed: the release tooling will promote an
archive only after exact clean-commit source verification, the approved
Round-1 Windows base hash, source-to-both-runtime byte parity, fresh-extract
native packaged smoke and exact checksum-set verification pass. The generated
`RC2_BUILD_PROVENANCE.json` and co-delivered SHA-256 manifest are authoritative
for the actual artifact build.

## Cross-audit answers

1. **Development base:** v0.5.2 Round-1 RC, source SHA
   `ca4c28ca0df8c3054507233e3f6e20777bc0a0ead1b9bb0cec308cba2b4f2b04`,
   frozen commit `29380aa682907b916a60a1d0d1960fbb181b2a60`. v0.5.1 is comparison evidence,
   not the Round-2 development base.
2. **Completion/rearm:** one tab/platform `PurchaseAttempt`; only positive
   ACTIVITY/DATE/AREA evidence may create N+1. TICKET, ORDER, CHECKOUT, PAYMENT,
   QUEUE and UNKNOWN remain protected.
3. **Duplicate submit:** exact attempt ID, generation and submit token fence all
   central/local mutation. Unknown outcomes fail closed. Stale callbacks cannot
   release, retry or contaminate a later attempt.
4. **Production path:** application and soak both execute the same
   `run_runtime_iteration`; no second lifecycle/refresh engine exists.
5. **Browser closure/recovery:** manual/clean/ambiguous closure does not reopen
   pages. Safe recovery requires exact target identity plus live target
   transport proof; restart uses full initial bootstrap.
6. **Long-run resources:** owned task/action counts and Zendriver mapper stay
   bounded in executed stress and final Edge loopback runs.
7. **Terminal failures:** 466/466 browser-interaction broad handlers perform
   terminal-first classification; zero manual-review dispositions remain.
8. **Platform coverage:** the route/sticky-state matrix uses real adapters and
   initializers for all ten registered families, including IndieVox.
9. **Custom dictionary:** one lossless parser/serializer is used by every
   text-question-capable family: TixCraft/TeamEar/IndieVox/Ticketmaster, KKTIX,
   FamiTicket, iBon, KHAM/ticket.com.tw/UDN, TicketPlus and
   HKTicketing/Galaxy/Ticketek. Online multi-line merge, settings migration,
   save, display and runtime hot reload use the same contract.
10. **No false platform wiring:** Cityline, FunOne and FANSI GO currently expose
    no text-question dictionary field. No dictionary content was injected into
    unrelated CAPTCHA, authentication, queue, challenge, risk-control or
    payment fields.
11. **Core preservation:** existing date/area/ticket/quantity selection,
    `allow_less_tickets`, platform click algorithms, formal/leak cadence,
    notifications, OCR/manual CAPTCHA boundaries, queue and payment handoff are
    retained except where exact lifecycle/ownership correction was required.
12. **Performance:** normal hot-path median deltas versus Round-1 are at most
    +2.68% and p95 at most +2.69%. Lossless dictionary parsing adds roughly
    2 microseconds only on a text-question page. Recovery proof overhead is
    recovery-only.
13. **Final tests:** custom dictionary 26 focused and 520/520 fresh-process
    twice; adjacent 441 twice; final fresh-process full suite 1150 passed twice.
14. **Static/type/security:** compileall, Ruff, strict mypy (28 files), Node
    syntax, production requirements pip-audit, Bandit high-severity and global
    exception audit all passed.
15. **Actual browser:** final local Edge evidence passed one 183.187-second run
    and three concurrent named-instance runs of 61.828/62.672/62.688 seconds,
    with zero duplicate submit, errors and CDP errors; max one automated tab per
    instance.
16. **Not tested:** live third-party inventory/purchase/payment, CAPTCHA,
    Queue-it, challenge and risk-control flows; packet capture; same-browser
    concurrent multi-tab automation; eight-hour actual-browser soaks.
17. **Release status:** RC2/prerelease only. The workflow rejects unqualified or
    FINAL filenames and rejects a dirty/uncommitted release snapshot.

## Evidence index

- `ROUND2_TEST_REPORT_v0.5.2.md`
- `ROUND2_OBSERVED_FAILURES_FIX_LOOPS.md`
- `ROUND2_PRODUCTION_INTEGRATION_REPORT_v0.5.2.md`
- `ROUND2_LONG_RUN_STABILITY_REPORT_v0.5.2.md`
- `ROUND2_PERFORMANCE_COMPARISON.md`
- `ROUTE_REARM_MATRIX_v0.5.2.md`
- `REQUIREMENT_TEST_TRACEABILITY_v0.5.2.md`
- `IMPLEMENTATION_DIFF_v0.5.2_RC2.md`
