# HunterX v0.5.2 RC2 — Requirement/Test Traceability

Release status: **RC2**

Development base: **v0.5.2 Round-1 RC**

Long-run qualifier: **8H SOAK NOT VERIFIED**

The Round-1 RC is retained as provenance. Round 2 changes only lifecycle,
ownership, recovery, diagnostics, integration, and release verification needed
by direct reproducers; existing platform selectors and purchase click strategy
remain the product core.

| ID | Requirement | Production implementation | Direct / negative-control tests | Repeated, adjacent, or integration evidence | Status |
|---|---|---|---|---|---|
| P0-R2-01 | Detect a readable URL that fails to make an explicitly expected transition without false positives in idle/presale/leak/interval-zero states | `src/runtime_health.py`, `src/reload_guard.py`, platform wiring | `test_v052_round2_expected_progress.py`, `test_v052_round2_expected_progress_wiring.py`, Round-2 P0 negative controls | Focused observer/wiring, lifecycle and production-iteration adjacent suites; fresh-process critical repeats | PASS |
| P0-R2-02 | Execute the real outer production iteration in both the application and local browser harness | `src/nodriver_tixcraft.py::run_runtime_iteration`, `scripts/v052_browser_soak.py` | `test_v052_round2_production_iteration.py` plus AST ownership checks | 25 focused; 122 adjacent; 75/75 fresh-process invocations; 70 s single and three named local Edge instances | PASS in local synthetic scope |
| P0-R2-03 | Resolve three-tab contract without falsely claiming same-browser concurrent automation | `src/browser_session.py`, `src/runtime_diagnostics.py` | bootstrap/recovery/multitab diagnostic tests | One automated tab per named instance; passive bounded extra-tab diagnostics; local three-instance run | PASS; same-browser concurrent automation intentionally unsupported |
| P0-R2-04 | Never reacquire an unrelated same-platform event/tab | `src/browser_session.py` | `test_v052_round2_browser_recovery.py`, P0 negative control | Wrong event, empty target, duplicate exact target, canonical noise, saved owned target ID; repeated and adjacent recovery runs | PASS |
| P0-R2-05 | Safe restart replays the same complete prerequisites as initial bootstrap | `src/nodriver_tixcraft.py::bootstrap_owned_browser`, `src/browser_session.py` | `test_v052_round2_browser_bootstrap.py` | Initial/restart call order, failure-closed incomplete bootstrap, adjacent login/resource/recovery suites | PASS |
| P1-R2-06 | Prove selected target transport before reporting reacquire/rebind success | `src/browser_session.py` | browser recovery direct tests and dead-cached-transport negative control | Bounded read-only target-info proof on REACQUIRE and TRANSPORT_REBIND; repeated recovery suite | PASS |
| P1-R2-07 | Escalate terminal browser exceptions from browser-interaction broad handlers | `src/runtime_health.py`, audited platform/common/runtime handlers | global and per-platform Round-2 exception audit tests | 466/466 audited handlers classified, zero manual-review dispositions; dynamic terminal rethrow tests | PASS |
| P1-R2-08 | Prove real platform sticky-state reset and safe rearm for every registered family | `src/platform_engine.py`, real platform initializers | `test_v052_round2_route_state_matrix.py` | 106-case real 10-platform matrix; 1,060 fresh-process case executions; 159 adjacent route/lifecycle tests | PASS |
| P1-R2-09 | Resolve TICKET safe/protected mismatch | adapters, `src/platform_engine.py`, `docs/ROUTE_REARM_MATRIX_v0.5.2.md` | route-state matrix positive and negative controls | `PageClass.TICKET` is always protected; only adapter-classified AREA routes may rearm | PASS |
| P1-R2-10 | Eliminate unsafe process fallback for explicit TixCraft tabs | `src/platforms/tixcraft.py` | `test_v052_tixcraft_per_tab_state.py` | 214 focused TixCraft tests at completion of that batch; 92 adjacent; fresh-process repeats | PASS |
| P1-R2-11 | Prevent conflict between central `PurchaseAttempt` and TixCraft inner attempt | `src/platform_engine.py`, `src/platforms/tixcraft.py` | `test_v052_round2_tixcraft_central_bridge.py`, `test_v052_round2_tixcraft_rejection_release.py` | Route-only protection, positive AREA rearm, exact rejection release, stale token and delayed callback races; 239 TixCraft tests and 229 adjacent tests | PASS |
| R2-DICT | Make user dictionary parsing, storage, hot reload and answer delivery lossless and consistent across every text-question-capable registered platform family | `src/util.py`, `src/settings.py`, settings frontend, TixCraft/KKTIX/FamiTicket/iBon/KHAM/TicketPlus/HKTicketing handlers and registry hosts | `test_v052_round2_user_dictionary.py` actual production paths, parser/serializer/online-file/migration/hot-reload/registry/frontend controls | Pre-fix 18 failed/3 passed; post-fix 26 focused; 520/520 fresh-process twice; 441 adjacent twice; two 1150-test full suites | PASS |
| P1-R2-12 | Run 8 h single-instance and 8 h three-named-instance production-integration actual-browser soaks | production integration harness | environment-limited local Edge runs only | Final post-dictionary: 183.187 s single and 61.828/62.672/62.688 s across three named instances; zero duplicate/error/CDP | **NOT VERIFIED — RC2 ONLY** |
| R2-PERF | Preserve hot-path performance and keep heavy probes out of the normal 50 ms path | passive observer, dictionary canonical fast path and recovery-only CDP proof | `tests/benchmarks/v052_performance.py` | Five fresh processes per side, 21 samples, balanced A/B+B/A against Round-1 RC and v0.5.1; normal median ≤+2.68%, p95 ≤+2.69% versus Round-1 | PASS |
| R2-RELEASE | Build both artifacts from one clean exact commit and Round-1 RC Windows base; verify embedded parity and packaged smoke | release/build/verify scripts | release builder, archive, parity, workflow and packaged-smoke tests | Clean snapshot, RC2-only qualifier/base, joint source/runtime byte parity, fresh-extract smoke | FINAL GATE PENDING |
| R2-ANTI-FAKE | Preserve failing baseline evidence and do not skip, xfail, weaken assertions, or inflate unrelated tests | tests and external evidence | Round-1 RC direct negative controls, `ROUND2_FAILURE_FIX_LOG_v0.5.2.md` | Every observed failure stopped the line and was rerun through focused/repeated/adjacent gates | PASS to current checkpoint |

## Legacy Round-1 requirements retained

The following Round-1 contracts remain covered by their original suites and the
Round-2 full regression: attempt-scoped completion, duplicate-submit prevention,
TicketPlus login target return, deterministic refresh ownership, SPA/DOM drift,
bounded task/action/CDP resources, tab/instance isolation, source/Windows
packaging, and packaged executable version/settings smoke.

## Evidence boundaries

- Immutable negative controls and machine-generated browser/JUnit evidence are
  retained outside the release ZIP under `work/v052/round2/evidence`.
- Local browser integration uses loopback pages and does not navigate third-party
  ticketing, CAPTCHA, Queue-it, challenge, risk-control, checkout, or payment.
- No CAPTCHA, queue, challenge, risk-control, checkout, payment, or fraud-control
  bypass was implemented or tested.
- Final-gate rows are changed to PASS only after the exact clean commit and both
  RC2 artifacts complete their required verification.
