# HunterX v0.5.2 Round-1 RC Historical Audit

> This file is retained as Round-1 provenance. The authoritative Round-2 RC2
> audit is `ROUND2_FINAL_CROSS_AUDIT_v0.5.2.md`. Nothing in this historical
> report upgrades RC2 to FINAL.

Release status: **RC — not FINAL**  
Mandatory qualifier: **8H SOAK NOT VERIFIED**

## Final decision

All executed P0/P1 lifecycle, duplicate-submit, login-target, browser recovery, multi-tab/multi-instance, performance, full-suite, static/type/security and package gates passed after their documented fix loops. The required two eight-hour actual-browser soaks were not available, so only RC artifacts may be released.

## Required audit answers

1. **Common/platform root cause.** All registry families lacked an authoritative success/protected→safe-route attempt boundary, so terminal platform state could survive a completed purchase. TicketPlus additionally had a process-global `ticketplus_purchase_done` latch.
2. **Common vs platform-specific.** Common bugs were attempt lifetime, stale target/CDP classification, task/action ownership and safe-route rearm. TicketPlus-specific bugs were the global completion latch, submission watcher scope and login target return.
3. **Attempt-scoped flags.** `PlatformEngine` now owns an immutable `PurchaseAttempt` per tab/platform state. Existing platform dictionaries are reset only when the engine proves a new safe-route generation. Platform selector/click implementations were retained.
4. **No duplicate submit.** One exact submit token can be claimed per attempt. Protected/terminal/unknown states reject another claim; stale tokens cannot release or submit a new generation; ambiguous post-click outcomes fail closed.
5. **Area/safe-route rearm.** The adapter classifier and route lifecycle must positively identify `ACTIVITY`, `DATE` or `AREA`. `PageClass.TICKET` remains protected and never rearms by itself; platform paths named `ticket` rearm only where that adapter classifies them as `AREA`. A confirmed transition to a safe class clears attempt-local state, resets the existing refresh purchase guard and creates generation N+1.
6. **Formal/leak refresh ownership.** Existing `PlatformEngine` → `RefreshCoordinator` → `ReloadGuard`/`LeakWatchScheduler` ownership remains single. No second loop was introduced. Protected routes and interval=0 remain unchanged.
7. **Long-run causes.** Found causes include unclassified empty URL/target loss, swallowed terminal exceptions, unowned tasks/actions, repeated Windows ctypes bindings and Zendriver's unbounded write-only event mapper. Each has direct regression or executed soak evidence.
8. **Browser/CDP classes.** Transient URL miss, target closed, execution context lost, transport closed, timeout, confirmed crash, clean exit and unknown exit are classified separately.
9. **Automatic recovery.** Only confirmed safe contexts use bounded normal retry, target reacquire, transport rebind or circuit-limited restart. Login restore is bounded and target scoped.
10. **Fail closed.** Manual/clean or ambiguous browser closure, protected transaction uncertainty, unknown submit outcome, unsafe route and exhausted recovery stop automation. Browser pages are not reopened after user closure.
11. **Leak proof.** Final actual-browser task counts were 8→8, three-instance counts converged 24→8 during teardown, action count remained 0 in the harness, CDP mapper stayed 0, and the 100,000-cycle state/refresh counts stayed exactly 30/30.
12. **Three tabs/instances.** Direct tests use independent tabs, engines, targets and profile paths. The three-instance browser harness held max one tab each, isolated profiles and had 0 duplicates/errors.
13. **Performance.** Ten balanced rounds show dispatch -2.07%, three-tab -0.98%, URL -1.19%, idle refresh -0.32%, due refresh -0.04%, TicketPlus watcher -1.26%, and one/three-instance paths +1.04%/+0.79% median.
14. **Accepted Gemini advice.** Saved login target, post-login restore, SPA/navigation awareness, safe-route rearm, tab-scoped state, bounded stale-element validation and additive lifecycle defense.
15. **Rejected Gemini advice.** Body-wide mutation observers, random jitter, global click delays, hidden cadence changes, background keepalive claims, a second state manager and all CAPTCHA/queue/challenge/risk/payment bypass ideas.
16. **Accepted upstream ideas.** TicketPlus route/DOM behavior and preserving activity context around login were used as behavioral references. No upstream module replaced the HunterX product base.
17. **Modified core bytes.** Lifecycle/ownership touch points in `attempt_lifecycle`, `platform_engine`, `platform_contract`, `nodriver_tixcraft`, TicketPlus, browser session, runtime health/diagnostics, task registry, navigation context, DOM drift and Zendriver hardening were changed where required.
18. **Retained core.** Date/area/ticket/quantity selection, `allow_less_tickets`, platform-specific click algorithms, OCR/dictionary/notifications, profiles, formal/leak cadence, ReloadGuard, LeakWatchScheduler and protected queue/payment boundaries remain inherited.
19. **Observed failures.** Harness tab reuse, ctypes RSS leak, Zendriver event retention, initial mapper test design, dispatch performance regression, Mypy dynamic-init access and pip-audit CLI misuse were all retained and fixed through single→focused→repeated/adjacent verification. Details are in the test report.
20. **NOT TESTED/UNAVAILABLE.** Two eight-hour real-browser soaks and all live third-party purchase/payment/queue/CAPTCHA/challenge/risk-control scenarios. Actual browser tests used a local synthetic SPA.
21. **Artifact SHA-256.** Authoritative final RC ZIP hashes are in the co-delivered `SHA256SUMS_v0.5.2_RC.txt`. A source archive cannot embed its own final hash without changing that hash.
22. **Windows native smoke.** The co-delivered artifact gate runs both native executables with `--version`, runs the settings HTTP `/run` smoke, checks PE headers, dual runtimes, embedded source/version, CRC and forbidden state paths. The external release copy of this audit records the final result.
23. **Actual-browser duration/result.** Final post-fix runs were 600.062 seconds single instance and 300.031 seconds ×3 instances, plus a 256.709-second 100,000-cycle accelerated soak. All executed final runs had 0 duplicate submit, 0 general error and 0 CDP error. **8H SOAK NOT VERIFIED.**

## Artifact integrity note

Source and Windows ZIPs are built from the committed candidate snapshot. The source verifier checks exact git membership/content and safe paths. The Windows verifier checks CRC, path safety, versioned documentation, PE executables, runtime layout, embedded source parity and forbidden user/runtime state. Hashes are generated only after both verified archives and co-delivered reports are finalized.

