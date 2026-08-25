# HunterX v0.5.2 Round-1 RC Historical Test Report

> Retained for provenance. The authoritative Round-2 report is
> `ROUND2_TEST_REPORT_v0.5.2.md`.

Release status: **RC**
Long-run qualifier: **8H SOAK NOT VERIFIED**

## Scope and immutable baseline

- Product base: `hunterX_source_0.5.1.zip`, SHA-256 `BEF25688229B58929623A5B0326F7B7F0E8755FEB9F4294ECC6FD6EFA50FE113`.
- The extracted baseline was made read-only before candidate work. The baseline suite passed 788 tests.
- The two tickets_hunter archives were read-only references, not product bases.
- No live ticket purchase, payment, CAPTCHA, queue, challenge or risk-control bypass was exercised.

## Phase results

| Phase | Direct/negative evidence | Repeated/stress evidence | Result |
|---|---|---|---|
| 0 — baseline freeze | 788 baseline tests passed; input hashes recorded; 256 baseline files made read-only | Five baseline benchmark rounds | PASS |
| 1 — universal attempt lifecycle | BASE-v0.5.1 negative control failed 14 lifecycle cases and two TicketPlus cases | 20 repeated focused runs; 1,000-generation test; all 10 registry families including IndieVox and Ticketmaster | PASS |
| 2 — login target recovery | BASE lacks canonical target lifecycle; candidate TicketPlus login/expiry/restore tests | Tab-scoped expiry, successful restore, retry exhaustion and adjacent login regressions | PASS in local synthetic scope |
| 3 — browser/CDP recovery | BASE lacks failure taxonomy/rebind/restart decisions | 20 repeated fault-injection runs; target replacement fixture | PASS |
| 4 — multi-tab/multi-instance | BASE lacks attempt identity isolation | 20 repeated deterministic runs; three-instance browser runs | PASS |
| 5 — SPA/DOM drift/resources | BASE lacks route generation and owned task registry | 20 repeated runs; 100,000-cycle stress; 100,000-event mapper unit stress | PASS |
| 6 — performance | Ten balanced A/B and B/A rounds, 31 samples × 5,000 iterations per scenario | Twelve scenarios; failed v3 result triggered optimization and full v4/v5 rerun | PASS; see performance report |
| 7 — full regression | Final focused suite: 62 passed | Fresh process runs: 842 passed in 112.22 s; 842 passed in 110.70 s; exact-source run 842 passed in 111.62 s | PASS |
| 8 — local actual-browser | Real Edge/Zendriver against local synthetic SPA only | Final 10-minute single instance; final 5-minute three instance; 100,000 accelerated cycles | PASS for executed duration; 8H NOT VERIFIED |
| 9 — Windows package | Executed after the committed source snapshot | Native executable smoke and ZIP verification are recorded in the co-delivered audit | See final audit/artifact verifier |

No candidate test was skipped or xfailed. No failed assertion was relaxed, no fixture was changed to avoid a production branch, and no unrelated tests were added to inflate the pass count.

## P0/P1 direct results

- Success → protected route → adapter-confirmed `ACTIVITY`, `DATE` or `AREA` return creates a new attempt for every registry family. `PageClass.TICKET`, queue and unknown routes remain protected; ticket-named paths qualify only when classified as `AREA` by that adapter.
- The old attempt rejects a second submit claim; stale submit tokens cannot control a new generation.
- `SUBMIT_OUTCOME_UNKNOWN` is fail-closed and never auto-resubmits.
- TicketPlus process-global completion latch is absent; submit and login target ownership are tab/attempt scoped.
- Clean/manual or ambiguous browser closure never reopens a browser; confirmed safe transient/crash states use bounded recovery.
- Formal and leak-watch modes retain one refresh owner, protected-page behavior and interval=0 semantics.
- Three tabs and three instances keep attempts, targets, profiles and state independent.
- Production-created async tasks use the bounded task registry. Browser action ownership, CDP mapper size, attempt state and refresh-owner counts stayed bounded in executed stress.

## Final static/type/security gates

| Gate | Result |
|---|---|
| `compileall src tests scripts` | PASS |
| AST parse | PASS — 127 Python files |
| JSON/TOML/YAML parse | PASS — 2 JSON, 1 TOML, 7 YAML |
| Ruff | PASS |
| configured Mypy | PASS — 25 source files |
| Node `--check` | PASS — 4 JavaScript files |
| `git diff --check` | PASS; CRLF conversion notices only |
| pip-audit production requirements | PASS — no known vulnerabilities found |
| Bandit high severity | PASS |

## Observed failures and Stop-the-Line loops

1. The first 15-minute browser run failed with `ConnectionClosedError` because the harness reused and then closed its active tab. The failure JSON was retained; `new_tab=True` fixed the harness and a 40-second target-replacement regression passed.
2. A 15-minute resource run exposed near-linear HunterX RSS growth. The first root cause was repeated ctypes structure/API binding in every Windows sample. Bindings were cached once; a 20,000/2,000-call microstress then grew only 696,320 bytes.
3. A follow-up 15-minute run still showed residual linear RSS growth. Source inspection found Zendriver retaining every write-only `EventTransaction` in `Connection.mapper`. BASE-v0.5.1 retained all 2,000 negative-control events. The v0.5.2 compatibility guard discards events but retains request transactions; 20 repeated runs and actual-browser mapper counts remained zero.
4. Initial mapper tests had three failures: two synchronous tests created `asyncio.Future` without a loop, and Zendriver's metaclass rejected normal class assignment. Tests were corrected to use purpose-built fake event objects, while production uses `type.__setattr__` only for the one guarded initializer. Single, focused and adjacent suites then passed.
5. Initial final performance A/B showed dispatch +5.86% and three-tab +6.17%. Immutable decision tuples, constant page sets and a stable-route fast path removed redundant work. Ten balanced AB/BA rounds then passed.
6. Mypy rejected dynamic `connection_class.__init__` access. The initializer is now retrieved from the class namespace and verified callable; single-file Mypy, focused tests, Ruff and full Mypy passed.
7. The first pip-audit command used an incompatible `--disable-pip` option. This was recorded as a runner failure, not a vulnerability result. The correct requirements audit passed with no known vulnerabilities.

## Not tested / unavailable

- **8H SOAK NOT VERIFIED**: two eight-hour actual-browser runs were unavailable in this execution window; artifacts are RC only.
- Live third-party onsale inventory, real submit, payment, Queue-it, CAPTCHA, challenge and risk-control flows were not executed.
- The real-browser harness used local synthetic routes and DOM. It validates Edge/Zendriver/CDP lifecycle, not third-party production markup availability.
