# HunterX v0.5.2 Final GitHub Actions Audit

Audit date: 2026-08-25. Public repository state was queried independently; archived reports were not accepted as proof.

## Current public runs

- CodeQL run `32749278817` at `bbdd1f1...`: success.
- CI run `32749278747`: failed. Linux job `97502106688` failed at Ruff because of the reproduced malformed test; Windows job `97502106300` failed at the RC2 base download step.
- CI run `32748525280`: Linux test/lint/audit succeeded; Windows still failed downloading the absent RC2 base.
- Release run `32747446688`, job `97496348578`: source validation failed; asset build and publish were skipped.

Unauthenticated download of complete GitHub job logs returned HTTP 403, so only public job annotations, step states, repository content, and local exact-SHA reproductions are claimed.

## Local corrections

- Repaired the exact Ruff/collection defect.
- Expanded workflow compilation to `src tests scripts`.
- Standardized pytest execution through `python -m pytest`.
- Preserved the exact RC2 Windows-base filename and SHA check.

## Remaining external blocker

The public Releases list currently ends at v0.5.1. The workflows require prerelease tag `v0.5.2-rc2` containing the byte-approved `hunterX_windows_0.5.2_rc2.zip`. Until that immutable asset is published and this exact candidate is pushed, rerun results cannot be claimed green.

Status: **LOCAL FIX VERIFIED; REMOTE ACTIONS NOT YET VERIFIED**.
