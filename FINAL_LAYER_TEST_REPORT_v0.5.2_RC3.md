# HunterX v0.5.2 RC3 Final-Layer Test Report

## Release status

**v0.5.2 RC3 — 8H SOAK NOT VERIFIED**

The two required 8-hour actual-browser gates were not executed. This report does not call the build FINAL.

## Verified results

- RC2 production-boundary negative control: failed as expected with uncaught `ConnectionClosedError` escaping `_run_main()`.
- RC3 terminal-boundary focused: 9 passed.
- critical terminal-boundary fresh processes: 20/20 passed.
- browser/attempt adjacent regression: 146 passed.
- cross-platform lifecycle regression: 349 passed.
- existing dictionary acceptance on immutable RC2: 30 passed.
- existing plus Final-Layer dictionary acceptance on RC3: 33 passed.
- TicketPlus focused regression: 39 passed.
- release-pipeline focused after RC3 extension: 119 passed.
- pre-release-engineering complete fresh suites: 1162/1162 twice.
- post-release-engineering complete fresh process suite 1: 1175 passed in 142.18 s.
- post-release-engineering complete fresh process suite 2: 1175 passed in 142.39 s.
- terminal exception audit: 466/466 handlers classified; 0 manual-review findings.
- Ruff: passed for `src`, `tests`, and `scripts`.
- compileall: passed.
- configured strict mypy gate: passed for 28 configured source files.
- pip-audit against `requirement.txt`: no known vulnerabilities.
- Bandit CI high-severity gate: 0 findings.

The canonical build additionally performs exact-commit source verification, Windows archive verification, fresh-extract native packaged smoke, joint artifact parity, and strict SHA-256 verification. Exact artifact hashes are intentionally stored in the external `SHA256SUMS_v0.5.2_RC3.txt`, avoiding a self-referential archive hash.

## Final audit questions

1. **Cause:** terminal browser failures were correctly re-raised by helpers, but RC2 `_run_main()` had no authoritative lifecycle catch, so the exception reached the process boundary.
2. **Failure type:** the real evidence proves a closed target/transport session; whole-browser death was not proven. RC3 queries the browser exit state before choosing recovery.
3. **Why `asyncio.run()` saw it:** no handler existed between `run_runtime_iteration()` and `_run_main()`/`runtime.main()`.
4. **Authoritative owner:** `_run_main()` owns escalation; `RuntimeHealthSupervisor` selects policy and `BrowserSessionManager` performs bounded recovery.
5. **Why no EXE crash:** recognized terminal failures are converted at that owner into recover/stop/fail-closed results; non-terminal programming errors still propagate.
6. **ACTIVITY/DATE/AREA:** bounded reacquire/rebind is attempted; safe restart is allowed only under the strict transport/browser/safe-route predicate.
7. **TICKET:** no restart or replay; protected state is monitored/fail-closed according to the existing supervisor.
8. **Unknown submit:** `SUBMIT_OUTCOME_UNKNOWN` remains fail-closed with zero automatic resubmit.
9. **Queue/checkout/payment:** protected, zero replay, zero blind recovery mutation.
10. **Manual browser close:** it does not reopen automatically.
11. **Wrong-event reacquire:** still fail-closed; sole same-platform fallback remains removed and target proof remains mandatory.
12. **Restart bootstrap:** yes; it uses the existing full bootstrap factory and platform prerequisites.
13. **TixCraft login return:** preserved and covered by transition/recovery tests.
14. **TicketPlus:** focused suite passed; lifecycle and stale watcher fences remain intact.
15. **Stale callback:** exact attempt/generation/token fences prevent mutation of a newer attempt.
16. **Duplicate submit:** no tested lifecycle path can duplicate submit; old/unknown ownership is never replayed.
17. **Registered route/rearm:** cross-platform route/lifecycle suites passed.
18. **Named instances:** one owned active tab per named instance remains unchanged; diagnostics do not adopt unrelated tabs.
19. **Dictionary settings:** `advanced.user_guess_string` is normalized, saved, hot-reloaded, and read by the runtime parser.
20. **Production consumers:** TixCraft text question, KKTIX custom question, FamiTicket verification question, iBon card/question input, KHAM/Ticket.com/UDN question resolution, TicketPlus order custom fields, and HKTicketing-family entitlement/date password handlers.
21. **Hot reload:** passed without process restart.
22. **Special characters:** commas, quotes, backslashes, newlines, ASCII semicolons, and full-width semicolons were preserved by the shared parser contract.
23. **Online multiline:** the full response body is parsed; it is not truncated to the first line.
24. **KKTIX encoding:** safe JSON encoding remains in place and passed its existing tests.
25. **Performance:** no reproducible regression; see the performance report.
26. **Full suites:** yes, two independent post-release-engineering fresh processes passed 1175/1175 each; the exact-source gate is repeated from the committed archive.
27. **Packaged Windows smoke:** the canonical builder requires and performs fresh-extract `settings.exe` and `nodriver_tixcraft.exe` smoke before promotion; the final external build log is authoritative.
28. **Artifact byte parity:** the pair verifier requires both embedded `app_src` trees plus assets/www to match the exact source archive byte-for-byte; the final external pair-verification result is authoritative.
29. **8-hour gates:** no. Neither required 8-hour gate was executed.
30. **Decision:** RC3, because real RC2 production code required correction and the mandatory 8-hour gates remain unverified.
