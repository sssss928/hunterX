# HunterX v0.5.2 FINAL Release Notes

HunterX v0.5.2 completes the cross-platform attempt-lifecycle, browser
recovery, user-dictionary, and packaging hardening developed through RC, RC2,
and RC3.

## Main changes

- Rearms automation only after a positively classified safe activity/date/area
  transition while preserving the prior attempt's duplicate-submit fence.
- Treats ambiguous submit outcomes, queue/payment/checkout routes, and stale
  callbacks as fail-closed.
- Routes terminal browser failures to the authoritative lifecycle owner for
  bounded reacquire, transport rebind, safe restart, or fail-closed handling;
  clean user browser closure does not cause repeated reopening.
- Keeps TicketPlus login return, TixCraft central/inner attempt ownership,
  expected-progress detection, formal/leak-watch modes, interval=0,
  ReloadGuard, RefreshCoordinator, and LeakWatchScheduler under one owner.
- Preserves user custom-dictionary parsing across supported text-question
  platforms, including hot reload, multiline online content, delimiter
  preservation, and safe JSON encoding for KKTIX.
- Builds both isolated PyInstaller runtimes directly from one clean exact
  commit, with fresh-extract native smoke, source/runtime parity, schema-2
  source-native provenance, and exact checksums. No RC2/RC3 release asset is
  required.
- Keeps the Windows root concise: required executables/runtimes and end-user
  documents remain at the top level, while technical evidence is organized in
  `docs/release-audit/`; stale reports inherited from earlier bases are removed.
- Consolidates RC3 and FINAL automation into one manual, dry-run-first v0.5.2
  workflow. Publishing requires an explicit boolean input and refuses to
  overwrite an existing immutable `v0.5.2` tag or Release.

## Important qualification statement

The user explicitly waived the two eight-hour actual-browser gates. They are
**not verified and are not claimed as passed**. This decision is recorded in
`FINAL_8H_SOAK_WAIVER.json` and `FINAL_BUILD_PROVENANCE.json`. All other release
gates remain mandatory.

This project does not implement CAPTCHA, Queue-it, challenge, risk-control,
checkout, or payment bypasses. Use it only where legal and permitted by the
ticketing platform.
