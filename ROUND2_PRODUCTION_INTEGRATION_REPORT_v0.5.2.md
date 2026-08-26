# HunterX v0.5.2 Round 2 Production Integration Report

## Status

This report records the Round 2 production-iteration integration and the local
actual-browser evidence that was executed for it. It is RC evidence only.

> **8H SOAK NOT VERIFIED**

The evidence below must not be described as a FINAL release gate. No eight-hour
actual-browser soak was executed.

## Scope and authoritative runtime path

The integration establishes one authoritative browser/platform iteration in
`src/nodriver_tixcraft.py`:

- `RuntimeIterationContext` owns the mutable state for one running instance.
- `IterationResult` reports a bounded outcome without creating a second runtime
  state machine.
- `run_runtime_iteration(runtime_context)` performs one production iteration.
- Production `_run_main` calls `await run_runtime_iteration(runtime_context)`;
  it does not retain a duplicate URL-read/platform-dispatch branch.
- `scripts/v052_browser_soak.py` imports and calls the same
  `run_runtime_iteration` entry point.

Within that single path, an iteration reads the browser's actual URL, applies
`PlatformEngine.before_dispatch`, observes expected progress, binds the
task-local expected-progress context, applies pause and refresh gates, and then
calls the existing platform main for the selected registered family. Terminal
browser exceptions propagate to the browser lifecycle owner.

The expected-progress recovery semantics are fail-closed:

- A readable, safe-page `STALLED_ACTION` can request only a bounded
  `REACQUIRE`; it cannot request a browser restart. Successful reacquisition
  reconciles the exact expectation fence. Failed/deferred reacquisition stays
  in monitor mode and does not resume normal dispatch silently.
- `PROTECTED_NO_RECOVERY` stays in read-only monitor mode. Queue, checkout and
  payment state tests assert no reload, reacquire or restart.
- `SUBMIT_OUTCOME_UNKNOWN` calls the central
  `mark_submit_outcome_unknown_if_owned` only when attempt ID, attempt
  generation and submit token all match. A stale/mismatched fence cannot mutate
  the current submit owner. A positively proven safe rearm can create a later
  attempt and clear the old diagnostic fence.

No second `PlatformEngine`, refresh coordinator, leak scheduler, reload guard or
submission owner was added to the production or soak path.

## Local synthetic actual-browser design

The actual-browser harness starts headless Microsoft Edge through the existing
browser session manager and serves `synthetic_ticket_spa.html` from a
`ThreadingHTTPServer` bound to `127.0.0.1` on an ephemeral port. The visible
browser URL therefore remains a loopback URL.

For dispatch coverage only, a test-only route mapper supplies an in-memory
registered-platform route to the production iteration. The mapper is rejected
unless the actual browser URL is loopback, and its result must already belong
to the production platform registry. No fake public host was added to the
registry. The soak script contains no manual calls to `before_dispatch`,
`claim_submit`, `mark_attempt_completed` or `mark_submit_outcome_unknown`.

This is local synthetic testing. The harness did not navigate Edge to a
third-party ticketing website and did not intentionally send a request to a
third-party ticketing host. A packet-level network capture was not performed,
so this statement describes the harness routing and observed browser URLs, not
a general proof that Edge made no unrelated background network request.

The harness does not implement or test CAPTCHA bypass, Queue-it/challenge or
risk-control bypass, payment bypass, or checkout bypass. Protected route names
used by unit tests validate fail-closed lifecycle decisions only; they do not
simulate or bypass a real protected transaction.

## Automated test evidence

### Focused production-iteration suite

`tests/test_v052_round2_production_iteration.py` completed with **25 passed**.
The cases cover:

- TicketPlus, TixCraft and KKTIX production dispatch in `onsale` and
  `leak_watch` modes with interval `0`;
- the remaining seven registered platform-family dispatch branches using
  production-dispatch state fixtures;
- actual URL read and task-local expected-progress binding;
- pause and refresh-gate ownership;
- terminal exception propagation;
- stalled-action reacquire success and failure;
- protected monitor followed by a genuinely new safe attempt;
- checkout, payment and queue zero-recovery behavior;
- exact submit-unknown ownership, stale-owner negative control, and positive
  safe rearm;
- loopback-only route mapping; and
- AST assertions that production and soak share the iteration and that the
  soak has no manual lifecycle calls.

### Adjacent regression

The focused suite was run with expected-progress, browser recovery, browser
bootstrap, attempt lifecycle, TicketPlus attempt-scope, timing, P0 negative
control and terminal-exception audit suites. Result: **122 passed**.

The adjacent set was:

- `tests/test_v052_round2_production_iteration.py`
- `tests/test_v052_round2_expected_progress.py`
- `tests/test_v052_round2_expected_progress_wiring.py`
- `tests/test_v052_round2_browser_recovery.py`
- `tests/test_v052_round2_browser_bootstrap.py`
- `tests/test_v052_browser_recovery.py`
- `tests/test_v052_attempt_lifecycle.py`
- `tests/test_attempt_lifecycle.py`
- `tests/test_v052_ticketplus_attempt_scope.py`
- `tests/test_platform_timing_gate.py`
- `tests/test_v052_round2_p0_negative_controls.py`
- `tests/test_v052_terminal_exception_audit.py`

### Repeated critical regression

Five critical tests were run in **15 fresh pytest processes**, for **75/75
passed test invocations**. This repeated set covered terminal propagation,
stalled-action reacquire success, stalled-action reacquire failure,
protected-to-safe new-attempt recovery, and exact submit-unknown/positive-safe
rearm. This count does not claim that all 25 focused cases were repeated 15
times.

### Static and type gates for the integration files

- Python compilation passed for the production iteration, soak script and
  production-iteration test module.
- Ruff passed for those changed files.
- Strict mypy passed for `scripts/v052_browser_soak.py` after the synthetic
  route state was made explicitly typed.
- The static ownership test passed: soak imports/calls
  `run_runtime_iteration`, while manual lifecycle APIs are absent.

## Actual Edge loopback evidence

All three evidence groups below report `status: PASS`. Values are copied from
the named JSON files; RSS values are bytes.

### Evidence A: one named instance, cross-platform rotation

File: `.temp/round2_iteration_soak_70s.json`

| Metric | Recorded value |
|---|---:|
| Requested duration | 70.0 s |
| Recorded duration | 70.73499999986961 s |
| Cycles | 71 |
| State transitions | 15 |
| Success/continue cycles | 3 |
| Login-restore cycles | 4 |
| Fallback resolutions | 2 |
| Duplicate submit claims | 0 |
| Errors / CDP errors | 0 / 0 |
| Recovery count | 0 |
| Maximum tab count | 1 |
| HunterX task count start/end/max | 0 / 0 / 0 |
| asyncio task count start/end/max | 8 / 8 / 8 |
| Browser-action maximum | 0 |
| CDP-mapper count start/end/max | 0 / 0 / 0 |
| HunterX RSS start/end/max | 102932480 / 107933696 / 108027904 |
| Browser RSS start/end/max | 462102528 / 571699200 / 571699200 |
| Recorded `stalled_seconds_max` | 10.875 s |
| Target replacements / reload injections | 0 / 0 |

With the harness's 30-cycle rotation, cycles 1-29 selected TicketPlus, cycles
30-59 selected TixCraft, and cycles 60-71 selected KKTIX. This demonstrates the
three core production branches against the local synthetic tab; it is not a
real-site flow validation. The recorded maximum stalled interval includes
browser/bootstrap time before the first completed loop and must not be
interpreted as a measured platform action stall.

### Evidence B: three named instances

File: `.temp/round2_iteration_soak_3x15s.json`

| Instance | Mode | Recorded duration (s) | Cycles | Transitions | Continue | Login restore | Fallback | Duplicate | Errors/CDP | Max tabs | asyncio start/end/max | HunterX RSS max | Browser RSS max |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| 1 | onsale | 15.062999999849126 | 25 | 4 | 1 | 1 | 1 | 0 | 0/0 | 1 | 16/23/23 | 112267264 | 844075008 |
| 2 | leak_watch | 15.077999999979511 | 50 | 8 | 2 | 2 | 2 | 0 | 0/0 | 1 | 22/15/22 | 112275456 | 848592896 |
| 3 | leak_watch | 15.093999999808148 | 51 | 11 | 2 | 3 | 2 | 0 | 0/0 | 1 | 21/8/22 | 112275456 | 852500480 |

Each result also recorded zero recovery, zero browser-action maximum, zero
CDP-mapper start/end/max, zero target replacements, zero reload injections,
and HunterX task count `0/0/0`.

These are **three named HunterX instances**, each with its own
`BrowserSessionManager`, Edge driver and one automation-owned tab. They are not
three automated tabs inside one browser. The instances share one Python
process, event loop and loopback HTTP server; consequently, the per-instance
`asyncio_task_count` samples observe process-wide concurrent activity and must
not be treated as isolated per-driver leak measurements. Same-browser
multi-tab automation was not exercised by this evidence. Each instance's
recorded `max_tab_count` was 1.

### Evidence C: post-typing-fix smoke

File: `.temp/round2_iteration_soak_post_typefix_12s.json`

| Metric | Recorded value |
|---|---:|
| Requested duration | 12.0 s |
| Recorded duration | 13.656999999890104 s |
| Cycles | 25 |
| State transitions | 4 |
| Success/continue cycles | 1 |
| Login-restore cycles | 1 |
| Fallback resolutions | 1 |
| Duplicate submit claims | 0 |
| Errors / CDP errors | 0 / 0 |
| Recovery count | 0 |
| Maximum tab count | 1 |
| HunterX task count start/end/max | 0 / 0 / 0 |
| asyncio task count start/end/max | 8 / 8 / 8 |
| HunterX RSS start/end/max | 102682624 / 107388928 / 107388928 |
| Browser RSS start/end/max | 468590592 / 506482688 / 506482688 |
| Recorded `stalled_seconds_max` | 10.827999999979511 s |
| Target replacements / reload injections | 0 / 0 |

This smoke was executed after replacing the untyped synthetic route dictionary
with `SyntheticRouteState`; it confirms that the typed harness still launches
Edge and calls the production iteration. Its short duration does not cover all
platform rotations.

## Evidence limitations and release decision

### Final post-dictionary Edge confirmation

After the cross-platform user-dictionary implementation and parser fast path,
the production integration was run again rather than relying on the earlier
70-second evidence:

- `work/v052/round2/evidence/browser-soak/post-dictionary-single-180s.json`:
  PASS, 183.187 seconds, 153 cycles, six success/continue cycles, nine login
  restores, six fallback resolutions, one guarded reload injection, zero
  duplicate submit, zero errors, zero CDP errors, max one tab, owned tasks 0→0,
  asyncio tasks 8→8 and CDP mapper 0→0.
- `work/v052/round2/evidence/browser-soak/post-dictionary-3x60s.json`: PASS,
  three named instances ran 61.828/62.672/62.688 seconds for 69/70/70 cycles.
  Every instance recorded zero duplicate submit, zero errors, zero CDP errors,
  max one tab, bounded tasks and CDP mapper 0→0.

These newer runs supersede the shorter evidence for final RC2 source-gate
reporting, but retain the same local synthetic limitations.

- **8H SOAK NOT VERIFIED.** The longest recorded run in this report requested
  180 seconds.
- All browser evidence is local synthetic loopback evidence, not real ticketing
  site evidence.
- Real authentication, real inventory, real DOM drift, real queue/challenge,
  CAPTCHA and real checkout/payment behavior were not exercised.
- No CAPTCHA, Queue-it/challenge, risk-control, checkout or payment bypass was
  implemented or validated.
- The evidence runs did not reach the harness thresholds for target replacement
  or reload injection; both metrics are zero. Those paths are covered by unit
  and adjacent recovery tests, not by these three actual-browser JSON files.
- The three-instance run verifies separate named instances, not multiple
  automated tabs in one browser.
- Short-run RSS samples are observational and are not proof of long-run memory
  stability.
- No packet-level network capture was recorded.

Accordingly, these results support continued **v0.5.2 RC** evaluation only.
They do not satisfy the FINAL gate.
