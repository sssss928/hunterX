# HunterX v0.5.2 FINAL Release Audit

Release decision: **FINAL delivery requested by the user with an explicit waiver of the two eight-hour actual-browser gates.**

## Qualification truth

- Eight-hour single-instance actual-browser soak: **USER WAIVED / NOT VERIFIED**.
- Eight-hour three-named-instance actual-browser soak: **USER WAIVED / NOT VERIFIED**.
- The machine-readable authority is `FINAL_8H_SOAK_WAIVER.json`.
- `FINAL_BUILD_PROVENANCE.json` must record both gate values as false,
  `qualification_mode=USER_WAIVED_8H_GATES`, and `final_eligible=false`.
- No report or artifact may reinterpret the waiver as a PASS.

## Release scope

The release preserves the RC3 production source while adding only release-gate,
workflow, report, and packaging changes. The v0.5.2 lifecycle fixes remain in
force: attempt-scoped rearm, duplicate-submit fencing, unknown-submit
fail-closed behavior, bounded browser recovery, target ownership, expected
progress monitoring, deterministic refresh ownership, and per-tab/per-instance
state isolation.

The shared user dictionary remains restricted to supported text-question
handlers. It is not wired to CAPTCHA, login credentials, Queue-it, challenge,
risk-control, checkout, payment, or other protected flows.

## Required non-waived gates

FINAL packaging remains fail-closed on a clean exact commit, project-version
match, focused and full regression, static/type/security gates, Windows build,
fresh-extract executable smoke, source-to-both-runtime byte parity, archive
safety checks, and exact SHA-256 generation. A failure in any of those gates
invalidates the artifact set.

## Final conclusion

The files named `*_final.zip` are an explicit user-directed FINAL delivery,
not an eight-hour-qualified release. Their validity is established by the
companion test report, provenance inside the Windows ZIP, pair verification,
and `SHA256SUMS_v0.5.2_FINAL.txt`.
