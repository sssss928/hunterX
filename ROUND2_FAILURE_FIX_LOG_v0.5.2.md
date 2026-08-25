# HunterX v0.5.2 Round 2 — Observed Failure / Fix Log

This log records failures actually observed during the Round-2 audit. A failure
is not converted to PASS by skipping, xfail, assertion weakening, or fixture
avoidance. Each Stop-the-Line entry required a root-cause change followed by a
direct reproducer, focused regression, repeated execution, and adjacent tests.

## Immutable negative-control baseline

- Base: the unpacked Round-1 `hunterX_source_0.5.2_rc.zip`, frozen under
  `work/v052/round2/base-immutable`.
- Candidate development did not restart from v0.5.1.
- The Round-1 full suite passed 842 tests before Round-2 changes.
- Twelve Round-2 direct negative controls demonstrated the documented P0/P1
  gaps on the immutable base. Baseline evidence is retained outside the release
  source under `work/v052/round2/evidence/r2-0`.

## Stop-the-Line loops

### R2 browser target and recovery proof

Observed:

- A sole same-platform tab for another event could be selected as recovery.
- Empty target intent could hijack a platform tab.
- Duplicate canonical targets were selected arbitrarily.
- `TRANSPORT_REBIND` could report success while the selected target websocket
  raised `ConnectionError`.
- Restart used a reduced factory and skipped initial bootstrap prerequisites.

Fix:

- Recovery now fails closed unless a unique canonical target or the exact saved
  owned target ID proves identity.
- Reacquire and transport rebind use a bounded, read-only target-info CDP proof
  before committing `active_tab`.
- Initial start and safe restart share one full bootstrap owner.

Verification included wrong-event, empty-target, duplicate-target, dead cached
transport, saved-target route advance, and complete-bootstrap negative controls.

### Readable URL with no expected progress

Observed: loop/readability timestamps could remain healthy while an expected
reload, navigation, or submit transition made no progress. A naive idle timer
also produced false-positive designs for presale, leak mode, and interval zero.

Fix: attempt-scoped expected-progress expectations now carry tab identity,
attempt ID/generation, action owner/token, acceptable transitions, and a
deadline. Protected and submit-sensitive expiry remains passive/fail-closed;
only the existing recovery owner may mutate browser state.

### TicketPlus stale submission watcher

Observed: watcher N could resume after an await and mutate newly rearmed attempt
N+1 to `SUBMIT_OUTCOME_UNKNOWN`.

Fix: the watcher snapshots attempt ID/token at entry and revalidates both the
per-tab mapping and central attempt after every browser await. A stale watcher
only emits a bounded diagnostic and returns; it cannot cancel N+1 progress.

The deterministic race passed in 20 fresh processes after the fix.

### TixCraft process-global dispatch state

Observed: an explicit tab could resolve to `_default_state`, causing tab A/B to
share refresh and lifecycle data.

Fix: every explicit tab resolves to its PlatformEngine-owned mapping. The
process mapping remains only for direct legacy calls with `tab is None`, with a
once-per-process diagnostic.

### TixCraft local/central submit conflict

Observed: route text alone could clear the inner TixCraft submit state while the
central `PurchaseAttempt` remained protected, or rearm the central generation
without positive interactive AREA proof.

Fix: the inner attempt carries the exact central attempt ID, generation, submit
token, and safe-route proof. Route-only AREA observations do not clear either
owner. Confirmed interactive AREA evidence rearms both lifecycles together.

### TixCraft rejected-submit central fence leak

Observed on the candidate during final red-team: retryable/captcha reset cleared
the local `submit_in_flight` watcher but retained the exact central submit claim
and safe-rearm proof. A later valid submit on the same ticket form could never
claim the central owner. The failing JUnit is retained as
`work/v052/round2/evidence/p0-tixcraft-retry-release/pre_fix_failure.xml`.

Fix:

- `PlatformEngine.release_rejected_submit_if_owned` releases only an exact
  attempt ID + generation + submit token and removes only its matching proof.
- Ambiguous reset preserves the local and central fence.
- Only confirmed captcha/retryable/rejected/canceled/continue-shopping evidence
  may request the exact release.
- Stale owner/token release is rejected without changing the current attempt.

Post-fix direct and production-path suites are retained in the same evidence
directory. Focused rejection/bridge/recovery tests passed 56 tests; all tests
whose filenames contain `tixcraft` passed 239 tests; adjacent lifecycle,
production-iteration, route, multitab, and cross-platform suites passed 229.

### TixCraft delayed global-alert callback race

Observed after the preceding fix: CDP delivered an alert while attempt N was
current, but its coroutine first executed after N+1 was armed. The async handler
then reread shared state and released N+1.

Fix: the registered handler is a zendriver-compatible call-time coroutine
factory. It snapshots the immutable `TixCraftSubmitInFlight` at event delivery;
reset and recovery require that exact object to remain current after scheduling
and after awaits. A stale callback only defers and returns. The same production
callback still releases the exact current owner, preserving liveness.

The deterministic production callback race passed 20/20 fresh processes after
the fix.

### Terminal browser exceptions hidden by broad handlers

Observed: browser-interaction handlers could return ordinary fallback values for
terminal disconnect/target/session failures.

Fix: an AST audit enumerates browser-interaction broad handlers and requires the
terminal classifier as the first effective statement. The final audited source
set contains 466/466 classified handlers and zero manual-review dispositions.
Normal DOM-not-found and unsupported-operation fallbacks remain unchanged.

During this audit, an ambiguous zero-context mechanical patch temporarily
appended terminal guards after the end of `kktix.py`, producing an
`IndentationError`. Work stopped immediately, both affected platform files were
restored to their pre-batch bytes, and the audit was redone using unique AST
handler context with compile/audit after each small batch. No broken intermediate
source was retained; the final KKTIX/HKTicketing focused, repeated, adjacent,
compile, Ruff, and global AST gates passed.

### Test-contract conflicts found during integration

Two legacy tests assumed that an AREA URL string alone cleared a TixCraft submit.
Production correctly remained protected under the new positive-DOM contract.
The tests were changed to first assert that route-only evidence does not clear,
then supply confirmed interactive AREA health through the real reconciliation
path. Production was not weakened to satisfy the old expectation.

The first final full-suite run stopped at `1122 passed, 2 failed`. Both failures
were in `test_v044_runtime_ntp_coordination.py`: embedded direct-test scripts
still seeded TixCraft's retired process fallback while invoking production with
an explicit tab. One stale pending object therefore remained unserializable and
one leak scheduler was not the scheduler used by production. The tests were
changed to seed `platform._state_for_tab(tab)` and bind that same mapping around
the direct internal helper, matching the real entrypoint contract. No production
assertion was weakened. The two direct tests passed, the complete file passed 8,
and per-tab/refresh/production/route/cross-platform adjacent suites passed 181
before the full-suite count was restarted from run 1.

## Environment and runner limitations

### Cross-platform user dictionary

The initial focused negative control produced 18 failures and 3 passes. The
common parser only accepted a narrow legacy JSON fragment; settings display/save
lost escaping; the online file reader consumed one line; FamiTicket used a
nonexistent key; HKTicketing/iBon split storage syntax; KKTIX interpolated raw
JavaScript; and TeamEar/Ticketek AU were absent from dispatch coverage.

One lossless parser/serializer/merge contract now feeds migration, save, hot
reload and all text-question-capable platform families. Post-fix evidence is 26
focused, 520/520 fresh-process twice, 441 adjacent twice and two final 1150-test
full suites. Cityline, FunOne and FANSI GO have no such text-question field, so
the fix does not inject dictionary values into unrelated flows.

During adjacent integration, ten legacy TicketPlus tests failed only after the
adapter contract file. Isolation proved a test-only PlatformEngine ContextVar
leak. A file-local before/after cleanup fixture restored isolation without
changing production or assertions; the combined set then passed 65.

- A non-repository Python alias initially selected the wrong runner; all formal
  evidence uses the pinned Python 3.11 runtime recorded by the Round-2 audit.
- One focused command referenced the nonexistent filename
  `test_v052_ticketplus_submission_outcomes.py` and therefore collected no
  tests. The command was corrected to the real v0.5.1/v0.5.0 submission
  liveness/outcome files; the intended TicketPlus set then passed 64 tests.
- The first final Node syntax command used `node` from `PATH`, which was absent.
  PowerShell also did not propagate CommandNotFound through `$LASTEXITCODE`, so
  that combined command's zero exit was explicitly rejected as evidence. The
  four JavaScript files were rerun with the Codex workspace's absolute Node.js
  executable and passed 4/4.
- The first final `pip-audit` command used the conventional but nonexistent
  `requirements.txt`; the repository's production input is `requirement.txt`.
  The invalid-input result was rejected, then the correct production file was
  audited and reported no known vulnerabilities. The high-severity/high-
  confidence Bandit gate also passed.
- A later exploratory `pip-audit` of the entire temporary runner reported 17
  advisories in the runner's own pip/setuptools/unrelated installed
  cryptography. It was not treated as product evidence. The authoritative
  workflow command `pip-audit -r requirement.txt` was rerun and passed with no
  known vulnerabilities.
- The first final PowerShell parallel static command omitted the call operator
  before quoted executable paths, causing four parser errors before any gate
  ran. The commands were corrected with `&`; compileall, Ruff, strict mypy and
  Node syntax all passed.
- Local actual-browser tests navigate only synthetic loopback pages and do not
  exercise third-party ticketing, CAPTCHA, Queue-it, challenge, risk-control,
  checkout, payment, or bypass behavior.
- `8H SOAK NOT VERIFIED`; therefore this work may produce RC2 artifacts only.

## Final-gate status

This document is updated before packaging. RC2 is not accepted until clean
exact-commit source/Windows builds, joint embedded-source parity, fresh-extract
packaged smoke, full fresh-process suites, static/type/security checks,
performance A/B, hashes, and artifact verification all pass.
