# HunterX v0.5.2 Pre-Final Baseline Audit

Audit date: 2026-08-25 (Asia/Taipei)
Product baseline: public GitHub `main` at `bbdd1f1a9dc02fef37da18a43e7f20f928b39ffd`
Repository: `https://github.com/sssss928/hunterX`

## Freeze and provenance

- A new clone was created from the public repository with tags excluded.
- `git rev-parse HEAD` returned the exact 40-hex commit above.
- `git status --porcelain=v1 --untracked-files=all` was empty before this audit report was added.
- The product declares `APP_VERSION = "0.5.2"` in `src/hunter_metadata.py` and RC3 branding in `BUILD_INFO.txt`.
- Local immutable RC3 source ZIP: `hunterX_source_0.5.2_rc3.zip`, 11,541,977 bytes, SHA-256 `00B02901A90D4F4A71BA9F8645738306B2C224F0EB076ADC347DCEC9A23510CE`.
- Local immutable RC3 Windows ZIP: `hunterX_windows_0.5.2_rc3.zip`, 373,605,001 bytes, SHA-256 `253995951F0080042F685B2CE1A66C0B77D14DDDC67610F5807221F30351E1C2`.
- Approved RC2 Windows base: `hunterX_windows_0.5.2_rc2.zip`, 373,588,805 bytes, SHA-256 `47747A962CF5C4AE49654AEC574CA64AC52C27032FC5B1EC1F70D83C3D09DA48`.

The immutable local RC3 source archive predates later GitHub web edits. It is therefore evidence and a comparison base, not permission to replace the latest GitHub source tree.

## Production-code change gate

No `src/**` file was modified before this baseline audit. Production code may be changed only after a deterministic production defect is reproduced against this frozen baseline. A workflow, test, report, or external-release failure is not by itself permission to change purchasing logic.

## Reproduced baseline failures

### P0 — latest main cannot pass source validation

The latest commit changed `tests/test_settings_control_hardening.py`. The function body beginning near line 1148 lost indentation, while lines 1170 onward retained it.

Independent negative controls on the unmodified baseline:

- `python -m ruff check .` failed with `invalid-syntax: Unexpected indentation` at line 1170.
- `python -m compileall -q src tests scripts` failed with `IndentationError` at the same line.
- `python -m pytest --collect-only -q` collected 1,107 tests but terminated with the same collection error.

This is the root cause of the latest Linux CI Ruff failure. Restoring the intended test function indentation is a test synchronization repair, not a production behavior change.

### P0 — Windows CI and Release depend on an absent GitHub release

Both `.github/workflows/ci.yml` and `.github/workflows/release.yml` execute `gh release download v0.5.2-rc2 --pattern hunterX_windows_0.5.2_rc2.zip` and then verify the approved SHA-256. The public GitHub Releases API currently lists releases only through v0.5.1; `v0.5.2-rc2` is absent.

Observed GitHub evidence:

- CI run `32749278747`: Linux job failed at Ruff; Windows job failed at the RC2 base download step.
- Earlier CI run `32748525280`: Linux test/lint/audit passed completely; Windows still failed at the RC2 base download step.
- Release run `32747446688`: source validation failed and downstream asset jobs were skipped.
- CodeQL run `32749278817`: passed.

Public job annotations expose the failing step and exit code, but unauthenticated job-log download returned HTTP 403. That limitation must not be presented as a passing gate.

The approved local RC2 Windows ZIP has the exact hash expected by the workflows. Publishing that immutable byte sequence under the required prerelease tag is an external repository-state repair; changing the expected hash or silently falling back to another runtime is forbidden.

### P1 — required dictionary acceptance report is absent

`FINAL_LAYER_USER_DICTIONARY_ACCEPTANCE.md` is not present at the repository root. Existing dictionary tests and reports are claims that still require independent runtime and packaged verification. The missing report may be added only with actual evidence; it must not merely repeat earlier PASS text.

## Existing release claims requiring re-verification

- `FINAL_LAYER_PERFORMANCE_REPORT.md` claims no reproducible RC2-to-RC3 hot-path regression after focused repeats. This is not accepted as a current Final gate until a new balanced A/B run is executed.
- `FINAL_LAYER_LONG_RUN_REPORT.md` records only approximately two-minute single- and three-instance browser exercises.
- The same report explicitly states `8H SOAK NOT VERIFIED`; neither the 8-hour single-instance gate nor the 8-hour three-named-instance gate was executed.
- `FINAL_LAYER_ARTIFACT_VERIFICATION.md` describes a strict RC3 pair/parity contract. The local artifacts must be re-verified with the current scripts before their bytes can be used as release evidence.

## Baseline decision

The frozen baseline is RC3/pre-final and is not FINAL-eligible. Work may proceed with the smallest evidence-driven repairs. FINAL branding remains forbidden unless every mandatory gate—including both real 8-hour browser gates and current GitHub Actions/Release gates—has genuinely passed.
