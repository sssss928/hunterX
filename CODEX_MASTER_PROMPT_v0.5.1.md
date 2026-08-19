# CODEX MASTER MODIFICATION INSTRUCTION — HunterX v0.5.1

> Target repository: `sssss928/hunterX`  
> Base release: HunterX `v0.5.0` (release-matching source, not an older/corrected candidate archive)  
> Upstream reference: `bouob/tickets_hunter` `v2026.08.17`  
> Target release: HunterX `v0.5.1`

## 0. Mission and non-negotiable outcome

Upgrade HunterX v0.5.0 to v0.5.1 by **retaining HunterX's existing stronger
architecture and user-visible behavior**, selectively absorbing only upstream
behaviors that are demonstrably useful and compatible.

Do **not** wholesale replace HunterX platform modules with upstream files. Do
not regress existing refresh timing, onsale mode, leak-watch mode, per-tab state,
submit ownership, route protection, custom user-dictionary field filling,
multi-instance isolation, or guarded navigation/reload behavior.

The finished v0.5.1 must be a strict improvement: if a proposed upstream change
would weaken a HunterX invariant, keep the HunterX implementation and port only
the behavioral requirement or regression test.

No test may be declared passed unless it was actually executed and passed. No
failing test may be deleted, weakened, skipped, xfailed, replaced with an
irrelevant smoke test, or made vacuous merely to obtain green output. When a
failure is discovered, identify the actual cause, fix the product or the invalid
test fixture as appropriate, then rerun the affected suite and the final gates.

## 1. Safety / scope boundary

Preserve the existing manual boundaries around CAPTCHA, waiting-room/challenge,
queue, and payment states.

Do not add or improve:

- CAPTCHA bypass or automated challenge circumvention;
- Queue-it / waiting-room bypass or queue manipulation;
- anti-bot/risk-control evasion, fingerprint hiding, proxy/account pools;
- rate-limit evasion or intentionally abusive request frequency;
- automated payment or checkout confirmation;
- bulk/resale automation.

Existing user-supplied custom-dictionary field filling may be preserved and
made reliable as ordinary form filling, but do not turn it into a mechanism for
bypassing access controls.

## 2. Establish the correct baseline before editing

1. Confirm `src/hunter_metadata.py` reports `0.5.0` before modification.
2. Confirm the source contains the v0.5.0 TicketPlus state fields:
   - `submission_pending`
   - `submission_deadline`
   - `submission_next_probe_at`
   - `queue_active`
   - refresh deadline/identity state
   - failure-retry state
3. Confirm `nodriver_ticketplus_order()` arms the submission watch after a
   successful submit.
4. Confirm `nodriver_ticketplus_main()` checks the submission/failure-retry
   ownership path on `/order/<event>/<session>`.
5. Confirm onsale and leak-watch intervals are resolved by HunterX's existing
   scheduler and `time.monotonic()` deadlines.
6. Record the baseline test results before any product change.
7. If the source lacks these items, stop treating it as the v0.5.0 baseline and
   locate the actual release-matching source rather than reconstructing from an
   older candidate.

## 3. Architectural invariants that MUST NOT regress

### 3.1 Refresh timing

- A configured one-second refresh remains a deterministic one-second target
  window; do not replace it with random multi-second sleeps.
- Onsale/formal mode and leak-watch mode keep independent mode-aware timing.
- No catch-up burst after a delayed loop.
- One refresh dispatch per owned window/generation.
- Every full reload continues through the existing guarded refresh coordinator.
- Protected checkout/confirmation/payment/queue-sensitive routes remain
  protected.
- Partial TicketPlus inventory refresh may be preferred when already supported,
  with guarded full reload only as fallback.

### 3.2 Leak-watch

- A loaded AREA document is scanned once when the configured leak-watch interval
  is positive.
- The main hot loop must not repeatedly query the same stale DOM while waiting
  for the next reload deadline.
- At the deadline, reload first; after a successful reload, scan the fresh
  document immediately.
- Interval `0` keeps its established semantics.

### 3.3 Submit / attempt ownership

- Exactly one active submit attempt owns its tab/route until a terminal outcome
  or a bounded, explicit recovery transition occurs.
- Never release ownership because a fixed amount of wall time elapsed while the
  page is in an ambiguous protected transition.
- Never submit a second order under a visible failure popup or unresolved
  submitted attempt.
- Use monotonic time for liveness, retry, cooldown, queue, and submit deadlines.

### 3.4 Multi-instance / per-tab state

- Do not replace `PlatformStateProxy` / per-tab state with upstream global
  `_state = {}` semantics.
- No state from one browser tab/profile/instance may satisfy or clear another
  tab's attempt.

### 3.5 Existing TicketPlus custom dictionary behavior

- Preserve HunterX's existing semantic discovery and fill of relevant
  user-supplied member/code/verification-style fields.
- Do not replace it with a narrower upstream implementation that loses
  recognized labels or immediate field fill.
- Do not change it into CAPTCHA/challenge bypass logic.

## 4. Upstream v2026.08.17 — what to port and what NOT to port

Upstream `v2026.08.17` contains two important TicketPlus behavioral fixes:

1. A purchase-failure popup must not be mistaken for a queue because both use a
   Vuetify overlay/scrim.
2. After submit, outcome detection should react promptly instead of performing a
   blind fixed multi-second wait.

### Port these behaviors

- failure-dialog evidence has higher precedence than queue evidence;
- generic overlay/scrim alone is never sufficient proof of queue state;
- confirmation URL, failure popup, and positive queue evidence are checked
  promptly after submit;
- monitoring has an absolute upper liveness bound so stale evidence cannot own
  execution forever.

### Do NOT copy these upstream implementation details wholesale

- nested `while` polling loops inside the platform handler;
- `time.time()` control deadlines where HunterX already uses monotonic time;
- fixed `0.3s` sleep loops as the primary state-machine architecture;
- randomized `5–10s` queue loops that bypass HunterX's outer scheduler;
- global module state that weakens per-tab isolation;
- direct `tab.reload()` paths that bypass HunterX guarded refresh ownership;
- any upstream refresh cadence that would replace the user's configured
  deterministic interval;
- any anti-detection/bypass feature outside the stated safety boundary.

The correct migration strategy is **behavioral cherry-picking, not file-level
replacement**.

## 5. P0 — must-fix / release-blocking checklist

### 5.1 TicketPlus

Audit and satisfy all of the following:

1. Failure popup vs queue misclassification
   - inspect all visible dialog containers, not merely the first `.v-dialog`;
   - any visible failure dialog vetoes queue classification;
   - generic overlay remains diagnostic evidence only;
   - explicit queue route or queue wording remains positive evidence only when
     no failure dialog is active.

2. Submit-result detection
   - no blind 5–10 second wait after successful submit;
   - successful submit arms one state-machine-owned watch;
   - first outcome probe may run immediately;
   - subsequent probes run only when `submission_next_probe_at` is due;
   - confirmation > failure > queue > pending/unknown precedence is explicit.

3. Failure -> inventory recovery
   - confirmed/dismissed failure transitions to the existing guarded
     inventory-refresh retry path;
   - blocked failure keeps submit ownership while bounded;
   - a permanently blocked failure must expire into guarded recovery, never an
     endless 0.15s probe loop;
   - no second submit is allowed while failure state is unresolved.

4. Queue exit / queue reset
   - a real queue may extend only the *soft* submission liveness deadline;
   - add a separate absolute monotonic queue hard deadline;
   - the hard deadline must never slide when queue is observed again;
   - at the hard boundary transition to guarded recovery without another
     submit;
   - confirmation/failure/activity-route reset clears all queue timing state.

5. Refresh recovery
   - preserve the existing mode-aware non-blocking refresh deadline;
   - preserve partial inventory refresh first where supported;
   - fallback reload must remain guarded;
   - no direct unowned reload on protected routes.

6. Partial-refresh retry path
   - verify a rejected inventory attempt can return to a fresh inventory state;
   - no hot-loop re-scan of unchanged state before the refresh window;
   - no catch-up bursts.

7. Stale submission ownership
   - `submission_pending` must be checked before a separate outer popup
     sanitizer on `/order/...`;
   - otherwise a permanently blocked dialog can starve the submission deadline;
   - active submitted attempt always has first route ownership.

### 5.2 TixCraft (regression audit; change only if failing)

- attempt identity/generation/tab boundary is preserved on second attempt;
- delayed submit cannot fall back into AREA/TICKET stale recovery prematurely;
- submit/reload guards recover only from positive retry evidence;
- Queue-It/challenge/checkout/payment remain protected;
- OCR and blocking inference remain off the async browser loop and readiness is
  explicit/bounded;
- manual/non-auto-submit flow is not stolen back by automation.

### 5.3 Ticketmaster (regression audit; change only if failing)

- activity/detail page can advance to the ticket page when entry becomes
  available;
- requested ticket quantity is exact by default;
- `allow_less_tickets` never selects more than requested and chooses the best
  allowed lower quantity only when enabled;
- stale/insufficient inventory retries are bounded and refresh-owned;
- queue/checkout/payment routes are protected.

### 5.4 KKTIX (regression audit; change only if failing)

- qualification selection -> field readiness -> fill -> state recheck -> next
  sequencing remains intact;
- queue exit does not leak stale qualification state;
- qualification/member-code state resets between attempts when appropriate;
- failed attempt can retry without stale ownership poisoning the next attempt;
- refresh is owned by the shared coordinator rather than local blocking sleeps.

## 6. P1 — high-priority hardening checklist

Audit these after P0 is green. Only edit production behavior if a reproducible
failure exists.

1. Login is verified by actual authenticated state, not merely field fill/click.
2. Member/code semantic matching handles real label/placeholder/container
   variants without filling unrelated account/password/email/phone fields.
3. Vue delayed rendering is bounded and does not trigger duplicate actions.
4. Site/domain variants remain explicit rather than being conflated by loose
   string matching.
5. IndieVox and other inherited platform variants retain validation evidence.
6. KKTIX scheduler ownership remains consolidated.
7. Browser/CDP disconnect recovery reports a real degraded state rather than
   spinning silently.
8. Per-tab and multi-instance state isolation remains deterministic.
9. Tab ownership prevents automation from closing user-created tabs.
10. Windows paths containing spaces remain valid.
11. Validation reports distinguish actual PASS from skipped/unavailable gates.
12. Refresh ownership is audited across every platform touched by this release.
13. TixCraft modular boundaries remain intact; do not introduce another giant
    cross-platform state singleton.
14. OCR cache and notification lifecycle changes, if any, remain bounded and do
    not leak across attempts.

## 7. P2 — maintainability / performance checklist

After P0/P1 correctness is established:

- eliminate redundant DOM/CDP queries in hot loops;
- reuse per-cycle snapshots only when ownership/attempt identity proves they are
  fresh;
- keep reload/navigation intent observable by reason and route class;
- rate-limit repeated identical logs without hiding state transitions;
- keep notifications idempotent per successful order/attempt;
- keep platform/variant metadata authoritative and centralized;
- validate PyInstaller hidden imports/runtime layout;
- validate Windows archive paths, CRC, file contents and release checksums;
- audit CI/dependency declarations without weakening existing gates.

No P2 optimization may trade away P0/P1 correctness.

## 8. Required TicketPlus v0.5.1 implementation shape

Use the existing state machine and add the minimum state necessary for absolute
liveness:

- `submission_started_at`
- existing `submission_deadline` (soft deadline)
- existing `submission_next_probe_at`
- existing `queue_active`
- `queue_started_at`
- `queue_deadline` (absolute, non-sliding)

Recommended constants (keep explicit, named and tested):

- submission soft liveness: 30.0 seconds;
- blocked-failure probe cadence: 0.15 seconds;
- ordinary pending probe cadence: 0.20 seconds;
- queue probe cadence: 1.0 second;
- absolute queue monitoring fuse: 600.0 seconds.

These values govern local state-machine liveness only. They must not alter the
user's configured page refresh interval.

### State transitions

#### Submit success

`idle -> submission_pending`

- record monotonic start;
- arm soft deadline;
- first probe due immediately;
- clear queue state.

#### Pending / unknown

- retain submit ownership;
- do not duplicate submit;
- next probe only at configured short cadence;
- if soft deadline expires, transition to guarded inventory recovery.

#### Failure dismissed

- verify dialog closure first;
- clear submission watch;
- schedule existing guarded inventory retry.

#### Failure blocked

- keep submission ownership;
- re-probe only at the blocked cadence;
- cap next probe at the soft deadline;
- at deadline transition to guarded inventory retry.

#### Queue first observed

- mark queue active;
- record `queue_started_at`;
- set `queue_deadline = now + hard_fuse` exactly once;
- set the soft deadline to `min(now + soft_window, queue_deadline)`.

#### Queue observed again

- **do not modify `queue_deadline`**;
- only slide the soft deadline, capped by the hard deadline.

#### Queue hard deadline reached

- do not perform another submit;
- do not keep polling forever;
- transition to guarded inventory recovery;
- clear submit/queue ownership via the existing centralized clear function.

#### Confirmation

- clear all submit/queue/retry transient state;
- preserve existing notification/payment handoff boundary.

## 9. Required queue/failure classifier semantics

TicketPlus DOM classification must satisfy:

1. Gather all visible relevant dialog containers:
   - `[role="dialog"]`
   - `.v-dialog`
   - `.v-dialog__content`
   - `.v-overlay__content`
2. Ignore hidden/stale dialog nodes.
3. Compute failure evidence across **all** visible dialog texts.
4. If any failure dialog exists, queue verdict is false regardless of:
   - overlay/scrim;
   - body queue keyword;
   - queue-looking dialog elsewhere;
   - explicit queue route cached in the same sample.
5. Overlay alone is never queue evidence.
6. Keep explicit queue route/queue wording support when no failure dialog is
   active.

## 10. Regression tests that MUST exist and be meaningful

### TicketPlus v0.5.1 tests

Add deterministic tests for at least:

1. non-sliding absolute queue deadline;
2. queue hard deadline checked before an extra outcome probe;
3. blocked failure expires at the submission deadline;
4. 1,000 main-loop/state-machine iterations before `next_probe_at` cause zero
   extra outcome probes;
5. active `submission_pending` owns the `/order/...` route before the outer
   failure sanitizer;
6. stale blocked popup with no active submission moves to guarded retry and
   does not submit;
7. failure evidence vetoes overlay + queue dialog + body keyword + explicit
   queue route evidence;
8. confirmation clears queue/submission state;
9. failure retry uses the active onsale/leak-watch interval;
10. no duplicate submit under pending/blocked/queue/unknown states.

### Test-fixture validity rule

Every route fixture must actually enter the production branch it claims to
exercise. For TicketPlus main order tests use a real shape such as:

`https://ticketplus.com.tw/order/<event>/<session>`

Do not use an extra `/tickets` segment if production route detection rejects it.
Add/assert route-shape preconditions if necessary so a test cannot pass without
executing the intended branch.

### Cross-platform regression suites

Rerun existing suites covering:

- refresh coordinator / dispatch recovery;
- TixCraft attempt and leak-watch liveness;
- Ticketmaster selection and page progression;
- KKTIX qualification/queue behavior;
- platform state/multi-instance isolation;
- settings/profile race hardening;
- release/archive verification;
- build/version metadata.

## 11. Test integrity rules

For every test run record:

- exact command;
- pass/fail/skip counts;
- environment limitations;
- any temporary test-only stub/dependency.

If `zendriver` is unavailable in a non-Windows validation container, a temporary
**test-only** import stub may be used only to load deterministic fixtures that do
not exercise a real browser. It must never be committed or packaged.

Tests requiring the real Zendriver runtime must be reported as unavailable in
that environment, not passed.

If `pytest-benchmark` is unavailable and project `addopts` references it, run
ordinary tests with the addopts override and separately report that the plugin
benchmark gate was unavailable; do not rename smoke calls as benchmarks.

A skipped test is never a pass.

## 12. Mandatory validation gates

Run, fix, and rerun until green where the environment supports them:

1. `python -m compileall -q src tests scripts`
2. Python AST parse of all project `.py` files
3. JSON/TOML/YAML parse validation
4. `git diff --check`
5. JavaScript `node --check` for project JS files if Node is available
6. focused TicketPlus v0.5.0 + v0.5.1 tests
7. refresh/cross-platform focused suite
8. full deterministic pytest suite
9. repeat TicketPlus v0.5.1 liveness suite at least 20 times
10. long-loop/soak tests that are deterministic and feasible
11. release metadata/version consistency tests
12. source ZIP safety/content/CRC verification
13. Windows ZIP safety/content/CRC verification
14. checksums generated from final bytes and reverified

If Ruff/mypy/Bandit/pip-audit/PyInstaller/native Windows execution is not
available, report it as unavailable. Do not fabricate results.

## 13. Negative/regression validation

Prove the new tests can detect the old defect where feasible:

- run the new queue hard-fuse test against v0.5.0 and confirm it fails because
  the hard deadline did not exist / slid indefinitely;
- run the blocked-failure bounded test against v0.5.0 and confirm it fails;
- ensure the corrected duplicate-submit fixture reaches the real order branch.

Do not keep a deliberately failing baseline test in the final suite; document
this negative validation in the test report.

## 14. Version and documentation update

Set all current release-facing version metadata to `0.5.1`, including:

- `src/hunter_metadata.py`
- settings UI version text
- README current version/build examples/artifact names
- current build helper documentation
- CHANGELOG top section
- new `RELEASE_NOTES_v0.5.1.md`

Retain historical v0.5.0 notes/reports as history; do not rewrite history to
pretend v0.5.1 existed earlier.

## 15. Source artifact requirements

Create a final source archive named:

`hunterX_source_0.5.1.zip`

Requirements:

- one versioned root directory: `hunterX-0.5.1/`;
- no absolute paths;
- no `..` traversal;
- no backslash archive-member names;
- no transient test stub, `.git`, caches, `dist`, or local secrets;
- include v0.5.1 source, tests, CHANGELOG, release notes, Codex master prompt,
  validation report, and build scripts;
- verify CRC and extractability after creation.

## 16. Windows packaged artifact requirements

Preferred final artifact:

`hunterX_windows_0.5.1.zip`

The package must retain directly launchable `settings.exe` and
`nodriver_tixcraft.exe` plus their internal runtime directories.

If using the verified v0.5.0 Windows runtime as a binary base, first prove that
its executables load external `app_src` and that overlaying v0.5.1 source is the
established packaging design. Do not claim a native rebuild if no Windows /
Python 3.11 / PyInstaller build actually occurred.

After overlay:

- replace both runtime `app_src` trees with the final v0.5.1 source;
- synchronize shared assets/www files as required by the package design;
- remove old source-only caches/test stubs;
- verify executable files remain present and non-empty;
- verify archive member names, CRC and extraction;
- run available packaged source/version smoke checks;
- clearly label the artifact as an overlay-on-verified-runtime build if that is
  what was actually produced.

Never describe a Linux-side repack as a native Windows execution test.

## 17. Final release checksums

Generate `SHA256SUMS_v0.5.1.txt` from the exact final artifact bytes, at minimum
covering:

- `hunterX_source_0.5.1.zip`
- `hunterX_windows_0.5.1.zip` (or split parts if size limits require them)

Immediately recompute and compare each digest after writing the checksum file.

## 18. Required final validation report

Create `TEST_REPORT_v0.5.1.md` containing:

- baseline identity and provenance;
- upstream comparison (`v2026.08.07 -> v2026.08.17` behavioral findings);
- exact production files modified;
- exact tests added/fixed;
- the invalid old `/tickets` TicketPlus fixture discovery and correction;
- every command actually run;
- every pass/fail/skip result;
- failures encountered during development and their root cause/fix;
- repeated-run/soak results;
- archive verification;
- checksum verification;
- explicit environment limitations;
- explicit statement that no real purchase/payment/CAPTCHA bypass/queue bypass
  was performed.

## 19. Section 31 — upstream features worth moving into HunterX

Treat the following as a prioritized behavioral-port list:

### High value / move as behavior

- immediate post-submit outcome observation;
- purchase-failure popup vs queue discrimination;
- absolute queue liveness fuse;
- Ticketmaster activity-page recovery;
- Ticketmaster exact quantity and safe insufficient-quantity retry;
- KKTIX qualification-to-next sequencing and retry/reset correctness;
- browser disconnect reporting/recovery behavior where compatible;
- Windows path-with-spaces correctness;
- ownership/close-only-bot-created-tab behavior;
- focused regression tests for each real bug.

### Move only if HunterX lacks an equivalent

- platform-specific selector/layout fallbacks;
- clearer diagnostic messages;
- user-facing defaults such as exclusions, only through migration/default logic
  that never overwrites existing profiles;
- partial refresh mechanisms when they preserve the shared scheduler.

### Do not move / replace

- whole platform files when HunterX has stronger state/guards;
- random refresh timing that weakens configured deterministic refresh;
- blocking sleeps/nested polling replacing outer event-loop ownership;
- global state replacing per-tab isolation;
- direct unguarded reload/navigation;
- anti-detection/risk-control bypass mechanisms;
- CAPTCHA/queue bypass or automated payment.

## 20. Section 32 — most important conclusion

The central design rule for v0.5.1 and future upstream syncs is:

**HunterX should absorb upstream bug-fix knowledge and observable behavior, not
surrender its architecture.**

For every upstream change ask:

1. What user-visible defect did upstream fix?
2. Does HunterX already solve all or part of it differently?
3. Can the behavior be expressed as a regression test first?
4. What is the smallest HunterX-native change that makes that test pass?
5. Does the change preserve refresh timing, attempt ownership, protected routes,
   multi-instance state, and existing user settings?
6. Does the full suite prove no regression?

If HunterX is already stronger, keep HunterX and add only the missing edge-case
hardening/test. If upstream is stronger in a narrow behavior, port that behavior
behind HunterX's existing scheduler/state/guard contracts.

Do not measure success by number of upstream lines copied. Measure success by:

- fewer unbounded states;
- fewer false classifications;
- faster legitimate state observation without hot-loop load;
- deterministic refresh semantics;
- no duplicate submit;
- no stale state poisoning the next attempt;
- honest, reproducible validation evidence;
- releasable source and Windows artifacts.

## 21. Definition of Done

v0.5.1 is done only when all of the following are true:

- production code implements the bounded TicketPlus state machine described
  above;
- no existing HunterX strength was intentionally replaced by weaker upstream
  behavior;
- meaningful P0 regressions are green;
- P1/P2 touched areas are audited and green;
- all feasible deterministic tests pass after the final code change;
- repeated TicketPlus runs pass without intermittent failures;
- source and Windows artifacts are created from the final same source revision;
- artifact version strings are `0.5.1`;
- archive safety/CRC/checksum checks pass;
- no temporary Zendriver stub or local cache is packaged;
- `TEST_REPORT_v0.5.1.md` truthfully records every limitation;
- final delivery includes source, packaged build, checksums, report, release
  notes, and this Codex instruction file.
