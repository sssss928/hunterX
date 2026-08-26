# HunterX v0.5.2 RC3 Historical Failure/Fix Log

> Preserved RC3 evidence. The later FINAL naming decision and explicit 8H
> waiver are recorded in `FINAL_RELEASE_AUDIT_v0.5.2.md` and
> `FINAL_8H_SOAK_WAIVER.json`.

## FL-01 — malformed settings control test

- Pre-fix: Ruff, full compilation, and pytest collection failed at line 1170.
- Cause: partial de-indentation during a GitHub web edit.
- Fix: restored the intended function scope and retained the new browser-open event synchronization.
- Post-fix: single reproducer, 189 focused tests, 20 fresh critical runs, and two full suites passed.

## FL-02 — workflow gate detected syntax too late

- Pre-fix negative controls: two new workflow assertions failed.
- Fix: compile `src tests scripts`; invoke CI pytest and benchmark via `python -m pytest`.
- Post-fix: workflow tests and static gates passed.

## FL-03 — omitted release-contract reports

- Pre-fix: current GitHub tree lacked files named by `TOP_LEVEL_DOCUMENTS`, RC2 reports, and the Final-Layer dictionary report.
- Cause: later upload commits omitted files present in immutable RC3.
- Fix: restored the exact documents as evidence claims and added the new pre-final report set to the RC3 required manifest.
- Post-fix: focused release suite passed; final build remains gated on a clean commit.

## FL-04 — reported all-platform dictionary failure

- Result: NOT REPRODUCED.
- Evidence: 29 focused source/runtime tests, 20-process critical repeats, full suites, and packaged source parity.
- Production change: none.

## FL-05 — reported large RC3 slowdown

- Initial screen: noisy positive and negative deltas.
- Investigation: five high-density paired paths plus a separate 10-pair wrong-target run.
- Result: NOT REPRODUCED; final wrong-target delta median -0.26%, p95 +0.08%.
- Production change: none.

## FL-06 — Windows CI/Release base unavailable

- Cause: public GitHub prerelease `v0.5.2-rc2` is absent.
- Integrity response: no hash weakening and no substitute base.
- Fix: normal branch/PR Windows CI now runs source-based Windows runtime and
  release-contract tests and no longer downloads the unpublished RC3 asset.
  The optional artifact rebuild workflow is manual-only and remains strictly
  locked to the exact RC3 name/hash.
- Status: local workflow tests pass; a new public GitHub run is still required
  after the commit is pushed.

## FL-07 — Windows package root contained historical engineering clutter

- Pre-fix evidence: the previous delivered Windows ZIP had 70 top-level items,
  mostly reports inherited through RC, RC2, and RC3.
- Fix: FINAL packaging now uses a fail-closed root allowlist, retains both
  executables and their `_internal` runtime trees at root, keeps user documents
  at root, and organizes technical evidence under `docs/release-audit/`.
- Negative control: an unknown top-level directory is rejected instead of
  being silently deleted.
- Post-fix evidence: focused archive/builder tests and 20 fresh critical runs
  pass; the exact final item count is recorded after artifact construction.

## FL-08 — local actual-browser fixture startup race

- Pre-fix evidence: a three-process Edge run failed with
  `TypeError: Cannot read properties of undefined (reading 'push')` before the
  local fixture had installed `window.syntheticTicket`.
- Root cause: `driver.get()` established a target before fixture JavaScript
  initialization was observable.
- Fix: the soak harness now waits for the exact `push`, `replace`, and
  `rerender` API, bounded to five seconds, after initial load, replacement, and
  reload. Missing readiness fails closed.
- Post-fix evidence: focused production-iteration tests 28/28, 20 fresh
  critical runs 180/180, full suites 1198/1198 twice, single Edge 63 cycles,
  and three-process Edge 69/70/70 cycles. All post-fix browser runs had zero
  errors, CDP errors, or duplicate-submit claims.
- Product scope: no source under `src/` changed for this harness fix.

## Mandatory unresolved gate

At this historical checkpoint: **8H SOAK NOT VERIFIED**, so the artifact
remained RC3/pre-final. The later user waiver does not convert this into PASS.
